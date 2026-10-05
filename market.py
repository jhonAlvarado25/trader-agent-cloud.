from __future__ import annotations
import time
import requests
import pandas as pd

BASE_URLS = [
    "https://data-api.binance.vision",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
    "https://api.binance.com",
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TraderAgentCloudV2/2.0",
    "Accept": "application/json",
})
_last_working_base = None

def _request(path: str, params: dict, timeout: int = 12):
    global _last_working_base
    candidates = list(BASE_URLS)
    if _last_working_base in candidates:
        candidates.remove(_last_working_base)
        candidates.insert(0, _last_working_base)

    errors = []
    for base in candidates:
        try:
            r = SESSION.get(f"{base}{path}", params=params, timeout=timeout)
            if r.status_code == 200:
                _last_working_base = base
                return r.json()
            # Public market-data hosts published by Binance are redundant read-only
            # endpoints. A cloud edge can reject one host while another official
            # market-data host remains available. This failover never touches
            # private/account/trading endpoints.
            if r.status_code == 451:
                errors.append(f"{base}: HTTP 451")
                continue
            if r.status_code in (401, 403, 418, 429):
                raise RuntimeError(f"Binance Spot bloqueado: HTTP {r.status_code}")
            errors.append(f"{base}: HTTP {r.status_code}")
        except requests.RequestException as exc:
            errors.append(f"{base}: {type(exc).__name__}")

    raise RuntimeError("No fue posible consultar Binance Spot público. " + " | ".join(errors))

def get_active_endpoint() -> str:
    return _last_working_base or "Pendiente"

def get_live_price(symbol: str) -> float:
    data = _request("/api/v3/ticker/price", {"symbol": symbol}, timeout=10)
    return float(data["price"])

def get_klines(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    rows = []
    remaining = bars
    end_time = None

    while remaining > 0:
        request_limit = min(remaining, 1000)
        params = {"symbol": symbol, "interval": interval, "limit": request_limit}
        if end_time is not None:
            params["endTime"] = int(end_time)

        batch = _request("/api/v3/klines", params, timeout=15)
        if not batch:
            break

        rows = batch + rows
        end_time = batch[0][0] - 1
        remaining -= len(batch)

        if len(batch) < request_limit:
            break
        time.sleep(0.08)

    if not rows:
        raise RuntimeError(f"Sin datos para {symbol} {interval}")

    by_open = {r[0]: r for r in rows}
    rows = [by_open[k] for k in sorted(by_open)][-bars:]

    cols = [
        "open_time","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_base",
        "taker_quote","ignore"
    ]
    df = pd.DataFrame(rows, columns=cols)

    for c in ["open","high","low","close","volume","quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df.reset_index(drop=True)
