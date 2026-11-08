# Trader Agent Pro Terminal — Fase 1 PAPER

## Estado de esta versión

**Prototipo operativo PAPER** en rama aislada
\`feature/trader-pro-terminal-paper\`.

El proyecto V4.2 original y sus workflows se mantienen en \`main\`.
Esta fase no habilita ejecución de órdenes reales ni modifica las
credenciales privadas de Binance.

### Componentes creados

- \`terminal_pro.py\`: interfaz Streamlit adaptada a móvil/escritorio.
- \`paper_engine_pro.py\`: motor Spot LONG virtual con cálculo de riesgo,
  comprobación de límites, entradas idempotentes, cierres manuales,
  cierres por Stop/TP cuando el panel se ejecuta y bitácora SQLite.
- \`tests/test_paper_engine_pro.py\`: pruebas de cálculo, límites, gaps,
  idempotencia, cierre, reset.
- \`tests/test_terminal_pro_ui.py\`: prueba de renderizado sin llamadas web.
- \`.github/workflows/trader-pro-paper-ci.yml\`: CI dedicado a esta rama.

### Cómo desplegar SIN reemplazar V4.2

1. Abrir https://share.streamlit.io/ e iniciar sesión.
2. Seleccionar **Create app** → **Yup, I have an app**.
3. Escoger el repositorio **jhonAlvarado25/trader-agent-cloud.**
   (incluye el punto final del nombre).
4. Indicar rama: **feature/trader-pro-terminal-paper**.
5. Indicar ruta del archivo principal: **terminal_pro.py**.
6. Elegir un subdominio diferente al de la V4.2 actual, si se desea.
7. Pulsar **Deploy**.

No añadir Binance API Key/Secret. El prototipo consume únicamente
datos **públicos**. Tampoco necesita un token nuevo de Telegram.

Referencia oficial sobre selección de repositorio, rama y archivo:
https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy

### Uso del simulador

- La cuenta PAPER comienza con **30 USDT virtuales**, como ejemplo,
  no como lectura del saldo real.
- El tipo de cambio COP/USDT es **manual e ilustrativo**.
- La barra lateral permite escoger nuevo capital y luego reiniciar
  el laboratorio virtual (con confirmación explícita).
- **Escanear** muestra indicadores técnicos de diez activos.
  Se trata de **pre-scan**, no de una recomendación.
- **Evaluar estrategia V4.2** ejecuta la validación profunda únicamente
  cuando el usuario pulsa el botón.
- Una señal validada Spot LONG puede convertirse en simulación PAPER
  si los niveles siguen vigentes y pasan los límites.
- **Ejercicio manual** sirve solo para probar mecánica y no tiene
  respaldo estadístico. Queda marcado \`DEMO\`.
- La compra virtual incluye comisiones, slippage y Stop/TP.
- La pestaña Equity & journal muestra PnL realizado, trades, ganancias,
  pérdidas, curva y archivos CSV exportables.

### Límites PAPER

| Control | Valor inicial |
| --- | --- |
| Riesgo objetivo por operación | 1,20% de patrimonio virtual |
| Límite de pérdida diaria | 2,40% de capital PAPER inicial |
| Drawdown máximo para nuevas entradas | 6% |
| Nuevas entradas por día UTC | 1 |
| Posiciones abiertas simultáneas | 1 |
| Mercado PAPER | Spot LONG sin apalancamiento |
| Comisión simulada por lado | 0,10% |
| Slippage simulado por lado | 0,02% |
| Nocional mínimo de referencia | 5 USDT (no valida lotSize real Binance) |

**Advertencias:**

- La cifra del 1,20% es una **estimación al Stop**, no una garantía.
  Un gap puede generar pérdidas mayores, y el simulador lo registra.
- En una vela donde aparecen tanto SL como TP, se toma el SL por
  criterio conservador; no se inventa el orden intravela.
- Cuando el panel está cerrado **no existe un servicio ejecutándose**
  para vigilar o cerrar las posiciones PAPER. Al abrirlo, trata de
  reconstruir el recorrido de las velas de los últimos ~5 días y
  vuelve a revisar el precio vigente. No es una garantía de cobertura.
- El histórico de operaciones de esta primera fase se escribe en un
  **SQLite local**, cuyo almacenamiento no es permanente en Streamlit
  Community Cloud. Se puede perder tras un reinicio o redespliegue.
  **Exporte el journal con frecuencia.**
- Nunca comparar los resultados virtuales con resultados de un
  broker real sin reconciliar comisiones, spreads, liquidaciones,
  límites de cantidad, latencia y fills reales.

Documentación Streamlit sobre persistencia local:
https://docs.streamlit.io/develop/concepts/connections/connecting-to-data

### Qué sigue antes de hablar de LIVE

1. Almacenamiento persistente externo y journal con auditoría.
2. Worker gestionado que procese señales y gestione PAPER mientras
   el panel está cerrado, con heartbeat y detección de datos obsoletos.
3. Reconciliación contra fuentes de precios y gestión de gaps.
4. Validación de límites reales de símbolos, spreads y liquidez.
5. Pruebas continuas, métricas de calce con backtest y control de riesgos.
6. Verificación de elegibilidad de acceso privado a Binance desde
   la infraestructura elegida.
7. Integración LIVE separada con nueva API de permisos mínimos,
   controles anti-duplicado, kill-switch y autorización expresa.

El servicio monitor V4.2 existente no se modifica ni se sustituye.
