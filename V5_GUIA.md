# Trader Agent Cloud V5

## Objetivo y configuración inicial

Buscar rentabilidad **neta de costos**, sin prometer ganancias ni forzar operaciones.
Capital inicial editable: **COP 1.000.000**. Riesgo inicial: **0,5% = COP 5.000**.
Meta de comparación: **15–20% efectivo anual** (COP 150.000–200.000 en un año sobre ese capital).
Equivalente mensual compuesto: aproximadamente 1,17–1,53%, no una cuota mensual garantizada.

Modo inicial: **PAPER**. Cambiar a PILOTO_MANUAL no abre ninguna orden; exige evidencia
FUERTE, tasa COP/USDT confirmada y revisión manual. El objetivo EA jamás aumenta el
riesgo, el leverage ni el número de señales. El trading cripto no equivale a un CDT
en riesgo, protección del capital, liquidez o tratamiento tributario.

No se declara una tasa bancaria actual. El campo CDT es una cotización EA manual,
para el mismo plazo y antes de impuestos. Comparar realmente requiere también
costos de compra/venta de USDT, variación COP/USDT, impuestos y aportes/retiros.

## Uso desde el iPhone

1. Abre tu aplicación Streamlit habitual y verifica el título **Trader Agent Cloud V5**.
2. Arriba, cambia **Capital total de trading (COP)**. No es el nocional ni el margen.
3. Actualiza capital libre, riesgo ya comprometido y tasa realmente pagada por USDT.
   La tasa inicial 3350 es una suposición heredada, NO una cotización en vivo.
4. Usa **Señales V5 → Analizar oportunidad V5** para analizar un activo.
5. Si existe una candidata, pulsa **Revalidar y cargar niveles en el calculador**.
6. En **Valores Binance**, se mostrarán entrada LIMIT, cantidad, nocional, SL, TP,
   costos, pérdida/ganancia modeladas, R/R neto y, en Futures, margen ISOLATED y leverage.
7. Si cambias el capital, el tamaño se recalcula con los mismos niveles y cotización
   vigente. Una cotización de más de 60 segundos exige volver a validar; nunca se
   mantiene como ejecutable una cantidad antigua por comodidad.
8. Introduce las órdenes tú mismo en Binance SOLO después de revisar modo, saldo,
   posiciones, restricciones, protección y liquidación. La cantidad es del activo,
   no COP ni margen. Para Futures en modo unidireccional, apertura Reduce Only = NO;
   cierres = SÍ. En Hedge Mode se requiere Position Side y los flags de cierre no
   son iguales: revisa la configuración en Binance antes de copiar valores.
9. Registra cierres netos en **Bitácora** y descarga su JSON para conservarlos.

Un SHORT Spot queda bloqueado. Se aplican PRICE_FILTER, LOT_SIZE y mínimo nocional
del mercado, no decimales fijos. Si el mínimo Binance excede el tamaño permitido,
se espera: no se aumenta el riesgo para completar la orden. El mínimo puede impedir
ciertas operaciones, especialmente con capital pequeño y riesgo reducido.

Las verificaciones implementadas no equivalen a un chequeo completo de todas las
restricciones del broker (por ejemplo filtros porcentuales/precio de referencia,
límites por cuenta o reglas actualizadas al introducir la orden). Binance debe
aceptar y confirmar las órdenes; el calculador no envía ni valida una orden privada.

## Capital del panel versus Telegram

Los campos del panel se guardan en la sesión de ese navegador. **No modifican por sí
solos el proceso independiente de GitHub Actions.** Para que Telegram use lo mismo:

1. En el panel abre **Guardar capital y usar el mismo perfil en Telegram**.
2. Descarga `trading_profile.json`.
3. Reemplaza ese archivo en la raíz del repositorio de GitHub y confirma el cambio.
4. El monitor lo leerá en su próximo ciclo. El despliegue Streamlit que sigue main
   también recogerá ese archivo; reabre el panel para cargar el perfil actualizado.

Alternativamente, usa Repository variables en GitHub Actions:

| Variable | Unidad / ejemplo |
|---|---|
| TRADER_CAPITAL_COP | 1000000 |
| TRADER_AVAILABLE_COP | Capital libre COP; si solo cambia el capital, se iguala al nuevo capital |
| TRADER_RISK_PCT | 0.005 significa 0,5%, NO 0.5 |
| TRADER_COP_PER_USDT | Tasa manual realmente usada |
| TRADER_COMMITTED_RISK_COP | Pérdida modelada ya comprometida |
| TRADER_FX_CONFIRMED | true o false |
| TRADER_MODE | PAPER o PILOTO_MANUAL |

Las variables CLOUD prevalecen sobre el JSON. Por eso revisa el capital que aparece
en cada mensaje Telegram. Si hay diferencias, recalcula en el panel con tu capital
actual y no copies una cantidad de otra configuración. El JSON del repo es público
si tu repo es público; no incluyas bitácoras privadas, secretos ni saldos de cuenta.

## Validación V5

- Mercados nativos separados. FUTURES no utiliza un proxy Spot ni funding cero
  cuando no hay datos. HTTP 451 o datos incompletos bloquean ese mercado; no se
  intenta eludir restricciones geográficas ni habilitar permisos de trading.
- Señales en velas cerradas 1H/2H/4H, con contexto 1D cerrado antes de la señal.
- Pullback estricto, balanceado y breakout; stop 1,25 ATR/estructura y objetivo base
  2R bruto. No se han incorporado automáticamente 3R, trailing o señales sociales.
- El histórico de tres años se divide cronológicamente 60/20/20; operaciones que
  cruzan límites se purgan. Solo TRAIN/VALIDATION escogen la regla. Se comprueba TEST
  de una sola ganadora por activo; si falla, no se prueba la siguiente por su TEST.
- FUERTE exige 75 trades TEST, expectativa >0,05R, PF >=1,20, IC95% por bloques
  positivo, soporte bootstrap >=95%, expectativa positiva con costos estresados,
  3/4 ventanas OOS positivas y drawdown MC P95 compatible con el límite configurado.
- Walk-forward de reglas fijas con entrenamiento expansivo y ventanas OOS sucesivas.
  No reentrena un modelo ni optimiza parámetros en cada fold. El bootstrap preserva
  pequeños bloques, pero no elimina todos los sesgos, dependencia ni cambios de régimen.
- **EN OBSERVACIÓN** solo es apto para PAPER. FUERTE es una clasificación histórica,
  no probabilidad de ganar ni demostración de rentabilidad futura.
- La selección repetida y múltiples activos pueden seguir produciendo sobreajuste.
  Hace falta evaluación forward estable: una muestra inicial de 50–100 oportunidades
  ayuda a diagnosticar, pero no garantiza ni prueba por sí sola una ventaja.

## Costos y ejecución

Las comisiones y slippage del perfil son **supuestos editables**, no las tarifas
privadas confirmadas de tu cuenta. La cuenta de lectura puede mostrar eventos reales,
pero no se presume que esos supuestos coincidan. Ajusta las comisiones a tu categoría.
Se modelan costos sobre nocional de entrada/salida, spread actual y funding histórico
Futures con mark price. El tamaño reserva un escenario de funding según tasa e
intervalo actuales durante 72 horas; NO acota tasas futuras ni convierte funding en
un valor garantizado. Los costos pueden cambiar y el Stop puede ejecutarse peor.

La simulación entra en el open siguiente a la señal y aplica stop primero cuando SL
y TP se tocan en la misma vela. Incluye gaps adversos y un buffer Stop-Limit Spot.
Todavía modela ejecución con velas y supuestos, no colas reales del libro ni fills
parciales. La Stop-Limit puede no ejecutarse; no debe interpretarse COP 5.000 como
una pérdida máxima garantizada. En Futures el backtest utiliza precio del contrato;
el trigger elegido en Binance debe ser consistente y la liquidación real se verifica
en Binance, no con la aproximación 1/leverage.

## Monitor, controles y bitácoras

Se conserva el workflow `auto-recommend-v4.yml` para no romper su identificación,
pero ahora ejecuta `monitor_v5.py` y se llama **Trader Agent V5 Unified Monitor**.
Horario aproximado: minutos 07, 22, 37 y 52; GitHub puede retrasar u omitir ejecuciones.

La entrega se confirma solo cuando Telegram devuelve éxito. Se guarda el ID y su
registro sombra juntos, después del envío. Un fallo deja la señal reintentable.
No se garantiza exactamente una entrega ante cortes entre envío y checkpoint;
mensajes con el mismo ID no representan operaciones adicionales.

Las sombras PAPER llevan PENDING/OPEN/CLOSED/EXPIRED, vencimiento de alerta de diez
minutos y límite de permanencia de 72 horas, con seguimiento por velas de un minuto.
No confirman fills reales. Se excluye el minuto parcialmente transcurrido de la
alerta por ambigüedad; gaps, límites no ejecutados y funding intrabar siguen siendo
fuentes de diferencia. No utilizar resultados sombra como resultados reales.

Todos los activos comparten un mismo bucket de riesgo, hasta 1% y máximo dos sombras
activas; se evita repetir un activo. También se reservan nocional Spot o margen Futures
y costos de las sombras abiertas antes de dimensionar otra operación, sin reutilizar
el mismo capital disponible. El límite no usa una matriz de correlaciones.
Pausas: pérdida diaria 1,5%, semanal 3% y drawdown 5% en PAPER comparable con el mismo
capital; el panel también puede pausar con cierres REAL_MANUAL declarados. El monitor
no conoce automáticamente tu pérdida real, órdenes pendientes ni Stops de Binance:
debes actualizar riesgo comprometido y revisar posiciones antes de cada piloto.

El estado cloud utiliza cache GitHub y checkpoints en artifacts por 30 días. El cache
puede ser desalojado: no es una base de datos duradera ni una bitácora contable. Descarga
checkpoints importantes. La bitácora del panel es privada por sesión y exportable;
no se comparte automáticamente con cloud y se pierde al reiniciar si no la exportas.

**Cuenta / monitor** permite lecturas Spot y, si la API de lectura lo admite, posiciones
y eventos de PnL/comisiones/funding Futures. No reconstruye automáticamente operaciones
por posición ni enlaza fills con cada señal. Ante rechazo, usa reportes de Binance;
NO actives permisos de trading para resolver una lectura. No publiques secretos.

## Pruebas y alcance

Ejecutar `python -m unittest discover -s tests -v` y `python -m compileall -q .`.
Hay pruebas de capital editable/recalculo, tamaño/fees/riesgo, mínimos Binance,
particiones purgadas, no seleccionar sobre TEST, datos nativos sin proxy, expiración,
delivery confirmado, bitácoras separadas y pausas. Usan datos sintéticos y mocks:
**no prueban rentabilidad**, conectividad con tu cuenta ni entrega Telegram real.

Ningún módulo V5 coloca, cancela o modifica órdenes ni transfiere o retira dinero.
Permanecen archivos V3/V4 por compatibilidad/historial; solo app.py y el workflow
principal usan V5. El objetivo no convierte al agente en un instrumento de renta fija.
