from __future__ import annotations
import numpy as np
import pandas as pd
from config import StrategyConfig

SETUP_PULLBACK = "PULLBACK"
SETUP_BREAKOUT = "BREAKOUT_RETEST"

def identify_setups(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()

    # Pullback: régimen diario favorable + estructura 4H + retroceso controlado hacia EMA20.
    out["pullback_signal"] = (
        out["daily_regime"] &
        (out["close"] > out["ema50"]) &
        (out["ema20"] > out["ema50"]) &
        (out["ema50"] > out["ema200"]) &
        out["rsi"].between(cfg.pullback_rsi_min, cfg.pullback_rsi_max) &
        (out["vol_ratio"] >= cfg.pullback_vol_ratio_min) &
        (out["distance_ema20_atr"] <= cfg.pullback_max_ema20_atr) &
        (out["low"] <= out["ema20"] + 0.15 * out["atr"]) &
        (out["close"] >= out["ema20"])
    )

    # Detectar ruptura.
    out["breakout_raw"] = (
        out["daily_regime"] &
        (out["close"] > out["resistance_prev"] + cfg.breakout_buffer_atr * out["atr"]) &
        (out["close"] > out["ema20"]) &
        (out["ema20"] > out["ema50"]) &
        out["rsi"].between(cfg.breakout_rsi_min, cfg.breakout_rsi_max)
    )

    out["breakout_retest_signal"] = False
    out["breakout_level"] = np.nan

    # Retest dentro de las siguientes N velas después de una ruptura.
    last_breakout_idx = None
    last_breakout_level = np.nan

    for i in range(len(out)):
        if bool(out.iloc[i].get("breakout_raw", False)):
            last_breakout_idx = i
            last_breakout_level = float(out.iloc[i]["resistance_prev"])
            continue

        if last_breakout_idx is None:
            continue

        bars_since = i - last_breakout_idx
        if bars_since > cfg.retest_window_bars:
            last_breakout_idx = None
            last_breakout_level = np.nan
            continue

        row = out.iloc[i]
        atr = float(row["atr"]) if pd.notna(row["atr"]) else np.nan
        if not np.isfinite(atr):
            continue

        tolerance = cfg.retest_tolerance_atr * atr
        touched = float(row["low"]) <= last_breakout_level + tolerance
        held = float(row["close"]) >= last_breakout_level
        momentum_ok = (
            cfg.breakout_rsi_min <= float(row["rsi"]) <= cfg.breakout_rsi_max and
            float(row["vol_ratio"]) >= cfg.breakout_vol_ratio_min and
            float(row["close"]) >= float(row["ema20"])
        )
        regime_ok = bool(row["daily_regime"])

        if touched and held and momentum_ok and regime_ok:
            out.at[out.index[i], "breakout_retest_signal"] = True
            out.at[out.index[i], "breakout_level"] = last_breakout_level
            # Consumir el breakout para evitar señales duplicadas.
            last_breakout_idx = None
            last_breakout_level = np.nan

    return out

def current_setup(row: pd.Series) -> str | None:
    if bool(row.get("breakout_retest_signal", False)):
        return SETUP_BREAKOUT
    if bool(row.get("pullback_signal", False)):
        return SETUP_PULLBACK
    return None

def setup_gates(row: pd.Series, cfg: StrategyConfig) -> dict:
    common = {
        "Régimen 1D alcista": bool(row.get("daily_regime", False)),
        "Precio 4H > EMA200": float(row.get("close", 0)) > float(row.get("ema200", float("inf"))),
        "EMA20 > EMA50": float(row.get("ema20", 0)) > float(row.get("ema50", float("inf"))),
    }
    setup = current_setup(row)
    if setup == SETUP_PULLBACK:
        common.update({
            "RSI pullback": cfg.pullback_rsi_min <= float(row["rsi"]) <= cfg.pullback_rsi_max,
            "Cerca de EMA20": float(row["distance_ema20_atr"]) <= cfg.pullback_max_ema20_atr,
            "Volumen mínimo": float(row["vol_ratio"]) >= cfg.pullback_vol_ratio_min,
        })
    elif setup == SETUP_BREAKOUT:
        common.update({
            "Retest de ruptura": True,
            "RSI breakout": cfg.breakout_rsi_min <= float(row["rsi"]) <= cfg.breakout_rsi_max,
            "Volumen mínimo": float(row["vol_ratio"]) >= cfg.breakout_vol_ratio_min,
        })
    return common

def build_levels(signal_row: pd.Series, next_open: float, setup: str, cfg: StrategyConfig) -> dict:
    atr = float(signal_row["atr"])
    entry = float(next_open)

    if setup == SETUP_PULLBACK:
        swing = float(signal_row["swing_low_10"])
        stop_atr = entry - cfg.pullback_stop_atr * atr
        stop_swing = swing - cfg.stop_buffer_atr * atr
        stop = min(stop_atr, stop_swing)

    elif setup == SETUP_BREAKOUT:
        level = float(signal_row["breakout_level"])
        swing = float(signal_row["swing_low_6"])
        stop_level = level - cfg.breakout_stop_atr_below_level * atr
        stop_swing = swing - cfg.stop_buffer_atr * atr
        stop = min(stop_level, stop_swing)

    else:
        raise ValueError("Setup desconocido")

    if not (0 < stop < entry):
        raise ValueError("Stop inválido")

    risk_abs = entry - stop
    tp = entry + cfg.reward_risk * risk_abs
    return {
        "entry": entry,
        "stop": stop,
        "tp": tp,
        "risk_abs": risk_abs,
        "risk_pct": risk_abs / entry,
        "rr": cfg.reward_risk,
    }
