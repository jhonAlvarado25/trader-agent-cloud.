from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from market import get_klines

JOURNAL_PATH = Path(".state/decision_journal.json")


def _load() -> list[dict]:
    if not JOURNAL_PATH.exists():
        return []
    try:
        data = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL_PATH.write_text(
        json.dumps(rows[-2000:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def record_signals(signals: list[dict]) -> int:
    """Registra recomendaciones sin duplicarlas para calibración futura."""
    if not signals:
        return 0

    rows = _load()
    keys = {str(x.get("key")) for x in rows if x.get("key")}
    added = 0

    for s in signals:
        key = str(s.get("key") or "").strip()
        if not key or key in keys:
            continue

        p = s.get("win_test")
        try:
            p = min(max(float(p), 0.0), 1.0)
        except Exception:
            p = 0.5

        rows.append({
            "key": key,
            "symbol": s.get("symbol"),
            "instrument": s.get("instrument"),
            "direction": s.get("direction"),
            "timeframe": s.get("timeframe"),
            "signal_time": s.get("signal_time"),
            "entry": s.get("entry"),
            "stop": s.get("stop"),
            "take_profit": s.get("take_profit"),
            "predicted_probability": p,
            "opportunity_score": s.get("opportunity_score"),
            "risk_state": s.get("risk_state"),
            "status": "OPEN",
            "outcome": None,
            "resolved_time": None,
            "resolution": None,
        })
        keys.add(key)
        added += 1

    _save(rows)
    return added


def _resolve_one(item: dict, max_holding_hours: int = 72) -> dict:
    if item.get("status") != "OPEN":
        return item

    symbol = str(item.get("symbol") or "")
    direction = str(item.get("direction") or "").upper()
    entry = float(item.get("entry") or 0)
    stop = float(item.get("stop") or 0)
    tp = float(item.get("take_profit") or 0)
    signal_time = pd.Timestamp(item.get("signal_time"))

    if signal_time.tzinfo is None:
        signal_time = signal_time.tz_localize("UTC")
    else:
        signal_time = signal_time.tz_convert("UTC")

    if not symbol or direction not in {"LONG", "SHORT"} or entry <= 0 or stop <= 0 or tp <= 0:
        return item

    now = pd.Timestamp.now(tz="UTC")
    elapsed_h = max(0.0, (now - signal_time).total_seconds() / 3600)
    bars_needed = int(min(500, max(20, math.ceil(min(elapsed_h, max_holding_hours + 12) * 2) + 12)))
    df = get_klines(symbol, "30m", bars_needed)
    if df is None or df.empty:
        return item

    end_time = signal_time + pd.Timedelta(hours=max_holding_hours)
    x = df[(df["close_time"] >= signal_time) & (df["open_time"] <= end_time)].copy()
    if x.empty:
        return item

    for _, row in x.iterrows():
        high = float(row["high"])
        low = float(row["low"])

        if direction == "LONG":
            hit_stop = low <= stop
            hit_tp = high >= tp
        else:
            hit_stop = high >= stop
            hit_tp = low <= tp

        if hit_stop and hit_tp:
            item.update({
                "status": "RESOLVED",
                "outcome": 0,
                "resolved_time": str(row["close_time"]),
                "resolution": "STOP_SAME_BAR_CONSERVATIVE",
            })
            return item
        if hit_stop:
            item.update({
                "status": "RESOLVED",
                "outcome": 0,
                "resolved_time": str(row["close_time"]),
                "resolution": "STOP",
            })
            return item
        if hit_tp:
            item.update({
                "status": "RESOLVED",
                "outcome": 1,
                "resolved_time": str(row["close_time"]),
                "resolution": "TP",
            })
            return item

    if now >= end_time:
        eligible = x[x["close_time"] <= end_time]
        if eligible.empty:
            return item
        close = float(eligible.iloc[-1]["close"])
        gross = (close / entry - 1.0) if direction == "LONG" else (entry / close - 1.0)
        cost = 0.0024 if str(item.get("instrument")).upper() == "SPOT" else 0.0014
        outcome = 1 if gross - cost > 0 else 0
        item.update({
            "status": "RESOLVED",
            "outcome": outcome,
            "resolved_time": str(eligible.iloc[-1]["close_time"]),
            "resolution": "TIME_POSITIVE" if outcome else "TIME_NONPOSITIVE",
        })
    return item


def settle_open_signals(max_holding_hours: int = 72) -> int:
    rows = _load()
    resolved_before = sum(1 for x in rows if x.get("status") == "RESOLVED")

    for i, item in enumerate(rows):
        if item.get("status") != "OPEN":
            continue
        try:
            rows[i] = _resolve_one(dict(item), max_holding_hours=max_holding_hours)
        except Exception:
            continue

    _save(rows)
    resolved_after = sum(1 for x in rows if x.get("status") == "RESOLVED")
    return resolved_after - resolved_before


def calibration_summary() -> dict:
    rows = [x for x in _load() if x.get("status") == "RESOLVED" and x.get("outcome") in {0, 1}]
    if not rows:
        return {
            "resolved": 0,
            "wins": 0,
            "brier": None,
            "mean_probability": None,
            "observed_win_rate": None,
        }

    probs = []
    outcomes = []
    for item in rows:
        try:
            probs.append(min(max(float(item.get("predicted_probability", 0.5)), 0.0), 1.0))
            outcomes.append(int(item["outcome"]))
        except Exception:
            continue

    if not probs:
        return {
            "resolved": 0,
            "wins": 0,
            "brier": None,
            "mean_probability": None,
            "observed_win_rate": None,
        }

    brier = sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / len(probs)
    return {
        "resolved": len(probs),
        "wins": int(sum(outcomes)),
        "brier": float(brier),
        "mean_probability": float(sum(probs) / len(probs)),
        "observed_win_rate": float(sum(outcomes) / len(outcomes)),
    }
