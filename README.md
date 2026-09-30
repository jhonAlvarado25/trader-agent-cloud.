# Trader Agent Cloud V1

Panel móvil/cloud para BTC/USDT en temporalidad 4H.

## Estado del proyecto

**V1 = solo lectura.**

- No usa API keys.
- No accede a tu cuenta Binance.
- No ejecuta órdenes.
- No usa Futures, Margin ni apalancamiento.
- Consulta datos públicos de Binance.

## Funciones

- Precio BTC/USDT actualizado.
- EMA20 / EMA50 / EMA200.
- RSI(14).
- ATR(14).
- Volumen relativo.
- Stop técnico basado en ATR + swing.
- Take Profit objetivo 2R.
- Tamaño de posición en COP / USDT / BTC.
- Backtest histórico.
- Win rate bruto y ajustado.
- Break-even.
- Expectativa matemática en R.
- Profit Factor.
- Drawdown.
- Monte Carlo.
- Estados: NO OPERAR / VIGILAR / SETUP VÁLIDO.

## Despliegue

Consulta `DEPLOY_IPHONE.md`.

## Nota metodológica

Un `SETUP VÁLIDO` significa que las reglas predefinidas y los filtros estadísticos se cumplen. No representa una garantía ni una predicción cierta de rentabilidad. La estrategia debe someterse posteriormente a validación fuera de muestra / walk-forward antes de aumentar el capital.
