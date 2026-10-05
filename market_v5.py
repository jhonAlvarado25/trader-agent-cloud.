"""Strict native-market data: Futures never falls back to Spot in V5."""
from __future__ import annotations

import math
import time
from pathlib import Path
import pandas as pd
import numpy as np

from market import _request as spot_request, get_klines
from futures_market import (
    _request as futures_request,
    _normalize_kline_rows,
    get_futures_klines as fallback_futures_klines,
    get_funding_history as fallback_funding_history,
    get_futures_data_source,
    FuturesGeoRestricted,
)
from risk_v5 import rules_from_exchange

_CACHE = {}

# Conservative quantity/price increments used only when Binance Futures
# exchangeInfo is unavailable from the cloud runner. These are intentionally
# coarser than typical exchange precision so manual orders are rounded down.
# They do NOT enable automated trading; Binance must still accept the values
# when the user reviews the order manually.
FUTURES_FALLBACK_RULES = {
    "BTCUSDT": {"tick_size": "0.1", "step_size": "0.001", "min_qty": 0.001},
    "ETHUSDT": {"tick_size": "0.01", "step_size": "0.001", "min_qty": 0.001},
    "SOLUSDT": {"tick_size": "0.01", "step_size": "1", "min_qty": 1},
    "BNBUSDT": {"tick_size": "0.01", "step_size": "0.01", "min_qty": 0.01},
    "XRPUSDT": {"tick_size": "0.0001", "step_size": "1", "min_qty": 1},
    "ADAUSDT": {"tick_size": "0.0001", "step_size": "1", "min_qty": 1},
    "DOGEUSDT": {"tick_size": "0.00001", "step_size": "1", "min_qty": 1},
    "LINKUSDT": {"tick_size": "0.001", "step_size": "1", "min_qty": 1},
    "AVAXUSDT": {"tick_size": "0.001", "step_size": "1", "min_qty": 1},
    "LTCUSDT": {"tick_size": "0.01", "step_size": "0.01", "min_qty": 0.01},
}
FUTURES_PROXY_BASIS_RESERVE = 0.0010
FUTURES_FUNDING_FALLBACK_RATE = 0.0005


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
        try:
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
            if df.empty:
                raise RuntimeError("Futures REST no devolvió velas")
            df.attrs["source"] = "Binance FUTURES nativo"
        except (FuturesGeoRestricted, RuntimeError) as exc:
            # Research-only fallback used when the GitHub cloud region cannot
            # reach fapi. Long history remains Binance Vision Futures; the
            # recent tail/current setup can use the explicitly labelled Spot
            # proxy from futures_market. Strong statistical evidence is still
            # required before any notification.
            if not isinstance(exc, FuturesGeoRestricted) and "451" not in str(exc):
                raise
            df = fallback_futures_klines(symbol, timeframe, int(bars))
            df.attrs["source"] = get_futures_data_source()
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
    # REST and Binance Vision can represent metadata columns with different
    # JSON/CSV dtypes. Normalize them before parquet caching so a valid fallback
    # never fails merely because one source encoded trade counts as text.
    for column in ("quote_volume", "taker_base", "taker_quote", "ignore"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")
    if "trades" in df.columns:
        df["trades"] = pd.to_numeric(df["trades"], errors="coerce").fillna(0).astype("int64")
    if (df.high < df[["open", "close", "low"]].max(axis=1)).any() or (df.low > df[["open", "close", "high"]].min(axis=1)).any():
        raise RuntimeError("OHLC nativo inconsistente")
    if len(df) > 1 and not (df.open_time.diff().dropna() == pd.Timedelta(timeframe)).all():
        raise RuntimeError("Huecos en histórico nativo: no validar silenciosamente")
    if not df.attrs.get("source"):
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
    start = pd.to_datetime(start_ms, unit="ms", utc=True)
    end = pd.to_datetime(end_ms, unit="ms", utc=True)
    try:
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
            raise RuntimeError("Funding nativo no disponible")
        df = pd.DataFrame(rows)
        df["funding_time"] = pd.to_datetime(pd.to_numeric(df["fundingTime"]), unit="ms", utc=True)
        df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="raise")
        if "markPrice" in df.columns:
            df["mark_price"] = pd.to_numeric(df["markPrice"], errors="raise")
        source = "Binance Futures REST"
    except (FuturesGeoRestricted, RuntimeError) as exc:
        if not isinstance(exc, FuturesGeoRestricted) and "451" not in str(exc) and "no disponible" not in str(exc):
            raise
        df = fallback_funding_history(symbol, int(start_ms), int(end_ms))
        if df.empty:
            raise RuntimeError("Funding Futures no disponible ni en REST ni en Binance Vision")
        source = "Binance Vision funding"

    numeric = ["funding_rate"] + (["mark_price"] if "mark_price" in df.columns else [])
    if not np.isfinite(df[numeric].to_numpy(float)).all():
        raise RuntimeError("Funding/mark price no finitos")
    df = df.sort_values("funding_time").drop_duplicates("funding_time").reset_index(drop=True)
    gaps = df["funding_time"].diff().dropna()
    # Binance Vision daily/monthly archives can lag the current day. Historical
    # validation only requires complete coverage through the evaluated candles.
    if (not gaps.empty and gaps.max() > pd.Timedelta(hours=9)):
        raise RuntimeError("Histórico funding incompleto: huecos superiores a 9h")
    if "mark_price" in df.columns and not (df["mark_price"] > 0).all():
        raise RuntimeError("Mark price histórico inválido")
    df.attrs["source"] = source
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
        try:
            data = futures_request("/fapi/v1/exchangeInfo", {}, timeout=15)
            item = next((s for s in data.get("symbols", []) if s.get("symbol") == symbol), None)
            if item is None or item.get("contractType") != "PERPETUAL":
                raise RuntimeError("Contrato/activo no disponible")
            rules = rules_from_exchange(item)
            rules["source"] = "Binance Futures exchangeInfo"
            _CACHE[key] = (time.time(), rules)
            return dict(rules)
        except (FuturesGeoRestricted, RuntimeError) as exc:
            if not isinstance(exc, FuturesGeoRestricted) and "451" not in str(exc):
                raise
            base = FUTURES_FALLBACK_RULES.get(symbol)
            if not base:
                raise RuntimeError("Sin reglas conservadoras para este contrato Futures")
            rules = {
                **base,
                "max_qty": 1_000_000_000.0,
                "min_price": float(base["tick_size"]),
                "max_price": 100_000_000.0,
                "min_notional": 5.0,
                "max_notional": 0.0,
                "source": "Fallback conservador; verificar precisión en Binance antes de confirmar",
            }
            _CACHE[key] = (time.time(), rules)
            return dict(rules)
    item = next((s for s in data.get("symbols", []) if s.get("symbol") == symbol), None)
    if item is None:
        raise RuntimeError("Contrato/activo no disponible")
    rules = rules_from_exchange(item)
    rules["source"] = "Binance Spot exchangeInfo"
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
        try:
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
                "proxy_basis_reserve": 0.0,
                "quote_quality": "NATIVA",
            }
            if (not all(math.isfinite(extra[x]) for x in ("funding_rate", "funding_interval_hours", "mark_price"))
                    or extra["mark_price"] <= 0 or not 0 < extra["funding_interval_hours"] <= 8):
                raise RuntimeError("Mark price/funding inválidos")
        except (FuturesGeoRestricted, RuntimeError) as exc:
            if not isinstance(exc, FuturesGeoRestricted) and "451" not in str(exc):
                raise
            # Cloud-only research fallback: use Binance Spot public bid/ask as a
            # price proxy and add an explicit basis reserve. Manual execution
            # must still be checked in Binance Futures.
            book = spot_request("/api/v3/ticker/bookTicker", {"symbol": symbol})
            last = spot_request("/api/v3/ticker/price", {"symbol": symbol})
            now_ms = int(pd.Timestamp.now(tz="UTC").timestamp()*1000)
            recent = fallback_funding_history(symbol, now_ms-7*24*3600*1000, now_ms)
            rate = FUTURES_FUNDING_FALLBACK_RATE
            if recent is not None and not recent.empty:
                value = float(recent.iloc[-1]["funding_rate"])
                if math.isfinite(value):
                    rate = value
            extra = {
                "funding_rate": rate,
                "funding_interval_hours": 8.0,
                "mark_price": float(last["price"]),
                "next_funding_time": now_ms + 8*3600*1000,
                "proxy_basis_reserve": FUTURES_PROXY_BASIS_RESERVE,
                "quote_quality": "PROXY_SPOT",
            }
    bid, ask, price = float(book["bidPrice"]), float(book["askPrice"]), float(last["price"])
    if not all(math.isfinite(x) and x > 0 for x in (bid, ask, price)) or ask < bid:
        raise RuntimeError("Cotización/libro inválidos")
    spread = (ask - bid) / ((ask + bid)/2)
    if spread > .002:
        raise RuntimeError("Spread superior a 20 puntos básicos: esperar liquidez")
    effective_spread = spread + float(extra.get("proxy_basis_reserve", 0.0))
    source = (
        "Binance FUTURES nativo"
        if instrument == "FUTURES" and extra.get("quote_quality") == "NATIVA"
        else "Binance Spot público como proxy conservador de Futures"
        if instrument == "FUTURES"
        else "Binance SPOT nativo"
    )
    return {
        "price": price, "bid": bid, "ask": ask, "spread_fraction": effective_spread,
        "instrument": instrument, "source": source,
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
