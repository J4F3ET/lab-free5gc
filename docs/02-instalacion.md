# 02 — Instalación detallada

La versión larga de los seis pasos del README, con el **por qué** de cada
decisión y qué error concreto aparece cuando algo falta.

Está ordenada por **capas de dependencia**. Cada capa se valida antes de pasar a
la siguiente, y ese orden no es una formalidad:

> La causa raíz de la mayoría de fallos en este laboratorio es intentar depurar
> la capa 6 (el túnel del teléfono) cuando el problema real está en la capa 0
> (un módulo de kernel).

| Capa | Qué es | Criterio de éxito |
|---|---|---|
| 0 | Host: kernel, `gtp5g`, `sctp` | `lsmod` muestra los dos módulos |
| 1 | El contenedor LXC | `/dev/net/tun` accesible, nesting activo |
| 2 | Docker y sus redes | Los contenedores arrancan |
| 3 | Acceso desde la LAN | Grafana se ve desde tu portátil |
| 4 | El núcleo free5GC | Asociación PFCP establecida |
| 5 | La antena | `NG Setup Response` recibido |
| 6 | El teléfono | `uesimtun0` con IP y con `ping` |
| 7 | Observabilidad | Métricas visibles en Grafana |

---

## Capa 0 — El host Proxmox

### El problema de fondo con `gtp5g`

El UPF de free5GC **no** hace la encapsulación de datos en espacio de usuario:
delega en un módulo de kernel llamado `gtp5g`, que crea un dispositivo de red
llamado `upfgtp`.

Y aquí está el detalle que hay que interiorizar: **un contenedor LXC comparte el
kernel del anfitrión**. No tiene uno propio. Por tanto el módulo se compila y se
carga en el host Proxmox, aunque quien lo use viva dentro del contenedor.

Dos consecuencias que rompen el laboratorio si se ignoran:

**1. La versión de `gtp5g` debe ser compatible con la de free5GC.** Una
demasiado nueva o demasiado vieja produce errores de netlink que no explican
nada. Ancla la rama de `gtp5g` a la que documente el release de free5GC que uses.

> ⚠️ Verifica la matriz de compatibilidad en el repositorio de free5GC antes de
> compilar. Los tags de este documento (`v0.9.0`, `v3.4.4`) son los que usa este
> laboratorio, no una recomendación universal.

**2. Una actualización de kernel deja el módulo huérfano.** Tras un `apt upgrade`
y un reinicio, el UPF deja de arrancar sin explicación aparente y sin que nadie
haya tocado nada. La solución no es recompilar a mano cada vez: es **DKMS**, que
lo recompila automáticamente con cada kernel nuevo.

### Instalación con DKMS

```bash
apt update && apt install -y git build-essential dkms pve-headers-$(uname -r)
```

```bash
cd /usr/src && git clone https://github.com/free5gc/gtp5g.git gtp5g-0.9.0 && cd gtp5g-0.9.0 && git checkout v0.9.0
```

Si el repositorio no trae `dkms.conf`, créalo:

```bash
cat > /usr/src/gtp5g-0.9.0/dkms.conf <<'EOF'
PACKAGE_NAME="gtp5g"
PACKAGE_VERSION="0.9.0"
BUILT_MODULE_NAME[0]="gtp5g"
DEST_MODULE_LOCATION[0]="/updates/dkms"
AUTOINSTALL="yes"
EOF
```

```bash
dkms add -m gtp5g -v 0.9.0 && dkms build -m gtp5g -v 0.9.0 && dkms install -m gtp5g -v 0.9.0
```

Si el `Makefile` de `gtp5g` no encaja limpio con DKMS en tu versión, la
alternativa es `make && make install` — pero entonces **añade un hook de arranque
que verifique el módulo y falle ruidosamente**, o el próximo upgrade de kernel te
costará una tarde.

### Carga persistente

```bash
printf 'gtp5g\nsctp\n' > /etc/modules-load.d/free5gc.conf && modprobe gtp5g && modprobe sctp
```

**Sobre `sctp`:** la interfaz N2, entre la antena y el AMF, no usa TCP. Usa SCTP,
que es otro protocolo con su propio módulo de kernel. Si falta, el `bind()` del
AMF falla dentro del contenedor con un `protocol not supported` que despista
muchísimo, porque el mensaje apunta al AMF y el problema está en el host.

### Forwarding en el host

```bash
cat > /etc/sysctl.d/99-free5gc-host.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.conf.all.rp_filter = 2
EOF
```

```bash
sysctl --system
```

**Sobre `rp_filter = 2`:** el *reverse path filter* comprueba que un paquete
entrante llega por la interfaz por la que se le respondería. En modo estricto
(`1`), con varias redes de Docker y rutas asimétricas, descarta **en silencio**
paquetes perfectamente válidos. El modo laxo (`2`) sigue protegiendo contra
suplantación pero acepta esas rutas. Es una causa clásica de "el ping sale pero
no vuelve".

### ✅ Verificación de la capa 0

Las cuatro deben dar salida no vacía:

```bash
lsmod | grep gtp5g && lsmod | grep sctp && sysctl net.ipv4.ip_forward && ls -l /dev/net/tun
```

---

## Capa 1 — El contenedor LXC

### El fichero de configuración, línea por línea

Edita `/etc/pve/lxc/110.conf` con el contenedor **detenido**:

```ini
arch: amd64
cores: 4
features: nesting=1,keyctl=1
hostname: lab-free5gc
memory: 8192
swap: 2048
ostype: ubuntu
rootfs: local-lvm:vm-110-disk-0,size=64G
unprivileged: 0
net0: name=eth0,bridge=vmbr0,firewall=0,gw=192.168.1.1,ip=192.168.1.50/24,type=veth
onboot: 1

lxc.apparmor.profile: unconfined
lxc.cap.drop:
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net dev/net none bind,create=dir
lxc.mount.auto: proc:rw sys:rw cgroup:rw
```

| Línea | Qué hace | Qué pasa si falta |
|---|---|---|
| `unprivileged: 0` | Contenedor privilegiado | El UPF **no puede** crear `upfgtp` ni UERANSIM `uesimtun0`, aunque el módulo esté cargado. El mapeo de UIDs impide la operación de netlink. No hay atajo. |
| `features: nesting=1` | Permite Docker dentro | `dockerd` no arranca |
| `features: keyctl=1` | Acceso al keyring | Docker arranca, pero falla de forma **intermitente** al montar volúmenes — el peor tipo de fallo |
| `firewall=0` | Desactiva el firewall de Proxmox en esa interfaz | El firewall descarta SCTP y GTP-U por defecto en muchas configuraciones. Si lo quieres activo, abre `38412/sctp`, `2152/udp` y `8805/udp` a mano |
| `lxc.apparmor.profile: unconfined` | Sin confinamiento AppArmor | `permission denied` en operaciones que las capabilities **sí** permiten. Es el fallo más confuso de todos, porque `capsh --print` muestra los permisos presentes y aun así falla |
| `lxc.cap.drop:` (vacío) | Conserva todas las capabilities | Faltan `NET_ADMIN`, `SYS_MODULE`, `SYS_ADMIN` |
| `lxc.cgroup2.devices.allow: c 10:200 rwm` | Permite `/dev/net/tun` (major 10, minor 200) | No se pueden crear interfaces TUN |
| `lxc.mount.entry: /dev/net …` | Monta el directorio | `/dev/net/tun` no existe dentro |
| `lxc.mount.auto: proc:rw sys:rw cgroup:rw` | Montajes de kernel escribibles | Docker y `gtp5g` necesitan `sysfs` en lectura-escritura |

### Sobre la memoria

`memory: 8192` es lo recomendable. Con menos, el laboratorio **arranca igual**
pero solo en su perfil reducido — y ese es el motivo de que existan los perfiles.

> Comprueba la suma de la memoria asignada a **todos** los contenedores del host
> antes de aumentar este número. Sobreasignar memoria en Proxmox no da un error:
> simplemente el host empieza a usar swap y todo se vuelve lentísimo, incluida
> la interfaz web con la que intentarías arreglarlo.

### ✅ Verificación de la capa 1

```bash
pct start 110 && pct exec 110 -- bash -c 'ls -l /dev/net/tun && grep gtp5g /proc/modules'
```

Ese `/proc/modules` es **el del host**, visto desde dentro. Que aparezca `gtp5g`
confirma las dos cosas a la vez: el módulo está cargado y el contenedor es
privilegiado.

Si sale vacío, **no sigas**. Todo lo demás fallará de forma engañosa.

Algunas versiones de `gtp5g` exponen además un dispositivo de carácter.
Compruébalo en el host con `ls -l /dev/gtp5g`; si existe, añade su `major:minor`
al `lxc.cgroup2.devices.allow` igual que se hizo con `/dev/net`.

---

## Capa 2 — Docker dentro del contenedor

```bash
curl -fsSL https://get.docker.com | sh && systemctl enable --now docker
```

### El daemon

```bash
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "3" },
  "default-address-pools": [ { "base": "10.100.0.0/16", "size": 24 } ]
}
EOF
```

```bash
systemctl restart docker
```

**`log-opts`** evita que los logs llenen el disco. free5GC es muy hablador en
modo depuración, y un disco lleno en este laboratorio se manifiesta como
chequeos de salud que expiran — otra vez, un síntoma que no señala su causa.

**`default-address-pools`** es más importante de lo que parece. Por defecto
Docker se inventa redes en `172.17-31.x.x` y luego en **`192.168.x.x`**, que es
justo donde vive tu red doméstica. Cuando Docker se apropia de un rango que
solapa con tu LAN, el contenedor pierde el acceso a la red local sin ningún
mensaje. Esta línea lo confina a `10.100.x.x`.

> **Nota histórica:** en versiones anteriores este fichero llevaba `"mtu": 1400`.
> Era por Tailscale, cuyo MTU es 1280. Con acceso por LAN directa el camino son
> 1500 bytes completos, y el MTU se controla desde `.env` (`DOCKER_MTU`) en vez
> de aquí.

### Sysctl dentro del contenedor

```bash
cat > /etc/sysctl.d/99-free5gc.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
net.core.rmem_max = 26214400
net.core.wmem_max = 26214400
EOF
```

```bash
sysctl --system
```

Los `rmem_max` / `wmem_max` amplían los buffers de red. Sin ellos, las
transferencias grandes por el túnel pierden paquetes bajo carga.

### ✅ Verificación de la capa 2

```bash
docker run --rm hello-world
```

---

## Capa 3 — Acceso desde tu red local

### Por qué hace falta hacer algo

Docker no se limita a crear redes: también **instala reglas de cortafuegos**.
Una de ellas hace que el tráfico que viene de fuera del host hacia las redes
internas de Docker se descarte por defecto, en una cadena llamada `DOCKER-USER`.

Es una protección razonable. Pero aquí significa que, desde tu portátil, no
llegas a Grafana ni a la consola web aunque los contenedores estén perfectos.

### La solución del repositorio

[`deploy/lab5g-netrules.sh`](../deploy/lab5g-netrules.sh) abre exactamente tres
subredes y nada más:

```
10.100.200.0/24    para llegar a las piezas del núcleo (diagnóstico)
10.100.205.0/24    para Grafana, Prometheus y Dozzle
10.60.0.0/16       para alcanzar a los teléfonos desde la LAN
```

Las reglas son **idempotentes**: el script comprueba con `iptables -C` antes de
insertar, así que se puede ejecutar cien veces sin duplicar nada.

```bash
install -m 755 deploy/lab5g-netrules.sh /usr/local/sbin/lab5g-netrules.sh
```

```bash
install -m 644 deploy/lab5g-netrules.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now lab5g-netrules.service
```

**El servicio de systemd no es opcional.** Las reglas de iptables viven en
memoria: se pierden al reiniciar y también cada vez que Docker recrea sus
cadenas. Sin el servicio, el laboratorio funciona hoy y deja de funcionar mañana
sin que nadie haya tocado nada.

### Si el contenedor no responde en su IP

Tres cosas que han pasado de verdad en este laboratorio, en orden de frecuencia:

1. **Proxmox añadió la IP estática como secundaria** y el contenedor sigue
   respondiendo en la vieja. Compruébalo con `ip -brief addr show eth0`: si ves
   dos direcciones, quita la vieja con `ip addr del`.
2. **`dhclient` sigue reteniendo la dirección antigua.** `dhclient -r` no siempre
   la suelta; hace falta `ip addr del` explícito.
3. **Otra máquina ya tiene esa IP.** Compruébalo desde el host con
   `arping -D -I vmbr0 192.168.1.50` antes de asignarla.

> Si el contenedor tiene Tailscale instalado de una configuración anterior,
> revisa también sus reglas de enrutamiento: con `--accept-routes` activo puede
> capturar el tráfico hacia tu propia LAN y enviarlo por el túnel, dejando la IP
> local inalcanzable. Se desactiva con `tailscale set --accept-routes=false`.

### ✅ Verificación de la capa 3

Desde tu portátil:

```bash
ping 192.168.1.50
```

---

## Preparar el repositorio

```bash
git clone https://github.com/J4F3ET/lab-free5gc.git && cd lab-free5gc && cp .env.example .env
```

Edita `.env`. Como mínimo cambia `GRAFANA_ADMIN_PASSWORD`. Si tu red local no es
`192.168.1.0/24`, ajusta también `LAN_SUBNET`, `LAN_GW` y `LXC_IP`.

### Las 13 comprobaciones del preflight

```bash
bash ci/preflight.sh
```

Verifica de una sola pasada todo lo de las capas 0 a 3, y dice **cuál** falla:

| # | Comprueba | Capa |
|---|---|---|
| 1-2 | Módulos `gtp5g` y `sctp` | 0 |
| 3 | `/dev/net/tun` accesible | 1 |
| 4 | `CAP_NET_ADMIN` en el contenedor | 1 |
| 5 | Se puede crear y borrar una interfaz de prueba | 1 |
| 6-7 | `ip_forward` y `rp_filter` | 0/2 |
| 8 | Docker responde | 2 |
| 9 | `docker compose` disponible | 2 |
| 10 | La IP del contenedor y su gateway | 3 |
| 11 | Las tres reglas de `DOCKER-USER` | 3 |
| 12 | Memoria disponible | — |
| 13 | Espacio en disco | — |

Un `WARN` en memoria es normal en hardware modesto. Un `BAD` en cualquier otra
no lo es.

El script está pensado para funcionar **tanto como root como con un usuario sin
privilegios** (el del runner de CI), y eleva permisos solo en los comandos
concretos que lo necesitan. Si te dice `sin sudo sin contraseña`, ejecuta
[`deploy/bootstrap-runner.sh`](../deploy/bootstrap-runner.sh), que instala el
fichero de sudoers acotado — y lo valida con `visudo -cf` antes de escribirlo,
porque un sudoers mal formado deja el sistema sin sudo.

### Coherencia de la configuración

```bash
docker compose config -q && python3 ci/check-plmn.py
```

El primero valida la sintaxis del compose; el segundo compara PLMN, TAC,
S-NSSAI, IMSI, `nodeID` de PFCP, endpoints N3 y DNN entre **todos** los ficheros.
Necesita `python3-yaml` instalado como paquete del sistema:

```bash
apt install -y python3-yaml
```

> No uses `pip install pyyaml` en Debian 13: el entorno está marcado como
> *externally managed* (PEP 668) y falla.

---

## Arrancar

Ver [04 — Operación diaria](04-operacion.md), o la
[sección 6 del README](../README.md#paso-6--arrancar) para la versión corta.

---

**Anterior:** [01 — Conceptos 5G](01-conceptos-5g.md) ·
**Siguiente:** [03 — Referencia de servicios](03-servicios.md)
