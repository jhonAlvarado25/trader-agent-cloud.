from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

SIGNALS_PATH = Path("auto_signals.json")
TELEGRAM_API = "https://api.telegram.org"


def _money(value: float) -> str:
    return f"{float(value):,.0f}".replace(",", ".")


def _price(value: float | None) -> str:
    if value is None:
        return "-"
    value = float(value)
    return f"{value:,.4f}" if value < 100 else f"{value:,.2f}"


def _message(signal: dict) -> str:
    instrument = signal.get("instrument", "-")
    direction = signal.get("direction", "-")
    timeframe = signal.get("timeframe", "-")
    symbol = signal.get("symbol", "-")
    evidence = signal.get("evidence", "-")

    lines = [
        "TRADER AGENT — OPERACIÓN CANDIDATA",
        "",
        f"Activo: {symbol}",
        f"Mercado: {instrument}",
        f"Dirección: {direction}",
        f"Temporalidad: {timeframe}",
        f"Evidencia: {evidence}",
        "",
        f"Entrada ref.: {_price(signal.get('entry'))} USDT",
        f"Stop Loss: {_price(signal.get('stop'))} USDT",
        f"Take Profit: {_price(signal.get('take_profit'))} USDT",
        f"R/R: 1:{float(signal.get('rr', 0)):.1f}",
        "",
        f"Nocional: {float(signal.get('position_usdt', 0)):.2f} USDT",
        f"Cantidad: {float(signal.get('qty', 0)):.8f}",
        f"Riesgo neto est.: COP {_money(signal.get('net_loss_cop', 0))}",
        f"Ganancia neta est. al TP: COP {_money(signal.get('net_gain_cop', 0))}",
    ]

    if instrument == "FUTURES":
        lines.extend([
            "",
            f"Margen: {signal.get('margin_mode') or 'ISOLATED'}",
            f"Leverage: {int(signal.get('leverage', 1))}x",
            f"Margen aprox.: COP {_money(signal.get('margin_cop', 0))}",
        ])
    elif signal.get("limit_sl") is not None:
        lines.append(f"Limit SL: {_price(signal.get('limit_sl'))} USDT")

    lines.extend([
        "",
        f"Trades TEST: {int(signal.get('trades_test', 0))}",
        f"Expectativa TEST: {float(signal.get('expectancy_r', 0)):+.3f}R",
        f"P(expectativa > 0): {float(signal.get('prob_positive', 0))*100:.1f}%",
        "",
        "Revisar el precio actual antes de ejecutar. La alerta no abre órdenes automáticamente.",
    ])
    return "\n".join(lines)


def send_message(token: str, chat_id: str, text: str) -> None:
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Telegram HTTP {response.status_code}: {response.text[:300]}"
        )


def main() -> int:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        print("[TELEGRAM] No configurado: faltan TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.")
        return 0

    test_mode = (os.getenv("TELEGRAM_TEST") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if test_mode:
        send_message(
            token,
            chat_id,
            "Trader Agent V4.1 conectado correctamente con Telegram. "
            "Las próximas alertas llegarán cuando aparezca una operación candidata.",
        )
        print("[TELEGRAM] Mensaje de prueba enviado.")
        return 0

    if not SIGNALS_PATH.exists():
        print("[TELEGRAM] auto_signals.json no existe; sin mensajes.")
        return 0

    signals = json.loads(SIGNALS_PATH.read_text(encoding="utf-8"))
    if not signals:
        print("[TELEGRAM] Sin nuevas operaciones candidatas.")
        return 0

    sent = 0
    for signal in signals:
        send_message(token, chat_id, _message(signal))
        sent += 1

    print(f"[TELEGRAM] Alertas enviadas: {sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
