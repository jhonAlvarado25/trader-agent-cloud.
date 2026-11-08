"""Motor Spot PAPER: no importa ni llama a endpoints de ejecución Binance.

Todo el capital es virtual; los fill prices y comisiones son simulados.
SQLite local es temporal en Streamlit Community Cloud: exporte el journal.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from paper_pg_backend import connect_paper

RISK_FRACTION = 0.012
DAILY_LOSS_LIMIT = 0.024
MAX_DRAWDOWN = 0.06
FEE_FRACTION = 0.001
SLIPPAGE_FRACTION = 0.0002
MIN_NOTIONAL_USDT = 5.0


class PaperRiskBlocked(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(path: str | Path):
    return connect_paper(path)

def _log(con, event: str, detail: dict) -> None:
    con.execute(
        "INSERT INTO events(ts_utc,event,detail_json) VALUES (?,?,?)",
        (utc_now(), event, json.dumps(detail, ensure_ascii=False, allow_nan=False)),
    )


def init_paper(path: str | Path, initial_usdt: float = 30.0) -> None:
    initial = float(initial_usdt)
    if not math.isfinite(initial) or initial < 10:
        raise ValueError("El capital PAPER inicial debe ser >= 10 USDT.")
    with _connect(path) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS account(
            id INTEGER PRIMARY KEY CHECK(id=1),
            initial_usdt REAL NOT NULL,
            cash_usdt REAL NOT NULL,
            high_water_usdt REAL NOT NULL,
            created_at_utc TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS trades(
            id TEXT PRIMARY KEY,
            signal_key TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side='LONG'),
            source TEXT NOT NULL CHECK(source IN ('VALIDATED','DEMO')),
            evidence TEXT,
            timeframe TEXT,
            opportunity_score REAL,
            opened_at_utc TEXT NOT NULL,
            entry_reference REAL NOT NULL,
            entry_fill REAL NOT NULL,
            quantity REAL NOT NULL,
            entry_fee_usdt REAL NOT NULL,
            committed_usdt REAL NOT NULL,
            risk_budget_usdt REAL NOT NULL,
            estimated_stop_loss_usdt REAL NOT NULL,
            stop_price REAL NOT NULL,
            tp_price REAL NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')),
            closed_at_utc TEXT,
            exit_reason TEXT,
            exit_fill REAL,
            exit_fee_usdt REAL,
            net_pnl_usdt REAL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            event TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS worker_status(
            id INTEGER PRIMARY KEY CHECK(id=1),
            last_attempt_utc TEXT,
            last_success_utc TEXT,
            status TEXT NOT NULL,
            details_json TEXT NOT NULL
        )""")
        con.execute(
            "INSERT OR IGNORE INTO account VALUES(1,?,?,?,?)",
            (initial, initial, initial, utc_now()),
        )
        con.execute(
            "INSERT OR IGNORE INTO worker_status VALUES(1,NULL,NULL,'NOT_CONFIGURED','{}')"
        )


def _account(con) -> dict:
    row = con.execute("SELECT * FROM account WHERE id=1").fetchone()
    if row is None:
        raise RuntimeError("Cuenta PAPER no inicializada")
    return dict(row)


def _trades(con) -> list[dict]:
    return [dict(x) for x in con.execute("SELECT * FROM trades ORDER BY opened_at_utc DESC,id DESC")]



def _worker_status(con) -> dict:
    row=con.execute("SELECT * FROM worker_status WHERE id=1").fetchone()
    if not row:
        return {"status":"NOT_CONFIGURED","last_attempt_utc":None,"last_success_utc":None}
    data=dict(row)
    try:
        data["details"]=json.loads(data.get("details_json") or "{}")
    except (ValueError,TypeError):
        data["details"]={}
    return data


def paper_update_worker_status(path: str | Path, status: str, details: dict) -> None:
    """Guarda heartbeat durable; no almacena API keys ni contraseñas."""
    if status not in {"OK","DEGRADED","ERROR"}:
        raise ValueError("Estado del worker inválido")
    payload=json.dumps(details,ensure_ascii=False,allow_nan=False)
    if len(payload)>3000:
        payload=json.dumps({"note":payload[:2800],"truncated":True},ensure_ascii=False)
    now=utc_now()
    with _connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        if status=="OK":
            con.execute(
                "UPDATE worker_status SET last_attempt_utc=?,last_success_utc=?,status=?,details_json=? WHERE id=1",
                (now,now,status,payload),
            )
        else:
            con.execute(
                "UPDATE worker_status SET last_attempt_utc=?,status=?,details_json=? WHERE id=1",
                (now,status,payload),
            )


def _get_estimated_equity(con, marks: Mapping[str,float] | None) -> float:
    cash = float(_account(con)["cash_usdt"])
    opens = con.execute("SELECT symbol,entry_fill,quantity FROM trades WHERE status='OPEN'")
    for row in opens:
        p = (marks or {}).get(row["symbol"], row["entry_fill"])
        price = float(p)
        if not math.isfinite(price) or price <= 0:
            price = float(row["entry_fill"])
        # Conservador: valor de venta estimado después de coste de salida.
        cash += float(row["quantity"]) * price * (1-SLIPPAGE_FRACTION) * (1-FEE_FRACTION)
    return cash


def _daily_realized_pnl(con, day: str) -> float:
    row = con.execute(
        "SELECT COALESCE(SUM(net_pnl_usdt),0) AS amount FROM trades "
        "WHERE status='CLOSED' AND substr(closed_at_utc,1,10)=?",
        (day,),
    ).fetchone()
    return float(row["amount"])


def _gate(con, marks: Mapping[str,float] | None) -> dict:
    a = _account(con)
    equity = _get_estimated_equity(con, marks)
    hwm = max(float(a["high_water_usdt"]), equity)
    drawdown = max(0.0, 1-equity/hwm) if hwm > 0 else 1.0
    today = utc_now()[:10]
    daily_pnl = _daily_realized_pnl(con, today)
    entries_today = con.execute(
        "SELECT COUNT(*) AS n FROM trades WHERE substr(opened_at_utc,1,10)=?",
        (today,),
    ).fetchone()["n"]
    open_count = con.execute(
        "SELECT COUNT(*) AS n FROM trades WHERE status='OPEN'"
    ).fetchone()["n"]
    reasons = []
    if equity <= 0:
        reasons.append("Sin patrimonio PAPER disponible")
    if drawdown >= MAX_DRAWDOWN:
        reasons.append("Drawdown de 6% alcanzado")
    if daily_pnl <= -float(a["initial_usdt"]) * DAILY_LOSS_LIMIT:
        reasons.append("Límite de pérdida diaria de 2,4% alcanzado")
    if open_count >= 1:
        reasons.append("Ya existe una posición PAPER abierta")
    if entries_today >= 1:
        reasons.append("Máximo una nueva entrada por día UTC")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "equity_usdt": equity,
        "drawdown_pct": drawdown*100,
        "daily_realized_pnl_usdt": daily_pnl,
        "entries_today": int(entries_today),
        "open_count": int(open_count),
    }


def paper_snapshot(path: str | Path, marks: Mapping[str,float] | None = None) -> dict:
    with _connect(path) as con:
        a = _account(con)
        trades = _trades(con)
        equity = _get_estimated_equity(con, marks)
        closed = [t for t in trades if t["status"] == "CLOSED"]
        wins = sum(1 for t in closed if float(t["net_pnl_usdt"]) > 0)
        return {
            "initial_usdt": float(a["initial_usdt"]),
            "cash_usdt": float(a["cash_usdt"]),
            "high_water_usdt": max(float(a["high_water_usdt"]), equity),
            "equity_usdt": equity,
            "pnl_usdt": equity-float(a["initial_usdt"]),
            "realized_pnl_usdt": sum(float(t["net_pnl_usdt"]) for t in closed),
            "wins": wins,
            "losses": len(closed)-wins,
            "closed_count": len(closed),
            "open_trades": [t for t in trades if t["status"]=="OPEN"],
            "trades": trades,
            "gate": _gate(con, marks),
            "events": [dict(row) for row in con.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT 100"
            )],
            "worker": _worker_status(con),
        }


def paper_enter(
    path: str | Path,
    *,
    symbol: str,
    observed_price: float,
    stop_price: float,
    tp_price: float,
    signal_key: str,
    source: str,
    evidence: str = "",
    timeframe: str = "",
    opportunity_score: float | None = None,
    min_notional_usdt: float = MIN_NOTIONAL_USDT,
) -> dict:
    """Abre una compra Spot virtual, dimensionada por pérdida neta al stop.

    El Stop no garantiza una pérdida máxima real: gaps y slippage pueden ser
    mayores. Las órdenes reales Binance NO son llamadas.
    """
    if source not in {"VALIDATED", "DEMO"}:
        raise ValueError("Fuente no permitida. Use VALIDATED o DEMO.")
    symbol = str(symbol).strip().upper()
    if not symbol.endswith("USDT") or len(symbol) < 7:
        raise ValueError("Solo pares Spot */USDT para fase PAPER.")
    try:
        m, stop, tp = [float(x) for x in (observed_price, stop_price, tp_price)]
    except (ValueError, TypeError):
        raise ValueError("Precio de entrada/stop/objetivo inválido.") from None
    if not all(math.isfinite(x) and x > 0 for x in [m, stop, tp]):
        raise ValueError("Todos los precios deben ser positivos y finitos.")
    if not stop < m < tp:
        raise PaperRiskBlocked("LONG requiere Stop < precio actual < TP.")
    if not signal_key or len(str(signal_key)) > 200:
        raise ValueError("Identificador de señal requerido (hasta 200 caracteres).")
    min_notional = float(min_notional_usdt)
    if not 0 < min_notional <= 10000:
        raise ValueError("Nocional mínimo inválido.")

    with _connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute("SELECT id FROM trades WHERE signal_key=?", (signal_key,)).fetchone()
        if existing:
            raise PaperRiskBlocked("Esta señal PAPER ya fue registrada; se evitó una orden duplicada.")

        gate = _gate(con, {symbol: m})
        if not gate["allowed"]:
            raise PaperRiskBlocked(" / ".join(gate["reasons"]))

        a = _account(con)
        cash = float(a["cash_usdt"])
        risk_usdt = float(gate["equity_usdt"]) * RISK_FRACTION
        entry_fill = m*(1+SLIPPAGE_FRACTION)
        stop_fill = stop*(1-SLIPPAGE_FRACTION)
        loss_per_unit = entry_fill*(1+FEE_FRACTION) - stop_fill*(1-FEE_FRACTION)
        if loss_per_unit <= 0:
            raise PaperRiskBlocked("Stop inválido después de costos.")
        qty_by_risk = risk_usdt/loss_per_unit
        qty_by_cash = cash/(entry_fill*(1+FEE_FRACTION))
        qty = math.floor(min(qty_by_risk,qty_by_cash)*1e8)/1e8
        if qty <= 0:
            raise PaperRiskBlocked("Capital insuficiente.")
        principal = qty*entry_fill
        entry_fee = principal*FEE_FRACTION
        committed = principal+entry_fee
        stop_loss_usdt = qty*loss_per_unit
        if committed > cash + 1e-8 or stop_loss_usdt > risk_usdt + 1e-8:
            raise PaperRiskBlocked("El dimensionamiento excede capital o presupuesto de riesgo.")
        if principal < min_notional:
            raise PaperRiskBlocked(
                f"Nocional simulado {principal:.2f} USDT inferior al mínimo de referencia "
                f"{min_notional:.2f} USDT. Se bloquea."
            )
        tid = str(uuid.uuid4())
        now = utc_now()
        con.execute(
            """INSERT INTO trades(
                id,signal_key,symbol,side,source,evidence,timeframe,opportunity_score,
                opened_at_utc,entry_reference,entry_fill,quantity,entry_fee_usdt,
                committed_usdt,risk_budget_usdt,estimated_stop_loss_usdt,
                stop_price,tp_price,status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, str(signal_key), symbol, "LONG", source, evidence, timeframe,
             opportunity_score, now, m, entry_fill, qty, entry_fee,
             committed, risk_usdt, stop_loss_usdt, stop, tp, "OPEN")
        )
        con.execute("UPDATE account SET cash_usdt=cash_usdt-? WHERE id=1", (committed,))
        _log(con,"PAPER_ENTER",{
            "trade_id": tid,"symbol": symbol,"qty":qty,
            "reference":m,"fill":entry_fill,"stop":stop,"tp":tp,
            "risk_budget_usdt":risk_usdt,"worst_stop_loss_est_usdt":stop_loss_usdt,
            "source":source,"real_order_sent":False,
        })
        return dict(con.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone())


def _close_in_transaction(
    con,
    trade: dict,
    price: float,
    reason: str,
    resolved_at_utc: str | None = None,
) -> dict:
    if trade["status"] != "OPEN":
        raise PaperRiskBlocked("Posición ya cerrada.")
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Precio de salida inválido.")
    exit_fill = price*(1-SLIPPAGE_FRACTION)
    qty = float(trade["quantity"])
    exit_gross = qty*exit_fill
    exit_fee = exit_gross*FEE_FRACTION
    proceeds = exit_gross-exit_fee
    pnl = proceeds-float(trade["committed_usdt"])
    now = resolved_at_utc or utc_now()
    con.execute(
        """UPDATE trades SET status='CLOSED',closed_at_utc=?,exit_reason=?,
            exit_fill=?,exit_fee_usdt=?,net_pnl_usdt=? WHERE id=? AND status='OPEN'""",
        (now,reason,exit_fill,exit_fee,pnl,trade["id"]),
    )
    con.execute("UPDATE account SET cash_usdt=cash_usdt+? WHERE id=1", (proceeds,))
    equity = _get_estimated_equity(con,None)
    con.execute(
        "UPDATE account SET high_water_usdt=MAX(high_water_usdt,?) WHERE id=1",
        (equity,),
    )
    _log(con,"PAPER_EXIT",{
        "trade_id":trade["id"],"symbol":trade["symbol"],"price":exit_fill,
        "reason":reason,"net_pnl_usdt":pnl,"real_order_sent":False,
    })
    return dict(con.execute("SELECT * FROM trades WHERE id=?", (trade["id"],)).fetchone())


def paper_close(
    path: str | Path,
    trade_id: str,
    *,
    observed_price: float,
    reason: str="MANUAL",
) -> dict:
    if reason not in {"MANUAL","STOP","TP","STOP_AND_TP_SAME_CANDLE"}:
        raise ValueError("Motivo de salida inválido.")
    with _connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute("SELECT * FROM trades WHERE id=?",(trade_id,)).fetchone()
        if row is None:
            raise ValueError("No existe esa operación PAPER.")
        return _close_in_transaction(con,dict(row),float(observed_price),reason)


def paper_settle_candles(
    path: str | Path,
    symbol: str,
    candles: Sequence[Mapping],
) -> list[dict]:
    """Liquida por velas COMPLETAS posteriores a la entrada.

    Si Stop y TP aparecen en la misma vela, toma STOP primero. Si una vela
    abre por debajo del Stop, toma su apertura desfavorable. Nunca reconstruye
    secuencia intravela. Sin continuidad histórica no garantiza seguimiento.
    """
    symbol=str(symbol).upper()
    closed=[]
    with _connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        opens=[dict(r) for r in con.execute(
            "SELECT * FROM trades WHERE status='OPEN' AND symbol=?",(symbol,)
        )]
        for trade in opens:
            opened=datetime.fromisoformat(trade["opened_at_utc"])
            for bar in sorted(candles,key=lambda r:str(r["open_time"])):
                bar_open=bar["open_time"]
                if isinstance(bar_open,str):
                    bar_open=datetime.fromisoformat(bar_open)
                if bar_open.tzinfo is None:
                    bar_open=bar_open.replace(tzinfo=timezone.utc)
                # Una vela abierta antes de la entrada puede contener precios
                # PREVIOS a la compra. Nunca acreditar un TP con esa vela.
                # Si toca el Stop, se imputa STOP de forma conservadora.
                partial_entry_candle = bar_open < opened
                close_t=bar.get("close_time")
                if close_t is not None:
                    if isinstance(close_t,str):
                        close_t=datetime.fromisoformat(close_t)
                    if close_t.tzinfo is None:
                        close_t=close_t.replace(tzinfo=timezone.utc)
                    if close_t>datetime.now(timezone.utc):
                        continue
                    if close_t<=opened:
                        continue
                elif partial_entry_candle:
                    # Sin marca temporal de cierre no es posible afirmar
                    # que esta vela incluye precios posteriores a entrada.
                    continue
                high=float(bar["high"])
                low=float(bar["low"])
                open_price=float(bar["open"])
                if not all(math.isfinite(v) and v>0 for v in (high,low,open_price)):
                    continue
                sl=float(trade["stop_price"])
                tp=float(trade["tp_price"])
                if low<=sl:
                    # Conservador ante GAP a la baja y velas que tocan TP/SL.
                    ref=min(sl,open_price)
                    reason="STOP_AND_TP_SAME_CANDLE" if high>=tp else "STOP"
                elif not partial_entry_candle and high>=tp:
                    ref=tp
                    reason="TP"
                else:
                    continue
                close_time_text = close_t.astimezone(timezone.utc).isoformat() if close_t is not None else utc_now()
                closed.append(_close_in_transaction(con,trade,ref,reason,resolved_at_utc=close_time_text))
                break
    return closed


def paper_mark_current_price(path: str | Path, symbol: str, price: float) -> list[dict]:
    """Vigilancia al refrescar el panel, NO permanente cuando está cerrado."""
    if not math.isfinite(float(price)) or price<=0:
        return []
    closed=[]
    with _connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        trades=[dict(x) for x in con.execute(
            "SELECT * FROM trades WHERE status='OPEN' AND symbol=?",(symbol.upper(),)
        )]
        for trade in trades:
            if price<=trade["stop_price"]:
                # Si hay gap adverso se cierra al precio actual, no al stop.
                closed.append(_close_in_transaction(con,trade,float(price),"STOP"))
            elif price>=trade["tp_price"]:
                # Conservador: no acreditar precio favorable extra.
                closed.append(_close_in_transaction(con,trade,float(trade["tp_price"]),"TP"))
        current_equity=_get_estimated_equity(con,{symbol.upper():float(price)})
        con.execute(
            "UPDATE account SET high_water_usdt=MAX(high_water_usdt,?) WHERE id=1",
            (current_equity,),
        )
    return closed


def reset_paper(path: str | Path, starting_cash: float) -> None:
    """Borra ÚNICAMENTE el laboratorio virtual, nunca datos ni órdenes Binance."""
    initial=float(starting_cash)
    if not math.isfinite(initial) or initial<10:
        raise ValueError("Se requiere capital PAPER >= 10 USDT.")
    with _connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM trades")
        con.execute("DELETE FROM events")
        con.execute(
            "UPDATE account SET initial_usdt=?,cash_usdt=?,high_water_usdt=?,created_at_utc=? WHERE id=1",
            (initial,initial,initial,utc_now()),
        )
        _log(con,"PAPER_RESET",{"initial_usdt":initial,"real_order_sent":False})
