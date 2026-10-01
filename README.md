# Trader Agent Cloud V2

V2 cuantitativa para análisis Spot. No ejecuta órdenes ni accede a una cuenta Binance.

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
