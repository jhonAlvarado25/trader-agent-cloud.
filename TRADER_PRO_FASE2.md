# Trader Agent Pro Terminal — Fase 2: PAPER persistente + worker independiente

**Estado:** código preparado y probado en rama
\`feature/trader-pro-terminal-paper\`. El despliegue remoto **no está activo**
hasta conectar una base PostgreSQL y habilitar explícitamente el worker.

**Sin VM, sin conexión privada a Binance y sin órdenes reales.**

## Arquitectura

\`\`\`text
Streamlit / terminal_pro.py (panel protegido, rama feature)
       | Postgres TLS (TRADER_PAPER_DATABASE_URL)
       v
Supabase PostgreSQL: esquema trader_pro_paper
       ^
       | PostgreSQL TLS
GitHub Actions scheduled (workflow en main, checkout rama feature)
       |
       | Precios y velas públicas Binance Spot
       v
paper_worker_pro.py: revisa solo posiciones PAPER abiertas
\`\`\`

La página y el worker acceden a la **misma** cuenta PAPER. La cuenta y el
journal se guardan en PostgreSQL administrado, no en el disco temporal de
Streamlit.

El worker se programa cada 5 minutos, pero las ejecuciones de GitHub pueden
retrasarse, omitirse o detenerse. Esto **no** es vigilancia de baja latencia.
No sirve para ejecutar ni proteger posiciones reales. No recomendamos activar
órdenes reales con esta infraestructura.

## Antes de habilitarlo

- La app antigua V4.2 sigue en \`main\` y NO se ha modificado su motor.
- La nueva versión se publica desde la rama \`feature/trader-pro-terminal-paper\`
  con archivo principal \`terminal_pro.py\`.
- No tiene que introducir Binance API Key/Secret en Supabase ni en el worker.
- **Exportar journal CSV de su simulador local antes de conectarse a Supabase:**
  el PostgreSQL nuevo creará una cuenta PAPER independiente desde 30 USDT.
  No se migrarán las operaciones locales automáticamente.

## Paso 1 — Crear PostgreSQL en Supabase

Puede conectar Supabase a ChatGPT y autorizar la creación de un proyecto de
pruebas, o hacerlo desde https://supabase.com/dashboard .

1. Crear proyecto, escoger región y contraseña fuerte del proyecto.
2. Abrir botón **Connect** del proyecto → **Session pooler**.
3. Copiar la URI PostgreSQL indicada allí. La conexión de modo session pooler
   utiliza conectividad IPv4 y puerto 5432 según documentación de Supabase.
4. Conservar la URI únicamente en Secrets; **nunca** subirla a GitHub.

Este módulo usa \`psycopg[binary]\` (v3) con TLS obligatorio
(\`sslmode=require\`) y \`prepare_threshold=None\`.

La primera conexión inicializa automáticamente cuatro tablas dentro del
esquema privado de PostgreSQL \`trader_pro_paper\`:
\`account\`, \`trades\`, \`events\` y \`worker_status\`.

No se configuran credenciales del broker en la base de datos. El esquema
\`trader_pro_paper\` no se expone expresamente mediante el Data API público.

## Paso 2 — Proteger la aplicación Streamlit

Desde Streamlit Community Cloud → app de **Trader Agent Pro** →
Settings → Secrets, añadir:

\`\`\`toml
TRADER_PAPER_DATABASE_URL = "postgresql://postgres.PROYECTO:CONTRASENA@HOST.pooler.supabase.com:5432/postgres"
TRADER_PAPER_DASHBOARD_PASSWORD = "CONTRASENA_PRIVADA_UNICA_DE_MAS_DE_16_CARACTERES"
\`\`\`

Reemplace el URI ilustrativo por la cadena **exacta** de su proyecto y utilice
una contraseña de acceso distinta de la de la base de datos. Si la contraseña
contiene símbolos reservados, copie la URI oficial ya codificada.

La terminal bloqueará el acceso hasta escribir la contraseña de la página.
Es una autenticación básica del prototipo, **no** un sistema multiusuario ni
sustituye control de acceso profesional.

Si la conexión PostgreSQL falla, la terminal bloquea la operación:
no cambia silenciosamente al SQLite local ni simula otro saldo.

## Paso 3 — Configurar GitHub Actions sin exponer secretos

Repositorio \`jhonAlvarado25/trader-agent-cloud.\` → Settings →
Secrets and variables → Actions:

- En **Secrets**, crear \`TRADER_PAPER_DATABASE_URL\` con exactamente la
  misma URI PostgreSQL configurada en Streamlit.
- En **Variables**, crear \`TRADER_PAPER_WORKER_ENABLED\` con valor \`yes\`.
  Mientras la variable no exista o sea distinta de \`yes\`, el workflow
  estará **desactivado**.

El workflow se llama \`Trader Pro PAPER Worker\` y vive en \`main\`,
porque GitHub solo ejecuta cron desde la rama predeterminada.
El proceso utiliza la rama \`feature/trader-pro-terminal-paper\`.

GitHub Actions → \`Trader Pro PAPER Worker\` → Run workflow.
Inspeccionar el resultado. El panel deberá mostrar **ACTUALIZADO**
si existe un heartbeat OK realizado en los últimos 20 minutos.

En ausencia de worker, una señal nueva PAPER queda **bloqueada** en modo
PostgreSQL. En modo local SQLite la terminal sigue funcionando con el
comportamiento de Fase 1.

## Controles conservados

- Solo Spot LONG, posiciones **simuladas**, sin apalancamiento.
- Pérdida objetivo por Stop ≤ **1,20%** del patrimonio PAPER estimado,
  incluyendo comisión y deslizamiento supuestos.
- Una posición virtual abierta a la vez.
- Una nueva entrada virtual por día UTC.
- Pérdida diaria realizada límite 2,40% del capital PAPER inicial.
- Bloqueo de nuevas entradas desde 6% de drawdown virtual.
- Identificador único de señal y cuenta bloqueada transaccionalmente para
  evitar entrada/cierre simultáneo duplicado desde panel y worker.
- Worker no abre nuevas operaciones; solo intenta valorar y cerrar
  posiciones PAPER ya registradas por el usuario.
- Precios históricos faltantes, worker desactualizado o velas con huecos
  producen un estado **DEGRADED** y bloquean nuevas entradas.
- Siempre PAPER; ninguna llamada privada POST/DELETE Binance.

En el backfill, si una vela parcial de entrada toca el Stop, se aplica STOP
de manera conservadora; no se atribuye TP a precios potencialmente anteriores
a la entrada. Esto puede sesgar negativamente los resultados simulados, pero
evita atribuir ganancias no verificables.

## Qué puede y qué no puede garantizar el proceso

- Una operación puede permanecer abierta más de cinco minutos si GitHub
  atrasa su ejecución.
- Cuando Stop y TP ocurren en la misma vela completa, se trata como STOP.
- Pueden existir spreads, gaps o huecos de datos que hagan que la simulación
  difiera de una ejecución real.
- Para posiciones de más de ~5 días sin reconstrucción completa de velas,
  el worker declara \`DATA_GAP\` y no inventa el resultado.
- El saldo del panel es **virtual**, no refleja su cuenta Binance.
- Supabase y GitHub pueden aplicar cuotas, suspensiones y costos según el
  plan. Revise límites y cobros antes de activar servicios.

## Verificación

CI de la rama: \`Trader Pro PAPER CI\`.
Comprueba sintaxis, UI Streamlit, riesgo, transacciones virtuales, cálculo de
fees, slippage, protección de duplicados, adaptador SQL y heartbeats.
La conectividad real a **su** proyecto Supabase todavía requiere que lo cree
y configure los dos sistemas con secretos privados.

## Documentación pública relevante

- PostgreSQL con Streamlit:
  https://docs.streamlit.io/develop/tutorials/databases/postgresql
- Secretos de Streamlit:
  https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
- Conexión PostgreSQL Supabase:
  https://supabase.com/docs/guides/database/connecting-to-postgres
- GitHub Actions schedule:
  https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
