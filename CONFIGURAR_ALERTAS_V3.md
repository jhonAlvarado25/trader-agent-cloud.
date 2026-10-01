# Configurar alertas V3 en iPhone

La V3 revisa BTCUSDT, ETHUSDT, SOLUSDT y BNBUSDT después de cada cierre de vela 4H.

No genera alerta cuando el estado es **NO OPERAR**.

Cuando detecta una señal nueva **VIGILAR** o **SETUP VÁLIDO**, el workflow de GitHub Actions se marca con una alerta para revisión.

## 1. Instalar GitHub en el iPhone

Instala la app oficial de GitHub e inicia sesión con la misma cuenta propietaria del repositorio.

## 2. Activar notificaciones

En GitHub:

1. Abre el repositorio.
2. Pulsa **Watch**.
3. Selecciona **All Activity**.
4. En la app de GitHub habilita las notificaciones push de Actions.

En iOS verifica también:

**Ajustes → Notificaciones → GitHub → Permitir notificaciones**

## 3. Funcionamiento automático

El workflow está en:

`.github/workflows/market-monitor.yml`

V3.1 usa varios intentos para reducir el impacto de retrasos de GitHub Actions.

El workflow despierta a los minutos **07, 22, 37 y 52** de cada hora. Sin embargo, el análisis pesado se ejecuta **solo una vez por cada nueva vela 4H cerrada**. Los intentos posteriores de la misma vela se omiten automáticamente.

Si un análisis falla por datos o conectividad, esa vela no se marca como completada y el siguiente intento vuelve a probar.

Cuando no hay señal nueva, termina correctamente.

Cuando existe una nueva señal para revisar, el workflow genera una anotación de alerta y finaliza en estado fallido de forma intencional para que GitHub pueda enviar la notificación.

Esto no significa que el programa haya fallado técnicamente; en V3 el estado fallido funciona como disparador de notificación.

## 4. Evitar alertas duplicadas

El monitor conserva las últimas señales mediante el cache de GitHub Actions.

La clave usa:

`activo + estado + setup + cierre de vela`

Por tanto, una misma señal de la misma vela no debería generar varias alertas.

## 5. Revisar la señal

Cuando recibas una notificación:

1. Abre GitHub.
2. Entra al workflow **Trader Agent V3 Monitor**.
3. Abre el resumen del run.
4. Revisa activo, setup, probabilidad OOS, break-even, edge, expectativa, entrada, Stop y Take Profit.
5. Abre el dashboard de Streamlit para revisar el contexto completo antes de tomar una decisión.

## Seguridad

- No usa API Key de Binance.
- No accede a fondos.
- No compra ni vende.
- No usa Futures ni Margin.
- La ejecución continúa siendo manual.
