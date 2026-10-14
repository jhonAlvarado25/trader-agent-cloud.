from __future__ import annotations

import json
from pathlib import Path

from auto_monitor import OUTPUT, load_state, save_state


def main() -> int:
    if not OUTPUT.exists():
        print("[ACK] Sin archivo de señales.")
        return 0

    signals = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if not signals:
        print("[ACK] Sin señales nuevas.")
        return 0

    state = load_state()
    sent = set(state.get("sent_keys", []))
    for signal in signals:
        key = str(signal.get("key") or "").strip()
        if key:
            sent.add(key)

    state["sent_keys"] = list(sent)
    save_state(state)
    print(f"[ACK] Señales confirmadas después de Telegram: {len(signals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
