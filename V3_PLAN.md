# Trader Agent V3 — Plan de alertas

La V3 mantiene el análisis de V2 y añade un monitor programado.

## Objetivo

- Revisar BTCUSDT, ETHUSDT, SOLUSDT y BNBUSDT después de cada cierre 4H.
- No generar alertas cuando el estado sea NO OPERAR.
- Avisar cuando aparezca VIGILAR o SETUP VÁLIDO.
- Evitar alertas duplicadas para la misma vela y setup.
- Mantener el sistema sin permisos de trading ni retiros.

## Flujo

Binance público → motor cuantitativo → quality gate → monitor → notificación → revisión manual en el dashboard.

La ejecución de operaciones seguirá siendo manual en Binance.
