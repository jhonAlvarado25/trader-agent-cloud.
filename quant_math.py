from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd


FIB_RETRACE_LEVELS = (0.382, 0.500, 0.618, 0.786)
FIB_EXTENSION_LEVELS = (1.272, 1.618)


def add_fibonacci_features(
    df: pd.DataFrame,
    lookback: int = 55,
    tolerance_atr: float = 0.35,
) -> pd.DataFrame:
    """Añade zonas Fibonacci objetivas usando solo información previa.

    Los anclajes se calculan con máximo/mínimo de las velas anteriores
    (shift(1)) para evitar look-ahead. Fibonacci se usa como confluencia,
    nunca como señal autónoma.
    """
    out = df.copy()
    lookback = max(13, int(lookback))
    tol = max(0.05, float(tolerance_atr))

    prior_high = out["high"].rolling(lookback, min_periods=lookback).max().shift(1)
    prior_low = out["low"].rolling(lookback, min_periods=lookback).min().shift(1)
    span = (prior_high - prior_low).replace(0, np.nan)
    atr = pd.to_numeric(out["atr"], errors="coerce").replace(0, np.nan)
    close = pd.to_numeric(out["close"], errors="coerce")

    out["fib_swing_high"] = prior_high
    out["fib_swing_low"] = prior_low
    out["fib_span"] = span
    out["fib_long_retrace"] = (prior_high - close) / span
    out["fib_short_retrace"] = (close - prior_low) / span

    long_distances = []
    short_distances = []
    for level in FIB_RETRACE_LEVELS:
        long_price = prior_high - level * span
        short_price = prior_low + level * span
        out[f"fib_long_{int(round(level*1000)):03d}"] = long_price
        out[f"fib_short_{int(round(level*1000)):03d}"] = short_price
        long_distances.append((close - long_price).abs() / atr)
        short_distances.append((close - short_price).abs() / atr)

    long_matrix = pd.concat(long_distances, axis=1)
    short_matrix = pd.concat(short_distances, axis=1)
    out["fib_long_distance_atr"] = long_matrix.min(axis=1)
    out["fib_short_distance_atr"] = short_matrix.min(axis=1)

    long_idx = long_matrix.to_numpy(dtype=float)
    short_idx = short_matrix.to_numpy(dtype=float)
    levels = np.asarray(FIB_RETRACE_LEVELS, dtype=float)

    def nearest_level(matrix: np.ndarray) -> np.ndarray:
        result = np.full(matrix.shape[0], np.nan)
        valid = np.isfinite(matrix).any(axis=1)
        if valid.any():
            safe = np.where(np.isfinite(matrix[valid]), matrix[valid], np.inf)
            result[valid] = levels[np.argmin(safe, axis=1)]
        return result

    out["fib_long_nearest"] = nearest_level(long_idx)
    out["fib_short_nearest"] = nearest_level(short_idx)

    out["fib_long_confluence"] = (
        out["fib_long_retrace"].between(0.236, 0.786)
        & (out["fib_long_distance_atr"] <= tol)
    )
    out["fib_short_confluence"] = (
        out["fib_short_retrace"].between(0.236, 0.786)
        & (out["fib_short_distance_atr"] <= tol)
    )

    for level in FIB_EXTENSION_LEVELS:
        tag = int(round(level * 1000))
        out[f"fib_long_ext_{tag}"] = prior_low + level * span
        out[f"fib_short_ext_{tag}"] = prior_high - level * span

    out["atr_pct"] = atr / close.replace(0, np.nan)
    atr_median = out["atr_pct"].rolling(100, min_periods=30).median().shift(1)
    out["volatility_ratio_100"] = out["atr_pct"] / atr_median.replace(0, np.nan)
    return out


def _profit_factor(r: np.ndarray) -> float:
    positive = float(r[r > 0].sum())
    negative = abs(float(r[r < 0].sum()))
    if negative == 0:
        return float("inf") if positive > 0 else 0.0
    return positive / negative


def _max_drawdown_r(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    equity = np.cumsum(r)
    peak = np.maximum.accumulate(np.r_[0.0, equity])[1:]
    return float(np.max(peak - equity))


def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n))
    return max(0.0, (centre - margin) / denom)


def kelly_fraction(win_probability: float, avg_win_r: float, avg_loss_r: float) -> float:
    """Kelly binario aproximado para resultados expresados en múltiplos R."""
    p = min(max(float(win_probability), 0.0), 1.0)
    q = 1.0 - p
    if avg_win_r <= 0 or avg_loss_r <= 0:
        return 0.0
    b = float(avg_win_r) / float(avg_loss_r)
    return max(0.0, p - q / b)


def probabilistic_sharpe_ratio(r: np.ndarray, benchmark_sr: float = 0.0) -> float:
    """PSR de Bailey/López de Prado con retornos por operación.

    Devuelve la probabilidad aproximada de que el Sharpe poblacional supere
    benchmark_sr, incorporando asimetría y curtosis observadas.
    """
    x = np.asarray(r, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return 0.0

    std = float(np.std(x, ddof=1))
    if std <= 0:
        return 1.0 if float(np.mean(x)) > benchmark_sr else 0.0

    sr = float(np.mean(x) / std)
    skew = float(pd.Series(x).skew())
    kurt = float(pd.Series(x).kurt() + 3.0)
    variance_term = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    variance_term = max(variance_term, 1e-12)
    se = math.sqrt(variance_term / max(n - 1, 1))
    if se <= 0:
        return 1.0 if sr > benchmark_sr else 0.0
    z = (sr - benchmark_sr) / se
    return float(NormalDist().cdf(z))


def performance_math(
    trades: pd.DataFrame,
    span_years: float,
    risk_per_trade_pct: float,
) -> dict:
    """Métricas de matemática financiera para validar una estrategia.

    No optimiza parámetros ni fuerza resultados positivos. Resume retorno,
    riesgo, crecimiento geométrico, Kelly y robustez del Sharpe.
    """
    if trades is None or trades.empty or "net_r" not in trades:
        return {
            "n": 0,
            "expectancy_r": 0.0,
            "profit_factor": 0.0,
            "sharpe_trade_ann": 0.0,
            "sortino_trade_ann": 0.0,
            "calmar_r": 0.0,
            "psr_gt_zero": 0.0,
            "kelly_full": 0.0,
            "kelly_conservative": 0.0,
            "kelly_quarter_conservative": 0.0,
            "wilson_win_low": 0.0,
            "geometric_growth_pct_year": 0.0,
            "max_drawdown_r": 0.0,
        }

    r = pd.to_numeric(trades["net_r"], errors="coerce").dropna().to_numpy(float)
    if len(r) == 0:
        return performance_math(pd.DataFrame(), span_years, risk_per_trade_pct)

    years = max(float(span_years), 0.01)
    trades_per_year = len(r) / years
    mean_r = float(np.mean(r))
    std_r = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0

    downside = r[r < 0]
    downside_dev = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0

    sharpe = (mean_r / std_r) * math.sqrt(trades_per_year) if std_r > 0 else 0.0
    sortino = (mean_r / downside_dev) * math.sqrt(trades_per_year) if downside_dev > 0 else 0.0

    dd = _max_drawdown_r(r)
    annual_r = mean_r * trades_per_year
    calmar = annual_r / dd if dd > 0 else (float("inf") if annual_r > 0 else 0.0)

    positive = r[r > 0]
    negative = r[r <= 0]
    wins = len(positive)
    n = len(r)
    win_rate = wins / n
    avg_win = float(np.mean(positive)) if wins else 0.0
    avg_loss = abs(float(np.mean(negative))) if len(negative) else 1.0

    wilson_low = wilson_lower_bound(wins, n)
    kelly = kelly_fraction(win_rate, avg_win, avg_loss)
    kelly_cons = kelly_fraction(wilson_low, avg_win, avg_loss)

    risk = max(0.0, float(risk_per_trade_pct))
    account_returns = risk * r
    valid_growth = account_returns > -0.999999
    if valid_growth.all() and len(account_returns):
        mean_log = float(np.mean(np.log1p(account_returns)))
        geom_year = math.expm1(mean_log * trades_per_year)
    else:
        geom_year = -1.0

    return {
        "n": int(n),
        "expectancy_r": mean_r,
        "profit_factor": float(_profit_factor(r)),
        "sharpe_trade_ann": float(sharpe),
        "sortino_trade_ann": float(sortino),
        "calmar_r": float(calmar),
        "psr_gt_zero": float(probabilistic_sharpe_ratio(r, 0.0)),
        "kelly_full": float(kelly),
        "kelly_conservative": float(kelly_cons),
        "kelly_quarter_conservative": float(0.25 * kelly_cons),
        "wilson_win_low": float(wilson_low),
        "geometric_growth_pct_year": float(geom_year * 100.0),
        "max_drawdown_r": float(dd),
    }


def validation_score(stats: dict) -> float:
    """Puntuación estable para comparar variantes usando VALIDACIÓN, no TEST."""
    exp_r = max(-1.0, min(float(stats.get("expectancy_val_r", 0.0)), 2.0))
    pf = float(stats.get("pf_val", 0.0))
    psr = float(stats.get("psr_val", 0.0))
    sharpe = float(stats.get("sharpe_val", 0.0))

    pf_score = max(0.0, min((pf - 1.0) / 1.0, 1.0)) if math.isfinite(pf) else 1.0
    exp_score = max(0.0, min(exp_r / 0.50, 1.0))
    sharpe_score = max(0.0, min(sharpe / 2.0, 1.0))
    return 0.35 * exp_score + 0.25 * pf_score + 0.25 * psr + 0.15 * sharpe_score
