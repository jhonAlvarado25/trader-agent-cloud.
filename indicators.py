from __future__ import annotations
import numpy as np
import pandas as pd
from config import StrategyConfig

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)

def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def enrich(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = ema(out["close"], cfg.ema_fast)
    out["ema50"] = ema(out["close"], cfg.ema_mid)
    out["ema200"] = ema(out["close"], cfg.ema_slow)
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    out["atr_pct"] = out["atr"] / out["close"]
    out["vol_ma"] = out["volume"].rolling(cfg.volume_period).mean()
    out["vol_ratio"] = out["volume"] / out["vol_ma"]
    out["swing_low"] = out["low"].rolling(cfg.swing_lookback).min()
    out["distance_ema20_atr"] = (out["close"] - out["ema20"]).abs() / out["atr"]
    out["ret_1"] = out["close"].pct_change()
    return out
