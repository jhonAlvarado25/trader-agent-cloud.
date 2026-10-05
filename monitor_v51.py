"""Unattended market research. Public feed contains no account/profile data."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import pandas as pd

from config import CFG
from market import _request
from auto_decision_v5 import automatic_recommendation, revalidate_entry
from dashboard_v51 import public_plan, select_candidate, json_safe
from fx_v51 import automatic_fx
from profile_v5 import TradingProfile
from state_v5 import load_state, save_state, deliver_once
from monitor_v5 import update_paper, remaining_profile, open_paper_record
from journal_v5 import risk_stop
from telegram_notify import send_message
from market_v5 import history
from futures_lab import enrich_lab

OUTPUT = Path(".state/market_snapshot.json")


def telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise RuntimeError("Telegram no configurado")
    send_message(token, chat, text)


def opportunity_message(signal):
    side = "Comprar" if signal["direction"] == "LONG" else "Venta SHORT"
    market = "Spot" if signal["instrument"] == "SPOT" else "Futuros"
    identifier = hashlib.sha256(signal["key"].encode()).hexdigest()[:10]
    quality = signal.get("quote_quality", "NATIVA")
    data_note = (
        "Datos Futures del servidor cloud con proxy conservador; confirme precio y precisión en Binance antes de enviar.\n"
        if signal.get("execution_requires_binance_check") or quality == "PROXY_SPOT"
        else ""
    )
    return (f"TRADER V5.3 · CANDIDATA FUERTE PARA REVISAR\n{signal['symbol']} · {market} · {side}\n"
            f"Análisis: {signal['timeframe']} · validación histórica FUERTE\n"
            + data_note +
            "Abra su aplicación: indique solo el capital y verá precio, cantidad, SL y TP recalculados.\n"
            "Riesgo fijo: 1,5% del capital indicado. Una operación a la vez.\n"
            "Los montos se calculan en la app; este aviso no utiliza un capital antiguo de Telegram.\n"
            f"Señal {identifier} · vence {signal['expires_at']}\n"
            "Requiere revalidación manual. No se abrió ninguna orden. Rentabilidad no garantizada.")


def observation_message(signal):
    side = "LONG" if signal["direction"] == "LONG" else "SHORT"
    market = "Spot" if signal["instrument"] == "SPOT" else "Futuros"
    identifier = hashlib.sha256(signal["key"].encode()).hexdigest()[:10]
    return (
        f"TRADER V5.3 · OPORTUNIDAD EN OBSERVACIÓN\n"
        f"{signal['symbol']} · {market} · {side} · {signal['timeframe']}\n"
        "Hay una estructura interesante con expectativa positiva, pero NO cumple todos los filtros FUERTE.\n"
        "No es una instrucción de entrada y no se muestran cantidades de Binance.\n"
        f"Señal {identifier}. El agente seguirá validándola automáticamente."
    )


def overview():
    try:
        items = _request("/api/v3/ticker/24hr", {"symbols": json.dumps(list(CFG.symbols))})
        if not isinstance(items, list):
            raise ValueError("Resumen de precios inválido")
        out = {}
        for item in items:
            if item.get("symbol") not in CFG.symbols:
                continue
            last, opened = float(item["lastPrice"]), float(item["openPrice"])
            if not all(math.isfinite(x) and x > 0 for x in (last, opened)):
                raise ValueError("Precio de mercado no finito")
            out[item["symbol"]] = {"price": last, "change_24h": 100*(last/opened-1)}
        if len(out) == len(CFG.symbols):
            return out
    except Exception:
        pass

    # Resilient fallback from closed hourly candles. This avoids declaring the
    # whole monitor unhealthy when Binance's 24h ticker route is unavailable
    # from a cloud edge but candle market data can still be retrieved.
    out = {}
    for symbol in CFG.symbols:
        hourly = history(symbol, "1h", 30, "SPOT")
        if len(hourly) < 25:
            raise RuntimeError(f"Histórico horario insuficiente para resumen {symbol}")
        last = float(hourly.iloc[-1].close)
        opened = float(hourly.iloc[-25].open)
        out[symbol] = {"price": last, "change_24h": 100*(last/opened-1)}
    return out


def readable_verdict(result):
    if result.get("state") == "OPERACIÓN CANDIDATA":
        return "Candidata" if result.get("evidence") == "FUERTE" else "Solo observar"
    if result.get("data_errors") and "Sin señal" in result.get("reason", ""):
        return "Revisión parcial"
    return "Esperar"


def market_brief(symbol):
    daily = enrich_lab(history(symbol, "1d", 350, "SPOT"))
    row = daily.iloc[-1]
    if pd.Timestamp.now(tz="UTC")-pd.Timestamp(row.close_time) > pd.Timedelta(hours=26):
        raise ValueError("Contexto diario vencido")
    trend = "Alcista" if row.close > row.ema200 and row.ema50 > row.ema200 else (
        "Bajista" if row.close < row.ema200 and row.ema50 < row.ema200 else "Lateral")
    hourly = history(symbol, "1h", 350, "SPOT")
    if pd.Timestamp.now(tz="UTC")-pd.Timestamp(hourly.iloc[-1].close_time) > pd.Timedelta(minutes=66):
        raise ValueError("Contexto horario vencido")
    last20 = hourly.tail(20)
    return {"trend": trend, "range_low_20h": float(last20.low.min()), "range_high_20h": float(last20.high.max())}


def run_scan(state, symbols=None, notify=True):
    started = pd.Timestamp.now(tz="UTC")
    errors, markets, signals = [], [], []
    fx = None
    try:
        fx = automatic_fx()
    except Exception as exc:
        errors.append(f"Conversión COP: {type(exc).__name__}")
    # Neutral research budget, NEVER exported as the user's capital.
    profile = TradingProfile(cop_per_usdt=fx["cop_per_usdt"] if fx else 3350)
    try:
        prices = overview()
    except Exception as exc:
        prices = {}
        errors.append(f"Precios Spot: {type(exc).__name__}: {str(exc)[:160]}")
    for record in state["journal"]:
        try:
            update_paper(record)
        except Exception as exc:
            errors.append(f"Seguimiento simulado {record.get('symbol')}: {type(exc).__name__}")
    pause = risk_stop(profile, state["journal"])
    for symbol in (CFG.symbols if symbols is None else symbols):
        try:
            result = automatic_recommendation(symbol, profile=profile)
            data_errors = result.get("data_errors", [])
            errors.extend(f"{symbol}: {x}" for x in data_errors)
            if result["state"] == "OPERACIÓN CANDIDATA":
                signals.append(result)
            try:
                brief = market_brief(symbol)
            except Exception as exc:
                brief = {"trend": "Sin dato"}
                errors.append(f"Contexto {symbol}: {type(exc).__name__}")
            markets.append({"symbol": symbol, **prices.get(symbol, {}), **brief, "verdict": readable_verdict(result),
                            "reason": result["reason"], "evidence": result.get("stats", {}).get("evidence"),
                            "checked_at": pd.Timestamp.now(tz="UTC").isoformat(), "data_errors": data_errors})
        except Exception as exc:
            errors.append(f"{symbol}: {type(exc).__name__}: {str(exc)[:160]}")
            markets.append({"symbol": symbol, **prices.get(symbol, {}), "verdict": "Datos no disponibles",
                            "reason": "No es posible validar una entrada con los datos disponibles.",
                            "checked_at": pd.Timestamp.now(tz="UTC").isoformat()})
    chosen = select_candidate(signals)
    # Risk pause refers to simulated forward results, never an assertion about a real account.
    alerts = 0
    if notify and chosen and chosen.get("evidence") == "FUERTE" and not pause and fx:
        try:
            guidance = revalidate_entry(chosen, profile)
            if guidance.get("order"):
                chosen = guidance["order"]
                active = [r for r in state["journal"] if r["status"] in {"OPEN", "PENDING"}]
                shadow = None
                if not active:
                    shadow_guidance = revalidate_entry(chosen, remaining_profile(profile, active))
                    shadow = open_paper_record(chosen, shadow_guidance)
                def acknowledged():
                    if shadow:
                        state["journal"].append(shadow)
                if deliver_once(state, chosen, lambda s: telegram(opportunity_message(s)), on_delivered=acknowledged):
                    alerts += 1
        except Exception as exc:
            errors.append(f"Notificación/revalidación: {type(exc).__name__}")
    elif notify and chosen and chosen.get("evidence") == "EN OBSERVACIÓN":
        try:
            watch = dict(chosen)
            watch["key"] = "OBS|" + chosen["key"]
            deliver_once(state, watch, lambda s: telegram(observation_message(chosen)))
        except Exception as exc:
            errors.append(f"Aviso observación: {type(exc).__name__}")
    finished = pd.Timestamp.now(tz="UTC")
    report = {"version": "5.3", "started_at": started.isoformat(), "finished_at": finished.isoformat(),
              "scheduled_interval_minutes": 5, "scheduler": "GitHub Actions hourly supervisor; target 5-minute cycles",
              "risk_pct": .015, "fx": fx, "markets": markets,
              "candidate": public_plan(chosen) if chosen else None, "pause": pause,
              "errors": list(dict.fromkeys(errors))[:40], "alerts_delivered": alerts,
              "health": "PARCIAL" if errors else "ACTUALIZADO",
              "simulated_positions": len([r for r in state["journal"] if r["status"] in {"OPEN", "PENDING"}])}
    state.update(last_run=finished.isoformat(), errors=report["errors"], pause=pause)
    save_state(state)
    return json_safe(report)


def main():
    state = load_state()
    report = run_scan(state)
    # A truthful one-time setup notification, only after an actual scan.
    if os.getenv("GITHUB_EVENT_NAME") == "push" and "V53-ready" not in state["sent_keys"]:
        try:
            deliver_once(state, {"key": "V53-ready"}, lambda _: telegram(
                "TRADER V5.3 · MONITOR RESILIENTE\nRiesgo fijo 1,5%. Solo indique el capital en la app.\n"
                f"Primera revisión terminada: {len(report['markets'])} activos · estado {report['health']}.\n"
                "Supervisor horario con ciclos objetivo cada 5 minutos; GitHub puede retrasar el inicio. Telegram avisará por condición.\n"
                "No se ejecutan órdenes automáticamente."))
        except Exception as exc:
            report["errors"].append(f"Aviso de inicio: {type(exc).__name__}")
            report["health"] = "PARCIAL"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    text = (f"V5.3: {len(report['markets'])} activos revisados · riesgo fijo 1,5% · "
            f"estado {report['health']} · avisos confirmados {report['alerts_delivered']}\n")
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text + "\n".join(report["errors"]))


if __name__ == "__main__":
    main()
