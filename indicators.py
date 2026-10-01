from __future__ import annotations
import numpy as np
import pandas as pd
from config import StrategyConfig

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()

def rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50.0)

def atr(df: pd.DataFrame, n: int) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def enrich_4h(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = ema(out["close"], cfg.ema_fast)
    out["ema50"] = ema(out["close"], cfg.ema_mid)
    out["ema200"] = ema(out["close"], cfg.ema_slow)
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    out["atr_pct"] = out["atr"] / out["close"]
    out["vol_ma"] = out["volume"].rolling(cfg.volume_period).mean()
    out["vol_ratio"] = out["volume"] / out["vol_ma"]
    out["resistance_prev"] = out["high"].rolling(cfg.resistance_lookback).max().shift(1)
    out["swing_low_6"] = out["low"].rolling(6).min()
    out["swing_low_10"] = out["low"].rolling(10).min()
    out["distance_ema20_atr"] = (out["close"] - out["ema20"]).abs() / out["atr"]
    return out

def enrich_1d(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()
    out["ema50_d"] = ema(out["close"], cfg.ema_mid)
    out["ema200_d"] = ema(out["close"], cfg.ema_slow)
    out["ema50_slope_5d"] = out["ema50_d"] - out["ema50_d"].shift(5)
    out["daily_regime"] = (
        (out["close"] > out["ema200_d"]) &
        (out["ema50_d"] > out["ema200_d"]) &
        (out["ema50_slope_5d"] > 0)
    )
    return out

def merge_regime(df4: pd.DataFrame, df1d: pd.DataFrame) -> pd.DataFrame:
    left = df4.sort_values("close_time").copy()
    right = df1d[[
        "close_time","close","ema50_d","ema200_d","ema50_slope_5d","daily_regime"
    ]].sort_values("close_time").rename(columns={"close":"daily_close"})

    merged = pd.merge_asof(
        left,
        right,
        on="close_time",
        direction="backward"
    )
    merged["daily_regime"] = merged["daily_regime"].fillna(False).astype(bool)
    return merged
