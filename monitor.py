from __future__ import annotations

import json
import math
from pathlib import Path

from config import CFG
from engine import prepare_symbol, provisional_levels, quality_gate
from market import get_live_price

OUTPUT = Path("signals.json")
ALERT_STATES = {"VIGILAR", "SETUP VÁLIDO"}


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
    signals = []

    for symbol in CFG.symbols:
        try:
            signal = make_signal(symbol)
            if signal:
                signals.append(signal)
                print(
                    f"[ALERT] {symbol}: {signal['state']} "
                    f"{signal['setup']} exp={signal['expectancy_r']:+.2f}R"
                )
            else:
                print(f"[OK] {symbol}: sin alerta")
        except Exception as exc:
            print(f"[ERROR] {symbol}: {type(exc).__name__}: {exc}")

    OUTPUT.write_text(
        json.dumps(signals, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] {len(signals)} señal(es) escrita(s) en {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
