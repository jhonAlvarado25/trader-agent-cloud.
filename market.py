from __future__ import annotations
import time
import requests
import pandas as pd

BASE_URL = "https://api.binance.com"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TraderAgentV1/1.0"})

def get_live_price(symbol: str = "BTCUSDT") -> float:
    r = SESSION.get(
        f"{BASE_URL}/api/v3/ticker/price",
        params={"symbol": symbol},
        timeout=10,
    )
    r.raise_for_status()
    return float(r.json()["price"])

def _fetch_batch(symbol: str, interval: str, limit: int = 1000, end_time: int | None = None):
    params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
    if end_time is not None:
        params["endTime"] = int(end_time)
    r = SESSION.get(f"{BASE_URL}/api/v3/klines", params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def get_klines(symbol: str = "BTCUSDT", interval: str = "4h", bars: int = 3000) -> pd.DataFrame:
    """Descarga hasta `bars` velas cerradas/actuales paginando hacia atrás."""
    all_rows = []
    remaining = bars
    end_time = None

    while remaining > 0:
        batch = _fetch_batch(symbol, interval, min(remaining, 1000), end_time)
        if not batch:
            break
        all_rows = batch + all_rows
        earliest_open = batch[0][0]
        end_time = earliest_open - 1
        remaining -= len(batch)
        if len(batch) < min(remaining + len(batch), 1000):
            break
        time.sleep(0.08)

    if not all_rows:
        raise RuntimeError("Binance no devolvió velas.")

    # Eliminar duplicados por open time.
    seen = {}
    for row in all_rows:
        seen[row[0]] = row
    rows = [seen[k] for k in sorted(seen.keys())][-bars:]

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
