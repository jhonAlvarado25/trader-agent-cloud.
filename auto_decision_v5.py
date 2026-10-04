"""Native-market, validation-selected recommendations with read-only execution plans."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math
import pandas as pd

from auto_decision_v42 import enrich_auto, _mask
from futures_lab import LabCosts, bars_for_years, enrich_lab, simulate_config
from profile_v5 import TradingProfile
from market_v5 import history, funding_history, quote, exchange_rules, funding_reserve
from risk_v5 import size_order
from statistics_v5 import selection_score, final_evidence

TIMEFRAMES = ("1h", "2h", "4h")
SETUPS = ("STRICT_PULLBACK", "BALANCED_PULLBACK", "BALANCED_BREAKOUT")


def apply_regime(frame, daily, direction):
    d = enrich_lab(daily)
    regime = ((d.close > d.ema200) & (d.ema50 > d.ema200)) if direction == "LONG" else ((d.close < d.ema200) & (d.ema50 < d.ema200))
    context = pd.DataFrame({"daily_close_time": d.close_time, "regime_ok": regime})
    f = pd.merge_asof(frame.sort_values("close_time"), context.sort_values("daily_close_time"),
                      left_on="close_time", right_on="daily_close_time", direction="backward")
    col = "long_signal" if direction == "LONG" else "short_signal"
    f[col] = f[col] & f.regime_ok.fillna(False).astype(bool)
    return f


def detect_current_candidates(symbol, cfg=None):
    out, errors = [], []
    for instrument in ("SPOT", "FUTURES"):
        try:
            daily = history(symbol, "1d", 350, instrument)
            for tf in TIMEFRAMES:
                raw = history(symbol, tf, 350, instrument)
                if pd.Timestamp.now(tz="UTC") - raw.iloc[-1].close_time > pd.Timedelta(tf) * 1.1:
                    raise RuntimeError("Última vela nativa obsoleta")
                for direction in (("LONG",) if instrument == "SPOT" else ("LONG", "SHORT")):
                    for setup in SETUPS:
                        c = {"instrument": instrument, "direction": direction, "setup": setup, "timeframe": tf}
                        f = apply_regime(_mask(raw, c), daily, direction)
                        row = f.iloc[-1]
                        if not bool(row["long_signal" if direction == "LONG" else "short_signal"]):
                            continue
                        out.append({**c, "signal_time": row.close_time.isoformat(), "signal_close": float(row.close),
                                    "atr": float(row.atr), "row": row, "score": 1})
        except Exception as exc:
            errors.append(f"{instrument}: {type(exc).__name__}: {str(exc)[:160]}")
    detect_current_candidates.last_errors = errors
    return out


def evaluate_for_selection(symbol, candidate, profile):
    instrument, tf = candidate["instrument"], candidate["timeframe"]
    # Same horizon across timeframes. No Spot proxy and no zero-funding fallback.
    raw = history(symbol, tf, bars_for_years(tf, 3), instrument)
    daily = history(symbol, "1d", 3*366+350, instrument)
    frame = apply_regime(_mask(raw, candidate), daily, candidate["direction"]).dropna().reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("Histórico insuficiente después de indicadores/régimen")
    funding = pd.DataFrame(columns=["funding_time", "funding_rate"])
    if instrument == "FUTURES":
        funding = funding_history(symbol, int(frame.iloc[0].open_time.timestamp()*1000),
                                  int(frame.iloc[-1].close_time.timestamp()*1000))
    costs = LabCosts(profile.spot_fee_each_side, profile.futures_fee_each_side, profile.slippage_each_side)
    trades = simulate_config(frame, funding, candidate["direction"], instrument, 1.25, 2.0, tf, costs)
    if trades.empty:
        raise RuntimeError("Sin operaciones históricas de esta regla")
    start, end = frame.iloc[0].open_time, frame.iloc[-1].close_time
    ok, score, development = selection_score(trades, start, end)
    return {"candidate": candidate, "trades": trades, "frame": frame, "funding": funding,
            "start": start, "end": end, "selection_ok": ok, "score": score, "development": development}


def selected_test(evaluation, profile):
    c = evaluation["candidate"]
    stress_costs = LabCosts(profile.spot_fee_each_side * 1.25, profile.futures_fee_each_side * 1.25,
                            max(.0005, profile.slippage_each_side * 2))
    stressed = simulate_config(evaluation["frame"], evaluation["funding"], c["direction"], c["instrument"],
                               1.25, 2.0, c["timeframe"], stress_costs)
    return final_evidence(evaluation["trades"], stressed, evaluation["start"], evaluation["end"],
                          profile.risk_pct, max_dd_pct=profile.max_drawdown_pct)


def signal_key(symbol, candidate):
    return "|".join(("V5", symbol, candidate["instrument"], candidate["direction"],
                     candidate["setup"], candidate["timeframe"], str(candidate["signal_time"])))


def automatic_recommendation(symbol, cfg=None, operation_budget_cop=None, risk_pct=None,
                             cop_per_usdt=None, candidates=None, profile=None):
    if profile is None:
        capital = float(operation_budget_cop or 1_000_000)
        profile = TradingProfile(capital_cop=capital, available_cop=capital,
                                 risk_pct=float(risk_pct or .005), cop_per_usdt=float(cop_per_usdt or 3350))
    base = {"version": "V5", "symbol": symbol, "mode": profile.mode,
            "operation_budget_cop": profile.capital_cop, "risk_budget_cop": profile.risk_budget_cop}
    if profile.risk_budget_cop <= 0:
        return {**base, "state": "NO OPERAR", "reason": "Riesgo conjunto ya comprometido"}
    cs = candidates if candidates is not None else detect_current_candidates(symbol)
    if not cs:
        return {**base, "state": "NO OPERAR", "reason": "Sin señal nativa completa y régimen 1D compatible",
                "data_errors": getattr(detect_current_candidates, "last_errors", [])}
    evaluations, errors = [], []
    for candidate in cs:
        try:
            e = evaluate_for_selection(symbol, candidate, profile)
            if e["selection_ok"]:
                evaluations.append(e)
        except Exception as exc:
            errors.append(f"{candidate['instrument']} {candidate['timeframe']}: {type(exc).__name__}: {str(exc)[:160]}")
    if not evaluations:
        return {**base, "state": "NO OPERAR", "reason": "Ninguna regla supera entrenamiento/validación", "data_errors": errors}
    # Select using VALIDATION ONLY. A failed TEST does not trigger trying the runners-up.
    winner = max(evaluations, key=lambda e: e["score"])
    stats = selected_test(winner, profile)
    c = winner["candidate"]
    if stats["evidence"] == "INSUFICIENTE" or (profile.mode == "PILOTO_MANUAL" and stats["evidence"] != "FUERTE"):
        return {**base, "state": "NO OPERAR", "reason": "La regla elegida en validación no supera TEST para este modo",
                "stats": stats, "development": winner["development"], "data_errors": errors}
    snapshot = quote(symbol, c["instrument"])
    live = snapshot["ask"] if c["direction"] == "LONG" else snapshot["bid"]
    drift = abs(live - c["signal_close"]) / max(c["atr"], 1e-12)
    if drift > .5:
        return {**base, "state": "ESPERAR", "reason": f"Precio alejado {drift:.2f} ATR; no perseguir",
                "stats": stats}
    row, atr = c["row"], c["atr"]
    if c["direction"] == "LONG":
        stop = min(live - 1.25*atr, float(row.swing_low_10) - .10*atr)
        tp = live + 2*(live-stop)
    else:
        stop = max(live + 1.25*atr, float(row.swing_high_10) + .10*atr)
        tp = live - 2*(stop-live)
    rules = exchange_rules(symbol, c["instrument"])
    reserve = funding_reserve(snapshot)
    try:
        sized = size_order(profile, live, stop, tp, c["direction"], c["instrument"], rules,
                           reserve, snapshot["spread_fraction"])
    except ValueError as exc:
        return {**base, "state": "ESPERAR", "reason": str(exc), "stats": stats}
    created = pd.Timestamp.now(tz="UTC")
    return {**base, **sized, "state": "OPERACIÓN CANDIDATA", "symbol": symbol,
            "reason": f"{c['setup']} · selección VALIDATION · TEST {stats['evidence']} · {profile.mode}",
            "setup": c["setup"], "timeframe": c["timeframe"], "signal_time": c["signal_time"],
            "key": signal_key(symbol, c), "atr": atr, "entry_drift_atr": drift,
            "evidence": stats["evidence"], "stats": stats, "development": winner["development"],
            "created_at": created.isoformat(), "expires_at": (created+pd.Timedelta(minutes=10)).isoformat(),
            "max_holding_hours": 72, "funding_reserve": reserve, "quote": snapshot,
            "profile": asdict(profile), "data_errors": errors}


def revalidate_entry(signal, profile=None, snapshot=None):
    """Refresh both price AND size. Return no order values when the plan has expired."""
    now = pd.Timestamp.now(tz="UTC")
    if now > pd.Timestamp(signal["expires_at"]):
        return {"action": "NO ENTRAR", "reason": "La alerta venció; requiere una nueva validación", "order": None}
    p = profile or TradingProfile(**signal["profile"])
    if p.mode == "PILOTO_MANUAL" and signal.get("evidence") != "FUERTE":
        raise ValueError("Una señal PAPER/EN OBSERVACIÓN no se aprueba para piloto manual")
    snap = snapshot or quote(signal["symbol"], signal["instrument"])
    if snap["instrument"] != signal["instrument"]:
        raise ValueError("Cotización de un mercado distinto al recomendado")
    qtime = pd.Timestamp(snap["quoted_at"])
    if not -5 <= (now-qtime).total_seconds() <= 60:
        raise ValueError("Cotización obsoleta")
    current = snap["ask"] if signal["direction"] == "LONG" else snap["bid"]
    entry, stop, tp, atr = signal["entry"], signal["stop"], signal["take_profit"], signal["atr"]
    if atr <= 0:
        raise ValueError("ATR inválido")
    if not (stop < current < tp if signal["direction"] == "LONG" else tp < current < stop):
        return {"action": "NO ENTRAR", "reason": "Stop/objetivo original alcanzado", "order": None}
    drift = abs(current-entry)/atr
    favorable = current > entry if signal["direction"] == "LONG" else current < entry
    if drift <= .15:
        action, execution = "ENTRAR AHORA", current
    elif favorable and drift <= .5:
        action, execution = "COLOCAR LIMIT", entry
    else:
        return {"action": "NO ENTRAR", "reason": "Precio desplazado o señal invalidada", "order": None}
    sized = size_order(p, execution, stop, tp, signal["direction"], signal["instrument"],
                       exchange_rules(signal["symbol"], signal["instrument"]),
                       funding_reserve(snap), snap["spread_fraction"])
    return {"action": action, "reason": "Precio, cantidad y riesgo recalculados con datos nativos",
            "current": current, "order": {**signal, **sized, "profile": asdict(p), "quote": snap,
                                           "funding_reserve": funding_reserve(snap)}, "quoted_at": snap["quoted_at"]}
