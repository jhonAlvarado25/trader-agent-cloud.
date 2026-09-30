from __future__ import annotations
import math
import pandas as pd
from config import StrategyConfig

def evaluate_row(row: pd.Series, cfg: StrategyConfig) -> dict:
    required = ["close","ema20","ema50","ema200","rsi","atr","vol_ratio","swing_low","distance_ema20_atr"]
    if any(pd.isna(row.get(c)) for c in required):
        return {"signal": False, "reasons": ["Historial insuficiente para indicadores."]}

    gates = {
        "Precio > EMA200": row["close"] > row["ema200"],
        "EMA20 > EMA50": row["ema20"] > row["ema50"],
        "EMA50 > EMA200": row["ema50"] > row["ema200"],
        f"RSI {cfg.rsi_min:.0f}-{cfg.rsi_max:.0f}": cfg.rsi_min <= row["rsi"] <= cfg.rsi_max,
        f"Volumen relativo ≥ {cfg.min_volume_ratio:.2f}x": row["vol_ratio"] >= cfg.min_volume_ratio,
        f"Cerca EMA20 ≤ {cfg.max_distance_ema20_atr:.2f} ATR": row["distance_ema20_atr"] <= cfg.max_distance_ema20_atr,
        "Cierre ≥ EMA20": row["close"] >= row["ema20"],
    }
    return {
        "signal": all(gates.values()),
        "gates": gates,
        "passed": sum(bool(v) for v in gates.values()),
        "total": len(gates),
    }

def build_levels(row: pd.Series, cfg: StrategyConfig) -> dict:
    entry = float(row["close"])
    atr = float(row["atr"])
    swing = float(row["swing_low"])

    # Stop por debajo tanto del swing reciente como de una distancia mínima por ATR.
    stop_by_atr = entry - cfg.stop_atr_mult * atr
    stop_by_swing = swing - cfg.stop_buffer_atr * atr
    stop = min(stop_by_atr, stop_by_swing)

    if stop <= 0 or stop >= entry:
        raise ValueError("Stop inválido.")

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
