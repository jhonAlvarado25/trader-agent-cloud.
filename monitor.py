from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pandas as pd

from config import CFG
from engine import prepare_symbol, provisional_levels, quality_gate
from market import get_live_price
from binance_readonly import (
    BinanceReadOnlyClient,
    balance_map,
    base_asset_from_symbol,
    summarize_protection,
    permission_is_read_only,
)

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
        "kind": "MARKET",
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
        limit_sl = float(levels["stop"]) * (1.0 - CFG.stop_limit_buffer_pct)
        signal.update({
            "entry": float(levels["entry"]),
            "stop": float(levels["stop"]),
            "limit_sl": limit_sl,
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



def readonly_client_from_env() -> BinanceReadOnlyClient | None:
    key = (os.getenv("BINANCE_API_KEY") or "").strip()
    secret = (os.getenv("BINANCE_API_SECRET") or "").strip()
    if not key or not secret:
        return None
    return BinanceReadOnlyClient(key, secret)


def make_position_alerts(client: BinanceReadOnlyClient) -> list[dict]:
    alerts: list[dict] = []
    permissions = client.permissions()

    if not permission_is_read_only(permissions):
        alerts.append({
            "kind": "ACCOUNT",
            "symbol": "BINANCE",
            "state": "REVISAR PERMISOS API",
            "reason": "La clave conectada tiene uno o más permisos distintos de solo lectura.",
            "key": "BINANCE|API_PERMISSIONS|NOT_READ_ONLY",
        })
        return alerts

    account = client.account()
    balances = balance_map(account)

    for symbol in CFG.symbols:
        base = base_asset_from_symbol(symbol)
        bal = balances.get(base, {"free":0.0, "locked":0.0, "total":0.0})
        total = float(bal["total"])
        if total <= 0:
            continue

        live = float(get_live_price(symbol))
        notional = total * live
        if notional < CFG.min_position_notional_usdt:
            continue

        orders = client.open_orders(symbol)
        protection = summarize_protection(orders)
        candle_bucket = current_4h_bucket()

        if not protection:
            alerts.append({
                "kind": "ACCOUNT",
                "symbol": symbol,
                "state": "POSICIÓN SIN PROTECCIÓN",
                "reason": "Hay saldo relevante del activo, pero no se detectó una orden SELL abierta de protección.",
                "live_price": live,
                "asset_total": total,
                "asset_free": float(bal["free"]),
                "asset_locked": float(bal["locked"]),
                "key": f"{symbol}|NO_PROTECTION|{candle_bucket}",
            })
            continue

        stop = protection.get("stop_trigger")
        tp = protection.get("take_profit")
        limit_sl = protection.get("limit_sl")
        qty = float(protection.get("qty") or 0)

        base_payload = {
            "kind": "ACCOUNT",
            "symbol": symbol,
            "live_price": live,
            "asset_total": total,
            "asset_free": float(bal["free"]),
            "asset_locked": float(bal["locked"]),
            "protected_qty": qty,
            "stop": stop,
            "limit_sl": limit_sl,
            "take_profit": tp,
        }

        if stop is None or limit_sl is None or tp is None:
            alerts.append({
                **base_payload,
                "state": "PROTECCIÓN INCOMPLETA",
                "reason": "La protección abierta no contiene simultáneamente Limit TP, Stop Trigger y Limit SL.",
                "key": f"{symbol}|INCOMPLETE_PROTECTION|{candle_bucket}",
            })
            continue

        dist_stop = (live - stop) / live
        dist_tp = (tp - live) / live

        if live <= stop:
            alerts.append({
                **base_payload,
                "state": "STOP ALCANZADO — REVISAR EJECUCIÓN",
                "reason": "El precio está en o por debajo del Stop Trigger; verifica que la Stop-Limit se haya ejecutado.",
                "distance_stop_pct": dist_stop,
                "distance_tp_pct": dist_tp,
                "key": f"{symbol}|STOP_REACHED|{candle_bucket}",
            })
        elif live >= tp:
            alerts.append({
                **base_payload,
                "state": "TP ALCANZADO — REVISAR EJECUCIÓN",
                "reason": "El precio está en o por encima del Limit TP; verifica la ejecución y cancelación de la otra pata OCO.",
                "distance_stop_pct": dist_stop,
                "distance_tp_pct": dist_tp,
                "key": f"{symbol}|TP_REACHED|{candle_bucket}",
            })
        elif 0 <= dist_stop <= CFG.position_alert_distance_pct:
            alerts.append({
                **base_payload,
                "state": "CERCA DEL STOP",
                "reason": f"El precio está a {dist_stop*100:.2f}% del Stop Trigger.",
                "distance_stop_pct": dist_stop,
                "distance_tp_pct": dist_tp,
                "key": f"{symbol}|NEAR_STOP|{candle_bucket}",
            })
        elif 0 <= dist_tp <= CFG.position_alert_distance_pct:
            alerts.append({
                **base_payload,
                "state": "CERCA DEL TAKE PROFIT",
                "reason": f"El precio está a {dist_tp*100:.2f}% del Limit TP.",
                "distance_stop_pct": dist_stop,
                "distance_tp_pct": dist_tp,
                "key": f"{symbol}|NEAR_TP|{candle_bucket}",
            })

    return alerts

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

    # Revisión de posiciones reales, solo si existen secretos Binance.
    try:
        ro_client = readonly_client_from_env()
        if ro_client is None:
            print("[ACCOUNT] Binance read-only no configurado; se omite monitor de posiciones.")
        else:
            position_alerts = make_position_alerts(ro_client)
            print(f"[ACCOUNT] alertas de posición detectadas: {len(position_alerts)}")
            for alert in position_alerts:
                if alert["key"] in sent:
                    print(f"[SKIP] {alert['symbol']}: alerta de posición ya registrada")
                    continue
                new_signals.append(alert)
                sent.add(alert["key"])
                print(f"[NEW ACCOUNT] {alert['symbol']}: {alert['state']}")
    except Exception as exc:
        errors += 1
        print(f"[ERROR ACCOUNT] {type(exc).__name__}: {exc}")

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
