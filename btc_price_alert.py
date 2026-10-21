from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from market import get_live_price
from telegram_notify import send_message

SYMBOL = "BTCUSDT"
DEFAULT_TARGET = 85_300.0
STATE_PATH = Path(".state/btc_85300_alert.json")


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"triggered": False, "last_price": None, "last_check_utc": None}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Estado inválido")
        data.setdefault("triggered", False)
        data.setdefault("last_price", None)
        data.setdefault("last_check_utc", None)
        return data
    except Exception:
        return {"triggered": False, "last_price": None, "last_check_utc": None}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _target() -> float:
    raw = (os.getenv("BTC_ALERT_TARGET") or "").strip()
    if not raw:
        return DEFAULT_TARGET
    value = float(raw)
    if value <= 0:
        raise ValueError("BTC_ALERT_TARGET debe ser mayor que cero.")
    return value


def main() -> int:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")

    target = _target()
    state = _load_state()
    now = datetime.now(timezone.utc).isoformat()
    price = float(get_live_price(SYMBOL))

    print(
        f"[BTC ALERT] precio={price:.2f} USDT · objetivo={target:.2f} USDT · "
        f"triggered={state.get('triggered', False)}"
    )

    # Alerta de una sola vez. Si Telegram falla, no se marca como enviada,
    # por lo que el siguiente ciclo vuelve a intentarlo.
    if not bool(state.get("triggered")) and price >= target:
        message = (
            "ALERTA BTC\n\n"
            f"Bitcoin alcanzó o superó el nivel de {target:,.2f} USDT.\n"
            f"Precio detectado: {price:,.2f} USDT.\n\n"
            "Trader Agent V4.2 · Alerta informativa de precio."
        )
        send_message(token, chat_id, message)
        state["triggered"] = True
        state["triggered_price"] = price
        state["triggered_at_utc"] = now
        print("[BTC ALERT] Telegram enviado correctamente.")

    state["last_price"] = price
    state["last_check_utc"] = now
    state["target"] = target
    _save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
