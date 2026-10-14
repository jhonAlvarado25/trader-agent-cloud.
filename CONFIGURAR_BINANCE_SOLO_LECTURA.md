# Conectar Binance en modo solo lectura — Trader Agent V4.2

## Objetivo

La integración privada de Binance sirve únicamente para mostrar saldos Spot,
órdenes abiertas y operaciones recientes dentro del panel Streamlit.

El agente NO ejecuta órdenes y el módulo `binance_readonly.py` implementa
exclusivamente solicitudes GET.

## 1. Configuración correcta en Binance

Crea o conserva una API HMAC dedicada al Trader Agent.

Debe quedar:

- Enable Reading: ACTIVADO.
- Spot & Margin Trading: DESACTIVADO.
- Futures: DESACTIVADO.
- Margin: DESACTIVADO.
- Withdrawals: DESACTIVADO.
- Options / Portfolio Margin / FIX Trade: DESACTIVADO.

No reutilices una API con permisos de trading.

## 2. Streamlit Secrets

Guarda únicamente en Streamlit Community Cloud:

```toml
BINANCE_API_KEY = "TU_API_KEY"
BINANCE_API_SECRET = "TU_API_SECRET"
```

No pongas estas credenciales en GitHub, archivos del repositorio ni chats.

## 3. Cómo se conecta V4.2

El cliente prueba los endpoints REST oficiales publicados por Binance:

- https://api.binance.com
- https://api-gcp.binance.com
- https://api1.binance.com
- https://api2.binance.com
- https://api3.binance.com
- https://api4.binance.com

Primero valida conectividad pública y después prueba un endpoint USER_DATA
firmado. Conserva el primer host oficial que responda correctamente.

La firma sigue siendo HMAC SHA-256 y el `recvWindow` se mantiene en 5000 ms.

## 4. Diagnóstico en la aplicación

En **Mi cuenta Binance — SOLO LECTURA** pulsa:

**Probar conexión Binance ahora**

Resultados posibles:

### Conectado a Binance con permisos verificados de SOLO LECTURA

La integración está funcionando y la app mostrará el endpoint oficial activo,
saldo USDT, saldo del activo seleccionado, órdenes y últimas operaciones.

### CLAVES GUARDADAS · BINANCE BLOQUEA LA REGIÓN/IP DEL SERVIDOR STREAMLIT

Las claves existen, pero Binance rechazó la IP/región del servidor de Streamlit.
Esto no demuestra que la API Key o el Secret estén dañados.

No se deben usar VPN, proxies ni mecanismos para evadir las restricciones
geográficas de Binance.

### Binance rechazó las credenciales o la configuración de la API

Revisa que la API siga activa, que la clave y el secret correspondan entre sí
y que las restricciones configuradas en Binance permitan la IP desde la que se
está conectando el servicio.

## 5. Restricción por IP

Streamlit Community Cloud no debe asumirse como un servidor con IP pública fija
para una allowlist personal.

Si la API está configurada con una lista de IP y esa lista no coincide con la
infraestructura real de Streamlit, las solicitudes privadas pueden ser
rechazadas.

No desactives controles de seguridad únicamente para lograr una conexión.

## 6. Qué datos puede leer

- Saldos Spot no nulos.
- USDT libre.
- Saldo libre y bloqueado del activo seleccionado.
- Órdenes abiertas.
- TP/SL detectables en órdenes SELL.
- Últimos fills/operaciones del par.
- Permisos de la API cuando el endpoint de restricciones está disponible.

Los datos privados se cachean 60 segundos para reducir solicitudes y evitar
rate limits.

## 7. Qué NO puede hacer

El módulo no implementa llamadas POST, PUT o DELETE para:

- Comprar.
- Vender.
- Cancelar órdenes.
- Transferir fondos.
- Retirar fondos.

La ejecución continúa siendo manual en Binance.

## 8. Monitor automático

El monitor V4.2 de GitHub Actions utiliza datos públicos de mercado para buscar
oportunidades. Las credenciales privadas de Binance no son necesarias para
ese análisis ni para Telegram.
