"""Private runtime state. Delivery is acknowledged only AFTER Telegram success."""
from __future__ import annotations
import json
import os
from pathlib import Path

from journal_v5 import validate_records


STATE_PATH = Path(".state/v5_state.json")


def load_state(path=STATE_PATH):
    path = Path(path)
    if not path.exists():
        return {"version": 5, "sent_keys": [], "journal": [], "errors": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != 5 or not isinstance(state.get("sent_keys"), list):
        raise ValueError("Estado V5 incompatible; no reiniciar silenciosamente")
    validate_records(state.get("journal", []))
    return state


def save_state(state, path=STATE_PATH):
    validate_records(state.get("journal", []))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {**state, "sent_keys": list(dict.fromkeys(state.get("sent_keys", [])))[-2000:]}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def deliver_once(state, signal, sender, path=STATE_PATH, on_delivered=None):
    if signal["key"] in state["sent_keys"]:
        return False
    sender(signal)  # Any exception leaves the key retryable.
    if on_delivered is not None:
        on_delivered()
    state["sent_keys"].append(signal["key"])
    save_state(state, path)
    return True
