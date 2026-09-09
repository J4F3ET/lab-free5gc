# 03 — Referencia de servicios

La tabla de consulta rápida de [`docker-compose.yaml`](../docker-compose.yaml).
Cuando necesites una IP, un puerto o saber quién depende de quién, es este
documento.

> El fichero de compose es la fuente de la verdad. Si algo aquí no cuadra con
> él, gana el fichero — y avisa, porque este documento tiene un fallo.

---

## Índice

- [1. Los 22 servicios de un vistazo](#1-los-22-servicios-de-un-vistazo)
- [2. Direccionamiento completo](#2-direccionamiento-completo)
- [3. Puertos](#3-puertos)
- [4. Quién depende de quién](#4-quién-depende-de-quién)
- [5. Volúmenes: qué se pierde y qué no](#5-volúmenes-qué-se-pierde-y-qué-no)
- [6. Los permisos especiales, y por qué](#6-los-permisos-especiales-y-por-qué)
- [7. Chequeos de salud](#7-chequeos-de-salud)
- [8. Variables de entorno](#8-variables-de-entorno)

---

## 1. Los 22 servicios de un vistazo

| Servicio | Imagen | Perfil | Redes | Publica | Configuración que monta |
|---|---|---|---|---|---|
| `mongodb` | `mongo:${MONGO_TAG}` | — | sbi | — | — |
| `nrf` | `free5gc/nrf` | — | sbi | — | `config/nrfcfg.yaml` |
| `udr` | `free5gc/udr` | — | sbi | — | `config/udrcfg.yaml` |
| `udm` | `free5gc/udm` | — | sbi | — | `config/udmcfg.yaml` |
| `ausf` | `free5gc/ausf` | — | sbi | — | `config/ausfcfg.yaml` |
| `pcf` | `free5gc/pcf` | — | sbi | — | `config/pcfcfg.yaml` |
| `nssf` | `free5gc/nssf` | — | sbi | — | `config/nssfcfg.yaml` |
| `amf` | `free5gc/amf` | — | sbi | — | `config/amfcfg.yaml` |
| `smf` | `free5gc/smf` | — | sbi | — | `config/smfcfg.yaml` + `uerouting.yaml` |
| `upf` | `free5gc/upf` | — | sbi, n3, dn | — | `config/upfcfg.yaml` + `upf-nat.sh` |
| `gnb` | `free5gc/ueransim` | `ran` | sbi, n3, radio | — | `ueransim/gnb.yaml` |
| `ue` | `free5gc/ueransim` | `ran` | radio, obs | — | `ueransim/ue.yaml` + entrypoint |
| `dn-iperf` | `networkstatic/iperf3` | `ran` | dn | — | — |
| `webui` | `free5gc/webui` | `tools` | sbi | `5000` | `config/webuicfg.yaml` |
| `prometheus` | `prom/prometheus` | `obs` | obs | `9090` | `observability/prometheus/` |
| `grafana` | `grafana/grafana` | `obs` | obs | `3000` | `observability/grafana/provisioning/` |
| `cadvisor` | `gcr.io/cadvisor/cadvisor:v0.49.1` | `obs` | obs | — | monta `/` `/sys` `/var/lib/docker` |
| `node-exporter` | `prom/node-exporter` | `obs` | obs | — | monta `/proc` `/sys` `/` |
| `pushgateway` | `prom/pushgateway` | `obs` | obs | — | — |
| `dozzle` | `amir20/dozzle` | `obs` | obs | `8888` | el socket de Docker |
| `blackbox` | `prom/blackbox-exporter` | `probes` | *netns del `ue`* | — | `observability/blackbox/` |
| `iperf-probe` | `alpine:3.20` | `probes` | *netns del `ue`* | — | `observability/probe/` |

Las etiquetas de imagen salen de [`.env`](../.env.example): `FREE5GC_TAG`,
`UERANSIM_TAG`, `MONGO_TAG`.

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
| `.40` | `mongodb` | Base de datos |
| `.41` | `webui` | Consola de abonados |

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

### obs_net — `10.100.205.0/24`

| IP | Servicio |
|---|---|
| `.10` | `prometheus` |
| `.11` | `grafana` |
| `.12` | `cadvisor` |
| `.13` | `node-exporter` |
| `.14` | `pushgateway` |
| `.15` | `dozzle` |
| `.31` | `ue` — está aquí **solo** para que las sondas sean alcanzables |

### El pool de los teléfonos — `10.60.0.0/16`

No es una red de Docker y no aparece en el compose. La reparte el UPF cuando el
SMF se lo indica. El primer teléfono recibe `10.60.0.1`.

---

## 3. Puertos

### Publicados hacia la LAN

Los únicos accesibles desde fuera del LXC:

| Puerto en `192.168.1.50` | Servicio | Perfil |
|---|---|---|
| `3000` | Grafana | `obs` |
| `5000` | WebUI de free5GC | `tools` |
| `8888` | Dozzle (mapea al 8080 interno) | `obs` |
| `9090` | Prometheus | `obs` |

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
| `9091` | TCP | — | `iperf-probe` → `pushgateway` |

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
MongoDB.

```
mongodb  (service_healthy — se espera de verdad)
   │
   ├── nrf
   │    ├── udr ──► udm
   │    ├── ausf
   │    ├── pcf
   │    ├── nssf
   │    ├── amf   (además: ausf, udm, pcf, nssf)
   │    └── smf   (además: amf, upf)
   │
   └── webui

upf   (sin dependencias: arranca solo)

gnb   ── depende de amf, upf
 └── ue   ── depende de gnb, smf
      ├── blackbox      (network_mode: service:ue)
      └── iperf-probe   (network_mode: service:ue)

prometheus ──► grafana
```

### El orden real de arranque

Por eso el despliegue va **por etapas** y no de una vez:

```
1. mongodb, y se espera a que su chequeo de salud pase   (hasta 600 s)
2. el plano de control completo
3. ci/wait-ready.sh — espera a que se registren y a que PFCP se asocie
4. ci/provision-subscriber.sh — crea la SIM
5. el perfil 'ran'
6. (opcional) los perfiles 'obs' y 'probes'
```

Saltarse la espera del paso 1 fue exactamente lo que dejaba los 9 servicios del
núcleo en estado `created` sin un solo mensaje de error.

---

## 5. Volúmenes: qué se pierde y qué no

| Volumen | Servicio | Contiene | Si lo borras |
|---|---|---|---|
| `mongo-data` | `mongodb` | **Los abonados** | El teléfono deja de registrarse. Recupéralo con `ci/provision-subscriber.sh` |
| `prom-data` | `prometheus` | 15 días de métricas | Pierdes el histórico. Nada deja de funcionar |
| `grafana-data` | `grafana` | Usuarios y cambios hechos a mano en los paneles | El panel provisionado vuelve solo; lo que hayas editado a mano, no |

```bash
docker compose down      # conserva los tres volúmenes
```

```bash
docker compose down -v   # los BORRA. Reprovisiona el abonado después.
```

---

## 6. Los permisos especiales, y por qué

Casi todos los servicios corren sin privilegios. Estos cuatro no, y cada
excepción tiene un motivo concreto:

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

### `cadvisor`

```yaml
privileged: true
volumes: [ /:/rootfs:ro, /var/run:/var/run:rw, /sys:/sys:ro, ... ]
```

Para medir a los demás contenedores necesita ver el sistema de ficheros y los
cgroups del anfitrión. Los montajes son de solo lectura salvo `/var/run`.

### `blackbox` y `iperf-probe`

```yaml
network_mode: "service:ue"
```

No es un permiso, es algo más raro: **comparten la pila de red del `ue`**. Es lo
que hace que sus mediciones pasen por el túnel de verdad y no por la red de
Docker.

Tiene tres consecuencias prácticas:

1. No tienen IP propia — usan las del `ue`
2. No pueden arrancar si el `ue` no existe (`probes` necesita `ran`)
3. Prometheus los alcanza en `10.100.205.31`, que es la IP del `ue` en `obs_net`

---

## 7. Chequeos de salud

Solo MongoDB tiene uno, y su configuración merece explicación porque fue la
causa de un fallo de despliegue difícil de diagnosticar:

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

`start_period: 180s` le da tres minutos de gracia antes de empezar a contar. Los
otros servicios no tienen chequeo: se considera que están listos cuando se
registran en el NRF, y de eso se encarga
[`ci/wait-ready.sh`](../ci/wait-ready.sh), que sí espera de verdad y con un
presupuesto **por comprobación**.

---

## 8. Variables de entorno

Todas viven en `.env` (copia de [`.env.example`](../.env.example)).

| Variable | Por defecto | Para qué |
|---|---|---|
| `FREE5GC_TAG` | `v3.4.4` | Versión de las 9 imágenes de free5GC |
| `UERANSIM_TAG` | `latest` | Versión de la antena y el teléfono |
| `MONGO_TAG` | `6.0` | Versión de MongoDB |
| `MCC` / `MNC` | `732` / `101` | El operador |
| `IMSI` | `732101000000001` | La SIM que se provisiona |
| `SBI_SUBNET` … `OBS_SUBNET` | `10.100.20x.0/24` | Las cinco redes internas |
| `UE_POOL` | `10.60.0.0/16` | Las IP que reparte el UPF |
| `LAN_SUBNET` | `192.168.1.0/24` | Tu red local |
| `LAN_GW` | `192.168.1.1` | Tu router |
| `LXC_IP` | `192.168.1.50` | La IP fija del contenedor |
| `DOCKER_MTU` | `1500` | MTU de las redes de Docker |
| `UE_MTU` | `1464` | **Siempre `DOCKER_MTU - 36`** |
| `GRAFANA_ADMIN_PASSWORD` | `cambiame` | Cámbiala |

> ⚠️ `.env` **no está en el repositorio** y no debe estarlo: lleva la contraseña
> de Grafana. En CI se genera en cada job a partir de un secreto de GitHub.
> Y ojo con esto: `actions/checkout` ejecuta `git clean -ffdx`, que **borra el
> `.env`** por no estar trackeado. Por eso el job de smoke lo regenera.

---

**Anterior:** [02 — Instalación](02-instalacion.md) ·
**Siguiente:** [04 — Operación diaria](04-operacion.md)
