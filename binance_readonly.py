from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests


PRIVATE_BASE_URLS = (
    "https://api.binance.com",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
)

BLOCKED_HTTP_CODES = {403, 418, 429, 451}


class BinanceReadOnlyError(RuntimeError):
    pass


class BinanceLocationRestricted(BinanceReadOnlyError):
    pass


class BinanceAuthenticationError(BinanceReadOnlyError):
    pass


class BinanceRateLimitError(BinanceReadOnlyError):
    pass


class BinanceReadOnlyClient:
    """Cliente HMAC GET-only para Binance.

    Solo implementa lectura. No contiene POST/PUT/DELETE ni endpoints de trading.
    Intenta los endpoints REST oficiales documentados por Binance y conserva
    el primero que responda correctamente.
    """

    def __init__(self, api_key: str, api_secret: str, timeout: int = 12):
        if not api_key or not api_secret:
            raise ValueError("API key y secret son requeridos.")
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip().encode("utf-8")
        self.timeout = int(timeout)
        self.session = requests.Session()
        self.session.headers.update({
            "X-MBX-APIKEY": self.api_key,
            "User-Agent": "TraderAgentReadOnly/4.2",
            "Accept": "application/json",
        })
        self._working_base: str | None = None
        self._last_diagnostics: list[dict] = []

    @property
    def active_base_url(self) -> str | None:
        return self._working_base

    @property
    def last_diagnostics(self) -> list[dict]:
        return list(self._last_diagnostics)

    def _candidate_bases(self) -> list[str]:
        bases = list(PRIVATE_BASE_URLS)
        if self._working_base in bases:
            bases.remove(self._working_base)
            bases.insert(0, self._working_base)
        return bases

    @staticmethod
    def _body_message(response: requests.Response) -> tuple[object, str]:
        try:
            body = response.json()
            code = body.get("code", response.status_code) if isinstance(body, dict) else response.status_code
            msg = body.get("msg", str(body)) if isinstance(body, dict) else str(body)
            return code, str(msg)
        except Exception:
            return response.status_code, response.text[:400]

    @staticmethod
    def _is_location_block(status: int, message: str) -> bool:
        text = (message or "").lower()
        return (
            status == 451
            or "restricted location" in text
            or "eligibility" in text
            or "service unavailable from a restricted location" in text
        )

    def public_connectivity(self) -> dict:
        """Prueba los hosts oficiales sin usar credenciales privadas."""
        results = []
        working = None
        for base in self._candidate_bases():
            try:
                r = self.session.get(f"{base}/api/v3/time", timeout=self.timeout)
                code, msg = self._body_message(r)
                item = {
                    "base_url": base,
                    "http_status": int(r.status_code),
                    "ok": bool(r.ok),
                    "location_blocked": self._is_location_block(r.status_code, msg),
                    "message": "OK" if r.ok else msg[:180],
                }
                results.append(item)
                if r.ok and working is None:
                    working = base
            except requests.RequestException as exc:
                results.append({
                    "base_url": base,
                    "http_status": None,
                    "ok": False,
                    "location_blocked": False,
                    "message": type(exc).__name__,
                })

        if working:
            self._working_base = working
        self._last_diagnostics = results
        return {
            "ok": working is not None,
            "working_base_url": working,
            "results": results,
        }

    def _server_time_ms(self, base: str) -> int:
        try:
            r = self.session.get(f"{base}/api/v3/time", timeout=self.timeout)
            if r.ok:
                return int(r.json()["serverTime"])
        except Exception:
            pass
        return int(time.time() * 1000)

    def _signed_get(self, path: str, params: dict | None = None):
        errors: list[dict] = []
        saw_location_block = False
        saw_rate_limit = False
        saw_auth_error = False

        for base in self._candidate_bases():
            payload = dict(params or {})
            payload["recvWindow"] = 5000
            payload["timestamp"] = self._server_time_ms(base)

            query = urlencode(payload, doseq=True)
            signature = hmac.new(
                self.api_secret,
                query.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            url = f"{base}{path}?{query}&signature={signature}"
            try:
                r = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                errors.append({
                    "base_url": base,
                    "http_status": None,
                    "message": type(exc).__name__,
                })
                continue

            if r.ok:
                self._working_base = base
                self._last_diagnostics = errors + [{
                    "base_url": base,
                    "http_status": int(r.status_code),
                    "message": "OK",
                }]
                return r.json()

            code, msg = self._body_message(r)
            msg_low = msg.lower()
            errors.append({
                "base_url": base,
                "http_status": int(r.status_code),
                "code": code,
                "message": msg[:250],
            })

            if self._is_location_block(r.status_code, msg):
                saw_location_block = True
                continue

            if r.status_code in {418, 429}:
                saw_rate_limit = True
                continue

            if (
                r.status_code in {401, 403}
                or str(code) in {"-2014", "-2015"}
                or "api-key" in msg_low
                or "signature" in msg_low
            ):
                saw_auth_error = True
                # Un error de autenticación no mejora cambiando de host.
                break

        self._last_diagnostics = errors
        detail = " | ".join(
            f"{x['base_url']}: HTTP {x.get('http_status')} {x.get('message','')}"
            for x in errors
        )

        if saw_location_block:
            raise BinanceLocationRestricted(
                "Binance rechazó la conexión privada desde la región/IP del servidor. " + detail
            )
        if saw_rate_limit:
            raise BinanceRateLimitError(
                "Binance limitó temporalmente las solicitudes. " + detail
            )
        if saw_auth_error:
            raise BinanceAuthenticationError(
                "Binance rechazó la API Key/Secret o sus restricciones/permisos. " + detail
            )
        raise BinanceReadOnlyError("No fue posible consultar Binance. " + detail)

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
    """Extrae niveles de protección de órdenes SELL abiertas."""
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
