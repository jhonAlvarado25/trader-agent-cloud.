from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pandas as pd

from config import CFG
from engine import prepare_symbol, provisional_levels, quality_gate
from market import get_live_price

STATE_PATH = Path(".state/alerts.json")
OUTPUT = Path("new_signals.json")
ALERT_STATES = {"VIGILAR", "SETUP VÁLIDO"}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"sent_keys": [], "last_scan_bucket": None}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Estado inválido")
        data.setdefault("sent_keys", [])
        data.setdefault("last_scan_bucket", None)
        return data
    except Exception:
        return {"sent_keys": [], "last_scan_bucket": None}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(state.get("sent_keys", [])))[-250:]
    payload = {
        "sent_keys": keys,
        "last_scan_bucket": state.get("last_scan_bucket"),
        "last_scan_utc": state.get("last_scan_utc"),
    }
    STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def current_4h_bucket() -> str:
    return pd.Timestamp.now(tz="UTC").floor("4h").isoformat()


def make_signal(symbol: str) -> dict | None:
    result = prepare_symbol(symbol, CFG)
    gate = quality_gate(result, CFG)

    if gate["state"] not in ALERT_STATES or not gate.get("setup"):
        return None

    setup = gate["setup"]
    stats = result["setup_results"][setup]["stats_oos"]
    current = result["current"]
    levels = provisional_levels(result, CFG)
    live = get_live_price(symbol)

    signal = {
        "symbol": symbol,
        "state": gate["state"],
        "setup": setup,
        "reason": gate["reason"],
        "candle_time": str(current["close_time"]),
        "live_price": float(live),
        "adjusted_probability": float(stats["adjusted_p"]),
        "breakeven_probability": float(stats["breakeven_p"]),
        "edge_pp": float(stats["edge_pp"]),
        "expectancy_r": float(stats["expectancy_r"]),
        "profit_factor": (
            float(stats["profit_factor"])
            if math.isfinite(stats["profit_factor"])
            else None
        ),
        "oos_trades": int(stats["n"]),
    }

    if levels:
        signal.update({
            "entry": float(levels["entry"]),
            "stop": float(levels["stop"]),
            "take_profit": float(levels["tp"]),
            "risk_pct": float(levels["risk_pct"]),
            "rr": float(levels["rr"]),
        })

    signal["key"] = "|".join([
        signal["symbol"],
        signal["state"],
        signal["setup"],
        signal["candle_time"],
    ])
    return signal


def main() -> int:
    state = load_state()
    sent = set(state.get("sent_keys", []))
    bucket = current_4h_bucket()
    force_scan = (os.getenv("FORCE_SCAN") or "").strip().lower() in {"1", "true", "yes", "on"}

    if not force_scan and state.get("last_scan_bucket") == bucket:
        OUTPUT.write_text("[]", encoding="utf-8")
        print(f"[SKIP] Vela 4H {bucket} ya fue analizada correctamente.")
        return 0

    Path("scan_performed.flag").write_text(bucket, encoding="utf-8")
    new_signals = []
    errors = 0

    for symbol in CFG.symbols:
        try:
            signal = make_signal(symbol)
            if signal is None:
                print(f"[OK] {symbol}: NO OPERAR")
                continue

            if signal["key"] in sent:
                print(f"[SKIP] {symbol}: alerta ya registrada")
                continue

            new_signals.append(signal)
            sent.add(signal["key"])
            print(
                f"[NEW] {symbol}: {signal['state']} | {signal['setup']} | "
                f"exp={signal['expectancy_r']:+.2f}R | edge={signal['edge_pp']:+.1f}pp"
            )

        except Exception as exc:
            errors += 1
            print(f"[ERROR] {symbol}: {type(exc).__name__}: {exc}")

    state["sent_keys"] = list(sent)

    if errors == 0:
        state["last_scan_bucket"] = bucket
        state["last_scan_utc"] = pd.Timestamp.now(tz="UTC").isoformat()
        print(f"[STATE] Vela 4H marcada como analizada: {bucket}")
    else:
        print(f"[WARN] Hubo {errors} error(es); no se marca la vela como completada para permitir reintento.")

    save_state(state)
    OUTPUT.write_text(
        json.dumps(new_signals, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[DONE] nuevas señales: {len(new_signals)} | errores: {errors}")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
