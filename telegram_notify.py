from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

from market import get_live_price
from config import CFG

SIGNALS_PATH = Path("auto_signals.json")
TELEGRAM_API = "https://api.telegram.org"


def _money(value: float) -> str:
    return f"{float(value):,.0f}".replace(",", ".")


def _price(value: float | None) -> str:
    if value is None:
        return "-"
    value = float(value)
    return f"{value:,.4f}" if value < 100 else f"{value:,.2f}"



def _execution_guidance(signal: dict) -> dict:
    entry = float(signal.get("entry") or 0)
    stop = float(signal.get("stop") or 0)
    tp = float(signal.get("take_profit") or 0)
    atr = float(signal.get("atr") or 0)
    direction = str(signal.get("direction") or "").upper()

    if entry <= 0 or stop <= 0 or tp <= 0 or atr <= 0 or direction not in {"LONG","SHORT"}:
        return {
            "action": "REVISAR MANUALMENTE",
            "current": None,
            "rr": None,
            "drift_atr": None,
            "reason": "No hay datos suficientes para validar la entrada en tiempo real.",
        }

    current = float(get_live_price(signal["symbol"]))

    if direction == "LONG":
        if current <= stop:
            return {"action":"NO ENTRAR","current":current,"rr":0.0,"drift_atr":abs(current-entry)/atr,
                    "reason":"El precio ya alcanzó o atravesó el Stop Loss."}
        if current >= tp:
            return {"action":"NO ENTRAR","current":current,"rr":0.0,"drift_atr":abs(current-entry)/atr,
                    "reason":"El precio ya alcanzó el objetivo; la oportunidad original expiró."}
        risk = current-stop
        reward = tp-current
        moved_with_trade = current > entry
        moved_against_trade = current < entry
    else:
        if current >= stop:
            return {"action":"NO ENTRAR","current":current,"rr":0.0,"drift_atr":abs(current-entry)/atr,
                    "reason":"El precio ya alcanzó o atravesó el Stop Loss."}
        if current <= tp:
            return {"action":"NO ENTRAR","current":current,"rr":0.0,"drift_atr":abs(current-entry)/atr,
                    "reason":"El precio ya alcanzó el objetivo; la oportunidad original expiró."}
        risk = stop-current
        reward = current-tp
        moved_with_trade = current < entry
        moved_against_trade = current > entry

    rr = reward/risk if risk > 0 else 0.0
    drift_atr = abs(current-entry)/atr

    if drift_atr <= CFG.alert_enter_now_atr and rr >= CFG.alert_min_current_rr:
        return {
            "action":"ENTRAR AHORA",
            "current":current,
            "rr":rr,
            "drift_atr":drift_atr,
            "reason":"El precio sigue suficientemente cerca de la entrada calculada y conserva el R/R mínimo.",
        }

    if moved_with_trade and drift_atr <= CFG.alert_limit_max_atr:
        return {
            "action":"COLOCAR LIMIT",
            "current":current,
            "rr":rr,
            "drift_atr":drift_atr,
            "reason":"El precio avanzó en la dirección esperada; no perseguirlo. Esperar retroceso a la entrada de referencia.",
        }

    if moved_against_trade:
        return {
            "action":"NO ENTRAR",
            "current":current,
            "rr":rr,
            "drift_atr":drift_atr,
            "reason":"El precio se movió contra la señal. Esperar una nueva validación del agente.",
        }

    return {
        "action":"NO ENTRAR",
        "current":current,
        "rr":rr,
        "drift_atr":drift_atr,
        "reason":"El precio se alejó demasiado de la entrada calculada o el R/R actual ya no cumple el mínimo.",
    }

def _message(signal: dict) -> str:
    instrument = signal.get("instrument", "-")
    direction = signal.get("direction", "-")
    timeframe = signal.get("timeframe", "-")
    symbol = signal.get("symbol", "-")
    evidence = signal.get("evidence", "-")
    try:
        guidance = _execution_guidance(signal)
    except Exception as exc:
        guidance = {
            "action":"REVISAR MANUALMENTE",
            "current":None,
            "rr":None,
            "drift_atr":None,
            "reason":f"No fue posible refrescar el precio: {type(exc).__name__}.",
        }

    drift_text = "-" if guidance.get("drift_atr") is None else f"{float(guidance['drift_atr']):.2f} ATR"
    rr_text = "-" if guidance.get("rr") is None else f"1:{float(guidance['rr']):.2f}"

    lines = [
        f"ACCIÓN: {guidance['action']}",
        guidance["reason"],
        "",
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
        base_asset = symbol[:-4] if symbol.endswith("USDT") else symbol
        side_button = "Vender/Short" if direction == "SHORT" else "Comprar/Long"
        lines.extend([
            "",
            "CÓMO CARGARLO EN BINANCE FUTURES",
            f"Margen: {signal.get('margin_mode') or 'ISOLATED'}",
            f"Leverage: {int(signal.get('leverage', 1))}x",
            f"Botón: {side_button}",
            "Tipo de orden: LIMIT",
            (
                f"Precio LIMIT: {_price(signal.get('entry'))} USDT"
                if guidance.get("action") == "COLOCAR LIMIT"
                else f"Precio actual ref.: {_price(guidance.get('current'))} USDT"
            ),
            f"Monto: {float(signal.get('qty', 0)):.6f} {base_asset}",
            f"TP: {_price(signal.get('take_profit'))} USDT",
            f"SL: {_price(signal.get('stop'))} USDT",
            "Al abrir: Reduce only = NO",
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
        "VALIDACIÓN DE PRECIO AL ENVIAR",
        f"Precio actual: {_price(guidance.get('current'))} USDT",
        f"Desvío: {drift_text}",
        f"R/R actual: {rr_text}",
        "",
        (
            f"Si indica COLOCAR LIMIT, usa la entrada de referencia {_price(signal.get('entry'))} USDT "
            "y no una orden Market."
            if guidance.get("action") == "COLOCAR LIMIT"
            else "La alerta no abre órdenes automáticamente."
        ),
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
    payload = response.json()
    if payload.get("ok") is not True:
        raise RuntimeError("Telegram no confirmó la entrega del mensaje")


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
            "Trader Agent V5 conectado correctamente con Telegram. "
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
