# Conectar Binance en modo solo lectura

Trader Agent V3.3 puede leer tu cuenta Spot de Binance sin capacidad de enviar órdenes.

## 1. Crear una API de solo lectura en Binance

En Binance:

1. Abre **Perfil / Cuenta → Gestión de API**.
2. Crea una nueva API, por ejemplo: `Trader Agent Read Only`.
3. Usa una clave generada por el sistema (HMAC) para esta integración.
4. Mantén habilitado únicamente **Enable Reading / Habilitar lectura**.
5. **NO habilites Spot & Margin Trading**.
6. **NO habilites Futures**.
7. **NO habilites Margin**.
8. **NO habilites Withdrawals / Retiros**.

Trader Agent verifica esos permisos y mostrará una advertencia si detecta permisos de escritura.

## 2. No pongas las claves en GitHub

Nunca escribas la API Key o API Secret dentro de `app.py`, `config.py`, README ni ningún archivo del repositorio.

Tampoco las pegues en un chat.

## 3. Streamlit Community Cloud

En la configuración de tu aplicación Streamlit abre **Secrets** y añade:

```toml
BINANCE_API_KEY = "TU_API_KEY"
BINANCE_API_SECRET = "TU_API_SECRET"
```

Guarda. Streamlit reiniciará la aplicación.

Cuando abras Trader Agent aparecerá la sección **Mi cuenta Binance — SOLO LECTURA**.

## 4. GitHub Actions para el monitor automático

Si quieres que el monitor revise tu posición aunque la página de Streamlit esté cerrada:

Repositorio GitHub → **Settings → Secrets and variables → Actions → New repository secret**

Crea dos secretos:

- `BINANCE_API_KEY`
- `BINANCE_API_SECRET`

El workflow los entrega al proceso como variables de entorno. El valor de los secretos no se imprime en los logs.

## 5. Qué podrá leer V3.3

- Saldos Spot no nulos.
- Saldo libre y bloqueado.
- Órdenes abiertas por activo.
- Limit TP real.
- Stop / Trigger SL real.
- Limit SL real.
- Cantidad protegida.
- Últimos fills/operaciones del activo.
- Permisos de la API para verificar que sea de solo lectura.

## 6. Qué NO puede hacer

El módulo `binance_readonly.py` implementa exclusivamente solicitudes GET. No contiene endpoints para:

- Comprar.
- Vender.
- Cancelar órdenes.
- Transferir fondos.
- Retirar fondos.

La ejecución continúa siendo manual en Binance.
