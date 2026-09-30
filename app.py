from __future__ import annotations
import math
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config import CFG
from market import get_live_price, get_klines
from indicators import enrich
from strategy import evaluate_row, build_levels
from backtest import backtest, monte_carlo
from risk import position_size

st.set_page_config(
    page_title="Trader Agent Cloud V1",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Refresco de la app; el historial queda cacheado para no golpear Binance innecesariamente.
st_autorefresh(interval=15_000, key="market_refresh")

st.markdown("""
<style>
.block-container {
    padding-top: 1rem;
    padding-bottom: 2rem;
    max-width: 1250px;
}
div[data-testid="stMetric"] {
    border: 1px solid rgba(120,120,120,0.20);
    padding: 0.55rem 0.75rem;
    border-radius: 0.75rem;
}
.small-note {
    opacity: 0.72;
    font-size: 0.86rem;
}
.status-card {
    padding: 0.8rem 1rem;
    border-radius: 0.8rem;
    border: 1px solid rgba(120,120,120,0.22);
    margin-bottom: 0.8rem;
}
@media (max-width: 700px) {
    .block-container { padding-left: 0.7rem; padding-right: 0.7rem; }
    h1 { font-size: 1.75rem !important; }
    h2, h3 { font-size: 1.15rem !important; }
}
</style>
""", unsafe_allow_html=True)

st.title("Trader Agent Cloud V1")
st.caption("BTC/USDT · 4H · Solo lectura · Binance público · Diseñado para iPhone")

with st.sidebar:
    st.header("Capital y riesgo")
    capital_cop = st.number_input(
        "Capital para trading (COP)",
        min_value=10_000.0,
        value=float(CFG.default_capital_cop),
        step=50_000.0,
    )
    risk_pct = st.number_input(
        "Riesgo por operación (%)",
        min_value=0.05,
        max_value=2.0,
        value=CFG.default_risk_pct * 100,
        step=0.05,
    ) / 100
    cop_per_usdt = st.number_input(
        "COP por 1 USDT",
        min_value=1000.0,
        max_value=10000.0,
        value=float(CFG.default_cop_per_usdt),
        step=10.0,
    )
    st.divider()
    st.write("**Filtros V1**")
    st.write(f"R/R objetivo: 1:{CFG.reward_risk:.1f}")
    st.write(f"Muestra mínima: {CFG.min_backtest_trades}")
    st.write(f"Expectativa mínima: +{CFG.min_expectancy_r:.2f}R")
    st.write(f"Profit Factor mínimo: {CFG.min_profit_factor:.2f}")

@st.cache_data(ttl=240, show_spinner=False)
def load_history():
    return get_klines(CFG.symbol, CFG.interval, CFG.history_bars)

try:
    with st.spinner("Actualizando mercado..."):
        raw = load_history()
        df = enrich(raw, CFG)
        live = get_live_price(CFG.symbol)
except Exception as e:
    st.error("No fue posible consultar Binance en este momento.")
    st.code(str(e))
    st.info("La app es solo lectura y no usa claves API. Puedes volver a cargar en unos segundos.")
    st.stop()

now_utc = pd.Timestamp.now(tz="UTC")
closed = df[df["close_time"] <= now_utc].copy()
if len(closed) < 250:
    st.error("No hay suficientes velas cerradas para calcular la estrategia.")
    st.stop()

current = closed.iloc[-1]
ev = evaluate_row(current, CFG)
levels = build_levels(current, CFG)
trades, stats = backtest(closed, CFG)
mc = monte_carlo(stats, CFG)

sample_ok = stats["resolved"] >= CFG.min_backtest_trades
expectancy_ok = stats["expectancy_r"] >= CFG.min_expectancy_r
pf_ok = stats["profit_factor"] >= CFG.min_profit_factor
current_ok = bool(ev.get("signal", False))

if current_ok and sample_ok and expectancy_ok and pf_ok:
    state = "SETUP VÁLIDO"
    state_note = "Reglas técnicas cumplidas y evidencia histórica por encima de los mínimos V1."
elif current_ok:
    state = "VIGILAR"
    state_note = "Existe señal técnica, pero la evidencia estadística todavía no supera todos los filtros."
else:
    state = "NO OPERAR"
    state_note = "La última vela 4H cerrada no cumple todas las condiciones del setup."

risk = position_size(
    capital_cop,
    risk_pct,
    cop_per_usdt,
    levels["entry"],
    levels["stop"],
    levels["tp"],
)

# Hora Colombia
close_bogota = pd.Timestamp(current["close_time"]).tz_convert("America/Bogota")

top1, top2 = st.columns(2)
top1.metric("BTC/USDT en vivo", f"{live:,.2f} USDT")
top2.metric("Estado V1", state)
st.markdown(f'<div class="status-card"><b>{state}</b><br>{state_note}</div>', unsafe_allow_html=True)

a,b,c = st.columns(3)
a.metric("Prob. ajustada", f"{stats['adjusted_win_rate']*100:.1f}%")
b.metric("Expectativa", f"{stats['expectancy_r']:+.2f} R")
c.metric("Profit Factor", f"{stats['profit_factor']:.2f}" if math.isfinite(stats["profit_factor"]) else "∞")

st.caption(
    f"Última vela 4H cerrada: {close_bogota.strftime('%d/%m/%Y %H:%M')} Colombia · "
    f"Refresco visual cada ~15 s · historial recalculado cada ~4 min"
)

st.subheader("Plan de operación de referencia")
p1,p2 = st.columns(2)
p1.metric("Entrada", f"{levels['entry']:,.2f} USDT")
p2.metric("Stop técnico", f"{levels['stop']:,.2f} USDT", f"-{levels['risk_pct']*100:.2f}%")
p3,p4 = st.columns(2)
p3.metric("Take Profit", f"{levels['tp']:,.2f} USDT", f"1:{levels['rr']:.1f}")
p4.metric("Posición sugerida", f"COP {risk['position_cop']:,.0f}")

q1,q2 = st.columns(2)
q1.metric("USDT a usar", f"{risk['position_usdt']:,.2f}")
q2.metric("BTC aprox.", f"{risk['qty_btc']:.8f}")
q3,q4 = st.columns(2)
q3.metric("Pérdida neta estimada", f"COP {risk['net_loss_cop']:,.0f}")
q4.metric("Ganancia neta estimada", f"COP {risk['net_gain_cop']:,.0f}")

st.caption("Los niveles se calculan con la última vela 4H cerrada. Son un marco de riesgo, no una garantía de rentabilidad.")

with st.expander("Ver condiciones técnicas", expanded=False):
    gates = pd.DataFrame(
        [{"Regla": k, "Cumple": "Sí" if v else "No"} for k, v in ev.get("gates", {}).items()]
    )
    st.dataframe(gates, hide_index=True, use_container_width=True)
    st.write(
        f"EMA20: **{current['ema20']:,.2f}** · "
        f"EMA50: **{current['ema50']:,.2f}** · "
        f"EMA200: **{current['ema200']:,.2f}**"
    )
    st.write(
        f"RSI(14): **{current['rsi']:.1f}** · "
        f"ATR(14): **{current['atr']:,.2f} USDT ({current['atr_pct']*100:.2f}%)** · "
        f"Volumen relativo: **{current['vol_ratio']:.2f}x**"
    )

st.subheader("Validación histórica")
b1,b2 = st.columns(2)
b1.metric("Operaciones resueltas", f"{stats['resolved']}")
b2.metric("Win rate bruto", f"{stats['raw_win_rate']*100:.1f}%")
b3,b4 = st.columns(2)
b3.metric("Break-even 1:2", f"{stats['breakeven_win_rate']*100:.1f}%")
b4.metric("Máx. drawdown", f"{stats['max_drawdown_r']:.1f} R")

st.write(
    f"Resultado histórico: **{stats['net_r']:+.1f}R** · "
    f"Duración media: **{stats['avg_holding_bars']*4:.1f} h** · "
    f"Time stops: **{stats['time_stops']}**"
)

st.subheader("Monte Carlo — 100 operaciones")
m1,m2 = st.columns(2)
m1.metric("Resultado mediano", f"{mc['median_r']:+.1f} R")
m2.metric("Prob. terminar negativo", f"{mc['prob_negative']*100:.1f}%")
m3,m4 = st.columns(2)
m3.metric("Percentil 5%", f"{mc['p05_r']:+.1f} R")
m4.metric("Drawdown P95", f"{mc['dd95_r']:.1f} R")
st.caption("Simulación basada en el backtest; no predice el resultado de la próxima operación.")

with st.expander("Gráfico BTC/USDT 4H", expanded=False):
    plot_df = closed.tail(180)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=plot_df["close_time"], open=plot_df["open"], high=plot_df["high"],
        low=plot_df["low"], close=plot_df["close"], name="BTC"
    ))
    fig.add_trace(go.Scatter(x=plot_df["close_time"], y=plot_df["ema20"], name="EMA20", mode="lines"))
    fig.add_trace(go.Scatter(x=plot_df["close_time"], y=plot_df["ema50"], name="EMA50", mode="lines"))
    fig.add_trace(go.Scatter(x=plot_df["close_time"], y=plot_df["ema200"], name="EMA200", mode="lines"))
    fig.add_hline(y=levels["stop"], line_dash="dash", annotation_text="Stop")
    fig.add_hline(y=levels["tp"], line_dash="dash", annotation_text="TP")
    fig.update_layout(
        height=520,
        xaxis_rangeslider_visible=False,
        margin=dict(l=5, r=5, t=10, b=5),
        legend_orientation="h",
    )
    st.plotly_chart(fig, use_container_width=True)

with st.expander("Últimas operaciones del backtest", expanded=False):
    if trades.empty:
        st.warning("No se detectaron setups con los parámetros actuales.")
    else:
        show = trades.tail(20).copy()
        show["signal_time"] = show["signal_time"].astype(str)
        st.dataframe(show.sort_values("signal_time", ascending=False), hide_index=True, use_container_width=True)

with st.expander("Cómo leer el estado", expanded=False):
    st.markdown("""
**NO OPERAR**: falta alguna condición técnica del setup.

**VIGILAR**: existe señal técnica, pero la muestra, expectativa o Profit Factor no supera todavía el filtro estadístico.

**SETUP VÁLIDO**: la señal actual y los filtros estadísticos V1 se cumplen. Esto **no significa** que la operación vaya a ganar.

La probabilidad es histórica y ajustada con una prior Beta(5,5) para evitar exceso de confianza en muestras pequeñas.
""")

st.divider()
st.caption("Trader Agent Cloud V1 · Solo análisis · Spot · Sin API keys · Sin ejecución automática")
