from __future__ import annotations

import time
import requests
import pandas as pd

FUTURES_BASE_URLS = [
    "https://fapi.binance.com",
]
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TraderAgentV4/4.0",
    "Accept": "application/json",
})


def _request(path: str, params: dict, timeout: int = 15):
    errors = []
    for base in FUTURES_BASE_URLS:
        try:
            r = SESSION.get(f"{base}{path}", params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            errors.append(f"{base}: HTTP {r.status_code} {r.text[:120]}")
        except requests.RequestException as exc:
            errors.append(f"{base}: {type(exc).__name__}")
    raise RuntimeError("No fue posible consultar Binance Futures. " + " | ".join(errors))


def get_futures_klines(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    rows = []
    remaining = int(bars)
    end_time = None

    while remaining > 0:
        request_limit = min(remaining, 1500)
        params = {"symbol": symbol, "interval": interval, "limit": request_limit}
        if end_time is not None:
            params["endTime"] = int(end_time)

        batch = _request("/fapi/v1/klines", params, timeout=20)
        if not batch:
            break

        rows = batch + rows
        end_time = int(batch[0][0]) - 1
        remaining -= len(batch)

        if len(batch) < request_limit:
            break
        time.sleep(0.05)

    if not rows:
        raise RuntimeError(f"Sin datos Futures para {symbol} {interval}")

    by_open = {int(r[0]): r for r in rows}
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


def get_funding_history(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows = []
    cursor = int(start_ms)

    while cursor <= int(end_ms):
        batch = _request(
            "/fapi/v1/fundingRate",
            {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": int(end_ms),
                "limit": 1000,
            },
            timeout=20,
        )
        if not batch:
            break
        rows.extend(batch)
        last_ts = int(batch[-1]["fundingTime"])
        if last_ts < cursor:
            break
        cursor = last_ts + 1
        if len(batch) < 1000:
            break
        time.sleep(0.05)

    if not rows:
        return pd.DataFrame(columns=["funding_time","funding_rate"])

    df = pd.DataFrame(rows)
    df["funding_time"] = pd.to_datetime(pd.to_numeric(df["fundingTime"]), unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce").fillna(0.0)
    df = df[["funding_time","funding_rate"]].drop_duplicates("funding_time").sort_values("funding_time")
    return df.reset_index(drop=True)
