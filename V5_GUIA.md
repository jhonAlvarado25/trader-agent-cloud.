# Trader Agent V5.1 · Guía simple

## Lo único que debe hacer

1. Abra la aplicación habitual.
2. Escriba **Capital para invertir (COP)**: dinero libre destinado al agente.
3. Lea la decisión: **esperar**, **observar** o **candidata para revisar**.
4. Solo cuando exista una candidata validada verá los campos de Binance y sus cantidades.
5. Si decide ejecutar, revise el saldo USDT real, abra manualmente la entrada y confirme su
   protección. No abra otra operación reutilizando el mismo capital.

No elige moneda, estrategia, mercado, temporalidad, riesgo ni niveles.
**No descarga ni modifica JSON.** No necesita pulsar Analizar.
Cambiar capital recalcula los montos; se conserva durante la sesión del navegador.
Si se reinicia la sesión, vuelva a introducirlo. No se almacena como dato público.

## Riesgo fijo

**1,5% por operación**, solicitado por el usuario. Sobre COP 1.000.000 son COP 15.000
de presupuesto de pérdida modelada. Redondeos, saldo, costos o mínimos pueden reducir
el tamaño; no se aumenta para completar el mínimo de Binance.

No es una pérdida máxima garantizada: gaps, comisiones, funding y órdenes no ejecutadas
pueden superarla. No se elevó el límite de drawdown histórico para fabricar más señales:
con 1,5% algunas estrategias pueden dejar de superar ese filtro.

El calculador no conoce automáticamente otras posiciones u órdenes reales. La indicación
**una operación a la vez** debe cumplirse manualmente. No confundir riesgo 1,5% con un Stop
a 1,5% del precio: el Stop depende de la estrategia, la cantidad se adapta al riesgo.

## Qué recibe automáticamente

- Revisión programada en la nube cada cinco minutos, incluso con la app cerrada.
- Pantalla actualizada cada treinta segundos mientras la sesión permanezca activa.
- Precios Spot, variación 24 h y tendencia diaria de BTC, ETH, SOL, BNB, XRP, ADA, DOGE,
  LINK, AVAX y LTC. Los extremos de veinte velas horarias se explican en los detalles.
- Selección Spot/Futures y LONG/SHORT donde existan datos nativos completos.
- Tabla de entrada, cantidad, objetivo y protección; margen y apalancamiento en Futures.
- Telegram avisa por oportunidad, no a una hora fija. El mensaje no arrastra cantidades
  calculadas con un capital antiguo: las cifras se calculan al abrir la aplicación.

GitHub Actions es una programación de revisiones, **no un servicio de ticks 24/7 ni un
reloj garantizado**. Puede retrasarse u omitir ciclos. Se muestra la fecha real del informe.
Un informe mayor de doce minutos, una señal vencida, datos incompletos o precios no
revalidados no generan campos de ejecución. La evidencia EN OBSERVACIÓN no muestra órdenes.

## Conversión COP/USDT sin otro dato obligatorio

Se consulta la TRM vigente de Datos Abiertos Colombia / Superfinanciera y se añade una
reserva de conversión del 3%. Es una **estimación de dimensionamiento**, no una cotización
P2P, no la TRM exacta de USDT y no su costo real de compra. Ni el 3% ni la paridad USDT/USD
se garantizan. Verifique capital USDT suficiente antes de copiar una cantidad.

Si no hay TRM vigente se bloquean los montos. Se elimina la dependencia de que el usuario
confirme manualmente la tasa 3350 heredada. Los resultados en COP siguen siendo estimados;
la rentabilidad real requiere precios de compra/venta USDT, comisiones y tributos reales.

## Cómo introducir los valores

**Spot:** Limit de compra; después de confirmar la entrada, OCO / TP-SL de venta con
Take Profit, Stop/trigger, Limit SL y cantidad de protección. Una Stop-Limit puede no
ejecutarse. Confirme el saldo del activo después de comisiones.

**Futures:** USDⓈ-M, margen aislado y apalancamiento indicado (máximo 2x), entrada Limit,
cierres Stop Market y Take Profit Market con trigger de precio del contrato.
En modo unidireccional, apertura Reduce Only = NO y cierres = SÍ. Hedge Mode requiere
Position Side y otros flags: no copie instrucciones de modo unidireccional sin adaptarlas.
La liquidación real y las reglas finales se verifican en Binance. No se modifica ningún
modo de cuenta, leverage, permiso ni orden desde el agente.

El modelo de ganancia al TP es una aproximación conservadora de costos, no un cobro
asegurado; un TP Market puede ejecutarse distinto del trigger.

## Qué se conserva de la validación V5

- Datos nativos Spot/Futures separados; no se reemplaza Futures por un proxy Spot.
- Restricciones HTTP 401/403/418/429/451 detienen la ruta afectada, sin eludirlas.
- Velas cerradas 1H/2H/4H con contexto 1D; selección TRAIN/VALIDATION y TEST posterior.
- Clasificación FUERTE exige muestra, expectativa, PF, bootstrap por bloques, costos
  estresados, ventanas OOS y drawdown compatibles; no es una probabilidad de ganar.
- Ranking entre candidatas basado en validación, no en buscar el mayor TEST.
- Stop primero ante ambigüedad, gaps y funding nativo en la simulación.
- Los filtros de tamaño no equivalen a todos los controles privados del broker.
- La meta 15–20% EA es una referencia, no una promesa ni una obligación de operar.

## Detalles técnicos y límites

Entrada del panel: app.py. Monitor: monitor_v51.py.
El workflow conserva el nombre de archivo auto-recommend-v4.yml pero ahora ejecuta V5.1.
Se inicia también al publicar cambios del monitor en main. El primer arranque puede
enviar un aviso de estado; las alertas de oportunidades requieren éxito de Telegram.

El monitor publica solo información pública de mercado en la rama separada market-data,
mediante el token efímero de GitHub Actions con permiso de contenido. No se instala un
token de escritura en Streamlit ni se solicitan claves nuevas al usuario.
No se publican capital, cuenta, credenciales ni bitácoras. La rama separada evita reiniciar
la app al actualizar datos. Si se rechaza la publicación, falla visiblemente y no busca
otro destino ni relaja permisos.

Las sombras PAPER registran una simulación activa a la vez con el presupuesto fijo.
Las pausas por pérdidas simuladas conservan umbrales diario 1,5%, semanal 3% y drawdown 5%.
Se continúa mostrando el mercado durante una pausa. Estas sombras no prueban que el usuario
ejecutó ni supervisan sus posiciones reales. Las nuevas candidatas del panel no son una
medición de exposición de cuenta.

El estado de simulación usa cache y artifacts; no es una base contable durable. La bitácora
manual y lectura de cuenta de V5 se conservan en código/historial, pero se retiraron de la
pantalla principal para simplificarla. No se borra ningún archivo privado exportado.

El perfil JSON se conserva por compatibilidad técnica. Los puntos de entrada actuales
aplican el riesgo fijo 1,5%; las antiguas variables de riesgo no lo sobrescriben.
El monitor usa un presupuesto neutral para investigación. Por eso Telegram no muestra
montos ni exige sincronizar el capital del panel.

Las pruebas usan casos sintéticos, mocks y Streamlit AppTest. Verifican funcionamiento,
no rentabilidad, fills, ejecución real ni cumplimiento automático del riesgo de cuenta.
