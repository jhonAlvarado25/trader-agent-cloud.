from __future__ import annotations

import math
from typing import Any


def _finite(value, default=None):
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(float(value), hi))


def _trend_state(row) -> str:
    close = _finite(row.get("close"))
    e20 = _finite(row.get("ema20"))
    e50 = _finite(row.get("ema50"))
    e200 = _finite(row.get("ema200"))
    if None in {close, e20, e50, e200}:
        return "DESCONOCIDO"
    if close > e20 > e50 > e200:
        return "ALCISTA"
    if close < e20 < e50 < e200:
        return "BAJISTA"
    return "MIXTO"


def _momentum_state(rsi: float | None) -> str:
    if rsi is None:
        return "DESCONOCIDO"
    if rsi >= 70:
        return "SOBRECOMPRADO"
    if rsi <= 30:
        return "SOBREVENDIDO"
    if rsi >= 55:
        return "POSITIVO"
    if rsi <= 45:
        return "NEGATIVO"
    return "NEUTRO"


def _volume_state(vol_ratio: float | None) -> str:
    if vol_ratio is None:
        return "DESCONOCIDO"
    if vol_ratio >= 1.5:
        return "ALTO"
    if vol_ratio >= 0.8:
        return "NORMAL"
    return "BAJO"


def _volatility_state(vol_ratio: float | None) -> str:
    if vol_ratio is None:
        return "DESCONOCIDO"
    if vol_ratio >= 2.5:
        return "EXTREMA"
    if vol_ratio >= 1.6:
        return "ALTA"
    if vol_ratio <= 0.65:
        return "BAJA"
    return "NORMAL"


def _trend_points(direction: str, trend: str, row) -> float:
    d = direction.upper()
    if d == "LONG" and trend == "ALCISTA":
        return 20.0
    if d == "SHORT" and trend == "BAJISTA":
        return 20.0

    close = _finite(row.get("close"))
    e50 = _finite(row.get("ema50"))
    e20 = _finite(row.get("ema20"))
    if None in {close, e50, e20}:
        return 5.0
    if d == "LONG" and close > e50 and e20 > e50:
        return 12.0
    if d == "SHORT" and close < e50 and e20 < e50:
        return 12.0
    return 3.0


def _momentum_points(direction: str, rsi: float | None) -> float:
    if rsi is None:
        return 7.5
    if direction.upper() == "LONG":
        if 45 <= rsi <= 62:
            return 15.0
        if 40 <= rsi <= 68:
            return 10.0
    else:
        if 38 <= rsi <= 55:
            return 15.0
        if 32 <= rsi <= 60:
            return 10.0
    return 3.0


def _volume_points(vol_ratio: float | None) -> float:
    if vol_ratio is None:
        return 7.5
    if vol_ratio >= 1.20:
        return 15.0
    if vol_ratio >= 0.80:
        return 11.0
    if vol_ratio >= 0.60:
        return 6.0
    return 2.0


def _fib_points(candidate: dict) -> float:
    if bool(candidate.get("fib_confluence")):
        return 10.0
    distance = _finite(candidate.get("fib_distance_atr"))
    if distance is not None and distance <= 0.70:
        return 5.0
    return 0.0


def _volatility_points(vol_ratio: float | None) -> float:
    if vol_ratio is None:
        return 5.0
    if 0.70 <= vol_ratio <= 1.50:
        return 10.0
    if 0.50 <= vol_ratio < 0.70 or 1.50 < vol_ratio <= 2.00:
        return 6.0
    if vol_ratio <= 2.50:
        return 3.0
    return 0.0


def _statistics_points(stats: dict) -> float:
    val_score = _finite(stats.get("validation_score"), 0.0) or 0.0
    brier_skill = _finite(stats.get("brier_skill_test"), 0.0) or 0.0
    psr = _finite(stats.get("psr_test"), 0.0) or 0.0

    base = 18.0 * _clamp(val_score, 0.0, 1.0)
    calibration = 4.0 * _clamp((brier_skill + 0.25) / 0.75, 0.0, 1.0)
    robustness = 3.0 * _clamp(psr, 0.0, 1.0)
    return base + calibration + robustness


def _derivatives_points(direction: str, context: dict | None) -> float:
    if not context:
        return 2.5
    funding = _finite(context.get("funding_rate"))
    if funding is None:
        return 2.5

    # Funding es solo confluencia ligera. No se interpreta como señal autónoma.
    adverse = funding > 0 if direction.upper() == "LONG" else funding < 0
    magnitude = abs(funding)
    if not adverse:
        return 5.0 if magnitude >= 0.0001 else 4.0
    if magnitude >= 0.0005:
        return 0.5
    if magnitude >= 0.0002:
        return 2.0
    return 3.0


def build_state_snapshot(
    candidate: dict,
    stats: dict,
    risk_info: dict,
    cfg,
    futures_context: dict | None = None,
) -> dict[str, Any]:
    """Construye un estado compacto y determinístico para una oportunidad.

    El score es diagnóstico. No puede saltarse el filtro estadístico ni el
    motor de riesgo de V4.2.
    """
    row = candidate.get("row")
    if row is None:
        row = {}

    direction = str(candidate.get("direction") or "").upper()
    rsi = _finite(row.get("rsi"))
    vol_ratio = _finite(row.get("vol_ratio"))
    volatility_ratio = _finite(candidate.get("volatility_ratio"))

    trend = _trend_state(row)
    momentum = _momentum_state(rsi)
    volume = _volume_state(vol_ratio)
    volatility = _volatility_state(volatility_ratio)

    components = {
        "trend": _trend_points(direction, trend, row),
        "momentum": _momentum_points(direction, rsi),
        "volume": _volume_points(vol_ratio),
        "fibonacci": _fib_points(candidate),
        "volatility": _volatility_points(volatility_ratio),
        "statistics": _statistics_points(stats),
        "derivatives": _derivatives_points(direction, futures_context),
    }
    score = round(sum(components.values()), 1)

    actual_risk = _finite(risk_info.get("actual_risk_pct_budget"), 0.0) or 0.0
    budget_used = _finite(risk_info.get("budget_used_pct"), 0.0) or 0.0
    configured_risk = float(risk_info.get("target_risk_pct", cfg.default_risk_pct))

    risk_state = "SAFE"
    risk_reason = "Riesgo dimensionado dentro del límite configurado."

    if actual_risk > configured_risk * 1.02:
        risk_state = "BLOCKED"
        risk_reason = "El riesgo neto calculado supera el límite configurado."
    elif volatility_ratio is not None and volatility_ratio >= 2.50:
        risk_state = "BLOCKED"
        risk_reason = "Volatilidad extrema respecto a su régimen reciente."
    elif (
        (volatility_ratio is not None and volatility_ratio >= 1.60)
        or budget_used >= 0.95
    ):
        risk_state = "NEAR_LIMIT"
        risk_reason = "Riesgo permitido, pero con volatilidad alta o uso elevado del presupuesto."

    funding = _finite((futures_context or {}).get("funding_rate"))
    open_interest = _finite((futures_context or {}).get("open_interest"))

    fib_level = _finite(candidate.get("fib_nearest"))
    fib_label = (
        f"{fib_level*100:.1f}%"
        if bool(candidate.get("fib_confluence")) and fib_level is not None
        else "SIN CONFLUENCIA"
    )

    return {
        "regime": trend,
        "trend": trend,
        "momentum": momentum,
        "rsi": rsi,
        "volume_state": volume,
        "volume_ratio": vol_ratio,
        "volatility_state": volatility,
        "volatility_ratio": volatility_ratio,
        "fibonacci": fib_label,
        "funding_rate": funding,
        "open_interest": open_interest,
        "derivatives_source": (futures_context or {}).get("source"),
        "opportunity_score": score,
        "score_components": {k: round(v, 1) for k, v in components.items()},
        "risk_state": risk_state,
        "risk_reason": risk_reason,
        "setup": candidate.get("setup"),
        "direction": direction,
        "timeframe": candidate.get("timeframe"),
        "evidence": stats.get("evidence"),
        "brier_test": _finite(stats.get("brier_test")),
        "brier_skill_test": _finite(stats.get("brier_skill_test")),
    }
