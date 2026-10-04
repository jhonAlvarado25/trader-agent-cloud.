from __future__ import annotations

from dataclasses import asdict
import json
import math
import uuid
import pandas as pd
import streamlit as st

from profile_v5 import TradingProfile, load_profile, profile_json, goal_summary
from risk_v5 import size_order
from market_v5 import quote, exchange_rules
from auto_decision_v5 import automatic_recommendation, revalidate_entry
from journal_v5 import journal_summary, validate_records, risk_stop
from binance_readonly import BinanceReadOnlyClient, balance_map, permission_is_read_only
from monitor_status import get_last_scheduled_run, next_monitor_run, format_local

st.set_page_config(page_title="Trader Agent Cloud V5", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.block-container{padding-top:1rem;max-width:1100px}
@media(max-width:700px){.block-container{padding-left:.65rem;padding-right:.65rem}h1{font-size:1.65rem!important}}
</style>
""", unsafe_allow_html=True)
st.title("Trader Agent Cloud V5")
st.caption("Capital editable · rentabilidad neta · validación independiente · Binance manual")
st.info("Meta: 15–20% EA. Es un objetivo anual, no una rentabilidad garantizada. Trading cripto puede perder capital y no equivale al riesgo de un CDT.")

try:
    base = load_profile()
except Exception as exc:
    st.error(f"Perfil de inversión inválido: {exc}")
    st.stop()

with st.expander("Cargar un perfil guardado", expanded=False):
    uploaded_profile = st.file_uploader("Perfil V5 JSON (sin credenciales)", type=["json"], key="profile_upload")
    if uploaded_profile and st.button("Aplicar perfil cargado"):
        try:
            imported = TradingProfile(**json.loads(uploaded_profile.getvalue()))
            st.session_state["profile_base"] = asdict(imported)
            for key in ("capital_cop", "available_cop", "risk_pct_ui", "cop_per_usdt", "fx_confirmed",
                        "committed_risk_cop", "max_position_ui", "mode_ui", "cdt_ui", "last_capital"):
                st.session_state.pop(key, None)
            st.rerun()
        except Exception as exc:
            st.error(f"No se pudo aplicar el perfil: {exc}")
if "profile_base" in st.session_state:
    base = TradingProfile(**st.session_state["profile_base"])

st.subheader("Tu capital y riesgo")
capital = st.number_input("Capital total de trading (COP)", min_value=10_000.0, value=float(base.capital_cop),
                          step=50_000.0, key="capital_cop")
previous_capital = st.session_state.get("last_capital", capital)
if previous_capital != capital and "available_cop" in st.session_state:
    old_available = st.session_state["available_cop"]
    st.session_state["available_cop"] = capital if old_available == previous_capital else min(old_available, capital)
st.session_state["last_capital"] = capital
a, b = st.columns(2)
available = a.number_input("Capital disponible (COP)", min_value=0.0, max_value=float(capital),
                           value=None if "available_cop" in st.session_state else float(min(base.available_cop, capital)), step=10_000.0, key="available_cop")
risk_pct = b.number_input("Riesgo por operación (% del capital total)", min_value=.05, max_value=1.0,
                          value=float(base.risk_pct*100), step=.05, key="risk_pct_ui")/100
fx = st.number_input("COP pagados por 1 USDT (tasa manual)", min_value=1000.0, max_value=10000.0,
                     value=float(base.cop_per_usdt), step=10.0, key="cop_per_usdt")
fx_confirmed = st.checkbox("Confirmo que esta tasa corresponde a mi conversión real COP/USDT",
                           value=base.fx_confirmed, key="fx_confirmed")
with st.expander("Disponibilidad, posiciones y modo", expanded=False):
    committed = st.number_input("Pérdida modelada ya comprometida en posiciones/órdenes (COP)",
                                 min_value=0.0, value=float(base.committed_risk_cop), step=1000.0, key="committed_risk_cop")
    max_fraction = st.number_input("Máximo nocional por operación (% del capital)", min_value=1.0, max_value=100.0,
                                   value=float(base.max_position_fraction*100), key="max_position_ui")/100
    mode = st.selectbox("Modo", ["PAPER", "PILOTO_MANUAL"], index=0 if base.mode == "PAPER" else 1, key="mode_ui")
    st.caption("PAPER es simulación. PILOTO_MANUAL exige evidencia FUERTE y tasa confirmada. Ningún modo abre órdenes.")
    cdt = st.number_input("Tasa CDT EA para comparar (%; 0 = sin dato confirmado)", min_value=0.0, max_value=100.0,
                          value=float((base.cdt_ea or 0)*100), step=.1, key="cdt_ui")/100
    st.caption("Introduce una cotización bancaria vigente del mismo plazo. No se supone una tasa CDT nacional.")
try:
    profile = base.updated(capital_cop=float(capital), available_cop=float(available), risk_pct=float(risk_pct),
                            cop_per_usdt=float(fx), fx_confirmed=fx_confirmed,
                            committed_risk_cop=float(committed), max_position_fraction=float(max_fraction),
                            mode=mode, cdt_ea=cdt or None)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

goals = goal_summary(profile)
m1, m2, m3 = st.columns(3)
m1.metric("Presupuesto de riesgo restante", f"COP {profile.risk_budget_cop:,.0f}")
m2.metric(f"Meta anual {profile.target_ea_min*100:.0f}% EA", f"COP {goals['annual_gain_min_cop']:,.0f}")
m3.metric(f"Meta anual {profile.target_ea_max*100:.0f}% EA", f"COP {goals['annual_gain_max_cop']:,.0f}")
st.caption(f"Equivalente compuesto mensual: {goals['monthly_rate_min']*100:.2f}%–{goals['monthly_rate_max']*100:.2f}%. No se fuerza una cantidad de operaciones para alcanzar la meta.")
if cdt:
    st.caption(f"Referencia CDT introducida: {cdt*100:.2f}% EA → COP {capital*cdt:,.0f} en un año, antes de impuestos. El riesgo no es equivalente.")
if not fx_confirmed:
    st.warning("La tasa COP/USDT todavía es una suposición. Los valores PAPER son provisionales; confirma la tasa antes del piloto.")

with st.expander("Guardar capital y usar el mismo perfil en Telegram", expanded=False):
    st.download_button("Descargar mi perfil V5", profile_json(profile), file_name="trading_profile.json", mime="application/json")
    st.markdown("[Editar el perfil del monitor en GitHub](https://github.com/jhonAlvarado25/trader-agent-cloud./edit/main/trading_profile.json)")
    st.write("Para cambiar también el capital de Telegram, reemplaza trading_profile.json en GitHub por el perfil descargado y confirma el cambio. El monitor lo leerá en su siguiente ciclo.")
    st.caption("Cambiar un campo en este panel NO actualiza automáticamente GitHub Actions. También puedes configurar TRADER_CAPITAL_COP y otras variables descritas en V5_GUIA.md. No incluyas claves API.")
    st.caption("El perfil del panel se mantiene durante la sesión. Descárgalo para conservarlo y volver a cargarlo tras reinicios.")

signals_tab, calculator_tab, journal_tab, account_tab = st.tabs(["Señales V5", "Valores Binance", "Bitácora", "Cuenta / monitor"])

def json_safe(value):
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value

with signals_tab:
    selected_symbol = st.selectbox("Activo para evaluar", ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT","AVAXUSDT","LTCUSDT"])
    st.caption("Analiza 1H/2H/4H con contexto 1D. Escoge la regla usando VALIDATION, no buscando la mejor cifra TEST.")
    if st.button("Analizar oportunidad V5", type="primary"):
        with st.spinner("Validando mercados nativos, costos y evidencia..."):
            try:
                pause = risk_stop(profile, st.session_state.get("journal_v5", []), kind="REAL_MANUAL" if profile.mode == "PILOTO_MANUAL" else "PAPER")
                if pause:
                    raise ValueError(pause)
                st.session_state["v5_recommendation"] = automatic_recommendation(selected_symbol, profile=profile)
            except Exception as exc:
                st.error(f"No fue posible validar: {type(exc).__name__}. No operar sin datos.")
    recommendation = st.session_state.get("v5_recommendation")
    if recommendation:
        st.write(recommendation["state"], "—", recommendation["reason"])
        if recommendation.get("data_errors"):
            st.warning("Datos bloqueados o incompletos: " + " · ".join(recommendation["data_errors"]))
        if recommendation.get("stats"):
            stats = recommendation["stats"]
            q1, q2, q3 = st.columns(3)
            q1.metric("TEST trades", stats["trades_test"])
            q2.metric("Expectativa neta TEST", f"{stats['expectancy_test_r']:+.3f}R")
            q3.metric("Evidencia", stats["evidence"])
            with st.expander("Validación, estabilidad y escenarios"):
                st.write("El bootstrap mide soporte histórico, NO la probabilidad de ganar la próxima operación.")
                st.dataframe(pd.DataFrame([{"Criterio": k, "Cumple": "Sí" if v else "No"} for k,v in stats["checks"].items()]), hide_index=True)
                st.dataframe(pd.DataFrame(stats["walk_forward"]), hide_index=True)
                st.write(f"IC95% por bloques: {stats['ci_low']:+.3f}R / {stats['ci_high']:+.3f}R")
                st.write(f"Expectativa con costos estresados: {stats['stress_expectancy_r']:+.3f}R")
                st.write(f"MC drawdown P95 de 100 trades: {stats['mc_dd95_100']:.2f}R (no límite garantizado).")
        if recommendation["state"] == "OPERACIÓN CANDIDATA":
            st.warning("Señal PAPER: no es una orden real." if recommendation["mode"] == "PAPER" else "Piloto manual: verifica saldo, posiciones, protección y liquidación en Binance.")
            st.write(f"{recommendation['symbol']} · {recommendation['instrument']} {recommendation['direction']} · vence {format_local(recommendation['expires_at'])}")
            if st.button("Revalidar y cargar niveles en el calculador"):
                try:
                    guidance = revalidate_entry(recommendation, profile)
                    st.write(guidance["action"], guidance["reason"])
                    if guidance.get("order"):
                        order = guidance["order"]
                        for name, val in {"calc_symbol": order["symbol"], "calc_instrument": order["instrument"],
                                          "calc_direction": order["direction"], "calc_entry": order["entry"],
                                          "calc_stop": order["stop"], "calc_tp": order["take_profit"]}.items():
                            st.session_state[name] = val
                        st.session_state["calc_context"] = {
                            "symbol": order["symbol"], "instrument": order["instrument"],
                            "rules": order["rules"], "snapshot": quote(order["symbol"], order["instrument"]),
                        }
                        st.session_state["calc_signal_expires"] = order["expires_at"]
                        st.success("Niveles cargados. Abre Valores Binance; se calcularán con TU capital actual.")
                except Exception as exc:
                    st.error(f"Entrada no validada: {exc}")
            st.download_button("Descargar señal V5 JSON", json.dumps(json_safe(recommendation), ensure_ascii=False, indent=2, allow_nan=False),
                                file_name="senal_v5.json", mime="application/json")

with calculator_tab:
    st.subheader("Valores para introducir manualmente en Binance")
    st.caption("El calculador dimensiona niveles; por sí solo no recomienda una operación. Las cantidades cambian con tu capital y riesgo.")
    uploaded_signal = st.file_uploader("Cargar señal V5 JSON descargada del panel", type=["json"], key="signal_upload")
    if uploaded_signal and st.button("Cargar y revalidar señal JSON"):
        try:
            incoming = json.loads(uploaded_signal.getvalue())
            guidance = revalidate_entry(incoming, profile)
            if not guidance.get("order"):
                st.error(guidance["reason"])
            else:
                order = guidance["order"]
                for name, value in {"calc_symbol": order["symbol"], "calc_instrument": order["instrument"], "calc_direction": order["direction"],
                                    "calc_entry": order["entry"], "calc_stop": order["stop"], "calc_tp": order["take_profit"]}.items():
                    st.session_state[name] = value
                st.session_state["calc_signal_expires"] = incoming["expires_at"]
        except Exception as exc:
            st.error(f"Señal no validada: {type(exc).__name__}")
    cc1, cc2 = st.columns(2)
    calc_symbol = cc1.selectbox("Par", ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT","AVAXUSDT","LTCUSDT"], key="calc_symbol")
    instrument = cc2.selectbox("Mercado", ["SPOT", "FUTURES"], key="calc_instrument")
    direction = st.selectbox("Dirección", ["LONG", "SHORT"], key="calc_direction")
    entry = st.number_input("Precio de entrada LIMIT (USDT)", min_value=0.0, value=0.0, format="%.8f", key="calc_entry")
    stop = st.number_input("Stop / trigger (USDT)", min_value=0.0, value=0.0, format="%.8f", key="calc_stop")
    tp = st.number_input("Take Profit (USDT)", min_value=0.0, value=0.0, format="%.8f", key="calc_tp")
    if st.button("Validar cotización y calcular", type="primary"):
        try:
            st.session_state["calc_context"] = {
                "symbol": calc_symbol, "instrument": instrument,
                "snapshot": quote(calc_symbol, instrument), "rules": exchange_rules(calc_symbol, instrument),
            }
        except Exception as exc:
            st.session_state.pop("calc_context", None)
            st.error(f"Sin datos nativos fiables: {type(exc).__name__}. No se mostrarán valores ejecutables.")
    context = st.session_state.get("calc_context")
    if context and context["symbol"] == calc_symbol and context["instrument"] == instrument and min(entry, stop, tp) > 0:
        try:
            from market_v5 import funding_reserve
            pause = risk_stop(profile, st.session_state.get("journal_v5", []), kind="REAL_MANUAL" if profile.mode == "PILOTO_MANUAL" else "PAPER")
            if pause:
                raise ValueError(pause)
            snapshot = context["snapshot"]
            age = (pd.Timestamp.now(tz="UTC")-pd.Timestamp(snapshot["quoted_at"])).total_seconds()
            if age > 60:
                raise ValueError("Cotización vencida: pulsa Validar cotización y calcular")
            expires = st.session_state.get("calc_signal_expires")
            if expires and pd.Timestamp.now(tz="UTC") > pd.Timestamp(expires):
                raise ValueError("La señal cargada venció; solicita una nueva recomendación")
            sized = size_order(profile, entry, stop, tp, direction, instrument, context["rules"],
                                funding_reserve(snapshot), snapshot["spread_fraction"])
            rows = [("Par", calc_symbol), ("Mercado / dirección", f"{instrument} / {direction}"),
                    ("Precio LIMIT USDT", sized["entry_text"]), ("Cantidad del activo", sized["qty_text"]),
                    ("Nocional USDT", f"{sized['position_usdt']:.2f}"),
                    ("Stop / trigger", sized["stop_text"]), ("Take Profit", sized["tp_text"]),
                    ("Pérdida neta modelada COP", f"{sized['net_loss_cop']:,.0f}"),
                    ("Ganancia neta modelada al TP COP", f"{sized['net_gain_cop']:,.0f}"),
                    ("R/R neto modelado", f"1:{sized['rr_net']:.2f}")]
            if instrument == "FUTURES":
                rows += [("Margen", "ISOLATED"), ("Apalancamiento", f"{sized['leverage']}x"),
                         ("Margen aprox. USDT", f"{sized['margin_usdt']:.2f}"),
                         ("Reduce Only", "NO en apertura; SÍ en cierres (modo unidireccional)")]
            else:
                rows += [("Limit SL", sized["limit_sl_text"]), ("Cantidad de protección conservadora", sized["exit_qty_text"])]
            st.dataframe(pd.DataFrame(rows, columns=["Campo Binance", "Valor"]), hide_index=True, width="stretch")
            st.code("\n".join(f"{k}: {v}" for k,v in rows), language="text")
            st.caption(f"Fuente: {snapshot['source']} · cotización {format_local(snapshot['quoted_at'])}. Tamaño recalculado para COP {capital:,.0f}.")
            st.warning("No se envía ninguna orden. Una Stop-Limit puede no ejecutarse; el Stop y los costos modelados no garantizan la pérdida máxima. Para Futures comprueba trigger del contrato, liquidación y Reduce Only en Binance.")
            if instrument == "FUTURES":
                st.caption("La guía de Reduce Only supone modo unidireccional. En Hedge Mode se requiere Position Side; no copies esos flags sin adaptar y comprobar las órdenes de cierre.")
        except ValueError as exc:
            st.warning(str(exc))

with journal_tab:
    st.subheader("Resultados reales declarados / PAPER")
    st.session_state.setdefault("journal_v5", [])
    journal_upload = st.file_uploader("Restaurar bitácora JSON privada", type=["json"], key="journal_upload")
    if journal_upload and st.button("Restaurar bitácora"):
        try:
            st.session_state["journal_v5"] = validate_records(json.loads(journal_upload.getvalue()))
        except Exception as exc:
            st.error(f"Bitácora no válida: {exc}")
    with st.form("journal_form"):
        kind = st.selectbox("Registro", ["REAL_MANUAL", "PAPER"])
        journal_symbol = st.text_input("Par operado", "BTCUSDT")
        closed_at = st.text_input("Fecha/hora de cierre con zona", pd.Timestamp.now(tz="UTC").isoformat())
        net_usdt = st.number_input("Resultado NETO USDT, después de comisiones y funding (negativo si pérdida)", value=0.0)
        journal_fx = st.number_input("Tasa COP/USDT usada para valorar el cierre", min_value=1.0, value=float(fx))
        risk_cop = st.number_input("Riesgo inicialmente modelado COP", min_value=0.0, value=float(capital*risk_pct))
        if st.form_submit_button("Registrar cierre"):
            try:
                stamp = pd.Timestamp(closed_at)
                if stamp.tzinfo is None:
                    raise ValueError("Incluye zona horaria, por ejemplo -05:00 o +00:00")
                record = {"id": str(uuid.uuid4()), "kind": kind, "status": "CLOSED", "symbol": journal_symbol.upper(),
                          "closed_at": stamp.isoformat(), "net_pnl_usdt": float(net_usdt),
                          "net_pnl_cop": float(net_usdt*journal_fx), "risk_cop": float(risk_cop),
                          "capital_cop": float(capital)}
                validate_records([record])
                st.session_state["journal_v5"].append(record)
            except Exception as exc:
                st.error(str(exc))
    records = st.session_state["journal_v5"]
    for kind, title in (("REAL_MANUAL", "Real declarado manualmente"), ("PAPER", "Simulación PAPER")):
        js = journal_summary(records, kind)
        st.write(title, f"— {js['n']} cierres · PnL neto COP {js['net_cop']:,.0f} · acierto {js['win_rate']*100:.1f}%")
    if records:
        st.dataframe(pd.DataFrame(records), hide_index=True, width="stretch")
    st.download_button("Guardar mi bitácora privada", json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False),
                        file_name="bitacora_trader_v5.json", mime="application/json")
    st.caption("Bitácora de esta sesión: exporta para conservarla. No se publica en GitHub y no confirma fills mediante API. Las sombras cloud se guardan aparte; no equivalen a operaciones reales.")
    st.caption("PnL de operaciones no equivale al retorno total de la cuenta en COP: faltan variación USDT/COP sobre el capital, aportes/retiros, impuestos y costos de entrada/salida. No se anualiza una muestra corta.")

with account_tab:
    st.subheader("Binance — SOLO LECTURA")
    if st.button("Consultar saldos Spot configurados"):
        try:
            client = BinanceReadOnlyClient(str(st.secrets["BINANCE_API_KEY"]), str(st.secrets["BINANCE_API_SECRET"]))
            permissions = client.permissions()
            if not permission_is_read_only(permissions):
                raise ValueError("La API no es estrictamente de lectura; no se usará")
            st.dataframe(pd.DataFrame.from_dict(balance_map(client.account()), orient="index"), width="stretch")
            st.caption("Los saldos Spot no equivalen al capital total ni muestran exposición Futures. No se cambió el capital manual.")
        except Exception as exc:
            st.warning(f"Cuenta no consultada: {type(exc).__name__}. Mantén deshabilitados trading y retiros; no compartas secretos.")
    if st.button("Consultar posiciones y eventos PnL Futures (solo lectura)"):
        try:
            client = BinanceReadOnlyClient(str(st.secrets["BINANCE_API_KEY"]), str(st.secrets["BINANCE_API_SECRET"]))
            if not permission_is_read_only(client.permissions()):
                raise ValueError("La API no es estrictamente de lectura")
            positions = [p for p in client.futures_positions() if float(p.get("positionAmt", 0)) != 0]
            st.write("Posiciones Futures abiertas")
            st.dataframe(pd.DataFrame(positions), width="stretch")
            income = client.futures_income()
            df = pd.DataFrame(income)
            if not df.empty:
                selected = df[(df.asset == "USDT") & df.incomeType.isin(["REALIZED_PNL", "COMMISSION", "FUNDING_FEE"])]
                net = pd.to_numeric(selected.income, errors="raise").sum()
                st.metric("PnL + comisiones + funding USDT (30 días)", f"{net:.4f}")
                st.dataframe(selected, width="stretch")
            st.caption("Eventos de cuenta, no operaciones reconstruidas. Revisa posiciones y actualiza el riesgo comprometido; no se infiere un Stop ni se modifica el capital.")
        except Exception as exc:
            st.warning(f"Futures no consultado: {type(exc).__name__}. Si la API de lectura no lo permite, usa el reporte de Binance; no habilites trading para resolverlo.")
    st.write("Próximo ciclo programado:", format_local(next_monitor_run()))
    if st.button("Consultar estado del monitor cloud"):
        run = get_last_scheduled_run()
        if run:
            st.write(run.get("conclusion") or run.get("status"))
            st.markdown(f"[Abrir ejecución en GitHub]({run['html_url']})")
        else:
            st.info("No fue posible consultar el monitor. El horario no garantiza ejecución puntual.")
    st.markdown("[Guía V5 y configuración de Telegram](https://github.com/jhonAlvarado25/trader-agent-cloud./blob/main/V5_GUIA.md)")
    st.caption("V5 no compra, vende, cancela órdenes, mueve dinero ni cambia leverage. Recomendaciones y cálculo de cantidades solamente.")
