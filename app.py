"""V5.3: one screen, one investment amount, resilient automatic market research."""
from __future__ import annotations
import pandas as pd
import requests
from urllib.parse import urlparse
import streamlit as st
from dashboard_v51 import fetch_feed, feed_age, order_for_capital, binance_rows, MAX_FEED_AGE_SECONDS
from profile_v5 import FIXED_RISK_PCT
from monitor_status import format_local
from realtime_v52 import realtime_url, fetch_realtime, order_for_realtime, MAX_TICK_AGE
from binance_readonly import (
    BinanceReadOnlyClient,
    BinanceReadOnlyError,
    BinanceLocationRestricted,
    balance_map,
    permission_is_read_only,
)

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
</style><div class="eyebrow">TRADER AGENT · V5.3 RESILIENTE</div>
""", unsafe_allow_html=True)
st.title("Una cifra. Una decisión clara.")
st.caption("El agente revisa el mercado, elige el tipo de operación y calcula los campos de Binance. Usted decide si ejecuta.")


def cop(value):
    return "$" + f"{float(value):,.0f}".replace(",", ".") + " COP"


@st.cache_data(ttl=60, max_entries=1, show_spinner=False)
def readonly_binance_snapshot():
    """Read private account data without exposing credentials to the browser."""
    try:
        bridge_url = str(st.secrets.get("BINANCE_READONLY_BRIDGE_URL", "")).strip()
        bridge_token = str(st.secrets.get("BINANCE_READONLY_BRIDGE_TOKEN", "")).strip()
    except Exception:
        bridge_url = bridge_token = ""

    if bridge_url or bridge_token:
        if not bridge_url or not bridge_token:
            return {"configured": True, "connected": False, "error": "Bridge URL/token incomplete"}
        parsed = urlparse(bridge_url)
        if parsed.scheme != "https" or not parsed.netloc:
            return {"configured": True, "connected": False, "error": "BINANCE_READONLY_BRIDGE_URL must use HTTPS"}
        try:
            response = requests.get(
                bridge_url.rstrip("/") + "/snapshot",
                headers={"Authorization": f"Bearer {bridge_token}"},
                timeout=10,
            )
            data = response.json() if response.content else {}
            if response.status_code == 451:
                return {
                    "configured": True,
                    "connected": False,
                    "restricted_location": True,
                    "bridge": True,
                    "error": data.get("error", "Bridge location rejected by Binance"),
                }
            if not response.ok or not data.get("ok"):
                return {
                    "configured": True,
                    "connected": False,
                    "bridge": True,
                    "error": data.get("error", f"Bridge HTTP {response.status_code}"),
                }
            return {
                "configured": True,
                "connected": True,
                "read_only": bool(data.get("read_only")),
                "balances": data.get("balances", {}),
                "usdt_free": float(data.get("usdt_free", 0)),
                "usdt_locked": float(data.get("usdt_locked", 0)),
                "usdt_total": float(data.get("usdt_total", 0)),
                "bridge": True,
                "error": None,
            }
        except Exception as exc:
            return {
                "configured": True,
                "connected": False,
                "bridge": True,
                "error": f"{type(exc).__name__}: {str(exc)[:220]}",
            }

    try:
        api_key = str(st.secrets["BINANCE_API_KEY"]).strip()
        api_secret = str(st.secrets["BINANCE_API_SECRET"]).strip()
    except Exception:
        return {"configured": False, "connected": False, "error": None}

    if not api_key or not api_secret:
        return {"configured": False, "connected": False, "error": None}

    try:
        client = BinanceReadOnlyClient(api_key, api_secret)
        permissions = client.permissions()
        read_only = permission_is_read_only(permissions)
        result = {
            "configured": True,
            "connected": True,
            "read_only": read_only,
            "permissions": permissions,
            "balances": {},
            "usdt_free": 0.0,
            "usdt_locked": 0.0,
            "error": None,
        }
        # Do not read balances if a risky permission is detected. The safety
        # boundary is intentional: fix the key in Binance first.
        if read_only:
            balances = balance_map(client.account())
            usdt = balances.get("USDT", {"free": 0.0, "locked": 0.0, "total": 0.0})
            result.update(
                balances=balances,
                usdt_free=float(usdt["free"]),
                usdt_locked=float(usdt["locked"]),
                usdt_total=float(usdt["total"]),
            )
        return result
    except BinanceLocationRestricted as exc:
        return {
            "configured": True,
            "connected": False,
            "restricted_location": True,
            "error": str(exc),
        }
    except BinanceReadOnlyError as exc:
        return {
            "configured": True,
            "connected": False,
            "restricted_location": False,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "configured": True,
            "connected": False,
            "error": f"{type(exc).__name__}: {str(exc)[:220]}",
        }


def explain_binance_error(message):
    text = str(message or "")
    if "-2015" in text:
        return (
            "Binance rechazó la API Key, la restricción de IP o los permisos. "
            "Revise que la clave siga activa, tenga Enable Reading y que cualquier restricción de IP permita a Streamlit."
        )
    if "-1022" in text:
        return "La firma no coincide. Revise que API Key y Secret correspondan a la misma clave HMAC y que no tengan espacios adicionales."
    if "-1021" in text:
        return "Binance detectó una diferencia de tiempo. Recargue la app; si persiste, revisaremos la sincronización del cliente."
    lowered = text.lower()
    if "451" in text or "restricted location" in lowered or "eligibility" in lowered:
        return (
            "Binance está rechazando la ubicación del servidor donde corre Streamlit. "
            "Esto no demuestra que la API Key o la Secret sean incorrectas. "
            "No habilite trading ni retiros para intentar resolverlo."
        )
    return "No fue posible validar la conexión. No cambie permisos de trading ni retiros para intentar resolverlo."


with st.container(border=True):
    capital = st.number_input("Capital para invertir (COP)", min_value=10_000.0, max_value=1_000_000_000.0,
                              value=1_000_000.0, step=50_000.0, key="capital_cop",
                              help="Indique el capital libre que destina al agente; no reutilice dinero comprometido en otra operación.")
    st.metric("Riesgo fijo por operación · 1,5%", cop(capital*FIXED_RISK_PCT))
    st.caption("Es el presupuesto de pérdida modelada, no una pérdida máxima garantizada. El tamaño puede ser menor por costos, saldo o mínimos de Binance.")


binance_private = readonly_binance_snapshot()
with st.container(border=True):
    st.subheader("Mi cuenta Binance · SOLO LECTURA")
    if not binance_private.get("configured"):
        st.info("Las credenciales todavía no están disponibles en Streamlit Secrets.")
        st.caption("Se esperan BINANCE_API_KEY y BINANCE_API_SECRET. No las escriba en GitHub ni en el chat.")
    elif not binance_private.get("connected"):
        if binance_private.get("restricted_location"):
            st.warning("CLAVES GUARDADAS · SERVIDOR STREAMLIT BLOQUEADO POR REGIÓN")
            st.write(
                "Binance rechazó la ubicación del servidor de Streamlit antes de poder validar su cuenta. "
                "Por lo tanto, este mensaje no significa que sus claves estén mal."
            )
            st.write(
                "La conexión privada de Binance necesita ejecutarse desde un servidor en una región elegible. "
                "El análisis público del agente y Telegram continúan funcionando sin usar estas claves."
            )
        else:
            st.error("API configurada, pero Binance no confirmó la conexión.")
            st.write(explain_binance_error(binance_private.get("error")))
        with st.expander("Detalle técnico"):
            st.code(binance_private.get("error") or "Sin detalle", language="text")
    elif not binance_private.get("read_only"):
        st.error("Conexión detectada, pero la API NO cumple la política de solo lectura.")
        st.write("Desactive permisos de trading, Futures, Margin, Options, Portfolio Margin y retiros en Binance antes de usarla con el agente.")
        with st.expander("Permisos detectados"):
            p = binance_private.get("permissions", {})
            st.json({
                "Enable Reading": bool(p.get("enableReading", False)),
                "Spot & Margin Trading": bool(p.get("enableSpotAndMarginTrading", False)),
                "Futures": bool(p.get("enableFutures", False)),
                "Margin": bool(p.get("enableMargin", False)),
                "Withdrawals": bool(p.get("enableWithdrawals", False)),
                "Vanilla Options": bool(p.get("enableVanillaOptions", False)),
                "Portfolio Margin": bool(p.get("enablePortfolioMarginTrading", False)),
            })
    else:
        st.success(
            "CONECTADA · permisos verificados de SOLO LECTURA"
            + (" · conector regional HTTPS" if binance_private.get("bridge") else "")
        )
        a, b = st.columns(2)
        a.metric("USDT libre", f"{binance_private.get('usdt_free', 0):,.2f}")
        b.metric("USDT bloqueado", f"{binance_private.get('usdt_locked', 0):,.2f}")
        nonzero = binance_private.get("balances", {})
        st.caption(f"Activos con saldo no nulo: {len(nonzero)}. El agente no puede comprar, vender, cancelar, transferir ni retirar fondos.")
        with st.expander("Ver saldos no nulos"):
            rows = [
                {"Activo": asset, "Libre": values["free"], "Bloqueado": values["locked"], "Total": values["total"]}
                for asset, values in sorted(nonzero.items())
            ]
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                st.write("No se encontraron saldos Spot no nulos.")


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
        st.caption("Modo actual: supervisor cloud con ciclos objetivo cada 5 min durante cada sesión horaria. GitHub puede retrasar el inicio de una sesión; la pantalla indica siempre la hora real del último informe.")

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
            st.write("La regla superó sus filtros históricos y se revalidó el precio disponible. Esto no garantiza que esta operación gane.")
            if guidance["action"] == "REVALIDAR EN BINANCE" or order.get("execution_requires_binance_check"):
                st.warning(
                    "El servidor cloud está usando una referencia conservadora para Futures porque Binance bloqueó su REST nativo. "
                    "Antes de enviar la orden, confirme en Binance el precio de entrada y que la precisión/cantidad sean aceptadas."
                )
            if guidance["action"] == "COLOCAR LIMIT":
                st.info("Esperar el precio de entrada con una orden LIMIT. No perseguir el precio con una orden de mercado.")
            a, b = st.columns(2)
            a.metric("Pérdida modelada al Stop", cop(order["net_loss_cop"]))
            b.metric("Ganancia modelada al objetivo", cop(order["net_gain_cop"]))
            fx = report["fx"]
            st.caption(f"Conversión automática ESTIMADA: {fx['cop_per_usdt']:,.2f} COP/USDT (TRM + reserva 3%). No es una cotización P2P ni su costo real de compra.")
            if binance_private.get("connected") and binance_private.get("read_only"):
                free_usdt = float(binance_private.get("usdt_free", 0))
                free_cop_est = free_usdt * float(fx["cop_per_usdt"])
                required_usdt = float(st.session_state["capital_cop"]) / float(fx["cop_per_usdt"])
                if free_usdt + 1e-9 < required_usdt:
                    st.warning(
                        f"Saldo Spot USDT libre aproximado: {free_usdt:,.2f} USDT (~{cop(free_cop_est)}). "
                        f"Es inferior al capital indicado (~{required_usdt:,.2f} USDT). "
                        "Reduzca el capital o disponga del saldo necesario antes de ejecutar."
                    )
                else:
                    st.success(
                        f"Saldo Spot USDT libre suficiente para el capital indicado: {free_usdt:,.2f} USDT "
                        f"(~{cop(free_cop_est)} con la conversión estimada)."
                    )
            else:
                st.caption("No se pudo confirmar el saldo privado de Binance; verifique manualmente el USDT disponible antes de ejecutar.")
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
        st.write("Telegram distingue CANDIDATA FUERTE de OPORTUNIDAD EN OBSERVACIÓN. Solo una candidata FUERTE puede mostrar campos de ejecución; una observación no es una entrada.")
        st.write("El capital se conserva durante esta sesión. Si se reinicia, introdúzcalo nuevamente. No se publica en el informe de mercado.")
        st.write("La vigilancia sigue señales candidatas, no confirma sus compras, ventas, saldos ni pérdidas reales. El análisis de operaciones simuladas previas se actualiza con la investigación, no cada segundo. Las pausas de ese modelo se identifican como PAPER.")
        st.caption("Objetivo de referencia: 15–20% EA, sin garantía. No se fuerzan operaciones para llegar a él. Comisiones, conversión, funding y ejecución pueden diferir de los supuestos.")
        st.markdown("[Estado del monitor](https://github.com/jhonAlvarado25/trader-agent-cloud./actions) · [Guía breve](https://github.com/jhonAlvarado25/trader-agent-cloud./blob/main/V5_GUIA.md)")


automatic_screen()
