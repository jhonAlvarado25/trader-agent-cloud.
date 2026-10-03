from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from config import CFG
from auto_decision_v42 import automatic_recommendation, detect_current_candidates

STATE_PATH = Path(".state/auto_alerts.json")
OUTPUT = Path("auto_signals.json")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"sent_keys": []}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"sent_keys": []}
        data.setdefault("sent_keys", [])
        return data
    except Exception:
        return {"sent_keys": []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(state.get("sent_keys", [])))[-500:]
    STATE_PATH.write_text(
        json.dumps({"sent_keys": keys}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def make_signal(symbol: str) -> dict | None:
    # Pre-scan ligero cada 15 minutos. Solo ejecutamos el backtest profundo
    # si existe al menos una señal técnica completa en 30m/1H/2H/4H.
    candidates = detect_current_candidates(symbol, CFG)
    if not candidates:
        print(f"[{symbol}] NO OPERAR — sin señal técnica completa en 30m/1H/2H/4H")
        return None

    summary = ", ".join(
        f"{c['direction']} {c['timeframe']}" for c in candidates
    )
    print(f"[{symbol}] Pre-scan: {summary}. Ejecutando validación estadística.")

    rec = automatic_recommendation(
        symbol,
        CFG,
        operation_budget_cop=CFG.default_capital_cop,
        risk_pct=CFG.default_risk_pct,
        cop_per_usdt=CFG.default_cop_per_usdt,
        candidates=candidates,
    )

    if rec.get("state") != "OPERACIÓN CANDIDATA":
        print(f"[{symbol}] {rec.get('state')} — {rec.get('reason','')}")
        return None

    stats = rec.get("stats", {})
    signal_time = rec.get("signal_time") or pd.Timestamp.now(tz="UTC").floor("1h").isoformat()

    payload = {
        "symbol": symbol,
        "state": rec["state"],
        "reason": rec["reason"],
        "instrument": rec["instrument"],
        "direction": rec["direction"],
        "timeframe": rec["timeframe"],
        "evidence": rec["evidence"],
        "signal_time": signal_time,
        "entry": float(rec["entry"]),
        "stop": float(rec["stop"]),
        "limit_sl": None if rec.get("limit_sl") is None else float(rec["limit_sl"]),
        "take_profit": float(rec["take_profit"]),
        "rr": float(rec["rr"]),
        "leverage": int(rec["leverage"]),
        "margin_mode": rec.get("margin_mode"),
        "operation_budget_cop": float(rec["operation_budget_cop"]),
        "risk_budget_cop": float(rec["risk_budget_cop"]),
        "position_cop": float(rec["position_cop"]),
        "position_usdt": float(rec["position_usdt"]),
        "qty": float(rec["qty"]),
        "margin_cop": float(rec["margin_cop"]),
        "margin_usdt": float(rec["margin_usdt"]),
        "net_loss_cop": float(rec["net_loss_cop"]),
        "net_gain_cop": float(rec["net_gain_cop"]),
        "actual_risk_pct_budget": float(rec["actual_risk_pct_budget"]),
        "trades_test": int(stats.get("trades_test", 0)),
        "win_test": float(stats.get("win_test", 0.0)),
        "expectancy_r": float(stats.get("expectancy_test_r", 0.0)),
        "ci_low": float(stats.get("ci_low", 0.0)),
        "ci_high": float(stats.get("ci_high", 0.0)),
        "prob_positive": float(stats.get("prob_positive", 0.0)),
        "profit_factor": stats.get("pf_test"),
        "atr": float(rec.get("atr", 0.0)),
        "entry_drift_atr": float(rec.get("entry_drift_atr", 0.0)),
    }

    payload["key"] = "|".join([
        payload["symbol"],
        payload["instrument"],
        payload["direction"],
        payload["timeframe"],
        str(signal_time),
    ])
    return payload


def main() -> int:
    state = load_state()
    sent = set(state.get("sent_keys", []))
    new_signals = []
    errors = 0

    for symbol in CFG.symbols:
        try:
            signal = make_signal(symbol)
            if signal is None:
                continue
            if signal["key"] in sent:
                print(f"[SKIP] {symbol}: señal ya notificada")
                continue
            new_signals.append(signal)
            sent.add(signal["key"])
            print(
                f"[NEW] {symbol} {signal['instrument']} {signal['direction']} "
                f"{signal['timeframe']} | riesgo COP {signal['risk_budget_cop']:.0f}"
            )
        except Exception as exc:
            errors += 1
            print(f"[ERROR] {symbol}: {type(exc).__name__}: {exc}")

    state["sent_keys"] = list(sent)
    save_state(state)
    OUTPUT.write_text(
        json.dumps(new_signals, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[DONE] nuevas recomendaciones: {len(new_signals)} | errores: {errors}")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
