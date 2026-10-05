"""Pure 1-second signal supervision. No REST calls, account access or trading."""
from __future__ import annotations
from copy import deepcopy
import math
import os
from urllib.parse import urlparse
import pandas as pd
import requests

from auto_decision_v5 import revalidate_entry
from dashboard_v51 import public_plan, json_safe
from fx_v51 import validate_fx
from profile_v5 import TradingProfile, FIXED_RISK_PCT

MAX_TICK_AGE = 3.0
MAX_RESEARCH_AGE = 12 * 60
MAX_RULES_AGE = 3600


def utcnow():
    return pd.Timestamp.now(tz="UTC")


def age_seconds(stamp, now):
    stamp = pd.Timestamp(stamp)
    if stamp.tzinfo is None or pd.isna(stamp):
        raise ValueError("Fecha sin zona horaria")
    return (now-stamp).total_seconds()


def fresh(stamp, now, limit):
    return -1 <= age_seconds(stamp, now) <= limit


class QuoteBook:
    """Freshness is per component, never refreshed by an evaluation heartbeat."""
    def __init__(self, symbols):
        self.symbols = set(symbols)
        self.components = {}

    def clear(self, instrument):
        self.components = {k: v for k, v in self.components.items() if k[0] != instrument}

    def ingest(self, instrument, envelope, now=None):
        now = utcnow() if now is None else pd.Timestamp(now)
        data = envelope.get("data", envelope)
        symbol = data.get("s")
        if instrument not in {"SPOT", "FUTURES"} or symbol not in self.symbols:
            return
        channel = envelope.get("stream", "").split("@", 1)[-1]
        event = data.get("e", "")
        # Spot bookTicker has no exchange event timestamp; use receipt time.
        stamp = pd.to_datetime(data["E"], unit="ms", utc=True) if "E" in data else now
        if not fresh(stamp, now, MAX_TICK_AGE):
            raise ValueError("Evento retrasado o reloj desincronizado")
        if channel == "bookTicker" or event == "bookTicker":
            kind, values = "book", {"bid": float(data["b"]), "ask": float(data["a"])}
            if values["ask"] < values["bid"]:
                raise ValueError("Libro cruzado")
        elif event == "aggTrade":
            kind, values = "trade", {"price": float(data["p"])}
        elif event == "24hrMiniTicker":
            last, opened = float(data["c"]), float(data["o"])
            if opened <= 0:
                raise ValueError("Apertura inválida")
            kind, values = "ticker", {"price": last, "change_24h": 100*(last/opened-1)}
        elif event == "markPriceUpdate":
            kind, values = "mark", {"mark_price": float(data["p"]), "funding_rate": float(data["r"]),
                                     "next_funding_time": int(data["T"])}
            if values["next_funding_time"] <= now.timestamp()*1000:
                raise ValueError("Funding vencido")
        else:
            return
        for key, value in values.items():
            if not math.isfinite(value) or (key in {"bid", "ask", "price", "mark_price"} and value <= 0):
                raise ValueError("Evento no finito o precio inválido")
        key = (instrument, symbol, kind)
        previous = self.components.get(key)
        sequence = data.get("u") if kind == "book" else data.get("a") if kind == "trade" else None
        if previous and (stamp < pd.Timestamp(previous["at"]) or
                         (sequence is not None and previous.get("seq") is not None and sequence <= previous["seq"])):
            return
        self.components[key] = {**values, "at": stamp.isoformat(), "seq": sequence}

    def component(self, instrument, symbol, kind, now):
        item = self.components.get((instrument, symbol, kind))
        if not item or not fresh(item["at"], now, MAX_TICK_AGE):
            raise ValueError("Sin datos recientes: esperar reconexión o una nueva actualización")
        return item

    def quote(self, signal, metadata, now):
        instrument, symbol = signal["instrument"], signal["symbol"]
        book = self.component(instrument, symbol, "book", now)
        trade = self.component(instrument, symbol, "trade", now)
        spread = (book["ask"]-book["bid"])/((book["ask"]+book["bid"])/2)
        if spread > .002:
            raise ValueError("Spread superior a 20 puntos básicos")
        stamps = [book["at"], trade["at"]]
        extra = {"mark_price": None, "funding_rate": 0.0, "funding_interval_hours": 0.0}
        if instrument == "FUTURES":
            mark = self.component(instrument, symbol, "mark", now)
            interval = float(metadata["funding_interval_hours"])
            if not math.isfinite(interval) or not 0 < interval <= 8:
                raise ValueError("Intervalo funding inválido")
            stamps.append(mark["at"])
            extra = {k: mark[k] for k in ("mark_price", "funding_rate", "next_funding_time")}
            extra["funding_interval_hours"] = interval
        return {"symbol": symbol, "instrument": instrument, "bid": book["bid"], "ask": book["ask"],
                "price": trade["price"], "spread_fraction": spread,
                "quoted_at": min(stamps), "source": f"Binance {instrument} WebSocket nativo", **extra}


def waiting(reason):
    return {"action": "ESPERAR", "reason": reason, "order": None}


def order_for_realtime(feed, capital_cop, now=None):
    """Pure sizing for the user-entered capital. Fail closed; never fall back to REST."""
    now = utcnow() if now is None else pd.Timestamp(now)
    if feed.get("version") != "5.2" or not fresh(feed["finished_at"], now, MAX_TICK_AGE):
        return waiting("El monitor de un segundo perdió su actualización. No usar valores anteriores.")
    if not fresh(feed["research_at"], now, MAX_RESEARCH_AGE):
        return waiting("El análisis de estrategia está vencido.")
    if feed.get("pause"):
        return waiting(feed["pause"])
    signal = feed.get("candidate")
    if not signal or signal.get("evidence") != "FUERTE":
        return waiting("Ninguna entrada supera todos los filtros de validación.")
    if feed.get("blocked") or feed.get("invalidated"):
        return waiting("Entrada bloqueada o invalidada; esperar una señal nueva.")
    snap, meta = feed.get("live_quote"), feed.get("market_rules")
    if not snap or not meta:
        return waiting("Esperando cotización nativa y reglas verificadas.")
    if not fresh(snap["quoted_at"], now, MAX_TICK_AGE) or not fresh(meta["checked_at"], now, MAX_RULES_AGE):
        return waiting("Cotización o reglas vencidas.")
    if any(snap.get(k) != signal[k] or meta.get(k) != signal[k] for k in ("symbol", "instrument")):
        raise ValueError("La cotización o reglas no corresponden al activo y mercado")
    bid, ask, last = (float(snap[k]) for k in ("bid", "ask", "price"))
    if not all(math.isfinite(v) and v > 0 for v in (bid, ask, last)) or ask < bid:
        raise ValueError("Cotización inválida")
    spread = (ask-bid)/((ask+bid)/2)
    if spread > .002 or not math.isclose(spread, float(snap["spread_fraction"]), abs_tol=1e-10):
        raise ValueError("Spread inválido o excesivo")
    if signal["instrument"] == "FUTURES":
        mark, rate, interval = (float(snap[k]) for k in ("mark_price", "funding_rate", "funding_interval_hours"))
        if not all(math.isfinite(v) for v in (mark, rate, interval)) or mark <= 0 or not 0 < interval <= 8:
            raise ValueError("Datos de funding inválidos")
        if snap["next_funding_time"] <= now.timestamp()*1000:
            raise ValueError("Funding vencido")
    fx = validate_fx(feed.get("fx"), now)
    profile = TradingProfile(capital_cop=float(capital_cop), available_cop=float(capital_cop), cop_per_usdt=fx,
                             risk_pct=FIXED_RISK_PCT, max_open_risk_pct=FIXED_RISK_PCT, mode="PAPER")
    result = revalidate_entry(signal, profile=profile, snapshot=snap, rules=meta["rules"], now=now)
    if result.get("order"):
        result["order"]["conversion_estimated"] = True
    return result


class Supervisor:
    def __init__(self, symbols, invalidated=None):
        self.book = QuoteBook(symbols)
        self.research = None
        self.invalidated = dict(invalidated or {})
        self.blocked = None
        self.watched = set()
        self.plans = {}

    def install_research(self, report):
        # The same candle/setup must not silently acquire new levels or a new expiry.
        old = (self.research or {}).get("candidate")
        new = report.get("candidate")
        report = deepcopy(report)
        if old:
            self.plans.setdefault(old["key"], deepcopy(old))
        if new:
            self.plans.setdefault(new["key"], deepcopy(new))
            report["candidate"] = deepcopy(self.plans[new["key"]])
        self.plans = dict(list(self.plans.items())[-2000:])
        self.research = report

    def observe(self, now=None):
        """Latch SL/TP crossings on received trades, even between evaluation ticks."""
        now = utcnow() if now is None else pd.Timestamp(now)
        signal = (self.research or {}).get("candidate")
        if not signal:
            return
        try:
            trade = self.book.component(signal["instrument"], signal["symbol"], "trade", now)
        except ValueError:
            return
        price = trade["price"]
        low, high = sorted([signal["stop"], signal["take_profit"]])
        if not low < price < high:
            self.invalidated[signal["key"]] = now.isoformat()

    def tick(self, now=None):
        now = utcnow() if now is None else pd.Timestamp(now)
        source = self.research or {}
        signal = source.get("candidate")
        self.observe(now)
        markets = []
        for row in source.get("markets", [{"symbol": s, "verdict": "Esperar", "reason": "Preparando análisis"}
                                           for s in sorted(self.book.symbols)]):
            row = deepcopy(row)
            row.update(price=None, change_24h=None)
            try:
                ticker = self.book.component("SPOT", row["symbol"], "ticker", now)
                row.update(price=ticker["price"], change_24h=ticker["change_24h"], price_at=ticker["at"])
            except ValueError:
                row["verdict"] = "Precio sin actualizar"
            markets.append(row)
        report = {"version": "5.2", "finished_at": now.isoformat(),
                  "research_at": source.get("finished_at", now.isoformat()), "risk_pct": FIXED_RISK_PCT,
                  "evaluation_interval_seconds": 1, "fx": source.get("fx"), "markets": markets,
                  "candidate": public_plan(signal) if signal else None, "pause": source.get("pause"),
                  "blocked": self.blocked, "invalidated": bool(signal and signal["key"] in self.invalidated),
                  "live_quote": None, "market_rules": source.get("market_rules"),
                  "errors": list(source.get("errors", [])), "health": "PARCIAL"}
        if self.blocked:
            report["errors"].append(self.blocked)
        elif signal and source.get("market_rules"):
            try:
                report["live_quote"] = self.book.quote(signal, source["market_rules"], now)
                self.watched.add(signal["key"])
            except (ValueError, KeyError) as exc:
                report["errors"].append(str(exc))
                if signal["key"] in self.watched:
                    self.invalidated[signal["key"]] = now.isoformat()
                    report["invalidated"] = True
        try:
            guidance = order_for_realtime(report, 1_000_000, now)
        except (ValueError, KeyError, TypeError) as exc:
            guidance = waiting(str(exc))
        report["evaluation"] = {k: guidance[k] for k in ("action", "reason")}
        report["entry_valid"] = bool(guidance.get("order"))
        if (source and not report["errors"] and not self.blocked and
                all(row.get("price") is not None for row in markets) and
                fresh(report["research_at"], now, MAX_RESEARCH_AGE)):
            report["health"] = "ACTUALIZADO"
        return json_safe(report)


def realtime_url():
    return os.getenv("TRADER_REALTIME_URL", "").strip()


def fetch_realtime():
    url = realtime_url()
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "monitor"}):
        raise ValueError("El monitor remoto requiere HTTPS")
    response = requests.get(url, timeout=2, headers={"Cache-Control": "no-cache"})
    response.raise_for_status()
    if len(response.content) > 1_000_000:
        raise ValueError("Informe demasiado grande")
    feed = response.json()
    if feed.get("version") != "5.2" or not isinstance(feed.get("markets"), list):
        raise ValueError("Monitor incompatible")
    return feed
