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
from binance_readonly import (
    BinanceReadOnlyClient,
    BinanceReadOnlyError,
    balance_map,
    base_asset_from_symbol,
    summarize_protection,
    permission_is_read_only,
)
from futures_lab import run_lab, leverage_table
from auto_decision import automatic_recommendation

st.set_page_config(
    page_title="Trader Agent Cloud V4.1",
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

st.title("Trader Agent Cloud V4.1")
st.caption("Spot + Futures Lab · LONG/SHORT · 1H/2H/4H · Bootstrap · Monte Carlo · Solo lectura")

with st.sidebar:
    symbol = st.selectbox("Activo", list(CFG.symbols), index=list(CFG.symbols).index(CFG.primary_symbol))
    capital = st.number_input("Presupuesto por operación (COP)", min_value=10_000.0, value=float(CFG.default_capital_cop), step=50_000.0)
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

def _streamlit_secret(name: str):
    try:
        value = st.secrets[name]
        return str(value).strip() if value is not None else None
    except Exception:
        return None

def _readonly_client():
    api_key = _streamlit_secret("BINANCE_API_KEY")
    api_secret = _streamlit_secret("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        return None
    return BinanceReadOnlyClient(api_key, api_secret)


@st.cache_data(ttl=21600, show_spinner=False)
def cached_v4_lab(sym: str, years: int, simulations: int, lab_risk_pct: float):
    return run_lab(
        sym,
        years=years,
        timeframes=("1h","2h","4h"),
        stops=(1.0,1.25,1.5),
        rrs=(1.5,2.0,2.5,3.0),
        simulations=simulations,
        risk_per_trade_pct=lab_risk_pct,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_auto_recommendation(sym: str, operation_budget: float, risk_fraction: float, cop_usdt: float):
    return automatic_recommendation(
        sym,
        CFG,
        operation_budget_cop=operation_budget,
        risk_pct=risk_fraction,
        cop_per_usdt=cop_usdt,
    )

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
t2.metric("Estado V4.1", gate["state"])
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

        # Binance Spot OCO: el Limit SL queda ligeramente por debajo del Stop/Trigger.
        limit_sl = levels["stop"] * (1.0 - CFG.stop_limit_buffer_pct)
        pair_label = symbol[:-4] + "/USDT" if symbol.endswith("USDT") else symbol

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

        st.subheader("ORDEN BINANCE")
        if gate["state"] == "SETUP VÁLIDO":
            st.success("Parámetros completos para copiar en Binance Spot. Verifica el precio real antes de confirmar.")
        else:
            st.warning("Estos valores son de referencia. No ejecutar una nueva entrada mientras el estado sea VIGILAR.")

        ob1, ob2 = st.columns(2)
        ob1.metric("Par", pair_label)
        ob2.metric("Mercado", "SPOT")

        st.markdown("**1. Entrada de referencia**")
        ob3, ob4 = st.columns(2)
        ob3.metric("Precio entrada", f"{levels['entry']:,.2f} USDT")
        ob4.metric("Total a usar", f"{risk['position_usdt']:,.2f} USDT")
        ob5, ob6 = st.columns(2)
        ob5.metric("Cantidad aprox.", f"{risk['qty']:.8f}")
        ob6.metric("R/R objetivo", f"1:{levels['rr']:.1f}")

        st.markdown("**2. Protección OCO después de ejecutar la compra**")
        oc1, oc2 = st.columns(2)
        oc1.metric("Limit TP", f"{levels['tp']:,.2f} USDT")
        oc2.metric("Stop / Trigger SL", f"{levels['stop']:,.2f} USDT")
        oc3, oc4 = st.columns(2)
        oc3.metric("Limit SL", f"{limit_sl:,.2f} USDT")
        oc4.metric("Monto BTC aprox.", f"{risk['qty']:.8f}")

        st.code(
            f"""BINANCE SPOT
PAR: {pair_label}

ENTRADA
Precio de referencia: {levels['entry']:.2f} USDT
Total:                {risk['position_usdt']:.2f} USDT
Cantidad aprox.:      {risk['qty']:.8f}

OCO DE VENTA
Limit TP:             {levels['tp']:.2f} USDT
Stop / Trigger SL:    {levels['stop']:.2f} USDT
Limit SL:             {limit_sl:.2f} USDT
Monto aprox.:         {risk['qty']:.8f}""",
            language="text"
        )
        st.caption(
            f"Limit SL = Stop Trigger × (1 - {CFG.stop_limit_buffer_pct*100:.2f}%). "
            "Después de la compra, si Binance descontó comisión del activo comprado, usa el 100% del saldo disponible de esa operación "
            "en la OCO para evitar un error por saldo insuficiente. Una Stop-Limit puede no ejecutarse si el precio atraviesa el límite muy rápido."
        )

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

st.divider()
st.header("DECISIÓN AUTOMÁTICA V4.1")
st.caption(
    "Presupuesto máximo por operación: COP 1.000.000 · Riesgo objetivo: 1,20% = COP 12.000. "
    "El agente evalúa 1H/2H/4H, compara Spot vs Futures y solo propone una operación cuando la señal "
    "y la evidencia histórica mínima se cumplen. No ejecuta órdenes."
)

try:
    auto = cached_auto_recommendation(symbol, float(capital), float(risk_pct), float(cop_per_usdt))
except Exception as auto_exc:
    auto = {
        "state":"ERROR",
        "reason":f"No se pudo completar la recomendación automática: {auto_exc}",
        "symbol":symbol,
    }

ad1,ad2,ad3 = st.columns(3)
ad1.metric("Estado automático", auto.get("state","-"))
ad2.metric("Presupuesto", f"COP {float(auto.get('operation_budget_cop', capital)):,.0f}")
ad3.metric("Riesgo máximo", f"COP {float(auto.get('risk_budget_cop', capital*risk_pct)):,.0f}")

st.write(auto.get("reason",""))

if auto.get("state") == "OPERACIÓN CANDIDATA":
    st.success(
        f"Instrumento sugerido por el modelo: **{auto['instrument']}** · "
        f"{auto['direction']} · {auto['timeframe']} · evidencia {auto['evidence']}."
    )

    ar1,ar2,ar3,ar4 = st.columns(4)
    ar1.metric("Entrada ref.", f"{auto['entry']:,.2f} USDT")
    ar2.metric("Stop", f"{auto['stop']:,.2f} USDT")
    ar3.metric("Take Profit", f"{auto['take_profit']:,.2f} USDT")
    ar4.metric("R/R", f"1:{auto['rr']:.1f}")

    as1,as2,as3,as4 = st.columns(4)
    as1.metric("Posición / nocional", f"COP {auto['position_cop']:,.0f}")
    as2.metric("USDT nocional", f"{auto['position_usdt']:,.2f}")
    as3.metric("Cantidad", f"{auto['qty']:.8f}")
    as4.metric("Riesgo neto est.", f"COP {auto['net_loss_cop']:,.0f}")

    if auto["instrument"] == "SPOT":
        st.markdown("**Valores para Binance Spot**")
        st.code(
            f"""PAR: {auto['symbol'][:-4]}/USDT
MERCADO: SPOT
DIRECCIÓN: COMPRA
Total USDT:          {auto['position_usdt']:.2f}
Cantidad aprox.:     {auto['qty']:.8f}
Entrada referencia:  {auto['entry']:.2f}

OCO DE VENTA
Limit TP:            {auto['take_profit']:.2f}
Stop / Trigger SL:   {auto['stop']:.2f}
Limit SL:            {auto['limit_sl']:.2f}
Monto aprox.:        {auto['qty']:.8f}""",
            language="text"
        )
    else:
        side_label = "LONG / BUY" if auto["direction"] == "LONG" else "SHORT / SELL"
        st.markdown("**Valores para Binance USDⓈ-M Futures**")
        ft1,ft2 = st.columns(2)
        ft1.metric("Leverage sugerido", f"{auto['leverage']}x")
        ft2.metric("Margen aislado aprox.", f"COP {auto['margin_cop']:,.0f}")
        st.code(
            f"""PAR: {auto['symbol']}
MERCADO: USDⓈ-M FUTURES
MODO DE MARGEN: ISOLATED
DIRECCIÓN: {side_label}
LEVERAGE: {auto['leverage']}x

Nocional USDT:       {auto['position_usdt']:.2f}
Margen aprox. USDT:  {auto['margin_usdt']:.2f}
Cantidad aprox.:     {auto['qty']:.8f}
Entrada referencia:  {auto['entry']:.2f}
Stop Trigger:        {auto['stop']:.2f}
Take Profit:         {auto['take_profit']:.2f}
Reduce Only en SL/TP: Sí""",
            language="text"
        )

    st.caption(
        f"El dimensionamiento usa como máximo COP {auto['operation_budget_cop']:,.0f}, pero puede usar menos "
        f"si el Stop exige reducir el tamaño para respetar el riesgo neto objetivo de {risk_pct*100:.2f}%. "
        "El leverage reduce margen requerido; no aumenta el riesgo permitido."
    )

    stats = auto.get("stats",{})
    with st.expander("Evidencia estadística de la recomendación", expanded=False):
        ss1,ss2,ss3,ss4 = st.columns(4)
        ss1.metric("Trades TEST", f"{stats.get('trades_test',0)}")
        ss2.metric("Win TEST", f"{stats.get('win_test',0)*100:.1f}%")
        ss3.metric("Expectativa TEST", f"{stats.get('expectancy_test_r',0):+.3f}R")
        ss4.metric("Profit Factor", "-" if stats.get("pf_test") is None else f"{stats.get('pf_test'):.2f}")
        st.write(
            f"IC95% expectativa: **{stats.get('ci_low',0):+.3f}R a {stats.get('ci_high',0):+.3f}R** · "
            f"P(expectativa > 0): **{stats.get('prob_positive',0)*100:.1f}%**"
        )
elif auto.get("state") == "ESPERAR":
    st.warning(auto.get("reason","La señal existe, pero la entrada ya se alejó demasiado."))
elif auto.get("state") == "NO OPERAR":
    st.info("El agente seguirá esperando una configuración que supere tanto el filtro técnico como el estadístico.")

st.subheader("Mi cuenta Binance — SOLO LECTURA")
ro_client = _readonly_client()

if ro_client is None:
    st.info(
        "La integración está preparada, pero todavía faltan los secretos de Binance. "
        "Añade BINANCE_API_KEY y BINANCE_API_SECRET en Streamlit Secrets. "
        "No pegues las credenciales en GitHub ni en el chat."
    )
    st.caption("Consulta CONFIGURAR_BINANCE_SOLO_LECTURA.md en el repositorio.")
else:
    try:
        permissions = ro_client.permissions()
        readonly_ok = permission_is_read_only(permissions)
        account = ro_client.account()
        balances = balance_map(account)
        base_asset = base_asset_from_symbol(symbol)
        base_bal = balances.get(base_asset, {"free":0.0, "locked":0.0, "total":0.0})
        usdt_bal = balances.get("USDT", {"free":0.0, "locked":0.0, "total":0.0})
        orders = ro_client.open_orders(symbol)
        protection = summarize_protection(orders)

        if readonly_ok:
            st.success("Conectado a Binance con permisos verificados de SOLO LECTURA.")
        else:
            st.error(
                "La API está conectada, pero detecté uno o más permisos distintos de solo lectura. "
                "Desactiva trading, futuros, margen y retiros en Binance antes de continuar."
            )

        ac1,ac2 = st.columns(2)
        ac1.metric(f"{base_asset} total", f"{base_bal['total']:.8f}")
        ac2.metric("USDT disponible", f"{usdt_bal['free']:.2f}")
        ac3,ac4 = st.columns(2)
        ac3.metric(f"{base_asset} libre", f"{base_bal['free']:.8f}")
        ac4.metric(f"{base_asset} bloqueado", f"{base_bal['locked']:.8f}")

        st.caption(
            "Permisos: lectura={} · Spot/Margin trading={} · Futures={} · Margin={} · Retiros={}".format(
                "Sí" if permissions.get("enableReading") else "No",
                "Sí" if permissions.get("enableSpotAndMarginTrading") else "No",
                "Sí" if permissions.get("enableFutures") else "No",
                "Sí" if permissions.get("enableMargin") else "No",
                "Sí" if permissions.get("enableWithdrawals") else "No",
            )
        )

        if protection:
            st.markdown("**Protección real detectada en Binance**")
            pr1,pr2 = st.columns(2)
            pr1.metric(
                "Limit TP real",
                "-" if protection["take_profit"] is None else f"{protection['take_profit']:,.2f} USDT"
            )
            pr2.metric(
                "Stop / Trigger SL real",
                "-" if protection["stop_trigger"] is None else f"{protection['stop_trigger']:,.2f} USDT"
            )
            pr3,pr4 = st.columns(2)
            pr3.metric(
                "Limit SL real",
                "-" if protection["limit_sl"] is None else f"{protection['limit_sl']:,.2f} USDT"
            )
            pr4.metric("Cantidad protegida", f"{protection['qty']:.8f}")

            if protection["stop_trigger"] and protection["take_profit"]:
                dist_stop = (live - protection["stop_trigger"]) / live * 100
                dist_tp = (protection["take_profit"] - live) / live * 100
                st.write(
                    f"Distancia actual al Stop: **{dist_stop:+.2f}%** · "
                    f"Distancia al TP: **{dist_tp:+.2f}%**"
                )
        elif base_bal["total"] > 0:
            st.warning(
                f"Hay saldo {base_asset}, pero no detecté una orden SELL abierta de protección para {symbol}. "
                "Revisa manualmente si esa posición debe tener Stop/TP."
            )
        else:
            st.caption(f"No detecté saldo {base_asset} ni protección abierta para {symbol}.")

        with st.expander("Órdenes abiertas reales", expanded=False):
            if not orders:
                st.write("No hay órdenes abiertas para este par.")
            else:
                rows = []
                for o in orders:
                    rows.append({
                        "Lado": o.get("side"),
                        "Tipo": o.get("type"),
                        "Precio limit": float(o.get("price",0) or 0),
                        "Stop trigger": float(o.get("stopPrice",0) or 0),
                        "Cantidad": float(o.get("origQty",0) or 0),
                        "Ejecutada": float(o.get("executedQty",0) or 0),
                        "Estado": o.get("status"),
                        "Order List": o.get("orderListId"),
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        with st.expander("Últimas operaciones reales", expanded=False):
            try:
                trade_rows = ro_client.trades(symbol, 50)
                if trade_rows:
                    td = []
                    for t in trade_rows[-20:][::-1]:
                        tm = pd.to_datetime(int(t["time"]), unit="ms", utc=True).tz_convert("America/Bogota")
                        td.append({
                            "Fecha Colombia": tm.strftime("%d/%m/%Y %H:%M"),
                            "Lado": "COMPRA" if t.get("isBuyer") else "VENTA",
                            "Precio": float(t.get("price",0) or 0),
                            "Cantidad": float(t.get("qty",0) or 0),
                            "Total": float(t.get("quoteQty",0) or 0),
                            "Comisión": float(t.get("commission",0) or 0),
                            "Activo comisión": t.get("commissionAsset"),
                        })
                    st.dataframe(pd.DataFrame(td), hide_index=True, use_container_width=True)
                else:
                    st.write("No hay operaciones recientes para este par.")
            except Exception as trade_exc:
                st.warning(f"No pude consultar el historial de trades: {trade_exc}")

    except BinanceReadOnlyError as exc:
        st.error(f"No fue posible leer la cuenta Binance: {exc}")
        st.caption("Verifica la API Key, API Secret, permisos y configuración de restricciones.")
    except Exception as exc:
        st.error(f"Error al cargar la cuenta en modo lectura: {exc}")

st.divider()
st.header("FUTURES LAB V4.0")
st.caption(
    "Laboratorio histórico bajo demanda. Compara Spot LONG contra USDⓈ-M Futures LONG/SHORT "
    "en 1H, 2H y 4H. Incluye comisiones, slippage, funding histórico, división temporal 60/20/20, "
    "bootstrap y Monte Carlo. No ejecuta órdenes."
)

with st.expander("Configurar análisis V4.0", expanded=False):
    lv1,lv2,lv3 = st.columns(3)
    lab_symbol = lv1.selectbox(
        "Activo del laboratorio",
        list(CFG.symbols),
        index=list(CFG.symbols).index(symbol) if symbol in CFG.symbols else 0,
        key="v4_lab_symbol",
    )
    lab_years = lv2.select_slider(
        "Años de historia",
        options=[1,2,3,5],
        value=5,
        key="v4_lab_years",
    )
    lab_sims = lv3.selectbox(
        "Simulaciones bootstrap",
        [1000,2000,5000,10000],
        index=1,
        key="v4_lab_sims",
    )
    st.write(
        "El grid prueba: Stop ATR **1.0 / 1.25 / 1.5**, Take Profit **1.5R / 2R / 2.5R / 3R**, "
        "direcciones LONG y SHORT y temporalidades **1H / 2H / 4H**."
    )
    st.caption(
        "Con 5 años y 10.000 simulaciones el análisis puede tardar varios minutos. "
        "La tabla prioriza evidencia fuera de muestra; no significa que la primera fila vaya a ganar en el futuro."
    )

    if st.button("Ejecutar análisis estadístico V4.0", type="primary", use_container_width=True):
        with st.spinner("Descargando historia Spot/Futures y ejecutando backtests..."):
            try:
                st.session_state["v4_lab_results"] = cached_v4_lab(
                    lab_symbol, int(lab_years), int(lab_sims), float(risk_pct)
                )
                st.session_state["v4_lab_meta"] = {
                    "symbol": lab_symbol,
                    "years": int(lab_years),
                    "simulations": int(lab_sims),
                    "risk_pct": float(risk_pct),
                }
            except Exception as lab_exc:
                st.session_state.pop("v4_lab_results", None)
                st.error(f"El laboratorio no pudo completarse: {lab_exc}")

lab_results = st.session_state.get("v4_lab_results")
lab_meta = st.session_state.get("v4_lab_meta", {})

if isinstance(lab_results, pd.DataFrame) and not lab_results.empty:
    strong = int((lab_results["evidence"] == "FUERTE").sum())
    promising = int((lab_results["evidence"] == "PROMETEDORA").sum())
    insufficient = int((lab_results["evidence"] == "INSUFICIENTE").sum())

    la,lb,lc,ld = st.columns(4)
    la.metric("Configuraciones", f"{len(lab_results)}")
    lb.metric("Evidencia fuerte", f"{strong}")
    lc.metric("Prometedoras", f"{promising}")
    ld.metric("Muestra insuficiente", f"{insufficient}")

    if strong == 0:
        st.warning(
            "Ninguna configuración supera todavía el criterio FUERTE: ≥200 operaciones en TEST, "
            "IC95% de expectativa > 0, Profit Factor ≥1,20 y probabilidad bootstrap positiva ≥95%. "
            "Esto es información útil: no debemos aumentar riesgo solo por encontrar un backtest atractivo."
        )
    else:
        st.success(
            "Hay configuraciones que superan el filtro estadístico FUERTE. "
            "Deben seguir tratándose como evidencia histórica, no como garantía de beneficio futuro."
        )

    show = lab_results.head(30).copy()
    show["Configuración"] = (
        show["instrument"] + " " + show["direction"] + " " + show["timeframe"] +
        " · SL " + show["stop_atr"].map(lambda x:f"{x:.2f}ATR") +
        " · TP " + show["rr"].map(lambda x:f"{x:.1f}R")
    )
    table = pd.DataFrame({
        "Evidencia": show["evidence"],
        "Configuración": show["Configuración"],
        "Ops TEST": show["trades_test"],
        "Win TEST": show["win_test"].map(lambda x:f"{x*100:.1f}%"),
        "Exp. TEST": show["expectancy_test_r"].map(lambda x:f"{x:+.3f}R"),
        "IC95% bajo": show["ci_low"].map(lambda x:f"{x:+.3f}R"),
        "IC95% alto": show["ci_high"].map(lambda x:f"{x:+.3f}R"),
        "P(Exp>0)": show["prob_positive"].map(lambda x:f"{x*100:.1f}%"),
        "PF TEST": show["pf_test"].map(lambda x:"∞" if math.isinf(x) else f"{x:.2f}"),
        "R/año": show["r_per_year_test"].map(lambda x:f"{x:+.1f}"),
        "Retorno proxy/año": show["return_proxy_pct_year_at_risk"].map(lambda x:f"{x:+.1f}%"),
        "DD TEST": show["max_dd_test_r"].map(lambda x:f"{x:.1f}R"),
        "Costo fee": show["avg_fee_r"].map(lambda x:f"{x:.3f}R"),
        "Funding": show["avg_funding_r"].map(lambda x:f"{x:+.3f}R"),
    })
    st.dataframe(table, hide_index=True, use_container_width=True)

    st.caption(
        "Retorno proxy/año = R/año × riesgo configurado por operación. No incluye compounding y no es una proyección garantizada."
    )

    choices = []
    for idx,row in show.head(15).iterrows():
        choices.append((
            int(idx),
            f"{row['evidence']} · {row['instrument']} {row['direction']} {row['timeframe']} · "
            f"SL {row['stop_atr']:.2f}ATR · TP {row['rr']:.1f}R · Exp {row['expectancy_test_r']:+.3f}R"
        ))

    selected_label = st.selectbox(
        "Analizar eficiencia de apalancamiento de una configuración",
        [x[1] for x in choices],
        key="v4_selected_config",
    )
    selected_idx = next(x[0] for x in choices if x[1] == selected_label)
    selected = lab_results.loc[selected_idx]

    lev = leverage_table(selected, float(lab_meta.get("risk_pct", risk_pct)))
    lev_display = lev.copy()
    for col in ["Riesgo objetivo por trade","Nocional / capital","Margen aprox. / capital","Movimiento ~1/leverage"]:
        lev_display[col] = lev_display[col].map(lambda x:f"{x:.2f}%")
    st.dataframe(lev_display, hide_index=True, use_container_width=True)

    st.info(
        "En este cuadro el riesgo por operación se mantiene constante. Pasar de 1x a 2x/3x/5x "
        "reduce el margen requerido para controlar el mismo nocional; no multiplica automáticamente la expectativa en R. "
        "Usar el leverage para multiplicar el nocional sin respetar el Stop sí multiplica las pérdidas."
    )

    mc1,mc2,mc3,mc4 = st.columns(4)
    mc1.metric("MC mediana 100 trades", f"{selected['mc_median_100']:+.1f}R")
    mc2.metric("MC P5 100 trades", f"{selected['mc_p05_100']:+.1f}R")
    mc3.metric("Prob. 100 trades < 0", f"{selected['mc_prob_negative_100']*100:.1f}%")
    mc4.metric("Drawdown P95", f"{selected['mc_dd95_100']:.1f}R")

    with st.expander("Cómo interpreta V4.0 la evidencia", expanded=False):
        st.markdown("""
**INSUFICIENTE:** la muestra o la estabilidad estadística todavía no justifican confianza alta.

**PROMETEDORA:** al menos 75 operaciones TEST, expectativa positiva, PF ≥ 1,10 y bootstrap positivo ≥ 80%.

**FUERTE:** al menos 200 operaciones TEST, límite inferior del IC95% de expectativa > 0, PF ≥ 1,20 y bootstrap positivo ≥ 95%.

El conjunto de datos se separa cronológicamente **60% desarrollo / 20% validación / 20% TEST**. La clasificación utiliza principalmente el tramo TEST, no el resultado total.
        """)

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

with st.expander("Monitor V4.1 / alertas al iPhone", expanded=False):
    st.write("GitHub Actions despierta a los minutos 07, 22, 37 y 52 de cada hora para reducir el riesgo de retrasos del scheduler.")
    st.write("El análisis pesado se ejecuta una sola vez por cada nueva vela 4H cerrada; los intentos posteriores de la misma vela se omiten automáticamente.")
    st.write("Si aparece una señal nueva VIGILAR o SETUP VÁLIDO, el workflow se marca como alerta para que GitHub pueda notificarte.")
    st.write("Las operaciones siguen siendo manuales en Binance; V4.0 no contiene funciones para abrir Futures ni retirar fondos.")

st.caption(f"Fuente activa: {get_active_endpoint()} · V4.1 es solo análisis; no accede ni opera tu cuenta.")
