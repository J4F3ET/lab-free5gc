# 03 — Referencia de servicios

La tabla de consulta rápida de [`docker-compose.yaml`](../docker-compose.yaml).
Cuando necesites una IP, un puerto o saber quién depende de quién, es este
documento.

> El fichero de compose es la fuente de la verdad. Si algo aquí no cuadra con
> él, gana el fichero — y avisa, porque este documento tiene un fallo.

---

## Índice

- [1. Los 24 servicios de un vistazo](#1-los-24-servicios-de-un-vistazo)
- [2. Direccionamiento completo](#2-direccionamiento-completo)
- [3. Puertos](#3-puertos)
- [4. Quién depende de quién](#4-quién-depende-de-quién)
- [5. Volúmenes: qué se pierde y qué no](#5-volúmenes-qué-se-pierde-y-qué-no)
- [6. Los permisos especiales, y por qué](#6-los-permisos-especiales-y-por-qué)
- [7. Chequeos de salud](#7-chequeos-de-salud)
- [8. Variables de entorno](#8-variables-de-entorno)

---

## 1. Los 24 servicios de un vistazo

La columna **Perfil** dice cuándo se enciende. `(núcleo)` significa sin perfil:
siempre está en el compose. El resto sigue los perfiles de
[`docker-compose.yaml`](../docker-compose.yaml). Quién los arranca y en qué
orden está en [09 — Arranque seguro](09-arranque-seguro.md).

Los 24 servicios definidos son 20 siempre encendidos (núcleo 10, `ran` 3,
`compresion` 5, `obs` 2) y 4 bajo demanda (`grafana`, `blackbox`, `phy-sim`,
`iperf-probe`).

| Servicio | Imagen | Perfil | Redes | Publica | Configuración que monta |
|---|---|---|---|---|---|
| `mongodb` | `mongo:${MONGO_TAG}` | (núcleo) | sbi | — | — |
| `nrf` | `free5gc/nrf:${FREE5GC_TAG}` | (núcleo) | sbi | — | `config/nrfcfg.yaml` |
| `udr` | `free5gc/udr` | (núcleo) | sbi | — | `config/udrcfg.yaml` |
| `udm` | `free5gc/udm` | (núcleo) | sbi | — | `config/udmcfg.yaml` |
| `ausf` | `free5gc/ausf` | (núcleo) | sbi | — | `config/ausfcfg.yaml` |
| `pcf` | `free5gc/pcf` | (núcleo) | sbi | — | `config/pcfcfg.yaml` |
| `nssf` | `free5gc/nssf` | (núcleo) | sbi | — | `config/nssfcfg.yaml` |
| `amf` | `free5gc/amf` | (núcleo) | sbi | — | `config/amfcfg.yaml` |
| `smf` | `free5gc/smf` | (núcleo) | sbi | — | `config/smfcfg.yaml` + `uerouting.yaml` |
| `upf` | `free5gc/upf` | (núcleo) | sbi, n3, dn | — | `config/upfcfg.yaml` + `upf-nat.sh` |
| `gnb` | `free5gc/ueransim` | `ran` | sbi, n3, radio | — | `ueransim/gnb.yaml` |
| `ue` | `free5gc/ueransim` | `ran` | sbi, radio, obs | — | `ueransim/ue.yaml` + `ue-entrypoint.sh` |
| `dn-iperf` | `networkstatic/iperf3` | `ran` | dn | — | — |
| `cmp-dn` | `python:3.14-alpine` | `compresion` | dn, obs | `8088` (página de `envio`) | `compresion/` (solo lectura) + `/dev/net/tun` |
| `cmp-ue` | `python:3.14-alpine` | `compresion` | *netns de `ue`* | — | `compresion/` + `/dev/net/tun` |
| `envio` | `python:3.14-alpine` | `compresion` | *netns de `cmp-dn`* | — (lo publica `cmp-dn`) | `compresion/` |
| `envio-eco` | `python:3.14-alpine` | `compresion` | *netns de `ue`* | — | `compresion/` |
| `cmp-gen` | `python:3.14-alpine` | `compresion` | *netns de `ue`* | — | `compresion/` (generador de tráfico) |
| `prometheus` | `prom/prometheus:latest` | `obs` | obs | `9090` | `observability/prometheus/` |
| `pushgateway` | `prom/pushgateway:latest` | `obs` | obs | — | volumen `pushgw-data` |
| `grafana` | `grafana/grafana:latest` | `grafana` (bajo demanda) | obs | `3000` | `observability/grafana/provisioning/` |
| `phy-sim` | `python:3.12-alpine` | `obs-extra` | obs | — | `observability/probe/phy_sim_exporter.py` |
| `blackbox` | `prom/blackbox-exporter:latest` | `probes` | *netns de `ue`* | — | `observability/blackbox/` |
| `iperf-probe` | `alpine:3.20` | `probes` | *netns de `ue`* | — | `observability/probe/` |

Notas:

- Las etiquetas de imagen salen de `.env` (copia de [`.env.example`](../.env.example)):
  `FREE5GC_TAG`, `UERANSIM_TAG`, `MONGO_TAG`.
- `prometheus`, `grafana`, `blackbox` y `pushgateway` usan `latest`. El compose
  lo marca como pendiente de fijar.
- `dn-iperf` usa `networkstatic/iperf3` y el compose pide verificar el tag antes
  de usarlo.

---

## 2. Direccionamiento completo

Todas las IP son **fijas**. Varias configuraciones se refieren a otra pieza por
su dirección, así que no pueden cambiar entre arranques.

### sbi_net — `10.100.200.0/24`

La "oficina": el plano de control entero.

| IP | Servicio | Qué hace en esta red |
|---|---|---|
| `.10` | `nrf` | El directorio |
| `.11` | `amf` | SBI + **N2** hacia la antena |
| `.12` | `smf` | SBI + **N4** hacia el UPF |
| `.13` | `ausf` | SBI |
| `.14` | `udm` | SBI |
| `.15` | `udr` | SBI |
| `.16` | `pcf` | SBI |
| `.17` | `nssf` | SBI |
| `.20` | `upf` | Solo **N4**: recibe órdenes del SMF |
| `.30` | `gnb` | Solo **N2**: habla con el AMF |
| `.31` | `ue` | Solo para que `blackbox` alcance el SBI (sondas TCP) |
| `.40` | `mongodb` | Base de datos |

### n3_net — `10.100.201.0/24`

El túnel de datos, aislado a propósito para poder capturarlo limpio.

| IP | Servicio |
|---|---|
| `.20` | `upf` |
| `.30` | `gnb` |

### radio_net — `10.100.203.0/24`

La radio simulada. UERANSIM la implementa con UDP en el puerto 4997.

| IP | Servicio |
|---|---|
| `.30` | `gnb` — es el `linkIp` que el UE busca |
| `.31` | `ue` |

### dn_net — `10.100.204.0/24`

El destino del tráfico. Hace de "internet".

| IP | Servicio |
|---|---|
| `.10` | `dn-iperf` — el objetivo de los pings y las medidas |
| `.20` | `upf` — por aquí saca el tráfico, con NAT |
| `.30` | `cmp-dn` — el descompresor; recibe el túnel en UDP 7700 |

### obs_net — `10.100.205.0/24`

| IP | Servicio |
|---|---|
| `.10` | `prometheus` |
| `.11` | `grafana` |
| `.14` | `pushgateway` |
| `.16` | `phy-sim` |
| `.31` | `ue` — está aquí **solo** para que las sondas sean alcanzables |
| `.32` | `cmp-dn` — solo para publicar `lab5g_cmp_*` en el pushgateway |

### El pool de los teléfonos — `10.60.0.0/16`

No es una red de Docker y no aparece en el compose. La reparte el UPF cuando el
SMF se lo indica. El primer teléfono recibe `10.60.0.1`.

### El túnel del compresor — `10.99.0.0/30`

Tampoco es de Docker. Lo crean `cmp-ue` (`cmp0` = `10.99.0.1`, dentro del `ue`) y
`cmp-dn` (`cmp0` = `10.99.0.2`). Lo que entra por `cmp0` sale comprimido como UDP
7700 entre `10.60.0.1` y `10.100.204.30`, así que cruza la sesión PDU entera.

`cmp-ue` tiene que mandar **desde la IP de la sesión** (`10.60.0.1`), no desde
`0.0.0.0`: UERANSIM solo enruta por `uesimtun0` lo que sale con esa IP
(`ip rule from 10.60.0.1 lookup rt_uesimtun0`). Lo demás se va por `eth0` y no
pasa por el 5G. Lo mismo vale para cualquier prueba "directa" hecha desde el
`ue`, y por eso los `ping` de este manual llevan `-I uesimtun0`.

---

## 3. Puertos

### Publicados hacia la LAN

Los únicos accesibles desde fuera del LXC:

| Puerto en `192.168.1.50` | Servicio | Perfil | Notas |
|---|---|---|---|
| `3000` | Grafana | `grafana` | Bajo demanda: `lab5g.sh grafana up` |
| `9090` | Prometheus | `obs` | Tiene la API de administración activa (borrado y snapshot). **Solo LAN, nunca en internet ni en funnel** |
| `8088` | Página de envío (`envio`, la sirve `cmp-dn`) | `compresion` | Sin login. **Solo LAN y tailnet**, nunca en funnel |

La página de envío se usa desde la LAN en `http://192.168.1.50:8088`. Llega por el
DNAT de Docker al contenedor `cmp-dn` (`10.100.204.30`). La regla DOCKER-USER de
`deploy/lab5g-netrules.sh` no la bloquea. **No** hay que añadir `10.100.204.0/24`
a esas reglas: expondría la red de datos y el UPF. Por verificar en el servidor
(ver [09, sección 12](09-arranque-seguro.md#12-pendientes-y-cosas-por-confirmar)).

### Internos

No están publicados. Se usan entre contenedores, y son los que hay que conocer
para capturar tráfico:

| Puerto | Protocolo | Interfaz | Entre |
|---|---|---|---|
| `8000` | TCP (HTTP/2) | SBI | todas las piezas del núcleo |
| `38412` | **SCTP** | N2 | `gnb` ↔ `amf` |
| `8805` | UDP | N4 | `smf` ↔ `upf` |
| `2152` | UDP | N3 | `gnb` ↔ `upf` |
| `4997` | UDP | radio | `ue` ↔ `gnb` |
| `27017` | TCP | — | `udr` → `mongodb` |
| `5201` | TCP/UDP | — | `iperf-probe` → `dn-iperf` |
| `7700` | UDP | túnel del compresor | `cmp-ue` ↔ `cmp-dn` (dentro de la sesión PDU) |
| `9091` | TCP | — | `iperf-probe`, `cmp-ue`, `cmp-dn`, `phy-sim` → `pushgateway` |

Para capturar, dentro del contenedor correspondiente:

```bash
docker compose exec upf tcpdump -i any -n udp port 8805   # N4, las órdenes
```

```bash
docker compose exec gnb tcpdump -i any -n udp port 2152   # N3, los datos
```

---

## 4. Quién depende de quién

`depends_on` en Docker solo espera a que el contenedor **arranque**, no a que
esté listo — salvo cuando se usa `condition: service_healthy`, que es el caso de
MongoDB. Desde la retirada de `restart: unless-stopped`, el orden real de
arranque lo fija `lab5g-stack` (ver abajo).

```
mongodb  (service_healthy — se espera de verdad)
   │
   └── nrf
        ├── udr ──► udm
        ├── ausf
        ├── pcf
        ├── nssf
        ├── amf   (además: ausf, udm, pcf, nssf)
        └── smf   (además: amf, upf)

upf   (sin dependencias: arranca solo)

gnb   ── depende de amf, upf
 └── ue   ── depende de gnb, smf
      ├── cmp-ue       (netns del ue; depende de ue, cmp-dn)
      │    ├── envio-eco   (netns del ue)
      │    └── cmp-gen     (netns del ue)
      ├── blackbox      (network_mode: service:ue)     [perfil probes]
      └── iperf-probe   (network_mode: service:ue)     [perfil probes]

cmp-dn ── envio   (netns de cmp-dn)

prometheus ··► grafana   (sin depends_on: grafana tolera un datasource caído)
```

### El orden real de arranque

`lab5g-stack` arranca en 14 etapas, una a una y con compuertas de carga. La
lista completa y los criterios de salud están en
[09 — Arranque seguro](09-arranque-seguro.md#3-el-arranque-por-etapas).

Tras la pila, el job `deploy` del CI ejecuta además:

1. `ci/wait-ready.sh` — espera a que se registren y a que PFCP se asocie.
2. `ci/provision-subscriber.sh` — crea la SIM en MongoDB.

Saltarse la espera de MongoDB fue exactamente lo que dejaba los nueve servicios
del núcleo en estado `created` sin un solo mensaje de error. Por eso la etapa 1
es `mongodb` y espera a su chequeo de salud.

---

## 5. Volúmenes: qué se pierde y qué no

| Volumen | Servicio | Contiene | Si lo borras |
|---|---|---|---|
| `mongo-data` | `mongodb` | **Los abonados** | El teléfono deja de registrarse. Recupéralo con `ci/provision-subscriber.sh` |
| `prom-data` | `prometheus` | Métricas, con retención de 90 días o 3 GB (lo que llegue antes) | Pierdes el histórico. Nada deja de funcionar. Hay copia diaria: ver [09 §8](09-arranque-seguro.md#8-persistencia-y-copias) |
| `grafana-data` | `grafana` | Usuarios y cambios hechos a mano en los paneles | El panel provisionado vuelve solo; lo que hayas editado a mano, no. Sin copia |
| `pushgw-data` | `pushgateway` | Las últimas métricas empujadas (archivo de persistencia, escritura cada minuto) | Las métricas de sondas y compresión se recrean en el siguiente empuje |

Con la pila en marcha, un `docker compose down` **no es definitivo**: la
recuperación de `lab5g-stack` vuelve a crear lo que encuentre caído. Para bajar
todo de forma que se quede abajo, primero `sudo /usr/local/sbin/lab5g.sh stop`.

```bash
docker compose down      # conserva los volúmenes
```

```bash
docker compose down -v   # los BORRA. Reprovisiona el abonado después.
```

---

## 6. Los permisos especiales, y por qué

Solo `upf` es `privileged`. Los demás añaden capacidades mínimas cuando crean una
interfaz, y `pushgateway` corre como root para poder escribir su volumen.

### `upf` — el más privilegiado

```yaml
cap_add: [ NET_ADMIN, SYS_MODULE ]
privileged: true
devices: [ "/dev/net/tun:/dev/net/tun" ]
sysctls:
  net.ipv4.ip_forward: "1"
  net.ipv4.conf.all.rp_filter: "2"
```

Crea una interfaz de red **dentro del kernel del host** (`upfgtp`) hablando con
el módulo `gtp5g` por netlink. Sin estos permisos falla al arrancar con
`operation not permitted`.

`rp_filter: 2` (modo laxo) es necesario porque el tráfico entra por una interfaz
y sale por otra distinta. Con el modo estricto (`1`), el kernel descartaría en
silencio los paquetes de vuelta — el clásico "el ping sale pero no vuelve".

### `gnb` y `ue`

```yaml
cap_add: [ NET_ADMIN ]
devices: [ "/dev/net/tun:/dev/net/tun" ]
```

El `ue` crea `uesimtun0`, que es una interfaz TUN. De ahí el device.

### `blackbox`, `iperf-probe`, `cmp-ue`, `cmp-gen`, `envio-eco` y `envio`

```yaml
network_mode: "service:ue"
```

No es un permiso, es algo más raro: **comparten la pila de red de otro
contenedor**. `blackbox`, `iperf-probe`, `cmp-ue`, `cmp-gen` y `envio-eco` comparten
la del `ue`; `envio` comparte la de `cmp-dn`. Es lo que hace que sus mediciones
pasen por el túnel de verdad y no por la red de Docker.

Tiene tres consecuencias prácticas:

1. No tienen IP propia — usan las del contenedor al que se unen
2. No pueden arrancar si ese contenedor no existe (`probes` y `compresion` necesitan `ran`)
3. Prometheus alcanza las sondas en `10.100.205.31`, que es la IP del `ue` en `obs_net`

`cmp-ue` y `cmp-dn` necesitan además `cap_add: NET_ADMIN` y `/dev/net/tun`
para crear la interfaz `cmp0`. No son `privileged`.

---

## 7. Chequeos de salud

Solo MongoDB tiene un chequeo de Docker. Su configuración merece explicación
porque fue la causa de un fallo de despliegue difícil de diagnosticar:

```yaml
healthcheck:
  test: ["CMD","mongosh","--quiet","--eval","db.adminCommand('ping')"]
  interval: 10s
  timeout: 10s
  start_period: 180s
  retries: 20
```

`start_period` es la clave. **Sin él, Docker empieza a contar fallos desde el
segundo cero.** Con `interval: 10s` y `retries: 12`, el presupuesto total eran
120 segundos — y en este hardware `mongod` tarda unos **90 s solo en arrancar**.
El chequeo expiraba, compose abortaba el `up`, y los nueve servicios que
dependen de él se quedaban en estado `created` **sin producir una sola línea de
log**.

`start_period: 180s` le da tres minutos de gracia antes de empezar a contar.

Los demás servicios no tienen chequeo en el compose. Lo que sí comprueba
`lab5g-stack` es:

- `nrf`, `amf` y `smf`: TCP al puerto 8000 de su IP en `sbi_net`.
- `ue`: que exista `uesimtun0` con dirección.
- Los demás: que estén `running`.

El detalle está en [09 §5](09-arranque-seguro.md#5-cuándo-se-considera-sano-un-servicio).
[`ci/wait-ready.sh`](../ci/wait-ready.sh) espera además al registro en el NRF,
con un presupuesto **por comprobación**.

---

## 8. Variables de entorno

En el LXC, las variables viven en `/etc/lab5g/lab5g.env` (modo `640 root:deploy`,
fuera del repositorio). Se crea desde [`.env.example`](../.env.example) al ejecutar
`deploy/bootstrap-runner.sh`. Para trabajar en local, la copia es `.env`.

| Variable | Por defecto | Para qué |
|---|---|---|
| `FREE5GC_TAG` | `v3.4.4` | Versión de las imágenes de free5GC |
| `UERANSIM_TAG` | `latest` | Versión de la antena y el teléfono |
| `MONGO_TAG` | `6.0` | Versión de MongoDB |
| `MCC` / `MNC` | `732` / `101` | El operador |
| `IMSI` | `732101000000001` | La SIM que se provisiona |
| `SBI_SUBNET` … `OBS_SUBNET` | `10.100.20x.0/24` | Subredes de referencia. Por confirmar: el compose fija las redes con valores literales |
| `UE_POOL` | `10.60.0.0/16` | Las IP que reparte el UPF |
| `DN_SUBNET` | `10.100.204.0/24` | Subred de la DN, que recibe el contenedor `ue` |
| `LAN_SUBNET` | `192.168.1.0/24` | Tu red local |
| `LAN_GW` | `192.168.1.1` | Tu router |
| `LXC_IP` | `192.168.1.50` | La IP fija del contenedor |
| `DOCKER_MTU` | `1500` | MTU de las redes de Docker |
| `UE_MTU` | `1464` | **Siempre `DOCKER_MTU - 36`** |
| `GRAFANA_ADMIN_PASSWORD` | valor de ejemplo | Cámbiala. En el LXC: `lab5g.sh env-set GRAFANA_ADMIN_PASSWORD` |
| `VELOCIDAD_ENLACE_BPS`, `PERDIDA_IOT` | vacías | Opcionales, del perfil `compresion`. Vacías = sin límite de enlace y sin pérdida |

> ⚠️ `lab5g.env` y `.env` **no están en el repositorio** y no deben estarlo: llevan
> la contraseña de Grafana. En el pipeline, el secreto `GRAFANA_ADMIN_PASSWORD`
> entra por stdin en `lab5g.sh env-set` y se escribe en `/etc/lab5g/lab5g.env`.
> Como ese archivo está fuera del checkout, el `git clean -ffdx` de
> `actions/checkout` no lo toca.

---

**Anterior:** [02 — Instalación](02-instalacion.md) ·
**Siguiente:** [04 — Operación diaria](04-operacion.md)
