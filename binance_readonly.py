from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests


BASE_URL = "https://api.binance.com"


class BinanceReadOnlyError(RuntimeError):
    pass


class BinanceReadOnlyClient:
    """Cliente HMAC de solo lectura para Binance Spot.

    Este módulo NO implementa endpoints POST/PUT/DELETE de trading.
    """

    def __init__(self, api_key: str, api_secret: str, timeout: int = 12):
        if not api_key or not api_secret:
            raise ValueError("API key y secret son requeridos.")
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip().encode("utf-8")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "X-MBX-APIKEY": self.api_key,
            "User-Agent": "TraderAgentReadOnly/3.3",
        })

    def _server_time_ms(self) -> int:
        try:
            r = self.session.get(f"{BASE_URL}/api/v3/time", timeout=self.timeout)
            r.raise_for_status()
            return int(r.json()["serverTime"])
        except Exception:
            return int(time.time() * 1000)

    def _signed_get(self, path: str, params: dict | None = None):
        payload = dict(params or {})
        payload["recvWindow"] = 5000
        payload["timestamp"] = self._server_time_ms()

        query = urlencode(payload, doseq=True)
        signature = hmac.new(
            self.api_secret,
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        url = f"{BASE_URL}{path}?{query}&signature={signature}"
        r = self.session.get(url, timeout=self.timeout)
        if not r.ok:
            try:
                body = r.json()
                msg = body.get("msg", str(body))
                code = body.get("code", r.status_code)
            except Exception:
                code = r.status_code
                msg = r.text[:250]
            raise BinanceReadOnlyError(f"Binance API {code}: {msg}")
        return r.json()

    def permissions(self) -> dict:
        return self._signed_get("/sapi/v1/account/apiRestrictions")

    def account(self) -> dict:
        return self._signed_get("/api/v3/account", {"omitZeroBalances": "true"})

    def open_orders(self, symbol: str) -> list[dict]:
        return self._signed_get("/api/v3/openOrders", {"symbol": symbol})

    def trades(self, symbol: str, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        return self._signed_get("/api/v3/myTrades", {
            "symbol": symbol,
            "limit": limit,
        })


def balance_map(account: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in account.get("balances", []):
        free = float(item.get("free", 0) or 0)
        locked = float(item.get("locked", 0) or 0)
        if free != 0 or locked != 0:
            out[item["asset"]] = {
                "free": free,
                "locked": locked,
                "total": free + locked,
            }
    return out


def base_asset_from_symbol(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


def summarize_protection(open_orders: list[dict]) -> dict | None:
    """Extrae niveles de protección de órdenes SELL abiertas.

    Para OCO / order-list Spot, normalmente habrá una orden LIMIT de TP
    y una STOP_LOSS_LIMIT con stopPrice + price.
    """
    sells = [
        o for o in open_orders
        if str(o.get("side", "")).upper() == "SELL"
        and str(o.get("status", "")).upper() in {"NEW", "PARTIALLY_FILLED", "PENDING_NEW"}
    ]
    if not sells:
        return None

    take_profit = None
    stop_trigger = None
    limit_sl = None
    qty = 0.0
    order_list_ids = set()

    for o in sells:
        price = float(o.get("price", 0) or 0)
        stop_price = float(o.get("stopPrice", 0) or 0)
        orig_qty = float(o.get("origQty", 0) or 0)
        executed = float(o.get("executedQty", 0) or 0)
        qty = max(qty, max(0.0, orig_qty - executed))
        if o.get("orderListId") not in (None, -1, "-1"):
            order_list_ids.add(str(o.get("orderListId")))

        if stop_price > 0:
            stop_trigger = stop_price
            if price > 0:
                limit_sl = price
        elif price > 0:
            if take_profit is None or price > take_profit:
                take_profit = price

    return {
        "take_profit": take_profit,
        "stop_trigger": stop_trigger,
        "limit_sl": limit_sl,
        "qty": qty,
        "order_list_ids": sorted(order_list_ids),
        "orders": sells,
    }


def permission_is_read_only(p: dict) -> bool:
    if not p.get("enableReading", False):
        return False
    risky = [
        "enableSpotAndMarginTrading",
        "enableWithdrawals",
        "enableMargin",
        "enableFutures",
        "enableVanillaOptions",
        "enablePortfolioMarginTrading",
        "enableFixApiTrade",
    ]
    return not any(bool(p.get(k, False)) for k in risky)
