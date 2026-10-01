# Trader Agent V4.0 — Futures Lab

V4.0 añade un laboratorio cuantitativo bajo demanda. No abre posiciones ni cambia permisos de Binance.

## Qué compara

Para el activo seleccionado (BTC, ETH, SOL o BNB):

- Spot LONG.
- USDⓈ-M Futures LONG.
- USDⓈ-M Futures SHORT.
- Temporalidades: 1H, 2H y 4H.
- Stop: 1.00, 1.25 y 1.50 ATR.
- Objetivo: 1.5R, 2R, 2.5R y 3R.
- Historia seleccionable: 1, 2, 3 o 5 años.
- Bootstrap: 1.000, 2.000, 5.000 o 10.000 simulaciones.

## Datos

- Spot: velas públicas reales de Binance Spot.
- Futures: velas públicas reales USDⓈ-M de Binance Futures.
- Funding: historial real de funding USDⓈ-M.
- La señal se detecta en cierre y la entrada histórica se modela al open de la vela siguiente.
- Si Stop y TP se tocan dentro de una misma vela, V4 contabiliza Stop primero (supuesto conservador).

## Costos

Spot:
- fee por lado: 0,10 %;
- slippage por lado: 0,02 %.

Futures:
- fee por lado modelada: 0,05 %;
- slippage por lado: 0,02 %;
- funding histórico aplicado al período de cada operación.

Los costos son supuestos del modelo y deben actualizarse si cambian las condiciones reales de la cuenta.

## Separación temporal

V4 divide cronológicamente los datos:

- 60 % desarrollo;
- 20 % validación;
- 20 % TEST final.

La clasificación de evidencia utiliza principalmente TEST.

## Clasificación estadística

### INSUFICIENTE

No alcanza los mínimos de muestra/estabilidad.

### PROMETEDORA

Requiere:

- al menos 75 operaciones TEST;
- expectativa TEST > 0;
- Profit Factor TEST >= 1,10;
- probabilidad bootstrap de expectativa positiva >= 80 %.

### FUERTE

Requiere:

- al menos 200 operaciones TEST;
- límite inferior del IC95 % de expectativa > 0;
- Profit Factor TEST >= 1,20;
- probabilidad bootstrap de expectativa positiva >= 95 %.

La palabra FUERTE se refiere únicamente a evidencia histórica del modelo; no garantiza beneficios futuros.

## Métricas

- Win rate TEST.
- Expectativa neta en R.
- IC95 % bootstrap.
- P(expectativa > 0).
- Profit Factor.
- R/año.
- Retorno proxy anual al riesgo configurado.
- Drawdown TEST.
- Fee medio en R.
- Funding medio en R.
- Monte Carlo de 100 operaciones.
- Drawdown P95 de Monte Carlo.

## Apalancamiento

V4 muestra 1x, 2x, 3x y 5x como escenarios de eficiencia de margen.

El riesgo por operación se mantiene constante. Por ello el leverage no multiplica automáticamente R: reduce el margen necesario para controlar el mismo nocional.

La columna "Movimiento ~1/leverage" es solo una referencia de orden de magnitud y NO representa el precio real de liquidación. Binance calcula liquidación con margen de mantenimiento, tamaño de posición, balance, fees y otras variables.

## Uso recomendado

1. Ejecutar inicialmente 5 años con 2.000 bootstrap.
2. Revisar si existe evidencia PROMETEDORA/FUERTE.
3. Si aparece, repetir con 10.000 bootstrap.
4. Comparar parámetros vecinos: una ventaja robusta debería sobrevivir cambios pequeños en Stop/TP/timeframe.
5. No seleccionar una configuración únicamente porque tenga el mayor R/año.
6. Validar después con paper trading / capital mínimo antes de aumentar riesgo.

## Seguridad

V4.0 sigue siendo analítico:

- no contiene endpoints para abrir Futures;
- no cambia leverage;
- no transfiere fondos;
- no retira fondos;
- la integración de cuenta continúa en modo solo lectura.
