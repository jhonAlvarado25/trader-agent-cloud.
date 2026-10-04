"""Scheduled read-only monitor, acknowledged delivery, and shadow paper lifecycle."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import pandas as pd

from config import CFG
from profile_v5 import load_profile
from auto_decision_v5 import automatic_recommendation, revalidate_entry
from market_v5 import history, funding_history
from state_v5 import load_state, save_state, deliver_once
from telegram_v5 import send_signal
from journal_v5 import risk_stop


def open_paper_record(signal, guidance):
    order = guidance.get("order")
    if order is None or guidance["action"] not in {"ENTRAR AHORA", "COLOCAR LIMIT"}:
        return None
    now = pd.Timestamp.now(tz="UTC")
    return {
        "id": signal["key"], "kind": "PAPER", "status": "PENDING" if guidance["action"] == "COLOCAR LIMIT" else "OPEN",
        "symbol": signal["symbol"], "instrument": signal["instrument"], "direction": signal["direction"],
        "created_at": now.isoformat(), "opened_at": now.isoformat() if guidance["action"] == "ENTRAR AHORA" else None,
        "expires_at": signal["expires_at"], "entry": order["entry"], "stop": order["stop"],
        "take_profit": order["take_profit"], "qty": order["qty"], "risk_cop": order["net_loss_cop"],
        "margin_cop": order["margin_cop"], "costs_cop": order["costs_cop"],
        "limit_sl": order.get("limit_sl"),
        "cop_per_usdt": order["cop_per_usdt"], "capital_cop": order["operation_budget_cop"],
        "fee_each_side": order["profile"]["spot_fee_each_side"] if order["instrument"] == "SPOT" else order["profile"]["futures_fee_each_side"],
        "slippage_each_side": order["profile"]["slippage_each_side"],
        "net_pnl_usdt": 0.0, "net_pnl_cop": 0.0,
        "assumption": "Modelo PAPER; no confirma fills ni cierres reales en Binance",
    }


def remaining_profile(profile, active):
    """Reserve cash/margin as well as loss budget for all active shadows."""
    risk = sum(r["risk_cop"] for r in active)
    capital = 0.0
    for record in active:
        # Older checkpoints without cash fields are held conservatively until closed.
        margin = record.get("margin_cop", profile.capital_cop)
        costs = record.get("costs_cop", 0.0)
        if not all(math.isfinite(x) and x >= 0 for x in (margin, costs)):
            raise ValueError("Reserva de capital PAPER inválida")
        capital += margin + costs
    return profile.updated(committed_risk_cop=profile.committed_risk_cop+risk,
                           available_cop=max(0.0, profile.available_cop-capital))


def update_paper(record, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if record["status"] not in {"PENDING", "OPEN"}:
        return record
    created = pd.Timestamp(record["created_at"])
    bars = min(4600, max(5, int((now-created).total_seconds()/60)+3))
    candles = history(record["symbol"], "1m", bars, record["instrument"])
    if candles.empty or candles.iloc[0].open_time > created.ceil("min"):
        raise RuntimeError("Cobertura PAPER incompleta; conservar riesgo reservado y revisar checkpoint")
    # Discard the candle that was already in progress when the alert was sent.
    candles = candles[candles.open_time >= created.ceil("min")]
    for _, candle in candles.iterrows():
        if record["status"] == "PENDING":
            if candle.open_time > pd.Timestamp(record["expires_at"]):
                record["status"] = "EXPIRED"
                return record
            if candle.low <= record["entry"] <= candle.high:
                record["status"], record["opened_at"] = "OPEN", candle.open_time.isoformat()
            else:
                continue
        opened = pd.Timestamp(record["opened_at"])
        if candle.open_time < opened:
            continue
        long = record["direction"] == "LONG"
        hit_stop = candle.low <= record["stop"] if long else candle.high >= record["stop"]
        hit_tp = candle.high >= record["take_profit"] if long else candle.low <= record["take_profit"]
        expired = candle.close_time >= opened+pd.Timedelta(hours=72)
        if not (hit_stop or hit_tp or expired):
            continue
        # Same-bar ambiguity and gaps: conservative stop-first and worse opening price.
        if hit_stop:
            stop_fill = record.get("limit_sl") if record["instrument"] == "SPOT" else None
            stop_fill = stop_fill if stop_fill is not None else record["stop"]
            price = min(stop_fill, candle.open) if long else max(stop_fill, candle.open)
            outcome = "SL"
        elif hit_tp:
            price, outcome = record["take_profit"], "TP"
        else:
            price, outcome = float(candle.close), "TIME"
        qty, entry = record["qty"], record["entry"]
        gross = qty*(price-entry)*(1 if long else -1)
        costs = qty*(entry+price)*(record["fee_each_side"]+record["slippage_each_side"])
        funding_cost = 0.0
        if record["instrument"] == "FUTURES":
            funding = funding_history(record["symbol"], int((opened-pd.Timedelta(hours=9)).timestamp()*1000), int(candle.close_time.timestamp()*1000))
            funding = funding[(funding.funding_time > opened) & (funding.funding_time <= candle.close_time)]
            funding_cost = float((funding.funding_rate*funding.mark_price).sum())*qty*(1 if long else -1)
        net = gross-costs-funding_cost
        record.update(status="CLOSED", closed_at=candle.close_time.isoformat(), exit=price, outcome=outcome,
                      net_pnl_usdt=net, net_pnl_cop=net*record["cop_per_usdt"],
                      funding_usdt=funding_cost, trading_costs_usdt=costs)
        return record
    if record["status"] == "PENDING" and now > pd.Timestamp(record["expires_at"]):
        record["status"] = "EXPIRED"
    return record


def main():
    profile, state = load_profile(), load_state()
    errors, signals = [], []
    for record in state["journal"]:
        try:
            update_paper(record)
        except Exception as exc:
            errors.append(f"Seguimiento {record['symbol']}: {type(exc).__name__}")
    pause = risk_stop(profile, state["journal"])
    active = [r for r in state["journal"] if r["status"] in {"OPEN", "PENDING"}]
    # Conservative portfolio cap: ALL crypto signals share the same risk bucket.
    for symbol in CFG.symbols:
        if pause:
            break
        if any(r["symbol"] == symbol for r in active):
            continue
        p = remaining_profile(profile, active)
        if p.risk_budget_cop <= 0 or p.available_cop <= 0 or len(active) >= 2:
            break
        try:
            signal = automatic_recommendation(symbol, profile=p)
            if signal["state"] != "OPERACIÓN CANDIDATA":
                print(f"[{symbol}] {signal['state']}: {signal['reason']}")
                errors.extend(signal.get("data_errors", []))
                continue
            if signal["key"] in state["sent_keys"]:
                continue
            guidance = revalidate_entry(signal, p)
            if guidance.get("order") is None:
                continue
            signal = guidance["order"]
            record = open_paper_record(signal, guidance)
            def acknowledge():
                if record:
                    state["journal"].append(record)
            if deliver_once(state, signal, lambda s: send_signal(s, guidance), on_delivered=acknowledge):
                if record:
                    active.append(record)
                signals.append(signal)
                save_state(state)
        except Exception as exc:
            errors.append(f"{symbol}: {type(exc).__name__}")
    state.update(last_run=pd.Timestamp.now(tz="UTC").isoformat(), errors=errors[-50:], pause=pause)
    save_state(state)
    summary = f"V5 · modo {profile.mode} · capital COP {profile.capital_cop:,.0f} · riesgo {profile.risk_pct*100:.2f}%\n"
    summary += f"Alertas confirmadas: {len(signals)} · sombras activas: {len(active)} · errores/datos bloqueados: {len(errors)}\n"
    summary += (pause or "Sin pausa PAPER")+"\nMeta 15–20% EA: objetivo, no rentabilidad demostrada.\n"
    print(summary)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary)
    # No fake success notice: the summary explicitly reports missing data/delivery errors.
    fatal_delivery = any("RuntimeError" in error or "RequestException" in error for error in errors)
    return 2 if fatal_delivery else 0


if __name__ == "__main__":
    raise SystemExit(main())
