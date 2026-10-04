"""Exportable manual journal and clearly labelled shadow (paper) records."""
from __future__ import annotations
import json
import math
from pathlib import Path
import pandas as pd


def validate_records(records):
    if not isinstance(records, list) or len(records) > 10000:
        raise ValueError("Bitácora inválida o demasiado extensa")
    seen = set()
    for r in records:
        if not isinstance(r, dict) or r.get("kind") not in {"PAPER", "REAL_MANUAL"}:
            raise ValueError("Registro inválido")
        if r.get("id") in seen or not r.get("id"):
            raise ValueError("Identificador vacío/duplicado")
        seen.add(r["id"])
        if r.get("status") not in {"OPEN", "CLOSED", "PENDING", "EXPIRED"}:
            raise ValueError("Estado inválido")
        for field in ("net_pnl_usdt", "net_pnl_cop", "risk_cop"):
            value = r.get(field, 0)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("PnL/riesgo inválido")
        if r.get("risk_cop", 0) < 0:
            raise ValueError("Riesgo negativo")
        for field in ("opened_at", "closed_at"):
            if r.get(field):
                pd.Timestamp(r[field])
    return records


def journal_summary(records, kind):
    selected = [r for r in validate_records(records) if r["kind"] == kind and r["status"] == "CLOSED"]
    if not selected:
        return {"n": 0, "net_cop": 0.0, "net_usdt": 0.0, "win_rate": 0.0, "profit_factor": None}
    pnls = [r.get("net_pnl_cop", 0) for r in selected]
    pos, neg = sum(x for x in pnls if x > 0), abs(sum(x for x in pnls if x < 0))
    return {"n": len(selected), "net_cop": sum(pnls),
            "net_usdt": sum(r.get("net_pnl_usdt", 0) for r in selected),
            "win_rate": sum(x > 0 for x in pnls)/len(pnls),
            "profit_factor": pos/neg if neg else None}


def risk_stop(profile, records, now=None, kind="PAPER"):
    """Declared/shadow closed-trade loss limits; never claim real account visibility."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    today = now.tz_convert("America/Bogota").normalize()
    week = today-pd.Timedelta(days=today.weekday())
    closed = sorted([r for r in records if r.get("kind") == kind and r.get("status") == "CLOSED"],
                    key=lambda r: r["closed_at"])
    daily = weekly = cumulative = 0.0
    equity, peak = profile.capital_cop, profile.capital_cop
    max_dd = 0.0
    for r in closed:
        # Only the same capital/profile epoch is comparable for these circuit breakers.
        if r.get("capital_cop") != profile.capital_cop:
            continue
        pnl = r.get("net_pnl_cop", 0)
        stamp = pd.Timestamp(r["closed_at"]).tz_convert("America/Bogota")
        if stamp >= today:
            daily += pnl
        if stamp >= week:
            weekly += pnl
        cumulative += pnl
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak-equity)/peak)
    if daily <= -profile.capital_cop*profile.max_daily_loss_pct:
        return f"Pausa: límite de pérdida diaria {kind} alcanzado"
    if weekly <= -profile.capital_cop*profile.max_weekly_loss_pct:
        return f"Pausa: límite de pérdida semanal {kind} alcanzado"
    if max_dd >= profile.max_drawdown_pct:
        return f"Pausa: drawdown {kind} alcanzado; revisar la estrategia"
    return None
