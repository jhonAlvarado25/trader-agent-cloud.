# Trader Agent V6 · OCI Bogotá

Objetivo: mover el motor de vigilancia desde GitHub Actions a una VM permanente en OCI
Colombia Central (Bogotá), manteniendo la ejecución de órdenes manual.

## Fase 1 — crear la VM

Región: **Colombia Central (Bogotá) / sa-bogota-1**.

Configuración inicial recomendada:
- Ubuntu 24.04 LTS
- 2 vCPU
- 4 GB RAM
- 40–60 GB de disco
- IP pública
- SSH con clave
- abrir inicialmente solo TCP/22 desde su IP

No abra todavía 8501, 8787 ni 8788 a Internet.

## Fase 2 — instalar el agente y probar Binance público

En la VM:

```bash
git clone "https://github.com/jhonAlvarado25/trader-agent-cloud." trader-agent-cloud
cd trader-agent-cloud
chmod +x deploy/oci/bootstrap_v6.sh
./deploy/oci/bootstrap_v6.sh
```

El preflight consulta únicamente endpoints públicos de Binance Spot y USDⓈ-M Futures.
Si aparece HTTP 451/403/418/429, detener el despliegue y revisar la causa. No usar VPN,
proxy ni otro mecanismo para eludir una restricción.

Si los cuatro chequeos dan **OK**, continuar.

## Fase 3 — activar monitor 24/7 sin cuenta privada

```bash
cp deploy/oci/v6.env.example .env
nano .env
```

Configure Telegram y cambie:

```
TRADER_ACCESS_CONFIRMED=yes
```

Luego:

```bash
sudo docker compose -f compose.v6.yml up -d --build monitor
sudo docker compose -f compose.v6.yml ps
curl http://127.0.0.1:8787/health
```

El monitor queda reiniciándose automáticamente con Docker y no expone su puerto a Internet.

## Fase 4 — conectar Binance SOLO LECTURA

Solo después de que el monitor público sea estable, coloque en `.env` las claves HMAC
de solo lectura ya creadas en Binance y un token interno aleatorio.

La API de Binance debe tener lectura habilitada y trading/retiros deshabilitados.

Inicie el bridge privado:

```bash
sudo docker compose -f compose.v6.yml --profile private-account up -d account
sudo docker compose -f compose.v6.yml ps
```

El servicio account no publica el puerto 8788 al host.

## Fase 5 — dashboard privado de prueba

Para probar la app sin exponerla públicamente:

```bash
sudo docker compose -f compose.v6.yml --profile private-account --profile ui up -d
```

Desde su computador use un túnel SSH:

```bash
ssh -L 8501:127.0.0.1:8501 ubuntu@IP_PUBLICA_OCI
```

Y abra localmente http://127.0.0.1:8501.

No use HTTP público para mostrar saldos o datos de cuenta. HTTPS se configura en una fase
posterior antes de acceso remoto directo desde el iPhone.

## Estado de seguridad

- Monitor: datos públicos.
- Account bridge: GET de solo lectura.
- Puertos 8787/8788 no expuestos públicamente.
- API keys solo en el archivo .env de la VM.
- No hay funciones de compra, venta, transferencia o retiro.
- La ejecución de órdenes permanece manual.
