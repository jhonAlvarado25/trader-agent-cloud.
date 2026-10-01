from __future__ import annotations

import math
import pandas as pd

from config import StrategyConfig
from market import get_klines, get_live_price
from futures_market import get_futures_klines, get_funding_history
from futures_lab import (
    LabCosts,
    bars_for_years,
    enrich_lab,
    simulate_config,
    summarize_config,
)
from risk import position_size_directional


_EVIDENCE_RANK = {"INSUFICIENTE": 0, "PROMETEDORA": 1, "FUERTE": 2}


def _last_closed(df: pd.DataFrame) -> pd.Series:
    now = pd.Timestamp.now(tz="UTC")
    closed = df[df["close_time"] <= now]
    if closed.empty:
        raise RuntimeError("No hay velas cerradas")
    return closed.iloc[-1]


def detect_current_candidates(symbol: str, cfg: StrategyConfig) -> list[dict]:
    candidates: list[dict] = []

    for tf in cfg.auto_timeframes:
        # 350 barras alcanzan para EMA200 + contexto reciente.
        fut = enrich_lab(get_futures_klines(symbol, tf, 350)).dropna().reset_index(drop=True)
        spot = enrich_lab(get_klines(symbol, tf, 350)).dropna().reset_index(drop=True)
        if fut.empty or spot.empty:
            continue

        frow = _last_closed(fut)
        srow = _last_closed(spot)

        # LONG exige confirmación tanto en Spot como en Futures.
        if bool(frow.get("long_signal", False)) and bool(srow.get("long_signal", False)):
            candidates.append({
                "timeframe": tf,
                "direction": "LONG",
                "signal_time": frow["close_time"],
                "signal_close": float(frow["close"]),
                "atr": float(frow["atr"]),
                "row": frow,
            })

        # SHORT solo existe en Futures.
        if bool(frow.get("short_signal", False)):
            candidates.append({
                "timeframe": tf,
                "direction": "SHORT",
                "signal_time": frow["close_time"],
                "signal_close": float(frow["close"]),
                "atr": float(frow["atr"]),
                "row": frow,
            })

    return candidates


def _evaluate_candidate(symbol: str, candidate: dict, cfg: StrategyConfig) -> list[dict]:
    tf = candidate["timeframe"]
    direction = candidate["direction"]
    years = cfg.auto_history_years
    stop_atr = cfg.auto_stop_atr
    rr = cfg.auto_reward_risk
    costs = LabCosts()
    bars = bars_for_years(tf, years)

    outcomes: list[dict] = []

    # Futures histórico + funding para cualquier dirección.
    fut_raw = get_futures_klines(symbol, tf, bars)
    fut_df = enrich_lab(fut_raw).dropna().reset_index(drop=True)
    start_ms = int(fut_df["open_time"].iloc[0].timestamp()*1000)
    end_ms = int(fut_df["close_time"].iloc[-1].timestamp()*1000)
    funding = get_funding_history(symbol, start_ms, end_ms)

    fut_trades = simulate_config(
        fut_df, funding, direction, "FUTURES",
        stop_atr, rr, tf, costs
    )
    fut_stats = summarize_config(
        fut_trades,
        fut_df["open_time"].iloc[0],
        fut_df["close_time"].iloc[-1],
        cfg.auto_bootstrap_sims,
        cfg.default_risk_pct,
    )
    if fut_stats:
        outcomes.append({
            "instrument": "FUTURES",
            "direction": direction,
            "timeframe": tf,
            **fut_stats,
        })

    # Spot solo puede competir en LONG.
    if direction == "LONG":
        spot_raw = get_klines(symbol, tf, bars)
        spot_df = enrich_lab(spot_raw).dropna().reset_index(drop=True)
        spot_trades = simulate_config(
            spot_df,
            pd.DataFrame(columns=["funding_time","funding_rate"]),
            "LONG",
            "SPOT",
            stop_atr, rr, tf, costs
        )
        spot_stats = summarize_config(
            spot_trades,
            spot_df["open_time"].iloc[0],
            spot_df["close_time"].iloc[-1],
            cfg.auto_bootstrap_sims,
            cfg.default_risk_pct,
        )
        if spot_stats:
            outcomes.append({
                "instrument": "SPOT",
                "direction": direction,
                "timeframe": tf,
                **spot_stats,
            })

    return outcomes


def _choose_best(outcomes: list[dict]) -> dict | None:
    valid = [x for x in outcomes if x.get("evidence") in {"PROMETEDORA","FUERTE"}]
    if not valid:
        return None

    def key(x):
        return (
            _EVIDENCE_RANK.get(x.get("evidence"), 0),
            float(x.get("ci_low", -999)),
            float(x.get("expectancy_test_r", -999)),
            float(x.get("pf_test", 0)),
            int(x.get("trades_test", 0)),
        )

    return sorted(valid, key=key, reverse=True)[0]


def _levels_from_live(candidate: dict, live: float, cfg: StrategyConfig) -> dict:
    row = candidate["row"]
    direction = candidate["direction"]
    atr = float(row["atr"])
    entry = float(live)

    if direction == "LONG":
        stop_atr_level = entry - cfg.auto_stop_atr * atr
        stop_structure = float(row["swing_low_10"]) - 0.10*atr
        stop = min(stop_atr_level, stop_structure)
        risk_abs = entry - stop
        tp = entry + cfg.auto_reward_risk*risk_abs
    else:
        stop_atr_level = entry + cfg.auto_stop_atr * atr
        stop_structure = float(row["swing_high_10"]) + 0.10*atr
        stop = max(stop_atr_level, stop_structure)
        risk_abs = stop - entry
        tp = entry - cfg.auto_reward_risk*risk_abs

    if risk_abs <= 0 or tp <= 0:
        raise ValueError("Niveles automáticos inválidos")

    return {
        "entry": entry,
        "stop": stop,
        "tp": tp,
        "risk_abs": risk_abs,
        "risk_pct": risk_abs/entry,
        "rr": cfg.auto_reward_risk,
    }


def automatic_recommendation(
    symbol: str,
    cfg: StrategyConfig,
    operation_budget_cop: float | None = None,
    risk_pct: float | None = None,
    cop_per_usdt: float | None = None,
) -> dict:
    operation_budget_cop = float(operation_budget_cop or cfg.default_capital_cop)
    risk_pct = float(risk_pct or cfg.default_risk_pct)
    cop_per_usdt = float(cop_per_usdt or cfg.default_cop_per_usdt)

    candidates = detect_current_candidates(symbol, cfg)
    if not candidates:
        return {
            "state": "NO OPERAR",
            "reason": "No hay señal LONG/SHORT completa en 1H, 2H o 4H.",
            "symbol": symbol,
            "operation_budget_cop": operation_budget_cop,
            "risk_budget_cop": operation_budget_cop*risk_pct,
        }

    evaluated = []
    for candidate in candidates:
        try:
            outcomes = _evaluate_candidate(symbol, candidate, cfg)
            winner = _choose_best(outcomes)
            if winner:
                evaluated.append((candidate, winner, outcomes))
        except Exception:
            continue

    if not evaluated:
        return {
            "state": "NO OPERAR",
            "reason": "Hay señal técnica, pero ninguna alternativa Spot/Futures supera el filtro estadístico automático.",
            "symbol": symbol,
            "operation_budget_cop": operation_budget_cop,
            "risk_budget_cop": operation_budget_cop*risk_pct,
        }

    def rank(item):
        _, w, _ = item
        return (
            _EVIDENCE_RANK.get(w.get("evidence"), 0),
            float(w.get("ci_low", -999)),
            float(w.get("expectancy_test_r", -999)),
            float(w.get("pf_test", 0)),
        )

    candidate, winner, all_outcomes = sorted(evaluated, key=rank, reverse=True)[0]
    live = float(get_live_price(symbol))

    drift_atr = abs(live - candidate["signal_close"]) / max(candidate["atr"], 1e-12)
    if drift_atr > cfg.auto_max_entry_drift_atr:
        return {
            "state": "ESPERAR",
            "reason": (
                f"La señal es válida, pero el precio se alejó {drift_atr:.2f} ATR del cierre de señal; "
                "no perseguir la entrada."
            ),
            "symbol": symbol,
            "instrument": winner["instrument"],
            "direction": candidate["direction"],
            "timeframe": candidate["timeframe"],
            "evidence": winner["evidence"],
            "operation_budget_cop": operation_budget_cop,
            "risk_budget_cop": operation_budget_cop*risk_pct,
        }

    levels = _levels_from_live(candidate, live, cfg)

    if winner["instrument"] == "FUTURES":
        leverage = (
            cfg.auto_futures_leverage_strong
            if winner["evidence"] == "FUERTE"
            else cfg.auto_futures_leverage_promising
        )
        round_trip_cost = 2*(0.0005 + 0.0002)
    else:
        leverage = 1
        round_trip_cost = 2*(0.0010 + 0.0002)

    sizing = position_size_directional(
        operation_budget_cop=operation_budget_cop,
        risk_pct=risk_pct,
        cop_per_usdt=cop_per_usdt,
        entry=levels["entry"],
        stop=levels["stop"],
        tp=levels["tp"],
        round_trip_cost_pct=round_trip_cost,
        direction=candidate["direction"],
        leverage=leverage,
    )

    limit_sl = None
    if winner["instrument"] == "SPOT":
        limit_sl = levels["stop"] * (1.0 - cfg.stop_limit_buffer_pct)

    return {
        "state": "OPERACIÓN CANDIDATA",
        "reason": (
            f"Señal {candidate['direction']} {candidate['timeframe']} con evidencia "
            f"{winner['evidence']} en {winner['instrument']}."
        ),
        "symbol": symbol,
        "instrument": winner["instrument"],
        "direction": candidate["direction"],
        "timeframe": candidate["timeframe"],
        "evidence": winner["evidence"],
        "signal_time": str(candidate["signal_time"]),
        "entry_drift_atr": drift_atr,
        "entry": levels["entry"],
        "stop": levels["stop"],
        "limit_sl": limit_sl,
        "take_profit": levels["tp"],
        "rr": levels["rr"],
        "leverage": leverage,
        "margin_mode": "ISOLATED" if winner["instrument"] == "FUTURES" else None,
        "operation_budget_cop": operation_budget_cop,
        "risk_budget_cop": operation_budget_cop*risk_pct,
        "position_cop": sizing["position_cop"],
        "position_usdt": sizing["position_usdt"],
        "qty": sizing["qty"],
        "margin_cop": sizing["margin_cop"],
        "margin_usdt": sizing["margin_usdt"],
        "net_loss_cop": sizing["net_loss_cop"],
        "net_gain_cop": sizing["net_gain_cop"],
        "actual_risk_pct_budget": sizing["actual_risk_pct_budget"],
        "budget_used_pct": sizing["budget_used_pct"],
        "stats": {
            "trades_test": int(winner["trades_test"]),
            "win_test": float(winner["win_test"]),
            "expectancy_test_r": float(winner["expectancy_test_r"]),
            "ci_low": float(winner["ci_low"]),
            "ci_high": float(winner["ci_high"]),
            "prob_positive": float(winner["prob_positive"]),
            "pf_test": float(winner["pf_test"]) if math.isfinite(winner["pf_test"]) else None,
            "r_per_year_test": float(winner["r_per_year_test"]),
            "avg_fee_r": float(winner["avg_fee_r"]),
            "avg_funding_r": float(winner["avg_funding_r"]),
        },
        "alternatives": all_outcomes,
    }
