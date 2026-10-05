"""V5.1: one screen, one investment amount, automatic market research."""
from __future__ import annotations
import pandas as pd
import streamlit as st
from dashboard_v51 import fetch_feed, feed_age, order_for_capital, binance_rows, MAX_FEED_AGE_SECONDS
from profile_v5 import FIXED_RISK_PCT
from monitor_status import format_local
from realtime_v52 import realtime_url, fetch_realtime, order_for_realtime, MAX_TICK_AGE

REALTIME = bool(realtime_url())

st.set_page_config(page_title="Trader · Su próxima decisión", page_icon="📈", layout="centered", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.block-container{max-width:900px;padding-top:1.7rem;padding-bottom:2rem}
h1{font-size:2.1rem!important;letter-spacing:-.045em}h2,h3{letter-spacing:-.025em}
.eyebrow{font:600 .72rem system-ui;letter-spacing:.18em;color:#20b99a;margin-bottom:.7rem}
div[data-testid="stMetric"]{background:rgba(100,140,175,.07);border-radius:14px;padding:14px}
div[data-testid="stMetricLabel"]{font-size:.78rem}
div[data-testid="stMetricValue"]{font-size:1.55rem}
@media(max-width:700px){.block-container{padding:1rem .8rem}h1{font-size:1.7rem!important}
div[data-testid="stMetricValue"]{font-size:1.2rem}}
</style><div class="eyebrow">TRADER AGENT · V5.2 SIMPLE</div>
""", unsafe_allow_html=True)
st.title("Una cifra. Una decisión clara.")
st.caption("El agente revisa el mercado, elige el tipo de operación y calcula los campos de Binance. Usted decide si ejecuta.")


def cop(value):
    return "$" + f"{float(value):,.0f}".replace(",", ".") + " COP"


with st.container(border=True):
    capital = st.number_input("Capital para invertir (COP)", min_value=10_000.0, max_value=1_000_000_000.0,
                              value=1_000_000.0, step=50_000.0, key="capital_cop",
                              help="Indique el capital libre que destina al agente; no reutilice dinero comprometido en otra operación.")
    st.metric("Riesgo fijo por operación · 1,5%", cop(capital*FIXED_RISK_PCT))
    st.caption("Es el presupuesto de pérdida modelada, no una pérdida máxima garantizada. El tamaño puede ser menor por costos, saldo o mínimos de Binance.")


@st.cache_data(ttl=25, max_entries=2, show_spinner=False)
def current_feed():
    return fetch_feed()


@st.cache_data(ttl=20, max_entries=8, show_spinner=False)
def current_order(feed, amount):
    return order_for_capital(feed, amount)


@st.fragment(run_every=1 if REALTIME else 30)
def automatic_screen():
    try:
        report = fetch_realtime() if REALTIME else current_feed()
        age = feed_age(report)
    except Exception:
        st.warning("Esperar · todavía no puedo confirmar una revisión reciente del monitor.")
        st.write("El panel volverá a consultar automáticamente. No se muestran órdenes sin datos verificados.")
        with st.expander("Estado del servicio"):
            st.markdown("[Consultar monitor](https://github.com/jhonAlvarado25/trader-agent-cloud./actions)")
            st.caption("La programación del monitor no prueba que una revisión haya terminado. Aquí aparecerá la fecha del último informe recibido.")
        return
    recent = -1 <= age <= MAX_TICK_AGE if REALTIME else -5 <= age <= MAX_FEED_AGE_SECONDS
    time_text = format_local(report["finished_at"])
    if not recent:
        st.warning(f"Monitor sin actualización reciente · último informe: {time_text}")
    elif report.get("health") == "PARCIAL":
        st.info(f"Última revisión: {time_text} · cobertura parcial")
    else:
        st.caption(f"Última revisión confirmada: {time_text} · hora Colombia")
    if REALTIME:
        st.caption("Vigilancia de señales: cada segundo en el servidor. Los precios conservan su hora real de recepción; si dejan de llegar, se retiran los valores de entrada.")
        st.caption(f"Análisis de estrategia: {format_local(report['research_at'])} · Telegram: {report.get('telegram_status', 'SIN CONFIRMAR')}")
        if report.get("blocked"):
            st.warning(report["blocked"])
    else:
        st.caption("Modo actual: revisión programada cada 5 min; pantalla cada 30 s. El servicio de un segundo está preparado, pendiente de activar en un servidor permanente. Puede haber retrasos.")

    try:
        guidance = (order_for_realtime(report, float(st.session_state["capital_cop"])) if REALTIME
                    else current_order(report, float(st.session_state["capital_cop"])))
    except Exception as exc:
        guidance = {"action": "ESPERAR", "order": None,
                    "reason": str(exc) if isinstance(exc, ValueError) else "No se pudo revalidar el precio nativo. Espere a la siguiente revisión."}
    order = guidance.get("order")
    candidate = report.get("candidate")
    with st.container(border=True):
        if order:
            direction = "Comprar" if order["direction"] == "LONG" else "Venta SHORT"
            market = "Spot" if order["instrument"] == "SPOT" else "Futuros"
            st.subheader(f"Candidata para revisar: {order['symbol']}")
            st.write(f"**{market} · {direction} · análisis {order['timeframe']}**")
            st.write("La regla superó sus filtros históricos y se revalidó el precio. Esto no garantiza que esta operación gane.")
            if guidance["action"] == "COLOCAR LIMIT":
                st.info("Esperar el precio de entrada con una orden LIMIT. No perseguir el precio con una orden de mercado.")
            a, b = st.columns(2)
            a.metric("Pérdida modelada al Stop", cop(order["net_loss_cop"]))
            b.metric("Ganancia modelada al objetivo", cop(order["net_gain_cop"]))
            fx = report["fx"]
            st.caption(f"Conversión automática ESTIMADA: {fx['cop_per_usdt']:,.2f} COP/USDT (TRM + reserva 3%). No es una cotización P2P ni su costo real de compra. Confirme saldo USDT suficiente en Binance.")
            st.markdown("**Estos son los campos para Binance**")
            rows = binance_rows(order)
            st.dataframe(pd.DataFrame(rows, columns=["Campo", "Valor"]), hide_index=True, width="stretch",
                         height=min(640, 38+35*len(rows)))
            st.code("\n".join(f"{key}: {value}" for key,value in rows), language="text")
            st.caption(f"Cantidades recalculadas para {cop(st.session_state['capital_cop'])}. Vencimiento: {format_local(order['expires_at'])}.")
            st.warning("Una operación a la vez: no reutilice el mismo capital si ya tiene una posición abierta. Coloque la protección después de confirmar la entrada. No se ha enviado ninguna orden.")
            if order["instrument"] == "SPOT":
                st.caption("Una Stop-Limit puede no ejecutarse. Confirme la cantidad disponible tras la comisión; la cantidad indicada para protección es conservadora.")
            else:
                st.caption("Las instrucciones de Reduce Only suponen modo unidireccional. Hedge Mode usa Position Side y requiere adaptar los cierres. Verifique que la liquidación quede más allá del Stop.")
        else:
            st.subheader("Ahora: observar" if guidance["action"] == "SOLO OBSERVAR" else "Ahora: esperar")
            st.write(guidance["reason"])
            if candidate:
                st.caption(f"En revisión: {candidate['symbol']} · {candidate['instrument']} · {candidate['timeframe']}")
            st.write("El agente seguirá buscando una entrada con datos suficientes y costos compatibles. No necesita elegir monedas ni pulsar Analizar.")

    st.subheader("Qué está pasando en el mercado")
    st.caption("Precios Spot y variación de las últimas 24 horas al momento del informe. Una subida no es una recomendación de compra.")
    bulls = sum(item.get("trend") == "Alcista" for item in report["markets"])
    bears = sum(item.get("trend") == "Bajista" for item in report["markets"])
    if bulls or bears:
        st.write(f"Tendencia diaria del grupo: **{bulls} alcistas · {bears} bajistas**. El resto está lateral o sin contexto suficiente.")
    rows = []
    for item in report["markets"]:
        price, change = item.get("price"), item.get("change_24h")
        rows.append({"Activo": item["symbol"].removesuffix("USDT"),
                     "Precio USDT": "Sin dato" if price is None else f"{price:,.6f}".rstrip("0").rstrip("."),
                     "24 h / tendencia": "Sin dato" if change is None else f"{change:+.2f}% · {item.get('trend', 'Sin dato')}",
                     "Decisión": item["verdict"] if recent else "Informe vencido"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=390)
    with st.expander("Por qué espera / detalles del sistema"):
        for item in report["markets"]:
            st.write(f"**{item['symbol']}** — {item['reason']}")
            if item.get("range_low_20h") is not None:
                st.caption(f"Tendencia diaria: {item['trend']} · rango de las últimas 20 velas de 1 h: {item['range_low_20h']:g}–{item['range_high_20h']:g} USDT. Son extremos observados, no órdenes de entrada.")
        if report.get("errors"):
            st.write("Cobertura incompleta: los mercados afectados no se consideran validados.")
            for error in report["errors"]:
                st.caption(error)
        if candidate and candidate.get("stats"):
            stats = candidate["stats"]
            st.write(f"Clasificación histórica: {candidate['evidence']} · operaciones TEST: {stats.get('trades_test', 0)}")
            st.write("La clasificación es evidencia histórica, no una probabilidad de ganar. El riesgo de 1,5% se considera al evaluar el drawdown; aumentar el riesgo puede reducir las señales admisibles.")
        st.write("Telegram envía avisos por oportunidad, no cantidades basadas en otro capital. Los importes se calculan aquí; no necesita manejar archivos JSON.")
        st.write("El capital se conserva durante esta sesión. Si se reinicia, introdúzcalo nuevamente. No se publica en el informe de mercado.")
        st.write("La vigilancia sigue señales candidatas, no confirma sus compras, ventas, saldos ni pérdidas reales. El análisis de operaciones simuladas previas se actualiza con la investigación, no cada segundo. Las pausas de ese modelo se identifican como PAPER.")
        st.caption("Objetivo de referencia: 15–20% EA, sin garantía. No se fuerzan operaciones para llegar a él. Comisiones, conversión, funding y ejecución pueden diferir de los supuestos.")
        st.markdown("[Estado del monitor](https://github.com/jhonAlvarado25/trader-agent-cloud./actions) · [Guía breve](https://github.com/jhonAlvarado25/trader-agent-cloud./blob/main/V5_GUIA.md)")


automatic_screen()
