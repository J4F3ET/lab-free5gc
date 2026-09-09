# 04 — Operación diaria

Ya está instalado. Esto es lo que se hace con él.

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
export COMPOSE_PROFILES=ran
```

Sin esto, `docker compose logs ue` responde `no such service` aunque el
contenedor esté corriendo. Los servicios de un perfil inactivo son invisibles
para compose, y es la confusión número uno al operar este laboratorio.

### Arranque completo desde cero

Cuatro comandos, en este orden:

```bash
docker compose up -d --wait --wait-timeout 600 mongodb
```

```bash
docker compose up -d && bash ci/wait-ready.sh
```

```bash
bash ci/provision-subscriber.sh
```

```bash
docker compose --profile ran up -d
```

El primero es el que más sorprende: **espera hasta 10 minutos** a que MongoDB
esté sano antes de continuar. No es paranoia — arrancar el resto antes de tiempo
es lo que dejaba los nueve servicios del núcleo en estado `created`, sin logs y
sin explicación.

### Añadir la observabilidad

Solo cuando el núcleo ya funcione, y solo si te sobran ~2 GB de RAM:

```bash
docker compose --profile obs --profile probes up -d
```

### Parar sin perder nada

```bash
docker compose --profile ran --profile obs --profile probes --profile tools down
```

Hay que nombrar **todos** los perfiles: `down` a secas deja corriendo lo que
pertenece a perfiles no declarados.

### Parar y borrarlo todo

```bash
docker compose --profile ran --profile obs --profile probes --profile tools down -v
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

---

## 2. Ver qué está pasando

### Estado general

```bash
docker compose --profile ran --profile obs --profile probes ps
```

Lo que hay que mirar en la salida:

| Estado | Significa |
|---|---|
| `Up` | Corriendo |
| `Up (healthy)` | Corriendo y su chequeo pasa. Solo MongoDB tiene chequeo |
| `Restarting` | **Crash-loop.** Mira sus logs ya |
| `Created` | Nunca llegó a arrancar. Casi siempre: una dependencia no está sana |
| `Exited (0)` | Terminó bien. En este laboratorio, sospechoso |

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

### Con Dozzle, en el navegador

`http://192.168.1.50:8888` (necesita el perfil `obs`).

Es más cómodo cuando hay que seguir varios servicios a la vez: permite filtrar
por texto en vivo y ver dos contenedores en paralelo sin partir la terminal.

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

`http://192.168.1.50:3000` — usuario `admin`, contraseña la de tu `.env`.

El panel **lab5g-overview** ya está montado: no hay que importar nada ni
configurar la fuente de datos. Aparece solo al arrancar Grafana, porque el
directorio `observability/grafana/provisioning/` se monta dentro del contenedor.

### Cómo leerlo

**Fila 1 — Plano de control.** Un cuadro por pieza del núcleo, verde o rojo. Se
alimenta de `probe_success`, que es una sonda TCP: comprueba que el puerto
responde. Rojo aquí significa "ese servicio no acepta conexiones".

**Fila 2 — Plano de usuario.** La parte valiosa:

- `ue_tunnel_up` — ¿existe `uesimtun0`? Es 1 o 0
- Latencia **medida desde dentro del teléfono**, atravesando el túnel de verdad
- Velocidad de bajada y subida, en escalones: la sonda mide cada 5 minutos, así
  que la línea es escalonada por diseño y no por un fallo

**Fila 3 — Recursos.** CPU y memoria por contenedor, más el **iowait** del LXC.

> Ese último panel es el más útil del conjunto y el menos obvio. En este tipo de
> host el cuello de botella casi nunca es la CPU: es el disco. Cuando el iowait
> sube, los chequeos de salud empiezan a expirar y los servicios parecen caídos
> sin tener nada roto. Si ves timeouts que no se explican, mira ahí antes que en
> ningún otro sitio.

### Si un panel dice "Datasource not found"

La fuente de datos tiene un `uid` fijado a `prometheus` en
[`observability/grafana/provisioning/datasources/prometheus.yml`](../observability/grafana/provisioning/datasources/prometheus.yml).
Sin ese `uid` explícito, Grafana genera uno aleatorio en cada arranque y los
paneles provisionados dejan de encontrarlo. Si tocas ese fichero, mantén el
`uid`.

---

## 5. Consultas útiles de Prometheus

Para escribirlas directamente en `http://192.168.1.50:9090` o en un panel nuevo.

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

CPU del UPF — aquí se ve si `gtp5g` es el cuello de botella:

```promql
rate(container_cpu_usage_seconds_total{name="upf"}[2m])
```

Alguna pieza del núcleo caída:

```promql
probe_success{plane="control"} == 0
```

**iowait del LXC**, que es la métrica que más veces explica un fallo raro:

```promql
rate(node_cpu_seconds_total{mode="iowait"}[5m])
```

Memoria por contenedor, de mayor a menor:

```promql
topk(5, container_memory_working_set_bytes{name!=""})
```

Un truco que vale la pena: superpón en el mismo panel la **latencia del teléfono**
y la **CPU del UPF**. Si suben juntas, el problema es de capacidad. Si la
latencia sube sola, es de red.

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

### Con la consola web

```bash
docker compose --profile tools up -d webui
```

Y entra en `http://192.168.1.50:5000`. Es más cómoda para explorar, pero
innecesaria para el flujo normal.

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
no son negociables. Cambiar `DOCKER_MTU` obliga a recrear las redes:

```bash
docker compose --profile ran down && docker compose up -d && docker compose --profile ran up -d
```

---

## 9. Vigilar los recursos

### Lo que consume cada cosa, ahora mismo

```bash
docker stats --no-stream
```

### La regla práctica

| Perfil | Contenedores | RAM aproximada |
|---|---|---|
| núcleo | 10 | ~1.5 GB |
| + `ran` | 13 | ~1.8 GB |
| + `obs` | 19 | ~2.8 GB |
| + `probes` | 21 | ~2.9 GB |
| + `tools` | 22 | ~3.0 GB |

Son cifras de referencia en reposo. Bajo tráfico el UPF sube bastante.

### La señal de alarma

```bash
uptime
```

Si la carga (*load average*) supera el número de núcleos de forma sostenida, el
laboratorio está entrando en la espiral descrita en el README: los servicios se
reinician, cada reinicio consume el disco que los demás necesitan, y la carga
crece sola.

**Qué hacer si eso pasa:** bajar de perfil inmediatamente.

```bash
docker compose --profile obs --profile probes stop
```

No esperes a que "se estabilice". No lo hace: cuando la carga llega a 100 en 4
núcleos, ni siquiera SSH responde, y la única salida es la consola física.

---

**Anterior:** [03 — Referencia de servicios](03-servicios.md) ·
**Siguiente:** [05 — Diagnóstico](05-diagnostico.md)
