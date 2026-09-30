from __future__ import annotations
import time
import requests
import pandas as pd

# Endpoint recomendado por Binance para datos públicos de mercado.
# Los demás endpoints oficiales quedan como respaldo automático.
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
    "User-Agent": "TraderAgentCloudV1.1/1.0",
    "Accept": "application/json",
})

_last_working_base = None

def _request(path: str, params: dict, timeout: int = 12):
    global _last_working_base

    candidates = BASE_URLS[:]
    if _last_working_base in candidates:
        candidates.remove(_last_working_base)
        candidates.insert(0, _last_working_base)

    errors = []

    for base in candidates:
        try:
            response = SESSION.get(
                f"{base}{path}",
                params=params,
                timeout=timeout,
            )

            if response.status_code == 200:
                _last_working_base = base
                return response.json()

            errors.append(f"{base}: HTTP {response.status_code}")

        except requests.RequestException as exc:
            errors.append(f"{base}: {type(exc).__name__}")

    raise RuntimeError(
        "No fue posible consultar los endpoints públicos oficiales de Binance. "
        + " | ".join(errors)
    )

def get_live_price(symbol: str = "BTCUSDT") -> float:
    data = _request(
        "/api/v3/ticker/price",
        {"symbol": symbol},
        timeout=10,
    )
    return float(data["price"])

def _fetch_batch(
    symbol: str,
    interval: str,
    limit: int = 1000,
    end_time: int | None = None,
):
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": min(limit, 1000),
    }

    if end_time is not None:
        params["endTime"] = int(end_time)

    return _request("/api/v3/klines", params, timeout=15)

def get_klines(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    bars: int = 3000,
) -> pd.DataFrame:

    all_rows = []
    remaining = bars
    end_time = None

    while remaining > 0:
        request_limit = min(remaining, 1000)

        batch = _fetch_batch(
            symbol,
            interval,
            request_limit,
            end_time,
        )

        if not batch:
            break

        all_rows = batch + all_rows

        earliest_open = batch[0][0]
        end_time = earliest_open - 1

        remaining -= len(batch)

        if len(batch) < request_limit:
            break

        # Pausa conservadora para respetar límites públicos.
        time.sleep(0.10)

    if not all_rows:
        raise RuntimeError("Binance no devolvió velas.")

    # Eliminar posibles duplicados por tiempo de apertura.
    rows_by_time = {row[0]: row for row in all_rows}
    rows = [
        rows_by_time[key]
        for key in sorted(rows_by_time.keys())
    ][-bars:]

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_base",
        "taker_quote",
        "ignore",
    ]

    df = pd.DataFrame(rows, columns=columns)

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
    ]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True,
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True,
    )

    return df.reset_index(drop=True)
