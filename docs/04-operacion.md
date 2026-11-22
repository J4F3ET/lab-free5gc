# 04 — Operación diaria

Ya está instalado. Esto es lo que se hace con él.

> El arranque, la parada y la recuperación los hace la pila escalonada
> `lab5g-stack`. Cómo funciona y qué comandos usar cada día está en
> [09 — Arranque seguro y recuperación](09-arranque-seguro.md). Aquí aparecen los
> mismos comandos en contexto.

---

## Índice

- [1. Arrancar y parar](#1-arrancar-y-parar)
- [2. Ver qué está pasando](#2-ver-qué-está-pasando)
- [3. Leer los logs](#3-leer-los-logs)
- [4. Grafana](#4-grafana)
- [5. Consultas útiles de Prometheus](#5-consultas-útiles-de-prometheus)
- [6. Capturar tráfico](#6-capturar-tráfico)
- [7. Manejar abonados](#7-manejar-abonados)
- [8. Cambiar la configuración](#8-cambiar-la-configuración)
- [9. Vigilar los recursos](#9-vigilar-los-recursos)

---

## 1. Arrancar y parar

### Lo primero, en cada terminal

```bash
export COMPOSE_PROFILES=ran,compresion,obs,grafana,obs-extra,probes
```

Sin esto, `docker compose logs ue` responde `no such service` aunque el
contenedor esté corriendo. Los servicios de un perfil inactivo son invisibles
para compose, y es la confusión número uno al operar este laboratorio.

Si solo te interesa el núcleo y la RAN, basta con `COMPOSE_PROFILES=ran`.

En el LXC, el código vive en `/opt/lab5g`. Para que compose use el mismo proyecto
que la pila, exporta:

```bash
export COMPOSE_PROJECT_NAME=lab5g
```

`/opt/lab5g/.env` es un enlace a `/etc/lab5g/lab5g.env` (lo crea
`deploy/bootstrap-runner.sh`), así que compose lee el entorno real desde
`/opt/lab5g` sin más configuración.

### Arrancar la pila

```bash
sudo /usr/local/sbin/lab5g.sh start
```

Arranca, **de una en una y por etapas**, todo lo gestionado: observabilidad,
núcleo, `ran` y `compresion`. No arranca Grafana, las sondas ni `phy-sim`. Tarda
18.5 minutos en frío (medido el 21 de noviembre). Es la única forma recomendada: los servicios no tienen
reinicio automático de Docker, así que sin la pila nadie los vigila.

Para arrancar solo una parte:

```bash
sudo /usr/local/sbin/lab5g.sh start ran      # alias: nucleo, ran, compresion u obs
sudo /usr/local/sbin/lab5g.sh start amf      # un servicio concreto
```

Las sondas y `phy-sim` se arrancan por nombre, con la misma compuerta. Requieren
`ue` arriba:

```bash
sudo /usr/local/sbin/lab5g.sh start blackbox
sudo /usr/local/sbin/lab5g.sh start iperf-probe
sudo /usr/local/sbin/lab5g.sh start phy-sim
```

Detalle y criterios en [09 §3](09-arranque-seguro.md#3-el-arranque-por-etapas). La
configuración del host que lo hace posible (contenedor 110 con `--onboot` y
`--startup`, zram y swappiness) está en [09 §2](09-arranque-seguro.md#2-las-piezas).

### Grafana

Grafana es bajo demanda:

```bash
sudo /usr/local/sbin/lab5g.sh grafana up
```

Si la compuerta de memoria no pasa, `grafana up` se niega y dice por qué. Ver
[09 §9](09-arranque-seguro.md#encender-y-apagar-grafana).

### Parar sin perder nada

```bash
sudo /usr/local/sbin/lab5g.sh stop
```

Para todo, en orden inverso y uno a uno. Un apagado limpio medido el 21 de noviembre
tardó 254 s. Crea `/run/lab5g/parado`, que suspende
la recuperación hasta el próximo `start`.

Para parar solo algunos servicios:

```bash
sudo /usr/local/sbin/lab5g.sh stop cmp-gen cmp-ue cmp-dn envio envio-eco
```

Un servicio parado así queda anotado como parado a mano: la recuperación no lo
revive hasta que lo arranques.

### Parar y borrarlo todo

Primero para la pila, después borra los contenedores. Hay que nombrar **todos**
los perfiles:

```bash
sudo /usr/local/sbin/lab5g.sh stop
cd /opt/lab5g
docker compose --profile ran --profile compresion --profile obs --profile grafana --profile obs-extra --profile probes down -v
```

⚠️ El `-v` borra el volumen de MongoDB **y con él el abonado**. Después de esto,
`provision-subscriber.sh` es obligatorio.

### Reiniciar solo el teléfono

Lo más frecuente durante la depuración:

```bash
docker compose restart ue
```

O, sin recrear el contenedor, usando la CLI de UERANSIM:

```bash
docker compose exec ue nr-cli imsi-732101000000001 --exec "deregister normal"
```

Para parar y volver a arrancar solo el `ue` a través de la pila:

```bash
sudo /usr/local/sbin/lab5g.sh stop ue
sudo /usr/local/sbin/lab5g.sh start ue
```

El `ue` necesita `gnb` y `smf` arriba; si no, la CLI lo omite con error.

---

## 2. Ver qué está pasando

### Estado de la pila

```bash
sudo /usr/local/sbin/lab5g.sh status
```

Una fila por servicio con su estado, tiempo de vida y memoria, y al final la
carga del host y la etapa. Un servicio que agotó sus reinicios aparece como
`agotado`.

### Estado de los contenedores

```bash
docker compose --profile ran --profile compresion --profile obs --profile grafana --profile obs-extra --profile probes ps
```

Lo que hay que mirar en la salida:

| Estado | Significa |
|---|---|
| `Up` | Corriendo |
| `Up (healthy)` | Corriendo y su chequeo pasa. Solo MongoDB tiene chequeo de Docker |
| `Created` | Nunca llegó a arrancar. Casi siempre: una dependencia no está sana |
| `Exited (0)` | Terminó bien. En este laboratorio, sospechoso |
| `agotado` (en `lab5g.sh status`) | La recuperación se rindió: 5 reinicios en 60 min. Ver [09 §6](09-arranque-seguro.md#6-la-recuperación-y-sus-límites) |

Los servicios tienen `restart: "no"`, así que `Restarting` ya no aparece por sí
solo: quien reinicia es `lab5g-stack`.

### ¿Se registraron todas las piezas del núcleo?

```bash
docker compose exec nrf curl -s http://nrf:8000/nnrf-nfm/v1/nf-instances | grep -o '"nfType":"[A-Z]*"' | sort | uniq -c
```

Deben salir ocho tipos. Si falta alguno, ese servicio no arrancó o no encuentra
al NRF.

### ¿Funciona el laboratorio, sí o no?

```bash
COMPOSE_PROFILES=ran bash ci/smoke-ue.sh
```

La respuesta definitiva en un solo comando: recorre las seis condiciones
end-to-end y dice cuál falla.

### El estado del teléfono y la antena

UERANSIM trae su propia línea de comandos:

```bash
docker compose exec ue nr-cli imsi-732101000000001 --exec "status"
```

```bash
docker compose exec gnb nr-cli --dump
```

---

## 3. Leer los logs

### Por línea de comandos

```bash
docker compose logs -f --tail 100 smf
```

Varios a la vez, que es como se ve una conversación completa:

```bash
docker compose logs -f --tail 50 amf smf upf
```

Las decisiones de la pila (qué arranca, qué se reinicia, por qué espera) están en
el journal de systemd, no en los contenedores:

```bash
journalctl -u lab5g-stack -f
```

### Qué buscar en cada uno

| Servicio | Lo que confirma que va bien |
|---|---|
| `nrf` | `NF register success` — uno por cada pieza |
| `amf` | `Send NG-Setup response` |
| `smf` | `Association Setup Response` ← **el mensaje más importante del laboratorio** |
| `upf` | `gtp5g device created` |
| `gnb` | `NG Setup procedure is successful` |
| `ue` | `Registration is successful`, luego `PDU Session establishment is successful` |

Si el `smf` no dice `Association Setup Response`, nada de lo que venga después
funcionará, por muy bien que se vea el resto.

---

## 4. Grafana

`http://192.168.1.50:3000` — usuario `admin`, contraseña la de `GRAFANA_ADMIN_PASSWORD`.

Grafana **no está encendido por defecto**:

```bash
sudo /usr/local/sbin/lab5g.sh grafana up
sudo /usr/local/sbin/lab5g.sh grafana down
```

`up` exige que Prometheus tenga datos de menos de 2 minutos. También se puede
hacer desde GitHub Actions con el workflow `observability-control`:

```bash
gh workflow run observability-control.yml --repo J4F3ET/lab-free5gc -f accion=grafana-up
gh workflow run observability-control.yml --repo J4F3ET/lab-free5gc -f accion=grafana-down
```

Los tres paneles ya están montados: no hay que importar nada ni configurar la
fuente de datos. Aparecen solos al arrancar Grafana, porque el directorio
`observability/grafana/provisioning/` se monta dentro del contenedor:

- `lab5g-overview` — el estado de la red y del host
- `lab5g-cmp-algoritmo` y `lab5g-cmp-comparacion` — la compresión de paquetes (ver [docs/08](08-compresion-paquetes.md))

### Cómo leerlo

**Fila 1 — Plano de control.** Un cuadro por pieza del núcleo, verde o rojo. Se
alimenta de `probe_success`, que es una sonda TCP: comprueba que el puerto
responde. Rojo aquí significa "ese servicio no acepta conexiones".

**Fila 2 — Plano de usuario.** La parte valiosa:

- `ue_tunnel_up` — ¿existe `uesimtun0`? Es 1 o 0
- Latencia **medida desde dentro del teléfono**, atravesando el túnel de verdad
- Velocidad de bajada y subida, en escalones: la sonda mide cada 5 minutos, así
  que la línea es escalonada por diseño y no por un fallo

**Fila 3 — Recursos.** Tres paneles salen de la pila (`lab5g-stack` al
pushgateway): «Presion de I/O del host (%)» (`lab5g_host_io_presion_pct`),
«Memoria disponible del host» (`lab5g_host_mem_disponible_mb`) y «Memoria por
contenedor del lab» (`lab5g_ct_mem_mb`). El panel «CPU por contenedor del lab»
se quitó (22-11): su fuente, cAdvisor, está retirada.

El dashboard tiene además una sección de capa física simulada (`lab5g_phy_*`).

> Cuando el iowait o la presión de I/O suben, los chequeos de salud empiezan a
> expirar y los servicios parecen caídos sin tener nada roto. Si ves timeouts que
> no se explican, mira el disco antes que en ningún otro sitio.

### Si un panel dice "Datasource not found"

La fuente de datos tiene un `uid` fijado a `prometheus` en
[`observability/grafana/provisioning/datasources/prometheus.yml`](../observability/grafana/provisioning/datasources/prometheus.yml).
Sin ese `uid` explícito, Grafana genera uno aleatorio en cada arranque y los
paneles provisionados dejan de encontrarlo. Si tocas ese fichero, mantén el
`uid`.

---

## 5. Consultas útiles de Prometheus

Para escribirlas directamente en `http://192.168.1.50:9090` o en un panel nuevo.

Los dashboards usan `last_over_time(...)` para mostrar el último dato de cada
serie, porque las sondas y el pushgateway no publican de forma continua.

Latencia del plano de usuario, vista por el teléfono:

```promql
probe_duration_seconds{plane="user"}
```

¿Está arriba el túnel?

```promql
ue_tunnel_up
```

Velocidad de bajada en megabits:

```promql
ue_throughput_downlink_bps / 1000000
```

Alguna pieza del núcleo caída:

```promql
probe_success{plane="control"} == 0
```

Presión de I/O del host — la métrica que más veces explica un fallo raro:

```promql
lab5g_host_io_presion_pct
```

Memoria por servicio, de mayor a menor:

```promql
topk(5, lab5g_ct_mem_mb)
```

Servicios gestionados que no están corriendo, y los que agotaron sus reinicios:

```promql
lab5g_stack_servicio_up == 0
```

```promql
lab5g_stack_agotado == 1
```

Segundos desde la última publicación de la pila (si crece sin parar, la pila no publica):

```promql
time() - lab5g_stack_latido_segundos
```

Un truco que vale la pena: superpón en el mismo panel la **latencia del teléfono**
y la **presión de I/O**. Si suben juntas, el problema es de capacidad del host.
Si la latencia sube sola, es de red.

> No hay CPU por contenedor: el panel se quitó porque cAdvisor está retirado.
> Si hace falta, `docker stats --no-stream` dentro del LXC da el valor puntual.

---

## 6. Capturar tráfico

Cada red está separada precisamente para que esto sea legible.

Las órdenes del SMF al UPF (N4/PFCP) — aquí se ve si la asociación se establece:

```bash
docker compose exec upf tcpdump -i any -n udp port 8805
```

Los datos del túnel (N3/GTP-U):

```bash
docker compose exec gnb tcpdump -i any -n udp port 2152
```

La señalización de la antena (N2/SCTP):

```bash
docker compose exec gnb tcpdump -i any -n sctp port 38412
```

El tráfico ya desenvuelto, saliendo del UPF:

```bash
docker compose exec upf tcpdump -i upfgtp -n
```

Para llevártelo a Wireshark:

```bash
docker compose exec upf tcpdump -i any -n -w - udp port 2152 > /tmp/n3.pcap
```

---

## 7. Manejar abonados

### Recrear el de siempre

```bash
bash ci/provision-subscriber.sh
```

Es idempotente: usa `replaceOne` con `upsert`, así que ejecutarlo dos veces no
duplica nada.

### Añadir uno distinto

El script lee variables de entorno:

```bash
IMSI=732101000000002 K=8baf473f2f8fd09487cccbd7097c6862 bash ci/provision-subscriber.sh
```

Recuerda que el IMSI debe tener **15 dígitos exactos** y empezar por
`MCC + MNC` = `732101`.

### Ver los que hay

```bash
docker compose exec mongodb mongosh --quiet free5gc --eval 'db.subscriptionData.provisionedData.amData.find({}, {ueId:1}).toArray()'
```

### Sin consola web

La consola web de free5GC (`webui`) ya no está en el compose. Los abonados se
dan de alta solo por línea de comandos, con `ci/provision-subscriber.sh`.

---

## 8. Cambiar la configuración

### El ciclo

```bash
nano config/amfcfg.yaml
```

```bash
python3 ci/check-plmn.py
```

```bash
docker compose restart amf
```

**Ejecuta siempre `check-plmn.py` antes de reiniciar.** Los ficheros se montan
como volúmenes de solo lectura, así que un reinicio basta para aplicar el
cambio — pero si has roto la coherencia entre los once sitios donde aparecen los
tres números, el síntoma no será un error de arranque: será un teléfono que deja
de registrarse tres pasos más adelante.

### Si cambias el PLMN o el IMSI

Hay que rehacer el abonado, porque el que está en MongoDB lleva el PLMN viejo:

```bash
bash ci/provision-subscriber.sh && docker compose restart ue
```

Lee [06 — PLMN e identidades](06-plmn-e-identidades.md) antes de tocarlos: son
once ficheros, no uno.

### Si cambias el MTU

`UE_MTU` debe ser siempre `DOCKER_MTU - 36`. Los 36 bytes son la cabecera GTP-U y
no son negociables. Cambiar `DOCKER_MTU` obliga a recrear las redes. Para el
procedimiento, para la pila primero:

```bash
sudo /usr/local/sbin/lab5g.sh stop
cd /opt/lab5g
docker compose --profile ran --profile compresion --profile obs --profile grafana --profile obs-extra --profile probes down
sudo /usr/local/sbin/lab5g.sh start
```

Por confirmar que el `down` seguido de `lab5g.sh start` recree las redes con el
MTU nuevo. No se ha probado en este commit.

---

## 9. Vigilar los recursos

### Lo que consume cada cosa, ahora mismo

```bash
docker stats --no-stream
```

### La regla práctica

Los 24 servicios definidos son **20 siempre encendidos** (núcleo 10, `ran` 3,
`compresion` 5, `obs` 2) y **4 bajo demanda** (grafana, blackbox, phy-sim,
iperf-probe).

Las cifras de la tabla son **topes** (`mem_limit`), no uso real. Los topes de los
20 siempre encendidos suman **1824 MB**. El uso medido en reposo el 21 de
noviembre fue de unos **0.3 GB** para la pila. Grafana, solo, medía **524 MB**; con
Grafana encendido el conjunto ronda **0.85 GB**.

| Grupo | Servicios (tope en MB) | Contenedores | Suma de topes |
|---|---|---|---|
| Núcleo (sin perfil) | mongodb 384, nrf 48, udr 48, udm 48, ausf 48, pcf 64, nssf 48, amf 96, smf 96, upf 128 | 10 | 1008 MB |
| `ran` | gnb 48, ue 64, dn-iperf 32 | 3 | 144 MB |
| `compresion` | cmp-dn 64, cmp-ue 64, envio 96, envio-eco 96, cmp-gen 48 | 5 | 368 MB |
| `obs` | prometheus 256, pushgateway 48 | 2 | 304 MB |
| **Siempre encendidos** (lo que arranca `lab5g.sh start`) | | **20** | **1824 MB** |
| `grafana` (bajo demanda; con él la suma es 2592 MB) | grafana 768 | 1 | 768 MB |
| `obs-extra` (bajo demanda) | phy-sim 48 | 1 | 48 MB |
| `probes` (bajo demanda) | blackbox 48, iperf-probe 32 | 2 | 80 MB |

La protección real del arranque de Grafana no es la suma de topes. Es la compuerta
`GATE_MEM`: `lab5g.sh grafana up` exige al menos **300 MB** de `MemAvailable` y se
niega si no los hay. Los topes siguen siendo provisionales hasta medir
`memory.peak` con `docker stats`.

### La señal de alarma

```bash
uptime
```

Si la carga (*load average*) supera el número de núcleos de forma sostenida, el
laboratorio está entrando en la espiral descrita en [09 §1](09-arranque-seguro.md#1-por-qué-existe).

**Qué hacer si eso pasa:** el procedimiento completo está en
[09 §10](09-arranque-seguro.md#10-qué-hacer-si-el-host-se-satura). Lo primero,
soltar lo que no hace falta:

```bash
sudo /usr/local/sbin/lab5g.sh grafana down
sudo /usr/local/sbin/lab5g.sh stop cmp-gen cmp-ue cmp-dn envio envio-eco
```

No esperes a que "se estabilice". No lo hace: cuando la carga llega a 100 en 4
núcleos, ni siquiera SSH responde, y la única salida es la consola física.

### Al encender el LXC

`lab5g-stack.service` arranca solo al encender el LXC y levanta la pila por
etapas, con las mismas compuertas. Tarda unos 18.5 minutos. Grafana, las sondas y
`phy-sim` no arrancan solos.

Lo que paraste a mano **no sobrevive a un reinicio**: `/run/lab5g` se vacía y la
pila levanta todo lo gestionado. Si quieres que algo se quede parado, hay que
pararlo después de cada arranque. Además, al encender, el runner de CI puede
actualizarse si estuvo apagado mucho tiempo, y copiar cientos de MB al disco
durante unos minutos (por confirmar).

---

**Anterior:** [03 — Referencia de servicios](03-servicios.md) ·
**Siguiente:** [05 — Diagnóstico](05-diagnostico.md)
