"""Worker de seguimiento de posiciones PAPER abiertas sin VM.

Programable en GitHub Actions. No abre posiciones ni usa credenciales privadas
Binance. Solo precios públicos y Postgres remoto, con heartbeat persistente.

NO es proceso de baja latencia: GitHub schedule puede atrasarse u omitirse.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone

import pandas as pd

from market import get_klines, get_live_price
from paper_engine_pro import (
    init_paper, paper_snapshot, paper_settle_candles,
    paper_mark_current_price, paper_update_worker_status,
)
from paper_pg_backend import is_postgres_target


INTERVAL="15m"
MAX_BARS=500  # 5.2 días; si hay hueco, no inventar operaciones.


def replay_one(target: str, trade: dict, now: datetime | None=None) -> dict:
    now=now or datetime.now(timezone.utc)
    symbol=str(trade["symbol"])
    opened=datetime.fromisoformat(str(trade["opened_at_utc"]))
    if opened.tzinfo is None:
        opened=opened.replace(tzinfo=timezone.utc)
    age_seconds=max(0.0,(now-opened).total_seconds())
    bars_needed=max(40,int(math.ceil(age_seconds/900))+4)

    if bars_needed>MAX_BARS:
        return {
            "symbol":symbol,"status":"DATA_GAP",
            "message":"Posición demasiado antigua para reconstruir todas las velas en una consulta; revisión manual PAPER requerida.",
        }

    df=get_klines(symbol,INTERVAL,bars_needed)
    if df is None or df.empty:
        return {"symbol":symbol,"status":"DATA_GAP","message":"No hay velas disponibles."}

    oldest=df.iloc[0]["open_time"].to_pydatetime()
    if oldest.tzinfo is None:
        oldest=oldest.replace(tzinfo=timezone.utc)
    if oldest>opened:
        return {
            "symbol":symbol,"status":"DATA_GAP",
            "message":"Las velas devueltas no cubren la apertura de la operación; no se liquida.",
        }

    completed=df[df["close_time"]<=pd.Timestamp(now)].copy()
    # Fail closed si falta una vela completa entre la entrada y la última
    # disponible, o si el feed deja de avanzar. El ticker no sustituye el
    # camino histórico para reconstruir toques intravela.
    future=completed[completed["open_time"]>=pd.Timestamp(opened)].copy()
    if len(future)>1:
        spacing=future["open_time"].diff().dropna().dt.total_seconds()
        if (spacing>901).any():
            return {"symbol":symbol,"status":"DATA_GAP","message":"Faltan velas consecutivas; revisión manual PAPER requerida."}
    if completed.empty or (now-completed.iloc[-1]["close_time"].to_pydatetime()).total_seconds()>2700:
        return {"symbol":symbol,"status":"DATA_GAP","message":"Última vela completa demasiado antigua; no se liquida."}
    closes=paper_settle_candles(target,symbol,completed.to_dict("records"))
    if closes:
        return {"symbol":symbol,"status":"SETTLED_CANDLES","closed":[x["id"] for x in closes]}

    remaining=paper_snapshot(target)["open_trades"]
    if not any(x["id"]==trade["id"] for x in remaining):
        return {"symbol":symbol,"status":"ALREADY_CLOSED"}

    current=float(get_live_price(symbol))
    if not math.isfinite(current) or current<=0:
        return {"symbol":symbol,"status":"NO_PRICE"}
    ticks=paper_mark_current_price(target,symbol,current)
    return {
        "symbol":symbol,
        "status":"SETTLED_TICK" if ticks else "OPEN",
        "price":round(current,6),
        "closed":[x["id"] for x in ticks],
    }


def run_worker(target: str) -> dict:
    if not is_postgres_target(target):
        raise RuntimeError("El worker requiere PostgreSQL remoto; SQLite local no es compartido.")
    init_paper(target)
    snap=paper_snapshot(target)
    reports=[]
    for trade in snap["open_trades"]:
        try:
            reports.append(replay_one(target,trade))
        except Exception as exc:
            # Mensajes de excepción de drivers/proveedores pueden incluir
            # hosts, parámetros; no revelar secretos en heartbeat ni logs.
            reports.append({
                "symbol":str(trade.get("symbol")),
                "status":"ERROR",
                "exception":type(exc).__name__,
            })

    bad=[x for x in reports if x["status"] in {"DATA_GAP","NO_PRICE","ERROR"}]
    status="DEGRADED" if bad else "OK"
    details={
        "open_before":len(snap["open_trades"]),
        "closed":sum(len(x.get("closed",[])) for x in reports),
        "reports":reports,
        "mode":"PAPER_ONLY",
        "live_orders":False,
    }
    paper_update_worker_status(target,status,details)
    return {"status":status,**details}


def main() -> int:
    enabled=os.getenv("TRADER_PAPER_WORKER_ENABLED","no").lower()=="yes"
    target=os.getenv("TRADER_PAPER_DATABASE_URL","").strip()
    if not enabled or not target:
        print("[PAPER WORKER] No activado: configure DB y TRADER_PAPER_WORKER_ENABLED=yes.")
        return 0
    try:
        report=run_worker(target)
        print(
            f"[PAPER WORKER] {report['status']} | "
            f"posiciones={report['open_before']} | cierres={report['closed']}"
        )
        return 0 if report["status"]=="OK" else 2
    except Exception as exc:
        print(f"[PAPER WORKER] ERROR {type(exc).__name__} (detalles privados omitidos).")
        return 3


if __name__=="__main__":
    raise SystemExit(main())
