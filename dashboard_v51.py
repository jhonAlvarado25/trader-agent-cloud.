"""A capital-only interface to public research, without personal data in the feed."""
from __future__ import annotations
import math
import pandas as pd
import requests
from auto_decision_v5 import revalidate_entry
from profile_v5 import TradingProfile, FIXED_RISK_PCT
from fx_v51 import validate_fx

FEED_URL = "https://raw.githubusercontent.com/jhonAlvarado25/trader-agent-cloud./market-data/market_snapshot.json"
MAX_FEED_AGE_SECONDS = 12*60


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def public_plan(signal):
    allowed = ("version", "symbol", "instrument", "direction", "setup", "timeframe", "signal_time",
               "key", "atr", "entry", "stop", "take_profit", "evidence", "stats", "development",
               "created_at", "expires_at", "reason")
    return json_safe({k: signal[k] for k in allowed if k in signal})


def fetch_feed():
    response = requests.get(FEED_URL, params={"t": int(pd.Timestamp.now(tz="UTC").timestamp())//30}, timeout=10)
    response.raise_for_status()
    if len(response.content) > 1_000_000:
        raise ValueError("Informe demasiado grande")
    feed = response.json()
    if feed.get("version") != "5.1" or not isinstance(feed.get("markets"), list):
        raise ValueError("Informe de mercado incompatible")
    return feed


def feed_age(feed, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    stamp = pd.Timestamp(feed["finished_at"])
    if stamp.tzinfo is None:
        raise ValueError("Informe sin zona horaria")
    return (now-stamp).total_seconds()


def select_candidate(signals):
    """Prioritize validation evidence only; TEST is a gate, never a ranking score."""
    strong = [s for s in signals if s.get("evidence") == "FUERTE"]
    pool = strong or list(signals)
    if not pool:
        return None
    def score(signal):
        val = signal.get("development", {}).get("validation", {})
        return (float(val.get("expectancy_r", 0)), int(val.get("n", 0)))
    return max(pool, key=score)


def order_for_capital(feed, capital_cop, now=None):
    """Revalidate prices and size from the only user input. Never submit an order."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    age = feed_age(feed, now)
    if not -5 <= age <= MAX_FEED_AGE_SECONDS:
        return {"action": "ESPERAR", "reason": "El monitor no tiene una actualización reciente. No use señales antiguas.", "order": None}
    if feed.get("pause"):
        return {"action": "ESPERAR", "reason": feed["pause"], "order": None}
    signal = feed.get("candidate")
    if not signal:
        return {"action": "ESPERAR", "reason": "Ningún activo cumple todos los filtros de entrada en esta revisión.", "order": None}
    if signal.get("evidence") != "FUERTE":
        return {"action": "SOLO OBSERVAR", "reason": "Hay un patrón interesante, pero todavía no supera la validación para mostrar una entrada manual.", "order": None}
    fx = validate_fx(feed.get("fx"), now)
    profile = TradingProfile(capital_cop=float(capital_cop), available_cop=float(capital_cop),
                             cop_per_usdt=fx, fx_confirmed=False, risk_pct=FIXED_RISK_PCT,
                             max_open_risk_pct=FIXED_RISK_PCT, mode="PAPER")
    guidance = revalidate_entry(signal, profile=profile)
    if guidance.get("order"):
        guidance["order"]["conversion_estimated"] = True
    return guidance


def binance_rows(order):
    symbol = order["symbol"]
    asset = symbol.removesuffix("USDT")
    is_spot, long = order["instrument"] == "SPOT", order["direction"] == "LONG"
    rows = [("Dónde entrar", "Spot" if is_spot else "Futuros USDⓈ-M"),
            ("Par", symbol), ("Botón de apertura", "Comprar / Buy" if long else "Vender / Sell — abrir SHORT"),
            ("Tipo de entrada", "Limit"), ("Precio de entrada (USDT)", order["entry_text"]),
            (f"Cantidad ({asset})", order["qty_text"]), ("Valor de la posición (USDT)", f"{order['position_usdt']:.2f}")]
    if is_spot:
        rows += [("Después de la compra", "Proteger con OCO / TP-SL"),
                 ("Take Profit / precio de venta", order["tp_text"]),
                 ("Stop / disparador SL", order["stop_text"]),
                 ("Limit SL", order["limit_sl_text"]),
                 (f"Cantidad de protección ({asset})", order["exit_qty_text"])]
    else:
        rows += [("Modo de margen", "Aislado / Isolated"), ("Apalancamiento", f"{order['leverage']}x"),
                 ("Margen aproximado (USDT)", f"{order['margin_usdt']:.2f}"),
                 ("Lado de las órdenes de cierre", "Sell" if long else "Buy"),
                 ("Stop Market — disparador", order["stop_text"]),
                 ("Take Profit Market — disparador", order["tp_text"]),
                 ("Precio que dispara SL/TP", "Contract Price / último precio"),
                 ("Reduce Only — modo unidireccional", "NO en apertura · SÍ en cierres")]
    return rows
