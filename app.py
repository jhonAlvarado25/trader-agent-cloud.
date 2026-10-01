from __future__ import annotations
import math
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config import CFG
from market import get_live_price, get_active_endpoint
from engine import prepare_symbol, quality_gate, provisional_levels
from risk import position_size
from setups import SETUP_PULLBACK, SETUP_BREAKOUT, diagnose_setups
from monitor_status import get_last_scheduled_run, next_4h_close, format_local

st.set_page_config(
    page_title="Trader Agent Cloud V3.1",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st_autorefresh(interval=15_000, key="refresh")

st.markdown("""
<style>
.block-container{padding-top:1rem;padding-bottom:2rem;max-width:1250px}
div[data-testid="stMetric"]{border:1px solid rgba(120,120,120,.20);padding:.55rem .75rem;border-radius:.75rem}
.status{padding:.8rem 1rem;border-radius:.8rem;border:1px solid rgba(120,120,120,.22);margin:.4rem 0 .8rem 0}
@media(max-width:700px){
.block-container{padding-left:.65rem;padding-right:.65rem}
h1{font-size:1.7rem!important}
h2,h3{font-size:1.1rem!important}
}
</style>
""", unsafe_allow_html=True)

st.title("Trader Agent Cloud V3.1")
st.caption("Multi-timeframe · Pullback + Breakout/Retest · Walk-forward · Monte Carlo · Solo lectura")

with st.sidebar:
    symbol = st.selectbox("Activo", list(CFG.symbols), index=list(CFG.symbols).index(CFG.primary_symbol))
    capital = st.number_input("Capital para trading (COP)", min_value=10_000.0, value=float(CFG.default_capital_cop), step=50_000.0)
    risk_pct = st.number_input("Riesgo por operación (%)", min_value=0.05, max_value=2.0, value=CFG.default_risk_pct*100, step=0.05) / 100
    cop_per_usdt = st.number_input("COP por 1 USDT", min_value=1000.0, max_value=10000.0, value=float(CFG.default_cop_per_usdt), step=10.0)
    st.divider()
    st.write("**Costos asumidos**")
    st.write(f"Fee por lado: {CFG.fee_each_side*100:.2f}%")
    st.write(f"Slippage por lado: {CFG.slippage_each_side*100:.2f}%")
    st.write(f"Máx. posición: {CFG.max_position_fraction*100:.0f}% del capital")

@st.cache_data(ttl=300, show_spinner=False)
def cached_analysis(sym: str):
    return prepare_symbol(sym, CFG)

@st.cache_data(ttl=300, show_spinner=False)
def cached_scanner():
    rows = []
    for sym in CFG.symbols:
        try:
            r = prepare_symbol(sym, CFG)
            gate = quality_gate(r, CFG)
            setup = gate["setup"]
            row = r["current"]
            diag = diagnose_setups(row, CFG)

            item = {
                "Activo": sym,
                "Estado": gate["state"],
                "Setup": setup or "-",
                "Régimen": "Sí" if bool(row["daily_regime"]) else "No",
                "Pullback": f"{diag['pullback_score']}/{diag['pullback_total']}",
                "Breakout": f"{diag['breakout_score']}/{diag['breakout_total']}",
                "RSI": float(row["rsi"]),
                "Vol x": float(row["vol_ratio"]),
                "ATR %": float(row["atr_pct"]) * 100,
                "Exp R": None,
                "Edge pp": None,
                "PF": None,
                "Ops OOS": 0,
            }

            if setup:
                s = r["setup_results"][setup]["stats_oos"]
                item.update({
                    "Exp R": s["expectancy_r"],
                    "Edge pp": s["edge_pp"],
                    "PF": s["profit_factor"],
                    "Ops OOS": s["n"],
                })
            rows.append(item)
        except Exception as e:
            rows.append({
                "Activo": sym,
                "Estado": "ERROR",
                "Setup": "-",
                "Régimen": "-",
                "Pullback": "-",
                "Breakout": "-",
                "Detalle": str(e),
            })
    return pd.DataFrame(rows)

@st.cache_data(ttl=300, show_spinner=False)
def cached_monitor_status():
    return get_last_scheduled_run()

try:
    live = get_live_price(symbol)
    result = cached_analysis(symbol)
except Exception as exc:
    st.error("No se pudo actualizar el mercado.")
    st.code(str(exc))
    st.stop()

gate = quality_gate(result, CFG)
current = result["current"]
setup = gate["setup"]
diag = diagnose_setups(current, CFG)
monitor_status = cached_monitor_status()

st.subheader("Monitor automático")
m1,m2,m3 = st.columns(3)
m1.metric("Última vela 4H", format_local(current["close_time"]))
m2.metric("Próximo cierre 4H", format_local(next_4h_close()))
if monitor_status:
    conclusion = monitor_status.get("conclusion") or monitor_status.get("status") or "desconocido"
    label = "OK" if conclusion == "success" else ("ALERTA" if conclusion == "failure" else conclusion.upper())
    m3.metric("Último monitor", label)
    st.caption(
        f"Última ejecución programada: {format_local(monitor_status['created_at'])} · "
        f"Run #{monitor_status.get('run_number', '-')}. Hora Colombia (UTC-5)."
    )
else:
    m3.metric("Último monitor", "Sin dato")
    st.caption("No fue posible consultar el último run de GitHub. El análisis local del panel sigue funcionando.")

t1,t2 = st.columns(2)
t1.metric(f"{symbol} en vivo", f"{live:,.2f} USDT")
t2.metric("Estado V3", gate["state"])
st.markdown(f'<div class="status"><b>{gate["state"]}</b><br>{gate["reason"]}</div>', unsafe_allow_html=True)

if setup:
    stats_all = result["setup_results"][setup]["stats_all"]
    stats_oos = result["setup_results"][setup]["stats_oos"]
    mc = result["setup_results"][setup]["mc"]

    a,b,c = st.columns(3)
    a.metric("Setup actual", setup)
    b.metric("Prob. ajustada OOS", f"{stats_oos['adjusted_p']*100:.1f}%")
    c.metric("Edge vs break-even", f"{stats_oos['edge_pp']:+.1f} pp")

    d,e,f = st.columns(3)
    d.metric("Expectativa OOS", f"{stats_oos['expectancy_r']:+.2f} R")
    e.metric("Profit Factor OOS", f"{stats_oos['profit_factor']:.2f}" if math.isfinite(stats_oos["profit_factor"]) else "∞")
    f.metric("Operaciones OOS", f"{stats_oos['n']}")

    with st.expander("Filtros del Quality Gate", expanded=False):
        checks = pd.DataFrame([
            {"Filtro":k, "Cumple":"Sí" if v else "No"}
            for k,v in gate.get("checks", {}).items()
        ])
        st.dataframe(checks, hide_index=True, use_container_width=True)

    levels = provisional_levels(result, CFG)
    if levels:
        round_trip = 2*(CFG.fee_each_side + CFG.slippage_each_side)
        risk = position_size(
            capital, risk_pct, CFG.max_position_fraction, cop_per_usdt,
            levels["entry"], levels["stop"], levels["tp"], round_trip
        )

        st.subheader("Plan de riesgo de referencia")
        p1,p2 = st.columns(2)
        p1.metric("Entrada ref.", f"{levels['entry']:,.2f}")
        p2.metric("Stop técnico", f"{levels['stop']:,.2f}", f"-{levels['risk_pct']*100:.2f}%")
        p3,p4 = st.columns(2)
        p3.metric("Take Profit", f"{levels['tp']:,.2f}", f"1:{levels['rr']:.1f}")
        p4.metric("Posición sugerida", f"COP {risk['position_cop']:,.0f}")

        q1,q2 = st.columns(2)
        q1.metric("USDT", f"{risk['position_usdt']:,.2f}")
        q2.metric("Cantidad aprox.", f"{risk['qty']:.8f}")
        q3,q4 = st.columns(2)
        q3.metric("Pérdida neta est.", f"COP {risk['net_loss_cop']:,.0f}")
        q4.metric("Ganancia neta est.", f"COP {risk['net_gain_cop']:,.0f}")

        if risk["position_cap_hit"]:
            st.info("El límite de tamaño de posición (35% del capital) reduce el riesgo efectivo respecto al presupuesto máximo.")

    st.subheader("Validación temporal / Walk-forward")
    folds = result["setup_results"][setup]["folds"].copy()
    if not folds.empty:
        display_folds = folds.copy()
        display_folds["Expectativa"] = display_folds["expectancy_r"].map(lambda x: f"{x:+.2f}R")
        display_folds["PF"] = display_folds["profit_factor"].map(lambda x: f"{x:.2f}" if math.isfinite(x) else "∞")
        display_folds["Edge"] = display_folds["edge_pp"].map(lambda x: f"{x:+.1f} pp")
        st.dataframe(
            display_folds[["fold","trades","Expectativa","PF","Edge","net_r"]],
            hide_index=True, use_container_width=True
        )

    st.subheader("Monte Carlo empírico — 100 operaciones")
    m1,m2 = st.columns(2)
    m1.metric("Resultado mediano", f"{mc['median_r']:+.1f} R")
    m2.metric("Prob. terminar negativo", f"{mc['prob_negative']*100:.1f}%")
    m3,m4 = st.columns(2)
    m3.metric("Percentil 5%", f"{mc['p05_r']:+.1f} R")
    m4.metric("Drawdown P95", f"{mc['dd95_r']:.1f} R")
else:
    st.info("No se calculan niveles de entrada porque no existe un setup activo en la última vela 4H cerrada.")
    p1,p2 = st.columns(2)
    p1.metric("Progreso Pullback", f"{diag['pullback_score']}/{diag['pullback_total']} condiciones")
    p2.metric("Progreso Breakout", f"{diag['breakout_score']}/{diag['breakout_total']} condiciones")

    missing_pullback = [k for k,v in diag["pullback"].items() if not v]
    missing_breakout = [k for k,v in diag["breakout"].items() if not v]
    with st.expander("¿Por qué todavía NO OPERAR?", expanded=True):
        st.write("**Pullback — falta:** " + (" · ".join(missing_pullback) if missing_pullback else "ninguna condición"))
        st.write("**Breakout/Retest — falta:** " + (" · ".join(missing_breakout) if missing_breakout else "ninguna condición"))
        st.write(
            f"RSI actual: **{current['rsi']:.1f}** · "
            f"Volumen relativo: **{current['vol_ratio']:.2f}x** · "
            f"ATR: **{current['atr_pct']*100:.2f}%**"
        )

st.subheader("Escáner de mercados")
try:
    scan = cached_scanner()
    if not scan.empty:
        show = scan.copy()
        if "RSI" in show.columns:
            show["RSI"] = show["RSI"].apply(lambda x: "" if pd.isna(x) else f"{x:.1f}")
        if "Vol x" in show.columns:
            show["Vol x"] = show["Vol x"].apply(lambda x: "" if pd.isna(x) else f"{x:.2f}x")
        if "ATR %" in show.columns:
            show["ATR %"] = show["ATR %"].apply(lambda x: "" if pd.isna(x) else f"{x:.2f}%")
        if "Exp R" in show.columns:
            show["Exp R"] = show["Exp R"].apply(lambda x: "" if pd.isna(x) else f"{x:+.2f}")
        if "Edge pp" in show.columns:
            show["Edge pp"] = show["Edge pp"].apply(lambda x: "" if pd.isna(x) else f"{x:+.1f}")
        if "PF" in show.columns:
            show["PF"] = show["PF"].apply(lambda x: "" if pd.isna(x) else ("∞" if math.isinf(x) else f"{x:.2f}"))
        cols = [x for x in ["Activo","Estado","Setup","Régimen","Pullback","Breakout","RSI","Vol x","ATR %","Exp R","Edge pp","PF","Ops OOS"] if x in show.columns]
        st.dataframe(show[cols], hide_index=True, use_container_width=True)
        st.caption("Pullback y Breakout muestran cuántas condiciones cumple cada activo, incluso cuando el estado es NO OPERAR.")
except Exception as exc:
    st.warning(f"El escáner no pudo actualizar todos los activos: {exc}")

with st.expander("Indicadores y régimen actual", expanded=False):
    st.write(f"Régimen 1D alcista: **{'Sí' if current['daily_regime'] else 'No'}**")
    st.write(
        f"EMA20: **{current['ema20']:,.2f}** · "
        f"EMA50: **{current['ema50']:,.2f}** · "
        f"EMA200: **{current['ema200']:,.2f}**"
    )
    st.write(
        f"RSI: **{current['rsi']:.1f}** · "
        f"ATR: **{current['atr']:,.2f} ({current['atr_pct']*100:.2f}%)** · "
        f"Volumen relativo: **{current['vol_ratio']:.2f}x**"
    )

with st.expander("Gráfico 4H", expanded=False):
    df = result["df"].tail(180)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["close_time"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=symbol
    ))
    fig.add_trace(go.Scatter(x=df["close_time"], y=df["ema20"], name="EMA20", mode="lines"))
    fig.add_trace(go.Scatter(x=df["close_time"], y=df["ema50"], name="EMA50", mode="lines"))
    fig.add_trace(go.Scatter(x=df["close_time"], y=df["ema200"], name="EMA200", mode="lines"))
    fig.update_layout(height=520, xaxis_rangeslider_visible=False, margin=dict(l=5,r=5,t=10,b=5), legend_orientation="h")
    st.plotly_chart(fig, use_container_width=True)

with st.expander("Resultados históricos por estrategia", expanded=False):
    rows = []
    for name,data in result["setup_results"].items():
        s1 = data["stats_all"]
        s2 = data["stats_oos"]
        rows.append({
            "Setup":name,
            "Ops total":s1["n"],
            "Exp total":round(s1["expectancy_r"],3),
            "PF total":round(s1["profit_factor"],3) if math.isfinite(s1["profit_factor"]) else "∞",
            "Ops OOS":s2["n"],
            "Exp OOS":round(s2["expectancy_r"],3),
            "PF OOS":round(s2["profit_factor"],3) if math.isfinite(s2["profit_factor"]) else "∞",
            "Edge OOS pp":round(s2["edge_pp"],2),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

with st.expander("Monitor V3.1 / alertas al iPhone", expanded=False):
    st.write("GitHub Actions despierta a los minutos 07, 22, 37 y 52 de cada hora para reducir el riesgo de retrasos del scheduler.")
    st.write("El análisis pesado se ejecuta una sola vez por cada nueva vela 4H cerrada; los intentos posteriores de la misma vela se omiten automáticamente.")
    st.write("Si aparece una señal nueva VIGILAR o SETUP VÁLIDO, el workflow se marca como alerta para que GitHub pueda notificarte.")
    st.write("Las operaciones siguen siendo manuales en Binance; no hay claves de trading ni permisos de retiro.")

st.caption(f"Fuente activa: {get_active_endpoint()} · V3.1 es solo análisis; no accede ni opera tu cuenta.")
