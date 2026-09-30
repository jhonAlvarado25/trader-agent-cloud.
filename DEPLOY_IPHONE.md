# Publicar Trader Agent Cloud V1 y usarlo desde iPhone

## 1. Crear un repositorio en GitHub

Desde Safari o un computador:

1. Entra a GitHub e inicia sesión.
2. Crea un repositorio nuevo, por ejemplo: `trader-agent-cloud`.
3. Sube **los archivos contenidos en esta carpeta**, no el ZIP como un único archivo.
4. Verifica que en la raíz del repositorio queden, entre otros:
   - `app.py`
   - `requirements.txt`
   - `config.py`
   - `market.py`
   - `indicators.py`
   - `strategy.py`
   - `backtest.py`
   - `risk.py`
   - `.streamlit/config.toml`

> V1 no contiene claves API ni credenciales.

## 2. Desplegar en Streamlit Community Cloud

1. Abre `https://share.streamlit.io`.
2. Inicia sesión y conecta tu cuenta de GitHub.
3. Pulsa **Create app**.
4. Selecciona el repositorio que acabas de crear.
5. Branch: normalmente `main`.
6. Main file path / Entrypoint: `app.py`.
7. Elige un subdominio si la plataforma lo permite.
8. Pulsa **Deploy**.

La plataforma instalará las dependencias de `requirements.txt` y publicará la app.

## 3. Abrir desde el iPhone

Cuando termine el despliegue tendrás una dirección similar a:

`https://tu-trader-agent.streamlit.app`

En el iPhone:

1. Abre la URL en Safari.
2. Pulsa **Compartir**.
3. Selecciona **Añadir a pantalla de inicio**.
4. Ponle un nombre como `Trader Agent`.

Desde ese momento tendrás un icono en la pantalla de inicio.

## 4. Uso diario

- El precio en vivo se refresca aproximadamente cada 15 segundos.
- Los indicadores y el backtest se recalculan sobre velas 4H.
- Abre el menú lateral para modificar:
  - Capital COP
  - Riesgo por operación
  - Tasa COP/USDT
- No necesita que Binance esté abierto.
- No puede comprar, vender ni retirar fondos.

## 5. Seguridad para futuras versiones

Cuando construyamos V2:
- **Nunca** guardar una API secret en archivos del repositorio.
- Usar los `Secrets` del hosting.
- Crear una API Binance separada, inicialmente **solo lectura**.
- No habilitar retiros.
- Si algún día se habilita trading, utilizar una clave distinta, restringida y con controles adicionales.

## 6. Privacidad

V1 usa únicamente datos públicos de Binance. Si el repositorio o la app son públicos, no expone tu saldo porque todavía no está conectado a tu cuenta.
