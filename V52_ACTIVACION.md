# V5.2: vigilancia cada segundo

## Para el usuario

Usted indica solo el capital disponible en COP. El agente selecciona la candidata y calcula los
campos de Binance: par, tipo de mercado, precio, cantidad, stop y objetivo. El riesgo permanece
fijo en 1,5%: para $1.000.000 COP, el presupuesto de pérdida modelada es $15.000 COP.
La ejecución y los costos reales pueden superar esa pérdida; no es una garantía.

La V5.2 está preparada en el código. **No hay todavía un servidor permanente conectado ni una
prueba de acceso de producción aprobada. No está activo el monitoreo de un segundo.**
La app existente sigue indicando su frecuencia real mientras se configura el nuevo servicio.
No hay que editar ni subir archivos JSON para usar la aplicación.

Cuando se active, un proceso permanente recibirá precios por WebSocket y revisará las señales
cada segundo, incluso con la app cerrada. El análisis histórico se ejecutará aparte, con un
intervalo objetivo de cinco minutos. Revisar más veces no significa abrir más operaciones ni
mejorar automáticamente su rentabilidad. Los filtros estadísticos de V5 se mantienen.

La vigilancia se refiere a **señales candidatas**. No observa posiciones reales de su cuenta,
no confirma ejecuciones y no coloca ni modifica órdenes. Las operaciones simuladas anteriores
se revisan durante el análisis histórico; el servicio nuevo no registra compras manuales como
si hubieran ocurrido. En esta versión el aviso de Telegram tampoco abre una posición PAPER.

## Alojamiento evaluado — 5 de octubre de 2026

| Alternativa | Evaluación para este capital | Pendiente |
| --- | --- | --- |
| Equipo propio siempre encendido | Evita una suscripción nueva; consume electricidad e internet. Puede detenerse por suspensión o cortes. | Saber si existe un equipo disponible; verificar acceso permitido a los mercados utilizados. |
| Oracle Cloud Always Free | Opción para evaluar antes de pagar. Oracle ofrece recursos gratuitos y tiene región Bogotá. Esto no confirma cupo gratuito en esa región ni autorización de Binance. | Cuenta, región principal, capacidad disponible y elegibilidad efectiva. Oracle puede recuperar instancias consideradas inactivas. |
| Render gratuito | No se propone como garantía de monitor permanente: el plan puede suspender servicios sin tráfico entrante y pierde archivos locales al reiniciar. | No usar un plan que suspenda el proceso para prometer 24/7. |
| Render/Railway u otro servicio de pago | La integración puede facilitar el despliegue, pero no hay cuenta conectada ni tarifa completa aceptada. No se ha contratado. | Presupuesto completo, disco persistente, región y verificación de acceso permitido. |

El objetivo del 15–20% EA sobre $1.000.000 equivale, si se alcanzara, a $150.000–$200.000 COP
en un año. Un costo de infraestructura de $12.500–$16.667 COP mensuales ya consumiría ese
resultado antes de otros costos. Estas cifras son una comparación aritmética, no una proyección
de ganancias. Por eso se prioriza aprovechar un equipo o recurso gratuito existente.

El acceso nativo desde el runner anterior recibió HTTP 451. Cambiar de proveedor o región no
resuelve por sí mismo la autorización. Antes de activar debe confirmarse que el usuario y el
alojamiento están habilitados para Spot y Futures según Binance. No se utiliza VPN, proxy ni
rutas alternativas para eludir un rechazo. Si no se confirma Futures, esta configuración completa
queda pendiente; un modo exclusivamente Spot requeriría un ajuste explícito y sus pruebas.

Fuentes oficiales consultadas:

- [WebSocket Spot](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams).
- [WebSocket Futures y rutas oficiales](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect).
- [Separación de canales Futures](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice).
- [Regiones Oracle](https://docs.oracle.com/en-us/iaas/Content/General/Concepts/regions.htm).
- [Oracle Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm).
- [Capacidad e instancias inactivas Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).
- [Límites de Render gratuito](https://render.com/docs/free).

## Preparación técnica para quien active el servicio

No son pasos que el usuario de inversiones deba repetir. Se realizan una vez en el alojamiento
seleccionado, después de verificar sus permisos y aprobar cualquier costo.

1. Usar un host permanente con Docker Compose, hora sincronizada y almacenamiento persistente.
   Como punto inicial de dimensionamiento se sugieren 2 CPU y 4 GB RAM; medir el consumo del
   análisis real antes de dar por suficiente el tamaño. No es una garantía de rendimiento.
2. Descargar este repositorio y conservar su revisión verificada. El volumen `trader_state`
   mantiene invalidaciones y confirmaciones de Telegram. No ejecutar dos monitores sobre ese volumen.
3. Tras confirmar elegibilidad, configurar `TRADER_ACCESS_CONFIRMED=yes` en el entorno seguro
   del host. La variable registra esa decisión; no demuestra autorización por sí misma. El proceso
   hará además una prevalidación contra los hosts oficiales antes de abrir WebSocket.
4. Si se desean avisos, configurar `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en el almacén de
   secretos del servidor. No ponerlos en el repositorio, un enlace, un log ni el chat. No se necesita
   una clave Binance: esta versión solo consulta datos públicos. Los secretos de GitHub no se
   transfieren ni se extraen automáticamente.
5. Iniciar `docker compose -f compose.v52.yml up -d --build`. El paquete incluye monitor y app.
   Los puertos del host quedan limitados a `127.0.0.1`. Para acceso remoto se debe configurar
   HTTPS en un proxy inverso autorizado; no se entrega aún un dominio ni certificado.
6. La app del paquete usa `TRADER_REALTIME_URL=http://monitor:8787/snapshot`. Para una app ya
   alojada en otro servidor, configurar esa variable con el endpoint HTTPS definitivo del monitor.
   Nunca se cambia silenciosamente al informe de cinco minutos si falla el servicio configurado.
7. Comprobar `/health` y `/snapshot`: `alive` solo indica que el ciclo sigue funcionando;
   `ready` exige datos de mercado recientes y cobertura completa. Verificar también conexiones,
   hora de los precios, hora de la investigación, consumo, persistencia y una alerta de prueba.
8. Con el nuevo servicio confirmado, desactivar el workflow programado anterior para evitar
   avisos duplicados y dos historiales independientes. Esa desactivación **no se ha realizado**.

Si se recibe 401/403/418/429/451, el nuevo servicio guarda el bloqueo y detiene conexiones.
El administrador debe resolver el permiso o límite con el proveedor y revisar la causa antes
de retirar ese bloqueo; reiniciar el contenedor no lo borra. No debe eliminarse el estado completo,
pues también conserva señales invalidadas y mensajes ya confirmados.

## Comportamiento y límites comprobables

- Ciclo objetivo de 1 segundo con medición de retraso; no es un sistema de tiempo real estricto
  ni una garantía de latencia. El navegador actualiza mientras la sesión esté abierta.
- Precios recibidos directamente del mercado correspondiente. Futures usa `/public` para libro
  y `/market` para transacciones y mark price de un segundo.
- Una cotización de más de 3 segundos deja de ser utilizable. Repetir un ciclo no rejuvenece
  su fecha. Las reglas tienen vigencia máxima de una hora y la investigación de 12 minutos.
- La señal tiene su vencimiento propio de 10 minutos. Reanalizar el mismo patrón no modifica
  sus niveles ni extiende ese vencimiento.
- Si se observa un cruce de stop/objetivo, o se pierde continuidad después de empezar a vigilar,
  la señal queda invalidada. Una reconexión o reinicio no la rehabilita. No se detectan eventos
  que Binance no entregue; los huecos de información se tratan de forma conservadora.
- El cálculo de un segundo no realiza consultas REST por cada usuario ni vuelve a descargar
  el histórico. Solo la investigación separada y la prevalidación realizan esas consultas.
- Telegram se intenta únicamente con una candidata válida. La clave se confirma después de
  respuesta satisfactoria; los reintentos tienen espera y no se envía un mensaje cada segundo.
  Una interrupción entre entrega y guardado puede generar un duplicado, por lo que el aviso
  nunca debe interpretarse como una orden nueva.
- El informe no expone capital personal, bitácora, saldos ni credenciales. El capital introducido
  se utiliza en la sesión del panel para recalcular cantidades y permanece fuera del informe público.
- La conversión COP/USDT sigue siendo TRM más reserva estimada, no el precio de compra real.

## Validación de esta entrega

Pruebas automatizadas locales y CI cubren la regresión V5/V5.1, riesgo fijo, cálculo sin REST,
precios/estrategias vencidos, mercados incompatibles, cruce y rebote del stop, pérdida de conexión,
persistencia tras reinicio, rutas Futures, prevalidación 451, entrega de avisos y ciclo de un segundo.
El HTTP local se prueba con datos simulados identificados en los tests.

No se ha probado una conexión WebSocket de producción desde un host habilitado, ni construido
la imagen Docker en este entorno (no dispone de Docker), ni desplegado el servicio permanente.
Tampoco se ha validado rentabilidad futura o recibido permiso para contratar infraestructura.
