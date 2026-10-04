from __future__ import annotations
import os
import hashlib

from telegram_notify import send_message
from auto_decision_v5 import revalidate_entry


def message(signal, guidance):
    paper = signal.get("mode") == "PAPER"
    heading = "V5 — SIMULACIÓN PAPER (NO ORDEN REAL)" if paper else "V5 — PILOTO MANUAL (REVISAR EN PANEL)"
    identifier = hashlib.sha256(signal["key"].encode()).hexdigest()[:10]
    lines = [heading, f"ID señal: {identifier} (no duplicar operaciones con este ID)", f"Evaluación de entrada: {guidance['action']}", guidance["reason"], "",
             f"Activo: {signal['symbol']} · {signal['instrument']} {signal['direction']} {signal['timeframe']}",
             f"Evidencia: {signal['evidence']}",
             f"Vence: {signal['expires_at']}"]
    order = guidance.get("order")
    if order is None:
        return "\n".join(lines + ["Sin valores de orden: solicita una nueva validación en el panel."])
    lines.extend([
        "", f"Capital perfil CLOUD: COP {order['operation_budget_cop']:,.0f}",
        f"Tasa manual COP/USDT: {order['cop_per_usdt']:,.2f} · confirmada: {order['fx_confirmed']}",
        f"Precio LIMIT: {order['entry_text']} USDT", f"Cantidad: {order['qty_text']}",
        f"Nocional: {order['position_usdt']:.2f} USDT", f"SL: {order['stop_text']}",
        f"TP: {order['tp_text']}", f"R/R neto modelado: 1:{order['rr_net']:.2f}",
        f"Pérdida neta modelada: COP {order['net_loss_cop']:,.0f}",
        f"Ganancia neta modelada al TP: COP {order['net_gain_cop']:,.0f}",
    ])
    if order['instrument'] == 'FUTURES':
        lines.extend([f"ISOLATED · {order['leverage']}x · margen {order['margin_usdt']:.2f} USDT",
                      "Entrada: Reduce Only = NO. Cierre SL/TP: Reduce Only = SÍ.",
                      "Usar trigger de precio del contrato; validar liquidación real en Binance."])
    else:
        lines.extend([f"Limit SL: {order['limit_sl_text']}",
                      "Protección Spot: usar saldo realmente disponible tras la comisión."])
    stats = signal.get("stats", {})
    lines.extend([
        "", f"TEST: {stats.get('trades_test',0)} trades · Exp {stats.get('expectancy_test_r',0):+.3f}R",
        f"IC95% por bloques: [{stats.get('ci_low',0):+.3f}, {stats.get('ci_high',0):+.3f}]R",
        f"Bootstrap positivo: {stats.get('prob_positive',0)*100:.1f}% (NO probabilidad de ganar)",
        "Meta 15–20% EA = objetivo anual, no garantía ni cuota diaria.",
        "Si cambias el capital en el panel, usa los valores recalculados allí.",
        "Revalidar al abrir la alerta; puede haber vencido. Ninguna orden se abre automáticamente.",
        "Pérdidas y funding pueden superar el modelo; no equivale al riesgo de un CDT.",
    ])
    return "\n".join(lines)


def send_signal(signal, guidance=None):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise RuntimeError("Telegram no configurado: no marcar alerta como enviada")
    guidance = guidance or revalidate_entry(signal)
    send_message(token, chat, message(signal, guidance))
    return guidance
