from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path

import requests

from config import CFG
from engine import prepare_symbol, provisional_levels, quality_gate
from market import get_live_price
from risk import position_size

STATE_PATH = Path(".state/alerts.json")
ALERT_STATES = {"VIGILAR", "SETUP VÁLIDO"}


def env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[WARN] {name} inválido: {raw!r}; usando {default}")
        return default


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"sent_keys": []}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state no es dict")
        data.setdefault("sent_keys", [])
        return data
    except Exception as exc:
        print(f"[WARN] No se pudo leer estado previo: {exc}")
        return {"sent_keys": []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(state.get("sent_keys", [])))[-200:]
    STATE_PATH.write_text(
        json.dumps({"sent_keys": keys}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def telegram_send(text: str) -> bool:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        print("[INFO] Telegram no configurado. Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=payload, timeout=20)
    if response.status_code != 200:
        print(f"[ERROR] Telegram HTTP {response.status_code}: {response.text[:500]}")
        return False

    body = response.json()
    if not body.get("ok"):
        print(f"[ERROR] Telegram respondió: {body}")
        return False

    return True


def test_message() -> str:
    dashboard_url = (os.getenv("DASHBOARD_URL") or "").strip()
    link = f'\n<a href="{html.escape(dashboard_url)}">Abrir Trader Agent</a>' if dashboard_url else ""
    return (
        "<b>Trader Agent V3 — prueba de alertas</b>\n\n"
        "Telegram quedó conectado correctamente.\n"
        "A partir de ahora el monitor solo avisará cuando detecte "
        "<b>VIGILAR</b> o <b>SETUP VÁLIDO</b>."
        f"{link}"
    )


def format_alert(symbol: str, live: float, result: dict, gate: dict, capital: float, risk_pct: float, cop_per_usdt: float) -> str:
    setup = gate["setup"]
    stats = result["setup_results"][setup]["stats_oos"]
    current = result["current"]
    levels = provisional_levels(result, CFG)

    icon = "🟢" if gate["state"] == "SETUP VÁLIDO" else "🟡"
    state = html.escape(gate["state"])
    setup_text = html.escape(str(setup))

    lines = [
        f"{icon} <b>{html.escape(symbol)} — {state}</b>",
        f"Setup: <b>{setup_text}</b>",
        f"Precio en vivo: <b>{live:,.2f} USDT</b>",
        "",
        f"Prob. ajustada OOS: <b>{stats['adjusted_p']*100:.1f}%</b>",
        f"Break-even OOS: <b>{stats['breakeven_p']*100:.1f}%</b>",
        f"Edge: <b>{stats['edge_pp']:+.1f} pp</b>",
        f"Expectativa: <b>{stats['expectancy_r']:+.2f}R</b>",
        f"Profit Factor: <b>{stats['profit_factor']:.2f}</b>" if math.isfinite(stats["profit_factor"]) else "Profit Factor: <b>∞</b>",
        f"Operaciones OOS: <b>{stats['n']}</b>",
    ]

    if levels:
        round_trip_cost = 2 * (CFG.fee_each_side + CFG.slippage_each_side)
        risk = position_size(
            capital,
            risk_pct,
            CFG.max_position_fraction,
            cop_per_usdt,
            levels["entry"],
            levels["stop"],
            levels["tp"],
            round_trip_cost,
        )
        lines += [
            "",
            "<b>Plan de referencia</b>",
            f"Entrada: <b>{levels['entry']:,.2f}</b>",
            f"Stop: <b>{levels['stop']:,.2f}</b>",
            f"Take Profit: <b>{levels['tp']:,.2f}</b>",
            f"Posición sugerida: <b>COP {risk['position_cop']:,.0f}</b>",
            f"USDT aprox.: <b>{risk['position_usdt']:,.2f}</b>",
            f"Riesgo neto est.: <b>COP {risk['net_loss_cop']:,.0f}</b>",
        ]

    candle_time = current["close_time"]
    lines += ["", f"Vela evaluada: {html.escape(str(candle_time))}"]

    dashboard_url = (os.getenv("DASHBOARD_URL") or "").strip()
    if dashboard_url:
        lines += ["", f'<a href="{html.escape(dashboard_url)}">Abrir Trader Agent</a>']

    lines += [
        "",
        "<i>Alerta de revisión, no ejecución automática. Verifica la operación antes de entrar.</i>",
    ]
    return "\n".join(lines)


def signal_key(symbol: str, result: dict, gate: dict) -> str:
    candle = str(result["current"]["close_time"])
    return f"{symbol}|{gate['state']}|{gate.get('setup')}|{candle}"


def main() -> int:
    send_test = (os.getenv("SEND_TEST_ALERT") or "").strip().lower() in {"1", "true", "yes", "on"}
    if send_test:
        ok = telegram_send(test_message())
        print("[TEST] Alerta de prueba enviada." if ok else "[TEST] No se pudo enviar la alerta.")
        return 0 if ok else 1

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        print("[INFO] Monitor V3 instalado, pero Telegram todavía no está configurado. No se enviarán alertas.")
        return 0

    capital = env_float("TRADING_CAPITAL_COP", CFG.default_capital_cop)
    risk_pct = env_float("RISK_PCT", CFG.default_risk_pct)
    cop_per_usdt = env_float("COP_PER_USDT", CFG.default_cop_per_usdt)

    # Permitir RISK_PCT como 0.5 (=0,5%) o 0.005.
    if risk_pct > 0.05:
        risk_pct = risk_pct / 100.0

    state = load_state()
    sent = set(state.get("sent_keys", []))
    newly_sent = 0

    for symbol in CFG.symbols:
        print(f"[SCAN] {symbol}")
        try:
            result = prepare_symbol(symbol, CFG)
            gate = quality_gate(result, CFG)
            print(f"[SCAN] {symbol}: {gate['state']} / {gate.get('setup')}")

            if gate["state"] not in ALERT_STATES or not gate.get("setup"):
                continue

            key = signal_key(symbol, result, gate)
            if key in sent:
                print(f"[SKIP] Alerta ya enviada: {key}")
                continue

            live = get_live_price(symbol)
            message = format_alert(symbol, live, result, gate, capital, risk_pct, cop_per_usdt)

            if telegram_send(message):
                sent.add(key)
                newly_sent += 1
                print(f"[SENT] {key}")
            else:
                print(f"[ERROR] No se marcó como enviada: {key}")

        except Exception as exc:
            print(f"[ERROR] {symbol}: {type(exc).__name__}: {exc}")

    state["sent_keys"] = list(sent)
    save_state(state)
    print(f"[DONE] Alertas nuevas enviadas: {newly_sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
