# Configurar Telegram — Trader Agent V4.1

El agente ya está preparado para enviar una alerta a Telegram cuando `auto_monitor.py` produzca una nueva **OPERACIÓN CANDIDATA**.

## 1. Crear el bot

En Telegram:

1. Busca el contacto oficial **@BotFather**.
2. Envía `/newbot`.
3. Define un nombre, por ejemplo `Trader Agent`.
4. Define un username que termine en `bot`.
5. BotFather entregará un **token**.

No compartas ese token por chat, correo ni en archivos del repositorio.

## 2. Guardar el token en GitHub

En el repositorio:

`Settings → Secrets and variables → Actions → New repository secret`

Crea:

`TELEGRAM_BOT_TOKEN`

y pega como valor el token entregado por BotFather.

## 3. Obtener el Chat ID sin exponer el token

1. Abre el bot que acabas de crear.
2. Pulsa **START** o envía `/start`.
3. En GitHub abre:
   `Actions → Telegram Setup Test → Run workflow`
4. Abre el run terminado y revisa el paso **Discover chat or send test**.
5. El log mostrará algo como:

`TELEGRAM_CHAT_ID=123456789 | nombre`

El Chat ID no es el token.

## 4. Guardar el Chat ID

Crea otro Repository secret:

`TELEGRAM_CHAT_ID`

y coloca únicamente el número que mostró el workflow.

## 5. Probar

Vuelve a ejecutar:

`Actions → Telegram Setup Test → Run workflow`

Como ya existen ambos secretos, el bot debe enviarte:

`Trader Agent V4.1 conectado correctamente...`

## 6. Funcionamiento automático

El workflow `Trader Agent V4.1 Auto Recommendations` se ejecuta aproximadamente una vez por hora.

Cuando no hay señal, Telegram no envía nada.

Cuando aparece una nueva operación candidata, el mensaje incluye:

- activo;
- Spot o Futures;
- LONG/SHORT;
- timeframe;
- evidencia estadística;
- entrada;
- Stop Loss;
- Take Profit;
- R/R;
- nocional USDT;
- cantidad;
- pérdida neta estimada;
- ganancia neta estimada al TP;
- leverage y margen si es Futures;
- métricas TEST.

Telegram solo notifica. El agente **no ejecuta la operación en Binance**.
