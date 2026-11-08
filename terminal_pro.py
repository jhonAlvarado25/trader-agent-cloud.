"""Trader Agent Pro Terminal — PAPER ONLY preview.

Deploy Streamlit con entrypoint terminal_pro.py en la rama
feature/trader-pro-terminal-paper. No usa claves privadas ni ejecuta
órdenes reales. La base SQLite local puede borrarse al reiniciar Streamlit.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config import CFG
from market import get_klines, get_live_price
from auto_decision_v42 import automatic_recommendation, enrich_auto
from paper_engine_pro import (
    RISK_FRACTION,
    DAILY_LOSS_LIMIT,
    MAX_DRAWDOWN,
    PaperRiskBlocked,
    init_paper,
    paper_enter,
    paper_close,
    paper_snapshot,
    paper_mark_current_price,
    paper_settle_candles,
    reset_paper,
)

st.set_page_config(
    page_title="Trader Agent Pro | PAPER",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st_autorefresh(interval=30000, key="pro_terminal_refresher")
st.markdown("""
<style>
.block-container{padding-top:1.2rem;max-width:1400px}
div[data-testid="stMetric"]{background:rgba(100,130,150,.075);border:1px solid rgba(125,148,168,.15);padding:12px;border-radius:14px}
div[data-testid="stVerticalBlockBorderWrapper"]{border-radius:16px}
@media(max-width:700px){
  .block-container{padding-left:.7rem;padding-right:.7rem}
  h1{font-size:1.85rem!important}
  h2{font-size:1.25rem!important}
  div[data-testid="stMetricValue"]{font-size:1.45rem}
}
</style>""",unsafe_allow_html=True)

DB_PATH = Path(os.getenv("TRADER_PAPER_DB", ".state/pro_terminal_paper.db"))
RISK_COP_REFERENCE = 1_000_000


def money(x: float, unit: str="USDT") -> str:
    return f"{x:,.2f} {unit}"


def fmt_price(value) -> str:
    try:
        p=float(value)
        if not math.isfinite(p):
            return "N/D"
        return f"{p:,.6f}" if p<10 else f"{p:,.2f}"
    except Exception:
        return "N/D"


@st.cache_data(ttl=25, show_spinner=False)
def quote(symbol: str) -> float:
    return float(get_live_price(symbol))


@st.cache_data(ttl=40, show_spinner=False)
def recent_candles(symbol: str) -> pd.DataFrame:
    return get_klines(symbol, "15m", 500)


@st.cache_data(ttl=180, show_spinner=False)
def quick_scanner() -> pd.DataFrame:
    rows=[]
    now=pd.Timestamp.now(tz="UTC")
    for symbol in CFG.symbols:
        try:
            df=enrich_auto(get_klines(symbol,"1h",350))
            df=df[df["close_time"]<=now].dropna()
            if df.empty:
                raise RuntimeError("Sin velas completas")
            r=df.iloc[-1]
            labels=[]
            if bool(r["long_signal"]) or bool(r["bal_pb_long"]):
                labels.append("LONG")
            if bool(r["short_signal"]) or bool(r["bal_pb_short"]):
                labels.append("SHORT")
            if bool(r["bal_bo_long"]) or bool(r["bal_bo_short"]):
                labels.append("BREAKOUT")
            rows.append({
                "Activo":symbol,
                "Observación técnica":", ".join(labels) if labels else "SIN SETUP",
                "Precio":float(r["close"]),
                "RSI":round(float(r["rsi"]),1),
                "Vol x":round(float(r["vol_ratio"]),2),
                "ATR %":round(float(r["atr_pct"])*100,2),
                "Fibonacci":bool(r["fib_long_confluence"] or r["fib_short_confluence"]),
                "Última vela UTC":str(r["close_time"]),
            })
        except Exception as exc:
            rows.append({"Activo":symbol,"Observación técnica":"DATOS NO DISPONIBLES","Detalle":str(exc)[:100]})
    return pd.DataFrame(rows)


def elapsed_seconds(timestamp: str) -> float:
    try:
        dt=datetime.fromisoformat(str(timestamp))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return max(0.0,(datetime.now(timezone.utc)-dt).total_seconds())
    except Exception:
        return float("inf")


def validate_paper_candidate(rec: dict, live_price: float) -> tuple[bool,str]:
    if rec.get("state")!="OPERACIÓN CANDIDATA":
        return False,"La estrategia no aprobó una operación."
    if rec.get("instrument")!="SPOT" or rec.get("direction")!="LONG":
        return False,"La fase inicial PAPER únicamente ejecuta Spot LONG; Futures se mantiene en análisis."
    if rec.get("evidence") not in {"VALIDA","PROMETEDORA","FUERTE"}:
        return False,"La señal no superó los filtros estadísticos."
    if rec.get("risk_state")=="BLOCKED":
        return False,"El Risk Engine ha bloqueado la operación."
    age=elapsed_seconds(rec.get("_validated_at_utc",""))
    if age>600:
        return False,"La validación tiene más de 10 minutos. Actualice la señal."
    entry=float(rec.get("entry") or 0)
    stop=float(rec.get("stop") or 0)
    target=float(rec.get("take_profit") or 0)
    atr=float(rec.get("atr") or 0)
    if not 0<stop<live_price<target or atr<=0:
        return False,"Entrada/Stop/TP ya no son válidos al precio actual."
    drift=abs(live_price-entry)/atr
    rr=(target-live_price)/(live_price-stop)
    if drift>CFG.alert_enter_now_atr or rr<CFG.alert_min_current_rr:
        return False,f"Señal fuera de vigencia: desvío {drift:.2f} ATR, R/R {rr:.2f}."
    return True,"Señal vigente y validada para ejercicio PAPER."


st.title("Trader Agent Pro Terminal")
st.caption("PREVIEW · Mercado público Binance · Simulador Spot PAPER · Sin claves privadas · Sin órdenes reales")

st.info(
    "PAPER significa dinero virtual. Esta versión no accede a su saldo Binance ni abre operaciones reales. "
    "GitHub Actions continúa monitorizando V4.2 en la rama principal de forma independiente."
)

with st.sidebar:
    st.markdown("### Control del simulador")
    fx=st.number_input(
        "Conversión de referencia (COP por USDT)",
        min_value=1000.0,max_value=10000.0,value=3350.0,step=10.0,
        help="Manual e ilustrativa; no representa oferta de retiro P2P."
    )
    initial=st.number_input(
        "Capital virtual al reiniciar (USDT)",
        min_value=10.0,max_value=100000.0,value=30.0,step=5.0
    )
    st.caption("Riesgo: 1,20% · Pérdida diaria máxima: 2,40% del capital inicial · Drawdown: 6%.")
    st.caption("Una entrada por día UTC · Una posición abierta · Solo Spot LONG")
    st.warning(
        "La base SQLite vive en el disco temporal de Streamlit Community Cloud. "
        "Puede perderse al reiniciar o redesplegar la app. Exporte los datos."
    )

init_paper(DB_PATH, initial_usdt=30.0)

assets=list(CFG.symbols)
left,right=st.columns([2,1],vertical_alignment="center")
with left:
    symbol=st.selectbox("Par a estudiar", assets, index=0)
with right:
    st.caption("Datos públicos Binance Spot")
    try:
        live=quote(symbol)
        st.metric(symbol, fmt_price(live)+" USDT")
        market_error=None
    except Exception as exc:
        live=None
        market_error=f"{type(exc).__name__}: {exc}"
        st.error("Precio público temporalmente no disponible")

# Reconcilia posiciones PAPER al abrir/actualizar la página, NO 24x7.
# Primero se evalúan las velas completas desde la apertura y después el tick.
pre=paper_snapshot(DB_PATH)
closed_in_cycle=[]
for open_trade in pre["open_trades"]:
    sym=open_trade["symbol"]
    try:
        candles=recent_candles(sym)
        closed_in_cycle += paper_settle_candles(DB_PATH, sym, candles.to_dict("records"))
        px=live if sym==symbol and live is not None else quote(sym)
        closed_in_cycle += paper_mark_current_price(DB_PATH, sym, float(px))
    except Exception as exc:
        st.warning(f"Monitoreo PAPER de {sym} incompleto: {type(exc).__name__}. Revise la posición virtual.")

if closed_in_cycle:
    for tr in closed_in_cycle:
        st.toast(f"Salida PAPER {tr['symbol']}: {tr['exit_reason']} · {tr['net_pnl_usdt']:+.3f} USDT")

marks={symbol:live} if live is not None else {}
snap=paper_snapshot(DB_PATH,marks)
initial_usdt=snap["initial_usdt"]
equity=snap["equity_usdt"]
pct=(equity/initial_usdt-1)*100 if initial_usdt else 0

m1,m2,m3,m4=st.columns(4)
m1.metric("Patrimonio PAPER",money(equity),f"{pct:+.2f}%")
m2.metric("Equivalente estimado COP",f"$ {equity*fx:,.0f}")
m3.metric("PnL virtual total",f"{snap['pnl_usdt']:+.2f} USDT")
m4.metric("Trades cerrados",str(snap["closed_count"]),f"{snap['wins']} W / {snap['losses']} L")

gate=snap["gate"]
r1,r2,r3,r4=st.columns(4)
r1.metric("Caja virtual",money(snap["cash_usdt"]))
r2.metric("Riesgo máx. próximo trade",money(equity*RISK_FRACTION))
r3.metric("PnL realizado hoy",f"{gate['daily_realized_pnl_usdt']:+.2f} USDT")
r4.metric("Drawdown",f"{gate['drawdown_pct']:.2f}%")

if gate["allowed"]:
    st.success("Risk Engine: SAFE — disponible para evaluar una nueva entrada PAPER.")
else:
    st.warning("Risk Engine: BLOCKED — "+" · ".join(gate["reasons"]))

st.subheader("Centro de agentes")
agent_cards=[
    ("SCANNER","Escáner público de 10 activos","BAJO DEMANDA"),
    ("MARKET CHECK","Datos, volatilidad y elegibilidad","EN VALIDACIÓN"),
    ("STRATEGY","V4.2 + Fibonacci + estadística","BAJO DEMANDA"),
    ("SIZER","Stop, costos y riesgo 1,2%","ACTIVO"),
    ("PAPER EXECUTOR","Solo órdenes simuladas","PAPER"),
    ("LOGGER & EQUITY","SQLite local, PnL y auditoría","ACTIVO"),
]
ca,cb,cc=st.columns(3)
for i,(name,desc,status) in enumerate(agent_cards):
    with [ca,cb,cc][i%3]:
        with st.container(border=True):
            st.markdown(f"**{name}**")
            st.caption(desc)
            st.caption(f"Estado: {status}")

st.subheader("Live Flow · SCAN → CHECK → SIZE → PAPER → LOG")
tab_scan,tab_trade,tab_log=st.tabs(["Escáner & validación","Simulador PAPER","Equity & journal"])

with tab_scan:
    st.caption(
        "El escáner rápido identifica condiciones técnicas, NO recomienda ejecutar órdenes por sí solo. "
        "La validación profunda V4.2 usa el backtest y el Risk Engine."
    )
    if st.button("Escanear los 10 activos ahora",key="scan_now",use_container_width=True):
        quick_scanner.clear()
        with st.spinner("Consultando velas cerradas públicas..."):
            st.session_state["pro_last_scan"]=quick_scanner()
    if "pro_last_scan" in st.session_state:
        st.dataframe(st.session_state["pro_last_scan"],hide_index=True,use_container_width=True)
    else:
        st.info("Pulse Escanear para obtener el primer estado de mercado.")

    st.markdown("**Validación profunda de oportunidad**")
    st.caption(
        "La validación puede tardar varios minutos; solo se ejecuta al solicitarla. "
        "No se fuerza una recomendación si la evidencia es insuficiente."
    )
    if st.button(f"Evaluar {symbol} con estrategia V4.2",key="deep_validate",use_container_width=True):
        try:
            with st.spinner(f"Validando estadísticamente {symbol}..."):
                rec=automatic_recommendation(
                    symbol,CFG,
                    operation_budget_cop=equity*fx,
                    risk_pct=RISK_FRACTION,
                    cop_per_usdt=fx,
                )
            rec["_validated_at_utc"]=datetime.now(timezone.utc).isoformat()
            st.session_state["pro_validated_rec"]=rec
        except Exception as exc:
            st.error(f"Validación no disponible: {type(exc).__name__}: {exc}")

    rec=st.session_state.get("pro_validated_rec")
    if rec and rec.get("symbol")==symbol:
        st.markdown("**Resultado de la estrategia**")
        st.write(f"Estado: **{rec.get('state')}**")
        st.write(rec.get("reason",""))
        if rec.get("state")=="OPERACIÓN CANDIDATA":
            col1,col2,col3=st.columns(3)
            col1.metric("Instrumento",str(rec.get("instrument")))
            col2.metric("Evidencia",str(rec.get("evidence")))
            col3.metric("Opportunity Score",f"{float(rec.get('opportunity_score') or 0):.1f}/100")
            st.caption(
                f"Entrada {fmt_price(rec.get('entry'))} · "
                f"SL {fmt_price(rec.get('stop'))} · "
                f"TP {fmt_price(rec.get('take_profit'))}"
            )
        if live is not None:
            ok,explanation=validate_paper_candidate(rec,live)
            if ok:
                st.success(explanation+" Puede utilizar la señal en la pestaña Simulador PAPER.")
            else:
                st.warning(explanation)

with tab_trade:
    st.markdown("### Nueva posición virtual")
    st.caption(
        "Al ejecutar PAPER se simula compra Spot LONG a precio público más slippage; "
        "no se envía ninguna solicitud privada a Binance."
    )
    method=st.radio(
        "Origen del ejercicio",
        ["Señal validada V4.2","Ejercicio manual (sin evidencia)"],
        horizontal=True,
        key="paper_method",
    )
    current_rec=st.session_state.get("pro_validated_rec")
    eligible=bool(
        current_rec and current_rec.get("symbol")==symbol
        and live is not None and validate_paper_candidate(current_rec,live)[0]
    )
    if method=="Señal validada V4.2":
        if not eligible:
            st.info("Se requiere una señal SPOT LONG reciente y aprobada en la pestaña Escáner & validación.")
        stop=float(current_rec.get("stop")) if eligible else 0.0
        tp=float(current_rec.get("take_profit")) if eligible else 0.0
        source="VALIDATED"
        evidence=str(current_rec.get("evidence","")) if eligible else ""
        tf=str(current_rec.get("timeframe","")) if eligible else ""
        score=float(current_rec.get("opportunity_score") or 0) if eligible else None
        signal_key=(
            f"{symbol}|{current_rec.get('signal_time')}|{current_rec.get('setup')}|SPOT|PAPER"
            if eligible else ""
        )
    else:
        stop_pct=st.slider("Stop debajo del precio (%)",0.5,10.0,2.0,step=0.5)
        rr=st.select_slider("Relación beneficio/riesgo de práctica",options=[1.5,2.0,2.5,3.0],value=2.0)
        stop=live*(1-stop_pct/100) if live else 0.0
        tp=live*(1+stop_pct*rr/100) if live else 0.0
        source="DEMO"
        evidence="NO VALIDADA · solo práctica"
        tf="SIMULACIÓN"
        score=None
        signal_key=f"DEMO|{symbol}|{datetime.now(timezone.utc).date()}|{stop_pct}|{rr}"

    if live is not None and stop and tp:
        st.write(
            f"Precio observado **{fmt_price(live)}** · "
            f"Stop **{fmt_price(stop)}** · TP **{fmt_price(tp)}**"
        )
        st.caption(
            "El motor recalcula la cantidad exacta incluyendo comisión de entrada, salida y slippage "
            "y no deja superar el 1,2% estimado de pérdida al Stop."
        )
    if st.button(
        "Registrar compra PAPER (sin dinero real)",
        type="primary",
        use_container_width=True,
        disabled=(live is None or not gate["allowed"] or (method=="Señal validada V4.2" and not eligible)),
        key="paper_enter",
    ):
        try:
            tr=paper_enter(
                DB_PATH,
                symbol=symbol,
                observed_price=float(quote(symbol)),
                stop_price=stop,
                tp_price=tp,
                signal_key=signal_key,
                source=source,
                evidence=evidence,
                timeframe=tf,
                opportunity_score=score,
            )
            st.success(
                f"PAPER registrado: {tr['quantity']:.8f} {symbol[:-4]} · "
                f"Riesgo estimado {tr['estimated_stop_loss_usdt']:.4f} USDT."
            )
            st.rerun()
        except (PaperRiskBlocked,ValueError) as exc:
            st.error(f"Risk Engine rechazó la simulación: {exc}")
        except Exception as exc:
            st.error(f"No se pudo registrar PAPER: {type(exc).__name__}: {exc}")

    st.markdown("### Posiciones PAPER abiertas")
    if not snap["open_trades"]:
        st.caption("No hay posiciones virtuales abiertas.")
    for tr in snap["open_trades"]:
        with st.container(border=True):
            cc1,cc2,cc3=st.columns(3)
            cc1.metric("Par",str(tr["symbol"]))
            cc2.metric("Entrada fill",fmt_price(tr["entry_fill"]))
            cc3.metric("Cantidad",f"{tr['quantity']:.8f}")
            st.write(
                f"SL **{fmt_price(tr['stop_price'])}** · "
                f"TP **{fmt_price(tr['tp_price'])}** · "
                f"Riesgo estimado **{tr['estimated_stop_loss_usdt']:.3f} USDT**"
            )
            st.caption(
                "El cierre automático se evalúa al abrir/refrescar esta página. "
                "No existe un proceso continuo PAPER cuando está cerrada."
            )
            if st.button(
                f"Cerrar manualmente PAPER {tr['symbol']}",
                key=f"close_{tr['id']}",
                disabled=(live is None and tr["symbol"]==symbol),
            ):
                try:
                    px=float(quote(tr["symbol"]))
                    done=paper_close(DB_PATH,tr["id"],observed_price=px)
                    st.success(f"Cierre PAPER: {done['net_pnl_usdt']:+.3f} USDT")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Falló cierre PAPER: {exc}")

with tab_log:
    trades=snap["trades"]
    closed=sorted(
        [t for t in trades if t["status"]=="CLOSED"],
        key=lambda x:x["closed_at_utc"],
    )
    cumulative=initial_usdt
    points=[{"utc":snap.get("events",[])[-1]["ts_utc"] if snap["events"] else datetime.now(timezone.utc).isoformat(),"equity":initial_usdt}]
    for tr in closed:
        cumulative+=float(tr["net_pnl_usdt"])
        points.append({"utc":tr["closed_at_utc"],"equity":cumulative})
    curve=pd.DataFrame(points)
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=curve["utc"],y=curve["equity"],mode="lines+markers",name="Equity cerrada"))
    fig.update_layout(
        height=290,
        margin=dict(l=8,r=8,t=10,b=15),
        xaxis_title="Fecha UTC",yaxis_title="USDT virtual",
    )
    st.plotly_chart(fig,use_container_width=True)
    st.caption(
        "Curva sobre resultados realizados; no representa cotizaciones continuas ni patrimonio Binance. "
        "Muestras pequeñas no prueban rentabilidad."
    )
    if trades:
        tdf=pd.DataFrame(trades)
        cols=[k for k in [
            "opened_at_utc","symbol","source","status","entry_fill","quantity",
            "stop_price","tp_price","closed_at_utc","exit_reason","net_pnl_usdt"
        ] if k in tdf.columns]
        st.dataframe(tdf[cols],hide_index=True,use_container_width=True)
        st.download_button(
            "Exportar journal de operaciones CSV",
            data=tdf.to_csv(index=False).encode("utf-8"),
            file_name="trader_pro_paper_journal.csv",mime="text/csv",
            use_container_width=True,
        )
    else:
        st.info("El journal se llenará cuando registre su primera operación PAPER.")
    if snap["events"]:
        with st.expander("Bitácora auditada de eventos",expanded=False):
            edf=pd.DataFrame(snap["events"])
            st.dataframe(edf,hide_index=True,use_container_width=True)
            st.download_button(
                "Exportar eventos CSV",
                data=edf.to_csv(index=False).encode("utf-8"),
                file_name="trader_pro_paper_events.csv",mime="text/csv"
            )

st.divider()
with st.expander("Reiniciar laboratorio PAPER (borra únicamente datos virtuales)",expanded=False):
    st.warning("Antes de reiniciar, exporte el journal. La acción es irreversible.")
    confirmation=st.checkbox("Confirmo que deseo borrar todas las operaciones simuladas.")
    if st.button("Reiniciar con nuevo capital PAPER",disabled=not confirmation):
        reset_paper(DB_PATH,float(initial))
        st.session_state.pop("pro_validated_rec",None)
        st.success("Cuenta PAPER reiniciada.")
        st.rerun()

st.caption(
    "Trader Agent Pro Terminal · PREVIEW de la rama de desarrollo. "
    "Riesgo 1,2% estimado (no garantizado frente a gaps) · "
    "No automatiza trading real ni sustituye la validación del broker."
)
