"""Strict native-market data: Futures never falls back to Spot in V5."""
from __future__ import annotations

import math
import time
from pathlib import Path
import pandas as pd
import numpy as np

from market import _request as spot_request, get_klines
from futures_market import _request as futures_request, _normalize_kline_rows
from risk_v5 import rules_from_exchange

_CACHE = {}


def history(symbol, timeframe, bars, instrument):
    key = (symbol, timeframe, int(bars), instrument)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1].copy()
    disk = Path(".state/v5_market") / f"{instrument}_{symbol}_{timeframe}_{int(bars)}.parquet"
    if bars > 1000 and disk.exists() and time.time()-disk.stat().st_mtime < 21600:
        df = pd.read_parquet(disk)
        now = pd.Timestamp.now(tz="UTC")
        if len(df) and (df.close_time <= now).all():
            # Only historical validation uses this six-hour cache. Current signals
            # always request short native histories and a fresh uncached quote.
            df.attrs["source"] = f"Binance {instrument} nativo (histórico cacheado)"
            _CACHE[key] = (time.time(), df)
            return df.copy()
    if instrument == "SPOT":
        df = get_klines(symbol, timeframe, int(bars))
    elif instrument == "FUTURES":
        rows, remaining, end_time = [], int(bars), None
        while remaining > 0:
            limit = min(remaining, 1500)
            params = {"symbol": symbol, "interval": timeframe, "limit": limit}
            if end_time is not None:
                params["endTime"] = end_time
            batch = futures_request("/fapi/v1/klines", params, timeout=15)
            if not batch:
                break
            rows = batch + rows
            remaining -= len(batch)
            next_end = int(batch[0][0]) - 1
            if end_time is not None and next_end >= end_time:
                raise RuntimeError("Paginación Futures inconsistente")
            end_time = next_end
            if len(batch) < limit:
                break
        df = _normalize_kline_rows(rows, bars)
    else:
        raise ValueError("Mercado inválido")
    now = pd.Timestamp.now(tz="UTC")
    df = df[df["close_time"] <= now].copy().reset_index(drop=True)
    if df.empty:
        raise RuntimeError("Sin velas nativas cerradas")
    if not np.isfinite(df[["open", "high", "low", "close", "volume"]].to_numpy(float)).all():
        raise RuntimeError("Velas nativas con valores no finitos")
    if not (df[["open", "high", "low", "close"]] > 0).all().all() or (df.volume < 0).any():
        raise RuntimeError("Precios/volúmenes nativos inválidos")
    if (df.high < df[["open", "close", "low"]].max(axis=1)).any() or (df.low > df[["open", "close", "high"]].min(axis=1)).any():
        raise RuntimeError("OHLC nativo inconsistente")
    if len(df) > 1 and not (df.open_time.diff().dropna() == pd.Timedelta(timeframe)).all():
        raise RuntimeError("Huecos en histórico nativo: no validar silenciosamente")
    df.attrs["source"] = f"Binance {instrument} nativo"
    if bars > 1000:
        disk.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(disk, index=False)
    _CACHE[key] = (time.time(), df)
    return df.copy()


def funding_history(symbol, start_ms, end_ms):
    key = ("funding", symbol, int(start_ms), int(end_ms))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1].copy()
    cursor, rows = int(start_ms), []
    while cursor <= end_ms:
        batch = futures_request("/fapi/v1/fundingRate", {
            "symbol": symbol, "startTime": cursor, "endTime": int(end_ms), "limit": 1000,
        }, timeout=15)
        if not batch:
            break
        rows.extend(batch)
        last = int(batch[-1]["fundingTime"])
        if last < cursor:
            raise RuntimeError("Funding con paginación inconsistente")
        cursor = last + 1
        if len(batch) < 1000:
            break
    if not rows:
        raise RuntimeError("Funding nativo no disponible: no se permite suponer cero")
    df = pd.DataFrame(rows)
    df["funding_time"] = pd.to_datetime(pd.to_numeric(df["fundingTime"]), unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="raise")
    df["mark_price"] = pd.to_numeric(df["markPrice"], errors="raise")
    if not np.isfinite(df[["funding_rate", "mark_price"]].to_numpy(float)).all():
        raise RuntimeError("Funding/mark price no finitos")
    df = df.sort_values("funding_time").drop_duplicates("funding_time").reset_index(drop=True)
    start, end = pd.to_datetime(start_ms, unit="ms", utc=True), pd.to_datetime(end_ms, unit="ms", utc=True)
    gaps = df["funding_time"].diff().dropna()
    if (df.iloc[0]["funding_time"] - start > pd.Timedelta(hours=9) or
            end - df.iloc[-1]["funding_time"] > pd.Timedelta(hours=9) or
            (not gaps.empty and gaps.max() > pd.Timedelta(hours=9)) or
            not (df["mark_price"] > 0).all()):
        raise RuntimeError("Histórico funding incompleto: bloquear validación Futures")
    _CACHE[key] = (time.time(), df)
    return df.copy()


def exchange_rules(symbol, instrument):
    if instrument not in {"SPOT", "FUTURES"}:
        raise ValueError("Mercado inválido")
    key = ("rules", symbol, instrument)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 3600:
        return dict(hit[1])
    if instrument == "SPOT":
        data = spot_request("/api/v3/exchangeInfo", {"symbol": symbol})
    else:
        data = futures_request("/fapi/v1/exchangeInfo", {}, timeout=15)
    item = next((s for s in data.get("symbols", []) if s.get("symbol") == symbol), None)
    if item is None or (instrument == "FUTURES" and item.get("contractType") != "PERPETUAL"):
        raise RuntimeError("Contrato/activo no disponible")
    rules = rules_from_exchange(item)
    _CACHE[key] = (time.time(), rules)
    return dict(rules)


def quote(symbol, instrument):
    if instrument not in {"SPOT", "FUTURES"}:
        raise ValueError("Mercado inválido")
    if instrument == "SPOT":
        book = spot_request("/api/v3/ticker/bookTicker", {"symbol": symbol})
        last = spot_request("/api/v3/ticker/price", {"symbol": symbol})
        extra = {"funding_rate": 0.0, "funding_interval_hours": 0.0, "mark_price": None}
    else:
        book = futures_request("/fapi/v1/ticker/bookTicker", {"symbol": symbol}, timeout=10)
        last = futures_request("/fapi/v1/ticker/price", {"symbol": symbol}, timeout=10)
        premium = futures_request("/fapi/v1/premiumIndex", {"symbol": symbol}, timeout=10)
        info = futures_request("/fapi/v1/fundingInfo", {}, timeout=10)
        matched = next((x for x in info if x.get("symbol") == symbol), {})
        age = (pd.Timestamp.now(tz="UTC").timestamp()*1000 - int(premium["time"])) / 1000
        if not -5 <= age <= 60:
            raise RuntimeError("Mark price/funding obsoletos")
        extra = {
            "funding_rate": float(premium["lastFundingRate"]),
            "funding_interval_hours": float(matched.get("fundingIntervalHours", 8)),
            "mark_price": float(premium["markPrice"]),
            "next_funding_time": int(premium["nextFundingTime"]),
        }
        if (not all(math.isfinite(extra[x]) for x in ("funding_rate", "funding_interval_hours", "mark_price"))
                or extra["mark_price"] <= 0 or not 0 < extra["funding_interval_hours"] <= 8):
            raise RuntimeError("Mark price/funding inválidos")
    bid, ask, price = float(book["bidPrice"]), float(book["askPrice"]), float(last["price"])
    if not all(math.isfinite(x) and x > 0 for x in (bid, ask, price)) or ask < bid:
        raise RuntimeError("Cotización/libro inválidos")
    spread = (ask - bid) / ((ask + bid)/2)
    if spread > .002:
        raise RuntimeError("Spread superior a 20 puntos básicos: esperar liquidez")
    return {
        "price": price, "bid": bid, "ask": ask, "spread_fraction": spread,
        "instrument": instrument, "source": f"Binance {instrument} nativo",
        "quoted_at": pd.Timestamp.now(tz="UTC").isoformat(), **extra,
    }


def funding_reserve(snapshot, hours=72):
    if snapshot["instrument"] == "SPOT":
        return 0.0
    interval = snapshot["funding_interval_hours"]
    if interval <= 0:
        raise RuntimeError("Intervalo funding inválido")
    # Reserve payments even if the latest rate would currently be a credit.
    # This is a scenario, not a bound on future funding.
    return abs(snapshot["funding_rate"]) * (math.ceil(hours/interval) + 1)
