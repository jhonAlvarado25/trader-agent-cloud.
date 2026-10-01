# Trader Agent Cloud V3

V3 cuantitativa para análisis Spot con monitor programado y alertas para revisión. No ejecuta órdenes ni accede a una cuenta Binance.

## Cambios principales frente a V1

- Regla de régimen en **1D**.
- Entrada en **4H**.
- Dos estrategias separadas:
  - `PULLBACK`
  - `BREAKOUT_RETEST`
- Costos de trading y slippage incluidos en el backtest.
- Entrada histórica al **open de la vela siguiente** para reducir look-ahead.
- Operaciones no solapadas por estrategia.
- Probabilidad ajustada Beta(5,5).
- Break-even calculado con R ganadora/perdedora observada.
- Edge estadístico en puntos porcentuales.
- Walk-forward temporal en 4 bloques.
- Monte Carlo empírico usando los R históricos.
- Escáner: BTC, ETH, SOL y BNB.
- Límite de posición al 35 % del capital.
- Estados: `NO OPERAR`, `VIGILAR`, `SETUP VÁLIDO`.

## Quality Gate

Para declarar `SETUP VÁLIDO`, la estrategia que está activa debe cumplir:

- >= 40 operaciones históricas totales.
- >= 15 operaciones en la zona fuera de muestra (OOS).
- Expectativa OOS >= +0,05R.
- Profit Factor OOS >= 1,10.
- Edge OOS > 0 puntos porcentuales.

Los parámetros son deliberadamente conservadores y no garantizan resultados futuros.

## Metodología

### Régimen diario
Se considera favorable cuando:
- cierre 1D > EMA200,
- EMA50 > EMA200,
- pendiente EMA50 de 5 días > 0.

### Pullback
Busca tendencia 4H alcista y retroceso hacia EMA20, con RSI y volumen controlados.

### Breakout + Retest
Detecta ruptura de resistencia de 20 velas y exige un retesteo dentro de las siguientes 3 velas.

### Backtest
La señal se detecta al cierre de la vela. La entrada histórica se hace al open de la vela siguiente.
Si Stop y TP ocurren en la misma vela, se contabiliza Stop primero como supuesto conservador.

### Costos
Por defecto:
- fee: 0,10 % por lado,
- slippage estimado: 0,02 % por lado.

## Despliegue

Reemplaza los archivos de tu repositorio V1 por los de este proyecto o crea un repositorio nuevo.
El entrypoint sigue siendo `app.py`.

Streamlit Community Cloud detectará el commit y volverá a desplegar.


## V3 — Monitor automático

La V3 añade un monitor independiente del dashboard:

- Ejecuta el análisis aproximadamente 10 minutos después de cada cierre 4H.
- Revisa BTCUSDT, ETHUSDT, SOLUSDT y BNBUSDT.
- Solo considera **VIGILAR** y **SETUP VÁLIDO** como estados de alerta.
- Evita duplicados por activo + estado + setup + vela.
- Publica un resumen del run con métricas OOS y niveles de referencia.
- Si aparece una señal nueva, el workflow termina en alerta para activar notificaciones de GitHub.
- La ejecución de órdenes sigue siendo manual.

Consulta `CONFIGURAR_ALERTAS_V3.md` para configurar las notificaciones en iPhone.


## V3.1 — Visibilidad y reintentos

V3.1 corrige dos puntos observados en operación real:

- El dashboard ahora muestra la última vela 4H cerrada, el próximo cierre y el estado del último monitor automático.
- Incluso en **NO OPERAR** muestra RSI, ATR, volumen relativo y el avance de condiciones para Pullback y Breakout/Retest.
- El scheduler usa varios intentos por hora para reducir el impacto de retrasos de GitHub Actions.
- El análisis pesado se ejecuta una sola vez por cada vela 4H y se omiten los reintentos redundantes.
- Si un análisis presenta errores, la vela no se marca como completada para permitir un nuevo intento.


## V3.2 — Orden Binance completa

El dashboard incorpora un bloque **ORDEN BINANCE** cuando existe un setup activo.

Muestra todos los datos operativos necesarios para una entrada Spot y su protección OCO:

- Par y mercado.
- Precio de entrada de referencia.
- Total USDT.
- Cantidad aproximada del activo.
- Limit TP.
- Stop / Trigger SL.
- **Limit SL**.
- Monto aproximado para la OCO.

El Limit SL se calcula por defecto 0,12 % por debajo del Stop/Trigger SL. Después de una compra, conviene usar el 100 % del saldo realmente disponible de esa operación al crear la OCO, porque la comisión puede reducir ligeramente la cantidad del activo.
