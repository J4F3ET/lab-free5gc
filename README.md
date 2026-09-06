# Repo-de-Teoria-de-la-informacion-test-


# Laboratorio 5G emulado: free5GC + UERANSIM sobre LXC/Proxmox

**Guía de despliegue, enrutamiento, CI/CD y observabilidad**
Rediseño de `J4F3ET/lab-free5gc` — sin hardware de RF, plano de usuario GTP-U 100% emulado.

---

## 0. Cómo leer esta guía

Está ordenada por **capas de dependencia**. Cada capa se valida antes de pasar a la siguiente. La causa raíz de la mayoría de fallos en este tipo de laboratorio es intentar depurar la capa 6 (túnel `uesimtun0`) cuando el problema está en la capa 0 (módulo de kernel) o en la capa 3 (reglas de forwarding de Docker).

| Capa | Componente | Criterio de éxito |
|---|---|---|
| 0 | Host Proxmox: kernel, `gtp5g`, `sctp` | `lsmod` muestra ambos módulos |
| 1 | Contenedor LXC privilegiado | `/dev/net/tun` accesible, nesting activo |
| 2 | Docker + redes internas | Todos los contenedores `healthy` |
| 3 | Tailscale + forwarding + NAT | Ping desde un peer del tailnet a la IP interna |
| 4 | Plano de control free5GC | PFCP association SMF↔UPF establecida |
| 5 | UERANSIM gNB | NG Setup Response recibido |
| 6 | UERANSIM UE | `uesimtun0` con IP en 10.60.0.0/16 y ping saliente |
| 7 | Observabilidad | Métricas de UE, contenedor y host en Grafana |

**Nota sobre versiones:** no tengo acceso a la web en esta sesión, así que los tags de imagen (`v3.4.4`, etc.) y los IDs de dashboards de Grafana deben verificarse contra el registro real antes de usarlos. Los marco con ⚠️ donde aplica.

---

## 1. Autopsia: por qué falló el primer despliegue

Antes de la configuración nueva, vale la pena nombrar los modos de fallo. En un lab free5GC/LXC/Tailscale, prácticamente todos los problemas caen en estas siete categorías:

| #   | Modo de fallo                                                                        | Síntoma típico                                                                           | Capa |
| --- | ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- | ---- |
| F1  | `gtp5g` no cargado o versión incompatible                                            | UPF sale con `failed to create gtp5g device` o `netlink: family not found`               | 0    |
| F2  | LXC no privilegiado / sin `/dev/net/tun`                                             | UPF y UERANSIM fallan con `operation not permitted` al crear interfaces                  | 1    |
| F3  | `nodeID` PFCP no coincide entre `smfcfg` y `upfcfg`                                  | SMF reintenta asociación PFCP indefinidamente; UE registra pero no obtiene sesión PDU    | 4    |
| F4  | PLMN / TAC / S-NSSAI inconsistentes entre free5GC, UERANSIM y el suscriptor en Mongo | `NGSetupFailure`, o `Registration Reject` causa #7, o `PDU Session Establishment Reject` | 4-6  |
| F5  | `DOCKER-USER` con política DROP bloquea tráfico desde `tailscale0`                   | Ping funciona dentro del LXC pero no desde el tailnet                                    | 3    |
| F6  | Falta `ip_forward` o `MASQUERADE` para 10.60.0.0/16                                  | `uesimtun0` obtiene IP pero no hay conectividad saliente                                 | 3-6  |
| F7  | MTU: Tailscale (1280) + overhead GTP-U (36 B)                                        | ICMP funciona, TCP se cuelga en transferencias grandes                                   | 3    |

El plan siguiente está construido para hacer cada uno de estos siete fallos **imposible o detectable automáticamente** en el pipeline.

---

## 2. Arquitectura objetivo

### 2.1 Topología

```
┌─────────────────────────────────────────────────────────────────┐
│ HOST PROXMOX  (kernel 6.x)                                      │
│  ├─ módulo gtp5g  (DKMS)      ├─ módulo sctp                    │
│  ├─ vmbr0 192.168.1.0/24                                        │
│  │                                                              │
│  ├──────────────────────────────────────────────────────────┐   │
│  │ LXC 110 (privilegiado, nesting=1)   192.168.1.50         │   │
│  │  ├─ tailscale0  100.x.y.z  (subnet router)               │   │
│  │  ├─ dockerd                                              │   │
│  │  │                                                       │   │
│  │  │  sbi_net 10.100.200.0/24   (SBI + N2 + N4)            │   │
│  │  │   nrf .10  amf .11  smf .12  ausf .13  udm .14        │   │
│  │  │   udr .15  pcf .16  nssf .17  upf .20  gnb .30        │   │
│  │  │   mongo .40  webui .41                                │   │
│  │  │                                                       │   │
│  │  │  n3_net 10.100.201.0/24    (GTP-U: gNB ↔ UPF)         │   │
│  │  │   upf .20   gnb .30                                   │   │
│  │  │                                                       │   │
│  │  │  radio_net 10.100.203.0/24 (radio emulada UDP/4997)   │   │
│  │  │   gnb .30   ue .31                                    │   │
│  │  │                                                       │   │
│  │  │  dn_net 10.100.204.0/24    (Data Network)             │   │
│  │  │   upf .20   iperf3-server .10                         │   │
│  │  │                                                       │   │
│  │  │  obs_net 10.100.205.0/24   (observabilidad)           │   │
│  │  │   prometheus .10  grafana .11  cadvisor .12           │   │
│  │  │   node-exporter .13  pushgateway .14                  │   │
│  │  │                                                       │   │
│  │  │  UE pool: 10.60.0.0/16  (interfaz upfgtp en UPF)      │   │
│  │  └───────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Por qué esta separación de redes

No es purismo. Cada red aislada convierte un problema de "no funciona" en un problema localizable con un solo `tcpdump`:

- **`n3_net` separada** → puedes capturar GTP-U puro sin ruido de SBI. `tcpdump -i any udp port 2152` en `sbi_net` mezclado devuelve basura.
- **`radio_net` separada** → distingue un fallo de "radio" emulada (UE no encuentra gNB) de un fallo de N2/N3.
- **`dn_net` separada** → el `MASQUERADE` se aplica a una interfaz concreta y determinista, no a "la que Docker haya nombrado `eth0` hoy". Este es el origen de F6 en la mayoría de labs.
- **`obs_net` separada** → Prometheus no contamina las capturas del plano de usuario.

### 2.3 Plan de direccionamiento (tabla de referencia)

| Función    | sbi_net | n3_net | radio_net | dn_net | Puertos                        |
| ---------- | ------- | ------ | --------- | ------ | ------------------------------ |
| NRF        | .10     | —      | —         | —      | 8000/tcp                       |
| AMF        | .11     | —      | —         | —      | 8000/tcp, 38412/sctp (N2)      |
| SMF        | .12     | —      | —         | —      | 8000/tcp, 8805/udp (N4)        |
| AUSF       | .13     | —      | —         | —      | 8000/tcp                       |
| UDM        | .14     | —      | —         | —      | 8000/tcp                       |
| UDR        | .15     | —      | —         | —      | 8000/tcp                       |
| PCF        | .16     | —      | —         | —      | 8000/tcp                       |
| NSSF       | .17     | —      | —         | —      | 8000/tcp                       |
| UPF        | .20     | .20    | —         | .20    | 8805/udp (N4), 2152/udp (N3)   |
| gNB        | .30     | .30    | .30       | —      | 38412/sctp, 2152/udp, 4997/udp |
| UE         | —       | —      | .31       | —      | —                              |
| MongoDB    | .40     | —      | —         | —      | 27017/tcp                      |
| WebConsole | .41     | —      | —         | —      | 5000/tcp                       |
| iperf3 srv | —       | —      | —         | .10    | 5201/tcp+udp                   |

Pega esta tabla en el README del repo. La mitad de los errores de configuración son "escribí la IP de N4 donde iba la de N3".

---

## 3. Capa 0 — Host Proxmox

### 3.1 El problema de fondo con `gtp5g`

El UPF de free5GC no hace encapsulación GTP-U en espacio de usuario: delega en el módulo de kernel `gtp5g`, que crea un dispositivo de red llamado `upfgtp`. Como un contenedor LXC **comparte el kernel del host**, el módulo se compila y carga en el host Proxmox, no dentro del contenedor.

Dos consecuencias que rompen el lab si se ignoran:

1. **La versión de `gtp5g` debe ser compatible con la versión de free5GC.** Un `gtp5g` demasiado nuevo o viejo produce errores de netlink poco descriptivos. Ancla la rama de `gtp5g` a la que documente el release de free5GC que uses. ⚠️ Verifica la matriz de compatibilidad en el repo de free5GC antes de compilar.
2. **Una actualización de kernel de Proxmox deja el módulo huérfano.** Después de `apt upgrade` y reboot, el UPF deja de arrancar sin explicación aparente. La solución no es recompilar a mano cada vez: es **DKMS**.

### 3.2 Instalación con DKMS (recomendado)

```bash
# --- EN EL HOST PROXMOX, como root ---

apt update
apt install -y git build-essential dkms pve-headers-$(uname -r)

cd /usr/src
git clone https://github.com/free5gc/gtp5g.git gtp5g-0.9.0
cd gtp5g-0.9.0
# Ancla a un tag concreto. ⚠️ Ajusta al que corresponda a tu free5GC.
git checkout v0.9.0

# Registrar en DKMS para que sobreviva a upgrades de kernel
cat > /usr/src/gtp5g-0.9.0/dkms.conf <<'EOF'
PACKAGE_NAME="gtp5g"
PACKAGE_VERSION="0.9.0"
BUILT_MODULE_NAME[0]="gtp5g"
DEST_MODULE_LOCATION[0]="/updates/dkms"
AUTOINSTALL="yes"
EOF

dkms add     -m gtp5g -v 0.9.0
dkms build   -m gtp5g -v 0.9.0
dkms install -m gtp5g -v 0.9.0
```

Si el `Makefile` de `gtp5g` no encaja limpio con DKMS en tu versión, el fallback es compilar con `make && make install` y aceptar recompilar tras cada upgrade de kernel — pero entonces **añade un hook de arranque** que verifique y falle ruidosamente.

### 3.3 Carga persistente de módulos

```bash
# --- HOST PROXMOX ---
cat > /etc/modules-load.d/free5gc.conf <<'EOF'
gtp5g
sctp
EOF

modprobe gtp5g
modprobe sctp

# Verificación
lsmod | grep -E '^(gtp5g|sctp)'
dmesg | tail -20 | grep -i gtp5g
```

`sctp` es obligatorio: la interfaz N2 (NGAP entre gNB y AMF) usa SCTP, no TCP. Si el módulo no está en el host, el `bind()` del AMF falla dentro del contenedor con un `protocol not supported` que despista mucho.

### 3.4 Forwarding e IPv6 en el host

```bash
# --- HOST PROXMOX ---
cat > /etc/sysctl.d/99-free5gc-host.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.conf.all.rp_filter = 2
net.ipv6.conf.all.disable_ipv6 = 0
EOF
sysctl --system
```

`rp_filter = 2` (loose reverse path) en lugar de `1` (strict) es importante: con múltiples redes Docker y rutas anunciadas por Tailscale, el filtrado estricto descarta silenciosamente paquetes de retorno asimétricos. Es una causa clásica de "el ping sale pero no vuelve".

### 3.5 Verificación de capa 0

```bash
# Todo debe devolver salida no vacía
lsmod | grep gtp5g
lsmod | grep sctp
sysctl net.ipv4.ip_forward   # = 1
ls -l /dev/net/tun
```
---

## 4. Capa 1 — Contenedor LXC

### 4.1 Fichero de configuración completo

Edita `/etc/pve/lxc/110.conf` en el host (ajusta el ID). El contenedor debe estar **detenido** al editar.

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

# ---------- Requisitos free5GC / UERANSIM / Tailscale ----------
# AppArmor sin confinar: necesario para crear netdevs (upfgtp, uesimtun0)
# y para que Docker manipule iptables dentro del contenedor.
lxc.apparmor.profile: unconfined

# Conservar todas las capabilities (NET_ADMIN, SYS_MODULE, SYS_ADMIN)
lxc.cap.drop:

# /dev/net/tun  -> major 10, minor 200
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net dev/net none bind,create=dir

# Montajes de kernel escribibles (Docker + gtp5g necesitan sysfs rw)
lxc.mount.auto: proc:rw sys:rw cgroup:rw
```

Puntos que suelen romper el despliegue:

- **`firewall=0` en `net0`.** El firewall integrado de Proxmox sobre la interfaz del LXC descarta SCTP y GTP-U por defecto en muchas configuraciones. Si lo necesitas activo, tendrás que abrir explícitamente `38412/sctp`, `2152/udp`, `8805/udp` y `41641/udp` (Tailscale).
- **`unprivileged: 0`.** Con un LXC no privilegiado, el UPF no puede crear el netdev `upfgtp` ni UERANSIM `uesimtun0`, aunque el módulo esté cargado. No hay atajo: el mapeo de UIDs impide la operación de netlink.
- **`lxc.apparmor.profile: unconfined`.** Sin esto verás `permission denied` en operaciones que las capabilities deberían permitir. Es la causa de F2 más frecuente y más confusa, porque `capsh --print` muestra las capabilities presentes.
- **`keyctl=1`.** Docker lo necesita para el keyring; sin él, `dockerd` arranca pero falla de forma intermitente al montar volúmenes.

Arranca y comprueba:

```bash
pct start 110
pct exec 110 -- bash -c 'ls -l /dev/net/tun && capsh --print | grep -o net_admin'
```

### 4.2 Comprobación de que el LXC "ve" gtp5g

```bash
# --- DENTRO DEL LXC ---
grep gtp5g /proc/modules        # debe aparecer (el /proc/modules es el del host)
ls /proc/gtp5g 2>/dev/null      # algunas versiones exponen estadísticas aquí
```

Si `/proc/modules` está vacío o no muestra `gtp5g`, el contenedor no es privilegiado o el módulo no está cargado en el host. **No sigas hasta resolver esto** — todo lo demás fallará de forma engañosa.

Algunas versiones de `gtp5g` exponen un dispositivo de carácter. Comprueba en el host con `ls -l /dev/gtp5g`; si existe, añade su `major:minor` al `lxc.cgroup2.devices.allow` y un `lxc.mount.entry` equivalente al de `/dev/net`.

### 4.3 Docker dentro del LXC

```bash
# --- DENTRO DEL LXC (Ubuntu 22.04/24.04) ---
apt update && apt install -y ca-certificates curl gnupg lsb-release
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable --now docker
docker run --rm hello-world
```

Configura el daemon para MTU y logging controlado:

```bash
cat > /etc/docker/daemon.json <<'EOF'
{
  "mtu": 1400,
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "3" },
  "default-address-pools": [
    { "base": "10.100.0.0/16", "size": 24 }
  ]
}
EOF
systemctl restart docker
```

**Por qué `mtu: 1400`:** los paquetes del UE que salgan hacia el tailnet atraviesan `tailscale0` (MTU 1280) *y* la encapsulación GTP-U (+36 bytes). Bajar el MTU de las bridges Docker a 1400 elimina de raíz una clase entera de fallos "ping OK, TCP colgado" (F7). El `default-address-pools` evita que Docker invente subredes que colisionen con tus rutas de Tailscale.

### 4.4 Sysctl dentro del LXC

```bash
cat > /etc/sysctl.d/99-free5gc.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
net.ipv4.conf.all.src_valid_mark = 1
net.core.rmem_max = 26214400
net.core.wmem_max = 26214400
EOF
sysctl --system
```

`src_valid_mark = 1` es el sysctl que Tailscale necesita cuando convive con Docker: sin él, los paquetes marcados por Tailscale son descartados por el reverse path filter al volver de una bridge Docker.

---

## 5. Capa 3 — Tailscale y enrutamiento

Esta es la capa donde el despliegue anterior se rompió, así que la desarrollo entera.

### 5.1 Modelo mental

Tailscale hace dos cosas distintas aquí, y conviene no mezclarlas:

1. **Acceso remoto al laboratorio** (SSH, Grafana, WebConsole). Esto funciona con solo instalar Tailscale — no requiere enrutamiento de subredes.
2. **Subnet router**: exponer las redes Docker internas (`10.100.200.0/24`) y el pool de UEs (`10.60.0.0/16`) al resto del tailnet. Esto es lo que requiere forwarding, aprobación de rutas y reglas de firewall.

**Recomendación de diseño: no hagas pasar N2/N3/N4 por Tailscale.** El plano de control y el plano de usuario del núcleo 5G deben vivir enteros dentro de las redes Docker del mismo LXC. Tailscale es solo para *observar y administrar* el lab desde fuera. Si intentas repartir el gNB y el UPF en nodos distintos del tailnet, sumas MTU variable, latencia WireGuard y NAT a un stack que ya es difícil de depurar. Hazlo cuando el lab de un solo nodo funcione.

### 5.2 Instalación y subnet routing

```bash
# --- DENTRO DEL LXC ---
curl -fsSL https://tailscale.com/install.sh | sh

tailscale up \
  --hostname=lab-free5gc \
  --advertise-routes=10.100.200.0/24,10.100.205.0/24,10.60.0.0/16 \
  --accept-routes \
  --ssh
```

Desglose de las rutas anunciadas:

| Ruta | Para qué |
|---|---|
| `10.100.200.0/24` | Acceso directo a WebConsole, NRF, y captura desde fuera |
| `10.100.205.0/24` | Grafana y Prometheus |
| `10.60.0.0/16` | Alcanzar los UEs desde el tailnet (tráfico *hacia* el UE) |

### 5.3 El paso que casi siempre se olvida

Las rutas anunciadas **no se activan hasta que las apruebas manualmente** en la consola de administración de Tailscale (Machines → el nodo → Edit route settings → aprobar cada subred). Hasta entonces, `tailscale status` en un peer no mostrará las rutas y el ping fallará sin ningún mensaje de error útil.

Verificación desde otro nodo del tailnet:

```bash
tailscale status --json | jq '.Peer[] | select(.HostName=="lab-free5gc") | .PrimaryRoutes'
ip route get 10.100.200.11        # debe salir por tailscale0
```

Si quieres automatizarlo, usa `autoApprovers` en el fichero de ACL del tailnet:

```jsonc
{
  "autoApprovers": {
    "routes": {
      "10.100.200.0/24": ["tag:lab5g"],
      "10.100.205.0/24": ["tag:lab5g"],
      "10.60.0.0/16":    ["tag:lab5g"]
    }
  },
  "tagOwners": { "tag:lab5g": ["autogroup:admin"] }
}
```

…y levanta el nodo con `tailscale up --advertise-tags=tag:lab5g ...`. Esto también sobrevive a re-autenticaciones del runner de CI.

### 5.4 El bloqueo de Docker (fallo F5)

Docker pone la política de la cadena `FORWARD` en `DROP` y encadena su propia lógica. El tráfico que entra por `tailscale0` y quiere salir hacia una bridge Docker **cae en ese DROP**. El resultado es exactamente el síntoma que describiste: dentro del LXC todo se ve bien; desde fuera, nada responde.

La cadena `DOCKER-USER` existe precisamente para esto: Docker la consulta antes de sus propias reglas y nunca la sobrescribe.

```bash
# --- DENTRO DEL LXC ---
iptables -I DOCKER-USER -i tailscale0 -j ACCEPT
iptables -I DOCKER-USER -o tailscale0 -j ACCEPT
```

Versión más restrictiva (preferible fuera de un lab desechable), limitando a las subredes que realmente publicas:

```bash
iptables -I DOCKER-USER -i tailscale0 -d 10.100.200.0/24 -j ACCEPT
iptables -I DOCKER-USER -i tailscale0 -d 10.100.205.0/24 -j ACCEPT
iptables -I DOCKER-USER -i tailscale0 -d 10.60.0.0/16    -j ACCEPT
iptables -I DOCKER-USER -o tailscale0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

### 5.5 MSS clamping (fallo F7)

```bash
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN \
  -j TCPMSS --clamp-mss-to-pmtu
```

Sin esto: `ping` funciona, `curl` a una página pequeña funciona, y una descarga grande o una sesión SSH se cuelga a mitad. Es el fallo más caro de diagnosticar porque parece intermitente.

### 5.6 Persistencia de las reglas

Un reinicio del LXC o de `dockerd` borra lo anterior. Fija las reglas con una unidad systemd que se ejecute **después** de Docker y Tailscale:

```bash
cat > /usr/local/sbin/lab5g-netrules.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

# Idempotente: -C comprueba, -I inserta solo si falta
add() { iptables -C "$@" 2>/dev/null || iptables -I "$@"; }
addm(){ iptables -t mangle -C "$@" 2>/dev/null || iptables -t mangle -A "$@"; }

for NET in 10.100.200.0/24 10.100.205.0/24 10.60.0.0/16; do
  add DOCKER-USER -i tailscale0 -d "$NET" -j ACCEPT
done
add DOCKER-USER -o tailscale0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

addm FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

sysctl -qw net.ipv4.ip_forward=1
sysctl -qw net.ipv4.conf.all.rp_filter=2
sysctl -qw net.ipv4.conf.all.src_valid_mark=1
echo "[lab5g-netrules] reglas aplicadas"
EOF
chmod +x /usr/local/sbin/lab5g-netrules.sh

cat > /etc/systemd/system/lab5g-netrules.service <<'EOF'
[Unit]
Description=Reglas de red para el laboratorio free5GC
After=docker.service tailscaled.service
Requires=docker.service
PartOf=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/lab5g-netrules.sh

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now lab5g-netrules.service
```

`PartOf=docker.service` hace que se re-ejecute cuando Docker se reinicia, que es justo cuando las reglas se pierden.

### 5.7 Verificación de capa 3

Desde **otro** nodo del tailnet:

```bash
ping -c3 10.100.200.10          # NRF
curl -s http://10.100.205.11:3000/api/health   # Grafana
tracepath 10.100.200.20         # UPF; comprueba dónde se rompe el MTU
```

Si el ping al `.10` funciona pero `curl` a Grafana no, el problema es la regla `DOCKER-USER` para `10.100.205.0/24`. Si ambos van pero `tracepath` reporta `pmtu 1280` seguido de silencio, falta el MSS clamping.
---

## 6. Capa 4 — Corrección de parámetros base: PLMN, TAC y S-NSSAI

### 6.1 Elección del PLMN

El valor por defecto de free5GC es **MCC 208 / MNC 93**, que corresponde a Francia. Hay dos sustitutos razonables:

| Opción | MCC | MNC | Longitud MNC | Longitud MSIN | IMSI de ejemplo |
|---|---|---|---|---|---|
| **Red de pruebas (recomendada)** | `001` | `01` | 2 | 10 | `001010000000001` |
| **Colombia** | `732` | `101` | 3 | 9 | `732101000000001` |

**Recomiendo `001/01` como valor por defecto del laboratorio.** El par MCC 001 / MNC 01 está reservado por la ITU-T (Rec. E.212) precisamente para redes de prueba, no está asignado a ningún operador real, y es lo que esperan la mayoría de herramientas y guías. Como el lab es puramente emulado (sin RF), usar `732/101` no causa interferencia real, pero sí introduce un riesgo práctico: **el MNC de Colombia es de 3 dígitos**, y eso cambia la aritmética del IMSI y el comportamiento de varios parsers.

Si el objetivo del proyecto es específicamente representar una red colombiana, usa `732/101` y ten en cuenta lo siguiente. ⚠️ Verifica en la lista de asignaciones del MinTIC / ITU qué MNC concreto quieres representar; `101` está asignado a un operador real, así que para un lab conviene documentarlo como "simulación de", no como una red propia.

### 6.2 La aritmética del IMSI

Un IMSI son **siempre 15 dígitos**: `MCC (3) + MNC (2 ó 3) + MSIN (rellena hasta 15)`.

```
001 01  0000000001   ->  001010000000001   (MSIN de 10 dígitos)
732 101 000000001    ->  732101000000001   (MSIN de 9 dígitos)
```

Un IMSI de 16 dígitos porque copiaste el MSIN de 10 dígitos del ejemplo por defecto y le pusiste un MNC de 3 dígitos es un fallo silencioso: el UE envía el SUCI, el UDM no encuentra el suscriptor, y recibes un `Registration Reject`. Es exactamente el tipo de error que produce el síntoma F4.

### 6.3 Inventario: dónde aparece el PLMN

Cambiar el MCC/MNC en un solo sitio es la causa número uno de `NGSetupFailure`. Estos son **todos** los puntos que deben cambiar en conjunto:

| Fichero | Bloque | Notas |
|---|---|---|
| `config/nrfcfg.yaml` | `configuration.DefaultPlmnId` | |
| `config/amfcfg.yaml` | `servedGuamiList[].plmnId` | |
| `config/amfcfg.yaml` | `supportTaiList[].plmnId` + `tac` | |
| `config/amfcfg.yaml` | `plmnSupportList[].plmnId` + `snssaiList` | |
| `config/nssfcfg.yaml` | `supportedPlmnList` | |
| `config/nssfcfg.yaml` | `supportedNssaiInPlmnList[].plmnId` | |
| `config/nssfcfg.yaml` | `nsiList[]`, `amfSetList[]`, `taList[]` | Fácil de olvidar |
| `config/smfcfg.yaml` | `configuration.snssaiInfos` + `plmnList` | |
| `config/udrcfg.yaml` / `udmcfg.yaml` | (normalmente no llevan PLMN) | |
| `ueransim/gnb.yaml` | `mcc`, `mnc`, `tac`, `slices` | |
| `ueransim/ue.yaml` | `supi`, `mcc`, `mnc`, `sessions`, `configured-nssai` | |
| **MongoDB** (vía WebConsole) | PLMN del suscriptor | **No es un fichero.** Se pierde al recrear el volumen |

Ese último punto merece énfasis: el suscriptor vive en MongoDB, no en el repositorio. Si tu pipeline de CI recrea el volumen de Mongo, el suscriptor desaparece y el UE deja de registrarse aunque los YAML estén perfectos. La sección 8.4 lo resuelve con aprovisionamiento automático.

### 6.4 Las tres trampas de formato

Estas son incompatibilidades de notación entre free5GC y UERANSIM, no errores tuyos, y no producen mensajes de error legibles:

| Parámetro | free5GC | UERANSIM | Valor equivalente |
|---|---|---|---|
| **SD** (Slice Differentiator) | `sd: '010203'` (string hex, 6 chars, entrecomillado) | `sd: 0x010203` (entero hex) | 66051 decimal |
| **TAC** | `tac: '000001'` (string hex de 3 octetos) | `tac: 1` (entero decimal) | 1 |
| **MCC/MNC** | `mcc: '001'` (string, comillas obligatorias) | `mcc: '001'` (string) | — |

Si escribes `mcc: 001` sin comillas, YAML lo interpreta como el entero `1` y pierdes los ceros a la izquierda. Ese es probablemente el error más común de todo el ecosistema free5GC. **Entrecomilla siempre `mcc`, `mnc`, `sd` y `tac` en los ficheros de free5GC.**

### 6.5 Bloques a modificar (variante Colombia 732/101)

**`config/amfcfg.yaml`**

```yaml
configuration:
  amfName: AMF
  ngapIpList:
    - 10.100.200.11          # <- IP de N2 en sbi_net, no 0.0.0.0
  ngapPort: 38412
  sbi:
    scheme: http
    registerIPv4: amf        # nombre DNS resoluble por Docker
    bindingIPv4: 0.0.0.0
    port: 8000
  nrfUri: http://nrf:8000

  servedGuamiList:
    - plmnId:
        mcc: '732'
        mnc: '101'
      amfId: cafe00          # 6 dígitos hex: RegionId(2)+SetId(3)+Pointer(1)

  supportTaiList:
    - plmnId:
        mcc: '732'
        mnc: '101'
      tac: '000001'          # == tac: 1 en gnb.yaml

  plmnSupportList:
    - plmnId:
        mcc: '732'
        mnc: '101'
      snssaiList:
        - sst: 1
          sd: '010203'

  supportDnnList:
    - internet

  security:
    integrityOrder:  [ NIA2 ]
    cipheringOrder:  [ NEA0 ]   # NEA0 = sin cifrado, facilita el análisis con tcpdump

  networkName:
    full: lab5g-co
    short: lab5g
```

> `cipheringOrder: [NEA0]` desactiva el cifrado del plano de usuario NAS. Es deliberado: permite leer la señalización en Wireshark durante la depuración. En un despliegue real usarías `NEA2`.

**`config/smfcfg.yaml`** — bloque `userplaneInformation`, el que causa F3:

```yaml
configuration:
  smfName: SMF
  sbi:
    scheme: http
    registerIPv4: smf
    bindingIPv4: 0.0.0.0
    port: 8000
  nrfUri: http://nrf:8000

  serviceNameList:
    - nsmf-pdusession
    - nsmf-event-exposure
    - nsmf-oam

  snssaiInfos:
    - sNssai:
        sst: 1
        sd: '010203'
      dnnInfos:
        - dnn: internet
          dns:
            ipv4: 8.8.8.8
            ipv6: 2001:4860:4860::8888

  plmnList:
    - mcc: '732'
      mnc: '101'

  pfcp:
    listenAddr: 10.100.200.12
    externalAddr: 10.100.200.12
    nodeID: 10.100.200.12

  userplaneInformation:
    upNodes:
      gNB1:
        type: AN
      UPF:
        type: UPF
        nodeID: 10.100.200.20        # DEBE ser idéntico a upfcfg.pfcp.nodeID
        addr:   10.100.200.20
        sNssaiUpfInfos:
          - sNssai:
              sst: 1
              sd: '010203'
            dnnUpfInfoList:
              - dnn: internet
                pools:
                  - cidr: 10.60.0.0/16
        interfaces:
          - interfaceType: N3
            endpoints:
              - 10.100.201.20        # IP del UPF en n3_net, NO la de sbi_net
            networkInstances:
              - internet
    links:
      - A: gNB1
        B: UPF
```

Los dos errores mortales de este bloque:

1. **`nodeID` distinto** entre `smfcfg` y `upfcfg` → la asociación PFCP nunca se completa. El SMF reintenta en bucle y el UE se registra pero jamás obtiene sesión PDU.
2. **`endpoints` de N3 apuntando a la IP de SBI** → el SMF le dice al gNB que envíe GTP-U a `10.100.200.20`. El gNB lo intenta, no hay nadie escuchando en 2152 en esa interfaz, y `uesimtun0` aparece pero no pasa un solo paquete. Este es exactamente el síntoma que quieres evitar.

**`config/upfcfg.yaml`** — completo:

```yaml
version: 1.0.3
description: UPF configuration - lab5g

pfcp:
  addr: 10.100.200.20
  nodeID: 10.100.200.20
  retransTimeout: 1s
  maxRetrans: 3

gtpu:
  forwarder: gtp5g
  ifList:
    - addr: 10.100.201.20        # n3_net
      type: N3

dnnList:
  - dnn: internet
    cidr: 10.60.0.0/16
    natifname: dn0               # nombre lógico; ver script de iptables

logger:
  enable: true
  level: debug
  reportCaller: false
```

**`config/nssfcfg.yaml`** — el fichero que más se olvida:

```yaml
configuration:
  nssfName: NSSF
  sbi:
    scheme: http
    registerIPv4: nssf
    bindingIPv4: 0.0.0.0
    port: 8000
  nrfUri: http://nrf:8000
  supportedPlmnList:
    - mcc: '732'
      mnc: '101'
  supportedNssaiInPlmnList:
    - plmnId:
        mcc: '732'
        mnc: '101'
      supportedSnssaiList:
        - sst: 1
          sd: '010203'
  amfSetList:
    - amfSetId: '1'
      nrfAmfSet: http://nrf:8000/nnrf-nfm/v1/nf-instances
      supportedNssaiAvailabilityData:
        - tai:
            plmnId:
              mcc: '732'
              mnc: '101'
            tac: '000001'
          supportedSnssaiList:
            - sst: 1
              sd: '010203'
  taList:
    - tai:
        plmnId:
          mcc: '732'
          mnc: '101'
        tac: '000001'
      accessType: 3GPP_ACCESS
      supportedSnssaiList:
        - sst: 1
          sd: '010203'
```

### 6.6 UERANSIM

**`ueransim/gnb.yaml`**

```yaml
mcc: '732'
mnc: '101'

nci: '0x000000010'          # NR Cell Identity, 36 bits
idLength: 32                # longitud del gNB ID dentro del NCI
tac: 1                      # decimal; equivale a '000001' en amfcfg

linkIp: 10.100.203.30       # radio_net  - hacia el UE
ngapIp: 10.100.200.30       # sbi_net    - N2 hacia el AMF
gtpIp:  10.100.201.30       # n3_net     - N3 hacia el UPF

amfConfigs:
  - address: 10.100.200.11
    port: 38412

slices:
  - sst: 1
    sd: 0x010203            # notación hexadecimal, sin comillas

ignoreStreamIds: true
```

**`ueransim/ue.yaml`**

```yaml
supi: 'imsi-732101000000001'     # 3+3+9 = 15 dígitos
mcc: '732'
mnc: '101'

protectionScheme: 0              # 0 = null scheme (SUCI sin cifrar, útil para depurar)
homeNetworkPublicKey: ''
homeNetworkPublicKeyId: 0
routingIndicator: '0000'

key: '8baf473f2f8fd09487cccbd7097c6862'
op:  '8e27b6af0e692e750f32667a3b14605d'
opType: 'OPC'
amf: '8000'
imei:   '356938035643803'
imeiSv: '4370816125816151'

gnbSearchList:
  - 10.100.203.30                # linkIp del gNB en radio_net

uacAic:  { mps: false, mcs: false }
uacAcc:  { normalClass: 0, class11: false, class12: false,
           class13: false, class14: false, class15: false }

sessions:
  - type: 'IPv4'
    apn: 'internet'              # == dnn en smfcfg/upfcfg
    slice:
      sst: 1
      sd: 0x010203
    emergency: false

configured-nssai:
  - sst: 1
    sd: 0x010203
default-nssai:
  - sst: 1
    sd: 0x010203

integrity:  { IA1: true, IA2: true, IA3: true }
ciphering:  { EA1: true, EA2: true, EA3: true }
integrityMaxRate: { uplink: 'full', downlink: 'full' }
```

Las claves `key` y `op` son las de ejemplo de free5GC. Deben coincidir **exactamente** con lo que aprovisiones en el suscriptor; `opType: 'OPC'` (no `'OP'`) porque el valor dado es un OPc derivado, no un OP de operador.

### 6.7 Validador de consistencia — `ci/check-plmn.py`

Este script es el antídoto contra F4. Ejecútalo en el pipeline **antes** de desplegar; convierte una hora de depuración de señalización en un fallo de CI de dos segundos.

```python
#!/usr/bin/env python3
"""Verifica coherencia de PLMN / TAC / S-NSSAI / DNN / nodeID
entre los YAML de free5GC y de UERANSIM. Sale con codigo 1 si hay
cualquier discrepancia."""
import sys, pathlib, yaml

MCC = "732"
MNC = "101"
SST = 1
SD_STR = "010203"      # notacion free5GC
SD_INT = 0x010203      # notacion UERANSIM
TAC_STR = "000001"     # notacion free5GC
TAC_INT = 1            # notacion UERANSIM
DNN = "internet"
UPF_NODE_ID = "10.100.200.20"
UPF_N3_ADDR = "10.100.201.20"
UE_CIDR = "10.60.0.0/16"

errors = []

def load(p):
    path = pathlib.Path(p)
    if not path.exists():
        errors.append(f"[FALTA] {p}")
        return {}
    return yaml.safe_load(path.read_text()) or {}

def walk_plmn(node, origin):
    """Recorre el arbol y valida cualquier dict con mcc/mnc."""
    if isinstance(node, dict):
        if "mcc" in node and "mnc" in node:
            mcc, mnc = node["mcc"], node["mnc"]
            if not isinstance(mcc, str) or not isinstance(mnc, str):
                errors.append(
                    f"[TIPO] {origin}: mcc/mnc deben ir entrecomillados "
                    f"(recibido mcc={mcc!r} mnc={mnc!r})")
            if str(mcc) != MCC or str(mnc) != MNC:
                errors.append(f"[PLMN] {origin}: {mcc}/{mnc} != {MCC}/{MNC}")
        for v in node.values():
            walk_plmn(v, origin)
    elif isinstance(node, list):
        for v in node:
            walk_plmn(v, origin)

def walk_snssai(node, origin, sd_expected):
    if isinstance(node, dict):
        if "sst" in node and "sd" in node:
            if node["sst"] != SST or node["sd"] != sd_expected:
                fmt = lambda v: (f"0x{v:06x} ({v})" if isinstance(v, int)
                                 else repr(v))
                errors.append(
                    f"[SNSSAI] {origin}: sst={node['sst']} sd={fmt(node['sd'])} "
                    f"!= sst={SST} sd={fmt(sd_expected)}")
        for v in node.values():
            walk_snssai(v, origin, sd_expected)
    elif isinstance(node, list):
        for v in node:
            walk_snssai(v, origin, sd_expected)

# ---- free5GC ----
for f in ["amfcfg", "smfcfg", "nssfcfg", "nrfcfg"]:
    cfg = load(f"config/{f}.yaml")
    walk_plmn(cfg, f)
    walk_snssai(cfg, f, SD_STR)

amf = load("config/amfcfg.yaml").get("configuration", {})
for tai in amf.get("supportTaiList", []):
    if str(tai.get("tac")) != TAC_STR:
        errors.append(f"[TAC] amfcfg: {tai.get('tac')!r} != {TAC_STR!r}")
if DNN not in amf.get("supportDnnList", []):
    errors.append(f"[DNN] amfcfg.supportDnnList no contiene {DNN!r}")

# ---- nodeID PFCP: la causa de F3 ----
smf = load("config/smfcfg.yaml").get("configuration", {})
upf = load("config/upfcfg.yaml")
smf_upf = smf.get("userplaneInformation", {}).get("upNodes", {}).get("UPF", {})
if str(smf_upf.get("nodeID")) != UPF_NODE_ID:
    errors.append(f"[PFCP] smfcfg nodeID={smf_upf.get('nodeID')!r} != {UPF_NODE_ID!r}")
if str(upf.get("pfcp", {}).get("nodeID")) != UPF_NODE_ID:
    errors.append(f"[PFCP] upfcfg nodeID={upf.get('pfcp',{}).get('nodeID')!r} != {UPF_NODE_ID!r}")

# ---- endpoint N3: la causa de "uesimtun0 sube pero no pasa trafico" ----
for iface in smf_upf.get("interfaces", []):
    if iface.get("interfaceType") == "N3":
        if UPF_N3_ADDR not in [str(e) for e in iface.get("endpoints", [])]:
            errors.append(
                f"[N3] smfcfg endpoints={iface.get('endpoints')} "
                f"no contiene la IP de n3_net {UPF_N3_ADDR}")
for iface in upf.get("gtpu", {}).get("ifList", []):
    if str(iface.get("addr")) != UPF_N3_ADDR:
        errors.append(f"[N3] upfcfg gtpu addr={iface.get('addr')!r} != {UPF_N3_ADDR!r}")

# ---- pool de UEs coherente entre SMF y UPF ----
pools = [p.get("cidr") for u in smf_upf.get("sNssaiUpfInfos", [])
         for d in u.get("dnnUpfInfoList", []) for p in d.get("pools", [])]
if UE_CIDR not in pools:
    errors.append(f"[POOL] smfcfg pools={pools} no contiene {UE_CIDR}")
if UE_CIDR not in [d.get("cidr") for d in upf.get("dnnList", [])]:
    errors.append(f"[POOL] upfcfg dnnList no contiene {UE_CIDR}")

# ---- UERANSIM ----
gnb = load("ueransim/gnb.yaml")
ue  = load("ueransim/ue.yaml")
for name, cfg in (("gnb", gnb), ("ue", ue)):
    if str(cfg.get("mcc")) != MCC or str(cfg.get("mnc")) != MNC:
        errors.append(f"[PLMN] {name}.yaml: {cfg.get('mcc')}/{cfg.get('mnc')} != {MCC}/{MNC}")
if gnb.get("tac") != TAC_INT:
    errors.append(f"[TAC] gnb.yaml tac={gnb.get('tac')!r} != {TAC_INT} "
                  f"(equivalente decimal de '{TAC_STR}')")
walk_snssai(gnb.get("slices"), "gnb.yaml", SD_INT)
walk_snssai(ue.get("sessions"), "ue.yaml", SD_INT)
walk_snssai(ue.get("configured-nssai"), "ue.yaml", SD_INT)

# ---- IMSI: longitud y prefijo ----
supi = str(ue.get("supi", ""))
if not supi.startswith("imsi-"):
    errors.append(f"[SUPI] {supi!r} debe empezar por 'imsi-'")
else:
    imsi = supi[5:]
    if len(imsi) != 15:
        errors.append(f"[SUPI] IMSI {imsi!r} tiene {len(imsi)} digitos, deben ser 15 "
                      f"(MCC {len(MCC)} + MNC {len(MNC)} + MSIN {15-len(MCC)-len(MNC)})")
    if not imsi.startswith(MCC + MNC):
        errors.append(f"[SUPI] IMSI {imsi!r} no empieza por {MCC+MNC}")

# ---- DNN / APN ----
for s in ue.get("sessions", []):
    if s.get("apn") != DNN:
        errors.append(f"[DNN] ue.yaml apn={s.get('apn')!r} != {DNN!r}")

if errors:
    print(f"\n  {len(errors)} inconsistencia(s):\n")
    for e in errors:
        print(f"   {e}")
    sys.exit(1)
print(f"  Configuracion coherente: PLMN {MCC}/{MNC}, "
      f"S-NSSAI {SST}/{SD_STR}, TAC {TAC_STR}, DNN {DNN}")
```

Para cambiar de `732/101` a `001/01`, edita las constantes de la cabecera y el script te dirá exactamente qué ficheros quedaron desincronizados.
---

## 7. Capa 2 y 7 — `docker-compose.yml` con observabilidad integrada

### 7.1 Estructura del repositorio

```
lab-free5gc/
├─ .env
├─ docker-compose.yml
├─ config/                      # YAML de free5GC
│  ├─ nrfcfg.yaml   amfcfg.yaml   smfcfg.yaml   upfcfg.yaml
│  ├─ ausfcfg.yaml  udmcfg.yaml   udrcfg.yaml   pcfcfg.yaml
│  ├─ nssfcfg.yaml  webuicfg.yaml
│  └─ upf-nat.sh
├─ ueransim/
│  ├─ gnb.yaml   ue.yaml   ue-entrypoint.sh
├─ observability/
│  ├─ prometheus/prometheus.yml
│  ├─ blackbox/blackbox.yml
│  ├─ probe/iperf-probe.sh
│  └─ grafana/provisioning/
│     ├─ datasources/prometheus.yml
│     └─ dashboards/dashboards.yml
├─ ci/
│  ├─ preflight.sh   check-plmn.py   wait-ready.sh
│  ├─ provision-subscriber.sh   smoke-ue.sh   collect-logs.sh
└─ .github/workflows/deploy.yml
```

### 7.2 `.env`

```bash
# ⚠️ Verifica que estos tags existan antes de desplegar.
FREE5GC_TAG=v3.4.4
UERANSIM_TAG=latest
MONGO_TAG=6.0

# PLMN
MCC=732
MNC=101
IMSI=732101000000001

# Direccionamiento
SBI_SUBNET=10.100.200.0/24
N3_SUBNET=10.100.201.0/24
RADIO_SUBNET=10.100.203.0/24
DN_SUBNET=10.100.204.0/24
OBS_SUBNET=10.100.205.0/24
UE_POOL=10.60.0.0/16

GRAFANA_ADMIN_PASSWORD=cambiame
```

`GRAFANA_ADMIN_PASSWORD` no debe estar en el repositorio. Guárdalo como secreto de GitHub Actions y genera el `.env` en el runner (ver §8.3).

### 7.3 `docker-compose.yml`

```yaml
# =====================================================================
#  lab-free5gc — nucleo 5G emulado + observabilidad
# =====================================================================

x-f5gc-common: &f5gc-common
  restart: unless-stopped
  environment:
    GIN_MODE: release
  logging:
    driver: json-file
    options: { max-size: "20m", max-file: "3" }

networks:
  sbi_net:
    name: sbi_net
    ipam: { config: [ { subnet: 10.100.200.0/24 } ] }
    driver_opts: { com.docker.network.driver.mtu: "1400" }
  n3_net:
    name: n3_net
    ipam: { config: [ { subnet: 10.100.201.0/24 } ] }
    driver_opts: { com.docker.network.driver.mtu: "1400" }
  radio_net:
    name: radio_net
    ipam: { config: [ { subnet: 10.100.203.0/24 } ] }
    driver_opts: { com.docker.network.driver.mtu: "1400" }
  dn_net:
    name: dn_net
    ipam: { config: [ { subnet: 10.100.204.0/24 } ] }
    driver_opts: { com.docker.network.driver.mtu: "1400" }
  obs_net:
    name: obs_net
    ipam: { config: [ { subnet: 10.100.205.0/24 } ] }

volumes:
  mongo-data:
  prom-data:
  grafana-data:

services:

  # ------------------------------------------------------------------
  #  Datos
  # ------------------------------------------------------------------
  mongodb:
    image: mongo:${MONGO_TAG}
    container_name: mongodb
    restart: unless-stopped
    command: mongod --port 27017
    volumes: [ mongo-data:/data/db ]
    networks:
      sbi_net: { ipv4_address: 10.100.200.40 }
    healthcheck:
      test: ["CMD","mongosh","--quiet","--eval","db.adminCommand('ping')"]
      interval: 10s
      timeout: 5s
      retries: 12

  # ------------------------------------------------------------------
  #  Plano de control
  # ------------------------------------------------------------------
  nrf:
    <<: *f5gc-common
    image: free5gc/nrf:${FREE5GC_TAG}
    container_name: nrf
    command: ./nrf -c ../config/nrfcfg.yaml
    environment:
      DB_URI: mongodb://mongodb:27017/free5gc
      GIN_MODE: release
    volumes: [ ./config/nrfcfg.yaml:/free5gc/config/nrfcfg.yaml:ro ]
    networks:
      sbi_net: { ipv4_address: 10.100.200.10 }
    depends_on:
      mongodb: { condition: service_healthy }

  udr:
    <<: *f5gc-common
    image: free5gc/udr:${FREE5GC_TAG}
    container_name: udr
    command: ./udr -c ../config/udrcfg.yaml
    environment:
      DB_URI: mongodb://mongodb:27017/free5gc
      GIN_MODE: release
    volumes: [ ./config/udrcfg.yaml:/free5gc/config/udrcfg.yaml:ro ]
    networks: { sbi_net: { ipv4_address: 10.100.200.15 } }
    depends_on: [ nrf ]

  udm:
    <<: *f5gc-common
    image: free5gc/udm:${FREE5GC_TAG}
    container_name: udm
    command: ./udm -c ../config/udmcfg.yaml
    volumes: [ ./config/udmcfg.yaml:/free5gc/config/udmcfg.yaml:ro ]
    networks: { sbi_net: { ipv4_address: 10.100.200.14 } }
    depends_on: [ nrf, udr ]

  ausf:
    <<: *f5gc-common
    image: free5gc/ausf:${FREE5GC_TAG}
    container_name: ausf
    command: ./ausf -c ../config/ausfcfg.yaml
    volumes: [ ./config/ausfcfg.yaml:/free5gc/config/ausfcfg.yaml:ro ]
    networks: { sbi_net: { ipv4_address: 10.100.200.13 } }
    depends_on: [ nrf ]

  pcf:
    <<: *f5gc-common
    image: free5gc/pcf:${FREE5GC_TAG}
    container_name: pcf
    command: ./pcf -c ../config/pcfcfg.yaml
    volumes: [ ./config/pcfcfg.yaml:/free5gc/config/pcfcfg.yaml:ro ]
    networks: { sbi_net: { ipv4_address: 10.100.200.16 } }
    depends_on: [ nrf ]

  nssf:
    <<: *f5gc-common
    image: free5gc/nssf:${FREE5GC_TAG}
    container_name: nssf
    command: ./nssf -c ../config/nssfcfg.yaml
    volumes: [ ./config/nssfcfg.yaml:/free5gc/config/nssfcfg.yaml:ro ]
    networks: { sbi_net: { ipv4_address: 10.100.200.17 } }
    depends_on: [ nrf ]

  amf:
    <<: *f5gc-common
    image: free5gc/amf:${FREE5GC_TAG}
    container_name: amf
    command: ./amf -c ../config/amfcfg.yaml
    volumes: [ ./config/amfcfg.yaml:/free5gc/config/amfcfg.yaml:ro ]
    networks: { sbi_net: { ipv4_address: 10.100.200.11 } }
    depends_on: [ nrf, ausf, udm, pcf, nssf ]

  smf:
    <<: *f5gc-common
    image: free5gc/smf:${FREE5GC_TAG}
    container_name: smf
    command: ./smf -c ../config/smfcfg.yaml -u ../config/uerouting.yaml
    volumes:
      - ./config/smfcfg.yaml:/free5gc/config/smfcfg.yaml:ro
      - ./config/uerouting.yaml:/free5gc/config/uerouting.yaml:ro
    networks: { sbi_net: { ipv4_address: 10.100.200.12 } }
    depends_on: [ nrf, amf, upf ]

  # ------------------------------------------------------------------
  #  Plano de usuario  (requiere gtp5g cargado en el host Proxmox)
  # ------------------------------------------------------------------
  upf:
    image: free5gc/upf:${FREE5GC_TAG}
    container_name: upf
    restart: unless-stopped
    command: bash -c "/free5gc/upf-nat.sh && ./upf -c ../config/upfcfg.yaml"
    volumes:
      - ./config/upfcfg.yaml:/free5gc/config/upfcfg.yaml:ro
      - ./config/upf-nat.sh:/free5gc/upf-nat.sh:ro
    cap_add: [ NET_ADMIN, SYS_MODULE ]
    privileged: true
    devices: [ "/dev/net/tun:/dev/net/tun" ]
    sysctls:
      net.ipv4.ip_forward: "1"
      net.ipv4.conf.all.rp_filter: "2"
    networks:
      sbi_net: { ipv4_address: 10.100.200.20 }   # N4 (PFCP)
      n3_net:  { ipv4_address: 10.100.201.20 }   # N3 (GTP-U)
      dn_net:  { ipv4_address: 10.100.204.20 }   # Data Network
    logging:
      driver: json-file
      options: { max-size: "20m", max-file: "3" }

  # ------------------------------------------------------------------
  #  RAN emulada
  # ------------------------------------------------------------------
  gnb:
    image: free5gc/ueransim:${UERANSIM_TAG}
    container_name: gnb
    restart: unless-stopped
    command: ./nr-gnb -c ./config/gnb.yaml
    volumes: [ ./ueransim/gnb.yaml:/ueransim/config/gnb.yaml:ro ]
    cap_add: [ NET_ADMIN ]
    devices: [ "/dev/net/tun:/dev/net/tun" ]
    networks:
      sbi_net:   { ipv4_address: 10.100.200.30 }   # N2 (NGAP/SCTP)
      n3_net:    { ipv4_address: 10.100.201.30 }   # N3 (GTP-U)
      radio_net: { ipv4_address: 10.100.203.30 }   # radio emulada
    depends_on: [ amf, upf ]

  ue:
    image: free5gc/ueransim:${UERANSIM_TAG}
    container_name: ue
    restart: unless-stopped
    entrypoint: [ "/bin/bash", "/ueransim/ue-entrypoint.sh" ]
    volumes:
      - ./ueransim/ue.yaml:/ueransim/config/ue.yaml:ro
      - ./ueransim/ue-entrypoint.sh:/ueransim/ue-entrypoint.sh:ro
    cap_add: [ NET_ADMIN ]
    devices: [ "/dev/net/tun:/dev/net/tun" ]
    sysctls:
      net.ipv4.conf.all.rp_filter: "2"
    networks:
      radio_net: { ipv4_address: 10.100.203.31 }
      obs_net:   { ipv4_address: 10.100.205.31 }   # solo para exponer sondas
    depends_on: [ gnb, smf ]

  webui:
    <<: *f5gc-common
    image: free5gc/webui:${FREE5GC_TAG}
    container_name: webui
    command: ./webui -c ../config/webuicfg.yaml
    environment:
      DB_URI: mongodb://mongodb:27017/free5gc
      GIN_MODE: release
    volumes: [ ./config/webuicfg.yaml:/free5gc/config/webuicfg.yaml:ro ]
    networks: { sbi_net: { ipv4_address: 10.100.200.41 } }
    ports: [ "5000:5000" ]
    depends_on:
      mongodb: { condition: service_healthy }

  # ------------------------------------------------------------------
  #  Data Network — destino del trafico del UE
  # ------------------------------------------------------------------
  dn-iperf:
    image: networkstatic/iperf3   # ⚠️ verifica el tag antes de usar
    container_name: dn-iperf
    restart: unless-stopped
    command: -s
    networks: { dn_net: { ipv4_address: 10.100.204.10 } }

  # ==================================================================
  #  OBSERVABILIDAD
  # ==================================================================

  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    restart: unless-stopped
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=15d
      - --web.enable-lifecycle
    volumes:
      - ./observability/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prom-data:/prometheus
    networks: { obs_net: { ipv4_address: 10.100.205.10 } }
    ports: [ "9090:9090" ]

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_INSTALL_PLUGINS: ""
    volumes:
      - grafana-data:/var/lib/grafana
      - ./observability/grafana/provisioning:/etc/grafana/provisioning:ro
    networks: { obs_net: { ipv4_address: 10.100.205.11 } }
    ports: [ "3000:3000" ]
    depends_on: [ prometheus ]

  # Recursos del contenedor LXC por contenedor Docker
  cadvisor:
    image: gcr.io/cadvisor/cadvisor:v0.49.1
    container_name: cadvisor
    restart: unless-stopped
    privileged: true
    command:
      - --docker_only=true
      - --housekeeping_interval=10s
      - --store_container_labels=false
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:rw
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
      - /dev/disk/:/dev/disk:ro
    devices: [ "/dev/kmsg:/dev/kmsg" ]
    networks: { obs_net: { ipv4_address: 10.100.205.12 } }

  # CPU / RAM / red del LXC completo
  node-exporter:
    image: prom/node-exporter:latest
    container_name: node-exporter
    restart: unless-stopped
    pid: host
    command:
      - --path.procfs=/host/proc
      - --path.sysfs=/host/sys
      - --path.rootfs=/host/root
      - --collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)
      - --collector.netdev.device-include=^(eth\d+|tailscale0|upfgtp|uesimtun\d+|br-.*)$$
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /:/host/root:ro,rslave
    networks: { obs_net: { ipv4_address: 10.100.205.13 } }

  # Latencia medida DESDE el UE, a traves de uesimtun0
  blackbox:
    image: prom/blackbox-exporter:latest
    container_name: blackbox
    restart: unless-stopped
    command: --config.file=/config/blackbox.yml
    volumes: [ ./observability/blackbox/blackbox.yml:/config/blackbox.yml:ro ]
    cap_add: [ NET_RAW ]
    network_mode: "service:ue"      # <-- vive en el netns del UE
    depends_on: [ ue ]

  pushgateway:
    image: prom/pushgateway:latest
    container_name: pushgateway
    restart: unless-stopped
    networks: { obs_net: { ipv4_address: 10.100.205.14 } }

  # Throughput periodico a traves del tunel
  iperf-probe:
    image: alpine:3.20
    container_name: iperf-probe
    restart: unless-stopped
    entrypoint: [ "/bin/sh", "/probe/iperf-probe.sh" ]
    volumes: [ ./observability/probe/iperf-probe.sh:/probe/iperf-probe.sh:ro ]
    environment:
      IPERF_TARGET: 10.100.204.10
      PUSHGATEWAY: http://10.100.205.14:9091
      INTERVAL: "300"
      DURATION: "10"
    network_mode: "service:ue"      # <-- tambien en el netns del UE
    depends_on: [ ue ]
```

### 7.4 El truco clave: sondas dentro del namespace del UE

`network_mode: "service:ue"` hace que `blackbox` e `iperf-probe` compartan el *network namespace* del contenedor `ue`. Esto significa que **ven `uesimtun0` directamente**, sin trucos de `nsenter` ni `docker exec`.

La consecuencia práctica es que las métricas de latencia y throughput que produce Prometheus son las que experimenta el UE atravesando el túnel GTP-U completo: UE → radio emulada → gNB → N3 → UPF → `upfgtp` → Data Network. Eso es justo lo que quieres medir. Un `ping` desde el LXC no mide nada de eso.

Como el contenedor `ue` también está en `obs_net`, Prometheus alcanza al blackbox en `10.100.205.31:9115`, y la sonda de iperf alcanza al Pushgateway. El aislamiento se mantiene porque el `ue-entrypoint.sh` añade una **ruta específica** que fuerza el tráfico hacia el DN por el túnel.

### 7.5 `ueransim/ue-entrypoint.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

DN_SUBNET="${DN_SUBNET:-10.100.204.0/24}"
TUN_IFACE="${TUN_IFACE:-uesimtun0}"
TIMEOUT="${TUN_TIMEOUT:-90}"

echo "[ue] arrancando nr-ue..."
./nr-ue -c ./config/ue.yaml &
UE_PID=$!

echo "[ue] esperando a ${TUN_IFACE} (max ${TIMEOUT}s)..."
for i in $(seq 1 "$TIMEOUT"); do
  if ip link show "$TUN_IFACE" >/dev/null 2>&1; then
    UE_IP=$(ip -4 -o addr show "$TUN_IFACE" | awk '{print $4}' | cut -d/ -f1)
    if [ -n "$UE_IP" ]; then
      echo "[ue] ${TUN_IFACE} arriba con IP ${UE_IP} (tras ${i}s)"
      # Fuerza el trafico hacia el Data Network por el tunel 5G.
      ip route replace "$DN_SUBNET" dev "$TUN_IFACE" src "$UE_IP"
      # MTU explicito: 1400 (docker) - 36 (GTP-U) = 1364
      ip link set dev "$TUN_IFACE" mtu 1364
      echo "[ue] ruta y MTU configurados"
      break
    fi
  fi
  sleep 1
done

if ! ip link show "$TUN_IFACE" >/dev/null 2>&1; then
  echo "[ue] ERROR: ${TUN_IFACE} no aparecio en ${TIMEOUT}s" >&2
  echo "[ue] revisa: registro NAS en logs del AMF, asociacion PFCP en el SMF" >&2
fi

wait $UE_PID
```

El `mtu 1364` no es cosmético. Sin él, un `iperf3 -c` grande fragmenta o se cuelga (fallo F7). El cálculo: MTU de la bridge Docker (1400) menos el overhead GTP-U (20 IP + 8 UDP + 8 GTP-U = 36) = 1364.

### 7.6 `config/upf-nat.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

UE_POOL="${UE_POOL:-10.60.0.0/16}"
DN_PREFIX="${DN_PREFIX:-10.100.204.}"

sysctl -w net.ipv4.ip_forward=1

# Resolver la interfaz del Data Network POR SU IP, no por nombre.
# Docker no garantiza que dn_net sea eth0/eth1/eth2 de forma estable.
DN_IF=$(ip -o -4 addr show | awk -v p="$DN_PREFIX" '$4 ~ "^"p {print $2; exit}')

if [ -z "$DN_IF" ]; then
  echo "[upf] ERROR: no encuentro interfaz en ${DN_PREFIX}0/24" >&2
  ip -o -4 addr show >&2
  exit 1
fi

echo "[upf] Data Network en interfaz ${DN_IF}"

iptables -t nat -C POSTROUTING -s "$UE_POOL" -o "$DN_IF" -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s "$UE_POOL" -o "$DN_IF" -j MASQUERADE

iptables -C FORWARD -i upfgtp -j ACCEPT 2>/dev/null \
  || iptables -A FORWARD -i upfgtp -j ACCEPT

echo "[upf] NAT configurado para ${UE_POOL} -> ${DN_IF}"
```

Este script resuelve el fallo F6. La detección de interfaz por prefijo de IP es deliberada: el `docker-compose` original de free5GC asume `eth0`, y en cuanto añades una segunda red esa suposición se rompe de forma no determinista entre reinicios.

### 7.7 `observability/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    lab: lab-free5gc
    plmn: "732101"

scrape_configs:

  - job_name: prometheus
    static_configs: [ { targets: [ "localhost:9090" ] } ]

  # Recursos del LXC completo
  - job_name: node
    static_configs:
      - targets: [ "10.100.205.13:9100" ]
        labels: { scope: lxc }

  # Recursos por contenedor Docker
  - job_name: cadvisor
    static_configs:
      - targets: [ "10.100.205.12:8080" ]
        labels: { scope: container }
    metric_relabel_configs:
      # Reduce cardinalidad: quedate con los contenedores del lab
      - source_labels: [ name ]
        regex: "(nrf|amf|smf|upf|ausf|udm|udr|pcf|nssf|gnb|ue|mongodb)"
        action: keep

  # Latencia ICMP DESDE el UE a traves de uesimtun0
  - job_name: blackbox-ue-icmp
    metrics_path: /probe
    params: { module: [ icmp_ue ] }
    static_configs:
      - targets:
          - 10.100.204.10        # servidor iperf en el DN
          - 10.100.204.20        # interfaz DN del propio UPF
    relabel_configs:
      - source_labels: [ __address__ ]
        target_label: __param_target
      - source_labels: [ __param_target ]
        target_label: instance
      - target_label: __address__
        replacement: 10.100.205.31:9115    # blackbox en el netns del UE
      - target_label: plane
        replacement: user

  # Salud del plano de control por TCP
  - job_name: blackbox-sbi-tcp
    metrics_path: /probe
    params: { module: [ tcp_connect ] }
    static_configs:
      - targets:
          - 10.100.200.10:8000   # NRF
          - 10.100.200.11:8000   # AMF SBI
          - 10.100.200.12:8000   # SMF SBI
          - 10.100.200.40:27017  # MongoDB
    relabel_configs:
      - source_labels: [ __address__ ]
        target_label: __param_target
      - source_labels: [ __param_target ]
        target_label: instance
      - target_label: __address__
        replacement: 10.100.205.31:9115
      - target_label: plane
        replacement: control

  # Throughput empujado por la sonda iperf3
  - job_name: pushgateway
    honor_labels: true
    static_configs: [ { targets: [ "10.100.205.14:9091" ] } ]

  # ⚠️ free5GC no expone /metrics de Prometheus de forma nativa en todas
  # las versiones. Comprueba con:
  #     curl -s http://10.100.200.11:8000/metrics
  # Si responde, descomenta este bloque.
  # - job_name: free5gc-nfs
  #   static_configs:
  #     - targets:
  #         - 10.100.200.11:8000
  #         - 10.100.200.12:8000
  #         - 10.100.200.20:8000
```

### 7.8 `observability/blackbox/blackbox.yml`

```yaml
modules:

  icmp_ue:
    prober: icmp
    timeout: 5s
    icmp:
      preferred_ip_protocol: ip4
      ip_protocol_fallback: false
      # Sin source_ip_address: la ruta especifica que anade
      # ue-entrypoint.sh ya fuerza la salida por uesimtun0.

  tcp_connect:
    prober: tcp
    timeout: 5s
    tcp:
      preferred_ip_protocol: ip4

  http_2xx:
    prober: http
    timeout: 5s
    http:
      valid_status_codes: []
      preferred_ip_protocol: ip4
```

### 7.9 `observability/probe/iperf-probe.sh`

```sh
#!/bin/sh
set -eu

apk add --no-cache iperf3 curl iproute2 >/dev/null 2>&1 || true

TARGET="${IPERF_TARGET:-10.100.204.10}"
PGW="${PUSHGATEWAY:-http://10.100.205.14:9091}"
INTERVAL="${INTERVAL:-300}"
DURATION="${DURATION:-10}"
TUN="${TUN_IFACE:-uesimtun0}"

push() {   # nombre valor tipo ayuda direccion
  printf '# HELP %s %s\n# TYPE %s %s\n%s %s\n' "$1" "$4" "$1" "$3" "$1" "$2"
}

while true; do
  if ! ip link show "$TUN" >/dev/null 2>&1; then
    echo "[probe] $TUN no existe todavia; espero 30s"
    sleep 30
    continue
  fi

  UE_IP=$(ip -4 -o addr show "$TUN" | awk '{print $4}' | cut -d/ -f1)
  [ -z "$UE_IP" ] && { sleep 30; continue; }

  # --- Downlink (servidor -> UE) ---
  DL=$(iperf3 -c "$TARGET" -B "$UE_IP" -t "$DURATION" -R -J 2>/dev/null \
       | grep -o '"bits_per_second":[0-9.]*' | tail -1 | cut -d: -f2 || echo 0)

  # --- Uplink (UE -> servidor) ---
  UL=$(iperf3 -c "$TARGET" -B "$UE_IP" -t "$DURATION" -J 2>/dev/null \
       | grep -o '"bits_per_second":[0-9.]*' | tail -1 | cut -d: -f2 || echo 0)

  {
    push ue_throughput_downlink_bps "${DL:-0}" gauge "Throughput DL medido en uesimtun0"
    push ue_throughput_uplink_bps   "${UL:-0}" gauge "Throughput UL medido en uesimtun0"
    push ue_tunnel_up 1 gauge "1 si uesimtun0 existe con IP"
  } | curl -s --data-binary @- \
      "${PGW}/metrics/job/ue_iperf/instance/${UE_IP}/plane/user" || true

  echo "[probe] DL=${DL} UL=${UL} desde ${UE_IP}"
  sleep "$INTERVAL"
done
```

Es intencionalmente una sonda de baja frecuencia (cada 5 min). Un `iperf3` continuo saturaría el UPF y contaminaría las métricas de CPU que quieres correlacionar.

### 7.10 Provisioning de Grafana

`observability/grafana/provisioning/datasources/prometheus.yml`:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://10.100.205.10:9090
    isDefault: true
    editable: false
```

`observability/grafana/provisioning/dashboards/dashboards.yml`:

```yaml
apiVersion: 1
providers:
  - name: lab5g
    orgId: 1
    folder: 'lab-free5gc'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards/json
```

Coloca los JSON en `observability/grafana/provisioning/dashboards/json/`. Dashboards de la comunidad que encajan con este stack (⚠️ verifica los IDs en grafana.com, cambian):

- **Node Exporter Full** — ID 1860
- **cAdvisor / Docker** — ID 14282 o 193
- **Blackbox Exporter** — ID 7587 o 13659

### 7.11 Consultas PromQL que importan en este lab

Estas son las que responden "¿el problema es la red 5G o el contenedor?", que es el objetivo declarado de cruzar rendimiento con recursos:

```promql
# Latencia del plano de usuario, vista por el UE
probe_duration_seconds{job="blackbox-ue-icmp", instance="10.100.204.10"}

# Throughput DL/UL a traves del tunel
ue_throughput_downlink_bps / 1e6
ue_throughput_uplink_bps   / 1e6

# CPU del UPF: aqui es donde se ve si gtp5g esta siendo el cuello de botella
rate(container_cpu_usage_seconds_total{name="upf"}[2m]) * 100

# Correlacion: latencia del UE vs CPU del UPF (superpon en el mismo panel)
probe_duration_seconds{job="blackbox-ue-icmp"}
rate(container_cpu_usage_seconds_total{name="upf"}[2m])

# Paquetes GTP-U por segundo en N3 (interfaz del contenedor gnb)
rate(container_network_receive_packets_total{name="gnb"}[1m])

# Trafico atravesando upfgtp (la interfaz que crea gtp5g)
rate(node_network_receive_bytes_total{device="upfgtp"}[1m]) * 8
rate(node_network_transmit_bytes_total{device="upfgtp"}[1m]) * 8

# Presion de memoria del LXC completo
1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)

# Salud del plano de control: alguna NF caida
min_over_time(probe_success{job="blackbox-sbi-tcp"}[5m]) == 0

# El tunel del UE se cayo
ue_tunnel_up == 0 or absent(ue_tunnel_up)
```

**Advertencia sobre `node-exporter` en LXC:** Proxmox monta `lxcfs` sobre `/proc/meminfo`, `/proc/cpuinfo`, `/proc/stat` y algunos más, así que esas métricas reflejan los límites del contenedor. Pero `/proc/net/dev`, información de disco y varias otras **siguen viendo el host**. Si necesitas métricas fiables del hipervisor, añade `prometheus-pve-exporter` apuntando a la API de Proxmox desde fuera del LXC. Documenta esta limitación en el dashboard para no sacar conclusiones equivocadas.
---

## 8. CI/CD — self-hosted runner en el LXC

### 8.1 Advertencia de seguridad, primero

Un self-hosted runner ejecuta cualquier código que llegue al repositorio, con los permisos del usuario del runner. En este lab ese usuario está en el grupo `docker` dentro de un **LXC privilegiado que comparte el kernel del host Proxmox**. La cadena de escalada es corta: PR malicioso → runner → `docker run --privileged` → kernel del host.

Medidas mínimas, no opcionales:

- **Repositorio privado.** GitHub lo dice explícitamente: no uses self-hosted runners en repos públicos. Un fork puede abrir un PR que ejecute lo que quiera.
- Si necesitas que sea público, desactiva por completo los workflows en `pull_request` desde forks (Settings → Actions → *Require approval for all outside collaborators*) y no uses nunca `pull_request_target`.
- Dispara solo en ramas protegidas: `on: push: branches: [main]`.
- Usuario dedicado sin sudo (`runner`), solo en el grupo `docker`.
- Trata el LXC como desechable. Ten un snapshot limpio de Proxmox al que revertir.

### 8.2 Instalación del runner

```bash
# --- DENTRO DEL LXC ---
useradd -m -s /bin/bash runner
usermod -aG docker runner

su - runner
mkdir actions-runner && cd actions-runner
# ⚠️ Coge la URL de la version actual desde
#    Settings -> Actions -> Runners -> New self-hosted runner
curl -o actions-runner-linux-x64.tar.gz -L \
  https://github.com/actions/runner/releases/download/vX.Y.Z/actions-runner-linux-x64-X.Y.Z.tar.gz
tar xzf actions-runner-linux-x64.tar.gz

./config.sh --url https://github.com/J4F3ET/lab-free5gc \
            --token <TOKEN> \
            --name lxc-lab5g \
            --labels self-hosted,linux,x64,lxc,free5gc \
            --work _work \
            --unattended

exit
cd /home/runner/actions-runner
./svc.sh install runner
./svc.sh start
systemctl status actions.runner.*.service
```

Las etiquetas `lxc,free5gc` permiten que el workflow exija ese runner concreto con `runs-on: [self-hosted, lxc, free5gc]`.

### 8.3 El workflow

`.github/workflows/deploy.yml`:

```yaml
name: deploy-5g-lab

on:
  push:
    branches: [ main ]
    paths:
      - 'config/**'
      - 'ueransim/**'
      - 'observability/**'
      - 'docker-compose.yml'
      - '.github/workflows/deploy.yml'
  workflow_dispatch:
    inputs:
      skip_smoke:
        description: 'Saltar el smoke test del UE'
        type: boolean
        default: false

# Nunca dos despliegues simultaneos sobre el mismo LXC
concurrency:
  group: lab5g-deploy
  cancel-in-progress: false

env:
  COMPOSE_PROJECT_NAME: lab5g
  DOCKER_BUILDKIT: '1'

jobs:

  # ------------------------------------------------------------------
  #  1. Validacion estatica — sin tocar el despliegue vivo
  # ------------------------------------------------------------------
  validate:
    runs-on: [ self-hosted, lxc, free5gc ]
    steps:
      - uses: actions/checkout@v4

      - name: Verificar kernel, modulos y devices
        run: bash ci/preflight.sh

      - name: Validar sintaxis de docker-compose
        run: docker compose config --quiet

      - name: Coherencia de PLMN / TAC / S-NSSAI / nodeID
        run: |
          python3 -m pip install --quiet --user pyyaml
          python3 ci/check-plmn.py

  # ------------------------------------------------------------------
  #  2. Despliegue
  # ------------------------------------------------------------------
  deploy:
    needs: validate
    runs-on: [ self-hosted, lxc, free5gc ]
    steps:
      - uses: actions/checkout@v4

      - name: Generar .env con secretos
        run: |
          cp .env.example .env
          echo "GRAFANA_ADMIN_PASSWORD=${{ secrets.GRAFANA_ADMIN_PASSWORD }}" >> .env

      - name: Reaplicar reglas de red del host
        run: sudo -n /usr/local/sbin/lab5g-netrules.sh || true

      - name: Descargar imagenes
        run: docker compose pull --quiet

      - name: Levantar el nucleo (sin RAN todavia)
        run: |
          docker compose up -d --remove-orphans \
            mongodb nrf udr udm ausf pcf nssf amf upf smf webui \
            dn-iperf prometheus grafana cadvisor node-exporter pushgateway

      - name: Esperar a que el plano de control este listo
        run: bash ci/wait-ready.sh

      - name: Aprovisionar suscriptor en MongoDB
        run: bash ci/provision-subscriber.sh

      - name: Levantar RAN emulada y sondas
        run: docker compose up -d gnb ue blackbox iperf-probe

  # ------------------------------------------------------------------
  #  3. Smoke test end-to-end
  # ------------------------------------------------------------------
  smoke:
    needs: deploy
    if: ${{ !inputs.skip_smoke }}
    runs-on: [ self-hosted, lxc, free5gc ]
    steps:
      - uses: actions/checkout@v4

      - name: Validar tunel uesimtun0 y conectividad
        run: bash ci/smoke-ue.sh

      - name: Recolectar diagnostico si algo fallo
        if: failure()
        run: bash ci/collect-logs.sh

      - name: Publicar diagnostico
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: diagnostico-${{ github.run_number }}
          path: /tmp/lab5g-diag/
          retention-days: 7

  # ------------------------------------------------------------------
  #  4. Rollback si el smoke test fallo
  # ------------------------------------------------------------------
  rollback:
    needs: [ deploy, smoke ]
    if: failure()
    runs-on: [ self-hosted, lxc, free5gc ]
    steps:
      - name: Detener RAN para no dejar el lab en estado inconsistente
        run: docker compose stop gnb ue blackbox iperf-probe || true
      - name: Resumen
        run: |
          echo "::error::Smoke test fallido. El nucleo sigue en pie para" \
               "inspeccion; RAN detenida. Revisa el artefacto de diagnostico."
```

### 8.4 `ci/preflight.sh`

```bash
#!/usr/bin/env bash
# Verifica que el entorno LXC/host puede soportar free5GC.
# Falla RAPIDO y con mensajes accionables.
set -uo pipefail

fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }

echo "=== Preflight lab-free5gc ==="

# --- Capa 0: modulos de kernel (compartidos con el host Proxmox) ---
if grep -q '^gtp5g ' /proc/modules; then
  ok "modulo gtp5g cargado"
else
  bad "gtp5g NO cargado. En el HOST Proxmox: modprobe gtp5g"
  bad "  (si falla tras un upgrade de kernel, recompila via DKMS)"
fi

if grep -q '^sctp ' /proc/modules; then
  ok "modulo sctp cargado"
else
  bad "sctp NO cargado -> N2/NGAP fallara. En el HOST: modprobe sctp"
fi

# --- Capa 1: devices y capabilities ---
[ -c /dev/net/tun ] \
  && ok "/dev/net/tun presente" \
  || bad "/dev/net/tun ausente -> revisa lxc.mount.entry en la config del LXC"

if capsh --print 2>/dev/null | grep -q 'cap_net_admin'; then
  ok "CAP_NET_ADMIN disponible"
else
  bad "sin CAP_NET_ADMIN -> el LXC no es privilegiado o hay cap.drop"
fi

# Prueba real: crear y borrar un netdev dummy
if ip link add lab5gtest type dummy 2>/dev/null; then
  ip link del lab5gtest
  ok "puedo crear interfaces de red"
else
  bad "no puedo crear netdevs -> falta lxc.apparmor.profile: unconfined"
fi

# --- Capa 2: sysctl ---
[ "$(sysctl -n net.ipv4.ip_forward)" = "1" ] \
  && ok "ip_forward activo" \
  || bad "ip_forward=0 -> sysctl -w net.ipv4.ip_forward=1"

rp=$(sysctl -n net.ipv4.conf.all.rp_filter)
[ "$rp" = "2" ] && ok "rp_filter en modo loose (2)" \
                || warn "rp_filter=$rp; recomendado 2 con multiples redes Docker"

# --- Capa 2: Docker ---
docker info >/dev/null 2>&1 \
  && ok "docker operativo" \
  || bad "docker no responde"

mtu=$(docker network inspect bridge -f '{{index .Options "com.docker.network.driver.mtu"}}' 2>/dev/null)
[ -n "$mtu" ] && ok "MTU por defecto de Docker: $mtu" \
              || warn "MTU de Docker sin fijar; con Tailscale puede dar problemas de TCP"

# --- Capa 3: Tailscale ---
if command -v tailscale >/dev/null 2>&1; then
  if tailscale status >/dev/null 2>&1; then
    ok "tailscale conectado"
    routes=$(tailscale status --json 2>/dev/null \
             | grep -o '"AdvertisedRoutes":\[[^]]*\]' || true)
    [ -n "$routes" ] && ok "rutas anunciadas: $routes" \
                     || warn "sin rutas anunciadas"
  else
    warn "tailscale instalado pero no conectado"
  fi

  if iptables -C DOCKER-USER -i tailscale0 -d 10.100.200.0/24 -j ACCEPT 2>/dev/null \
     || iptables -C DOCKER-USER -i tailscale0 -j ACCEPT 2>/dev/null; then
    ok "regla DOCKER-USER para tailscale0 presente"
  else
    bad "falta ACCEPT en DOCKER-USER para tailscale0"
    bad "  -> ejecuta /usr/local/sbin/lab5g-netrules.sh"
  fi
fi

# --- Recursos ---
mem=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
[ "$mem" -ge 4096 ] && ok "memoria: ${mem} MiB" \
                    || warn "solo ${mem} MiB; recomendado >= 4096"

echo
[ $fail -eq 0 ] && echo "Preflight OK" || { echo "Preflight FALLIDO"; exit 1; }
```

### 8.5 `ci/wait-ready.sh`

```bash
#!/usr/bin/env bash
set -uo pipefail

TIMEOUT="${TIMEOUT:-180}"
deadline=$(( $(date +%s) + TIMEOUT ))

wait_for() {
  local label="$1" cmd="$2"
  printf '  esperando %-28s' "$label"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if eval "$cmd" >/dev/null 2>&1; then echo "OK"; return 0; fi
    sleep 2
  done
  echo "TIMEOUT"; return 1
}

fail=0

wait_for "MongoDB" \
  "docker compose exec -T mongodb mongosh --quiet --eval \"db.adminCommand('ping')\"" || fail=1

wait_for "NRF (SBI)" \
  "docker compose exec -T mongodb bash -c 'exec 3<>/dev/tcp/10.100.200.10/8000'" || fail=1

wait_for "AMF NGAP (SCTP 38412)" \
  "docker compose exec -T amf ss -lntu 2>/dev/null | grep -q 38412 \
   || docker compose logs amf 2>&1 | grep -qi 'ngap.*listen'" || fail=1

# El registro en el NRF es la senal real de que la NF esta viva
for nf in AMF SMF AUSF UDM UDR PCF NSSF UPF; do
  wait_for "registro de ${nf} en NRF" \
    "docker compose logs nrf 2>&1 | grep -qi '${nf}.*register'" || true
done

# La asociacion PFCP es el punto critico (fallo F3)
wait_for "asociacion PFCP SMF<->UPF" \
  "docker compose logs smf 2>&1 | grep -qiE 'association setup response|pfcp association.*success'" \
  || { echo
       echo '  >>> La asociacion PFCP no se establecio.'
       echo '  >>> Causas tipicas:'
       echo '      1. nodeID distinto entre smfcfg.yaml y upfcfg.yaml'
       echo '      2. UPF muerto porque gtp5g no esta cargado'
       echo '      3. UPF no alcanzable en 10.100.200.20:8805'
       echo
       docker compose logs --tail=40 upf
       fail=1; }

exit $fail
```

### 8.6 `ci/provision-subscriber.sh`

El suscriptor vive en MongoDB, no en git. Este script lo hace idempotente y reproducible, para que el lab sobreviva a un `docker compose down -v`.

```bash
#!/usr/bin/env bash
set -euo pipefail

MCC="${MCC:-732}"
MNC="${MNC:-101}"
IMSI="${IMSI:-732101000000001}"
K="${K:-8baf473f2f8fd09487cccbd7097c6862}"
OPC="${OPC:-8e27b6af0e692e750f32667a3b14605d}"
SST="${SST:-1}"
SD="${SD:-010203}"
DNN="${DNN:-internet}"

echo "[provision] suscriptor imsi-${IMSI} en PLMN ${MCC}/${MNC}"

docker compose exec -T mongodb mongosh --quiet free5gc <<EOF
const ueId   = "imsi-${IMSI}";
const plmnID = "${MCC}${MNC}";
const snssai = { sst: ${SST}, sd: "${SD}" };

db.subscriptionData.authenticationData.authenticationSubscription.replaceOne(
  { ueId: ueId },
  {
    ueId: ueId,
    authenticationMethod: "5G_AKA",
    permanentKey: { permanentKeyValue: "${K}",
                    encryptionKey: 0, encryptionAlgorithm: 0 },
    sequenceNumber: "16f3b3f70fc2",
    authenticationManagementField: "8000",
    milenage: { op: { opValue: "", encryptionKey: 0, encryptionAlgorithm: 0 } },
    opc: { opcValue: "${OPC}", encryptionKey: 0, encryptionAlgorithm: 0 }
  },
  { upsert: true }
);

db.subscriptionData.provisionedData.amData.replaceOne(
  { ueId: ueId, servingPlmnId: plmnID },
  {
    ueId: ueId, servingPlmnId: plmnID,
    gpsis: [ "msisdn-0900000000" ],
    subscribedUeAmbr: { uplink: "1 Gbps", downlink: "2 Gbps" },
    nssai: { defaultSingleNssais: [ snssai ], singleNssais: [ snssai ] }
  },
  { upsert: true }
);

db.subscriptionData.provisionedData.smData.replaceOne(
  { ueId: ueId, servingPlmnId: plmnID, singleNssai: snssai },
  {
    ueId: ueId, servingPlmnId: plmnID, singleNssai: snssai,
    dnnConfigurations: {
      "${DNN}": {
        pduSessionTypes: {
          defaultSessionType: "IPV4",
          allowedSessionTypes: [ "IPV4" ]
        },
        sscModes: { defaultSscMode: "SSC_MODE_1",
                    allowedSscModes: [ "SSC_MODE_2", "SSC_MODE_3" ] },
        "5gQosProfile": { "5qi": 9, arp: { priorityLevel: 8,
                          preemptCap: "", preemptVuln: "" }, priorityLevel: 8 },
        sessionAmbr: { uplink: "200 Mbps", downlink: "400 Mbps" }
      }
    }
  },
  { upsert: true }
);

db.policyData.ues.smData.replaceOne(
  { ueId: ueId },
  { ueId: ueId,
    smPolicySnssaiData: {
      "0${SST}${SD}": {
        snssai: snssai,
        smPolicyDnnData: { "${DNN}": { dnn: "${DNN}" } }
      }
    }
  },
  { upsert: true }
);

print("subscriptionData.authenticationSubscription: " +
      db.subscriptionData.authenticationData.authenticationSubscription
        .countDocuments({ueId: ueId}));
print("provisionedData.amData: " +
      db.subscriptionData.provisionedData.amData.countDocuments({ueId: ueId}));
print("provisionedData.smData: " +
      db.subscriptionData.provisionedData.smData.countDocuments({ueId: ueId}));
EOF

echo "[provision] listo"
```

⚠️ Los nombres de colección y el esquema de documentos de free5GC **cambian entre versiones mayores**. Antes de confiar en este script, crea un suscriptor a mano desde el WebConsole (`http://10.100.200.41:5000`, usuario `admin` / contraseña `free5gc`) y vuelca lo que realmente escribió:

```bash
docker compose exec -T mongodb mongosh --quiet free5gc \
  --eval 'db.getCollectionNames().forEach(c => {
            print("=== " + c);
            printjson(db.getCollection(c).findOne());
          })'
```

Luego ajusta el script a ese esquema. Este paso de calibración se hace una vez y evita depurar un `Registration Reject` fantasma durante horas.

### 8.7 `ci/smoke-ue.sh` — la validación end-to-end

```bash
#!/usr/bin/env bash
set -uo pipefail

TIMEOUT="${TIMEOUT:-120}"
DN_TARGET="${DN_TARGET:-10.100.204.10}"
fail=0

step() { printf '\n\033[1m>>> %s\033[0m\n' "$1"; }
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }

# ---- 1. NG Setup: el gNB hablo con el AMF ----
step "1/6  NG Setup (N2)"
if docker compose logs gnb 2>&1 | grep -qi 'NG Setup procedure is successful'; then
  ok "gNB registrado en el AMF"
else
  bad "sin NG Setup Response"
  docker compose logs --tail=30 gnb | sed 's/^/      /'
  docker compose logs --tail=30 amf | grep -i ngap | sed 's/^/      /'
fi

# ---- 2. Registro NAS del UE ----
step "2/6  Registro NAS del UE"
if docker compose logs ue 2>&1 | grep -qiE 'Registration is successful|RM-REGISTERED'; then
  ok "UE en estado registrado"
else
  bad "UE no registrado"
  docker compose logs --tail=40 ue | grep -iE 'reject|fail|cause' | sed 's/^/      /'
fi

# ---- 3. Aparicion de uesimtun0 ----
step "3/6  Interfaz uesimtun0"
UE_IP=""
for i in $(seq 1 "$TIMEOUT"); do
  UE_IP=$(docker compose exec -T ue sh -c \
          "ip -4 -o addr show uesimtun0 2>/dev/null | awk '{print \$4}' | cut -d/ -f1" \
          2>/dev/null | tr -d '\r\n')
  [ -n "$UE_IP" ] && break
  sleep 1
done

if [ -n "$UE_IP" ]; then
  ok "uesimtun0 con IP ${UE_IP} (tras ${i}s)"
  case "$UE_IP" in
    10.60.*) ok "IP dentro del pool 10.60.0.0/16" ;;
    *)       bad "IP ${UE_IP} FUERA del pool configurado" ;;
  esac
else
  bad "uesimtun0 no aparecio en ${TIMEOUT}s"
  echo "      --- SMF ---"; docker compose logs --tail=25 smf | sed 's/^/      /'
  echo "      --- UPF ---"; docker compose logs --tail=25 upf | sed 's/^/      /'
fi

# ---- 4. La interfaz upfgtp existe en el UPF ----
step "4/6  Interfaz upfgtp (gtp5g)"
if docker compose exec -T upf ip link show upfgtp >/dev/null 2>&1; then
  ok "upfgtp creada por el modulo gtp5g"
  docker compose exec -T upf ip -d link show upfgtp | sed 's/^/      /'
else
  bad "upfgtp NO existe -> gtp5g no cargado o UPF sin privilegios"
fi

# ---- 5. Conectividad a traves del tunel ----
step "5/6  ICMP a traves de uesimtun0"
if [ -n "$UE_IP" ]; then
  if docker compose exec -T ue ping -I uesimtun0 -c 4 -W 2 "$DN_TARGET" >/dev/null 2>&1; then
    ok "ping ${DN_TARGET} correcto"
  else
    bad "ping fallido -> NAT/routing en el UPF, o endpoint N3 incorrecto"
    echo "      Rutas del UE:"
    docker compose exec -T ue ip route | sed 's/^/        /'
    echo "      NAT del UPF:"
    docker compose exec -T upf iptables -t nat -L POSTROUTING -n -v | sed 's/^/        /'
  fi
fi

# ---- 6. Throughput minimo ----
# NOTA: la imagen de UERANSIM no incluye iperf3. Instalalo en el arranque
# (apt-get install -y iperf3 en ue-entrypoint.sh) o construye una imagen
# derivada. Si no esta disponible, este paso se omite sin marcar fallo.
step "6/6  Throughput (iperf3, 5s)"
if ! docker compose exec -T ue sh -c 'command -v iperf3' >/dev/null 2>&1; then
  printf '  \033[33mSKIP\033[0m iperf3 no disponible en la imagen del UE\n'
  UE_IP=""
fi
if [ -n "$UE_IP" ]; then
  OUT=$(docker compose exec -T ue sh -c \
        "iperf3 -c ${DN_TARGET} -B ${UE_IP} -t 5 -J 2>/dev/null" || true)
  BPS=$(echo "$OUT" | grep -o '"bits_per_second":[0-9.]*' | tail -1 | cut -d: -f2)
  if [ -n "${BPS:-}" ] && [ "${BPS%.*}" -gt 1000000 ]; then
    ok "throughput $(( ${BPS%.*} / 1000000 )) Mbps"
  else
    bad "throughput nulo o irrisorio -> sospecha de MTU (F7)"
    echo "      MTU de uesimtun0:"
    docker compose exec -T ue ip link show uesimtun0 | sed 's/^/        /'
  fi
fi

echo
[ $fail -eq 0 ] && echo "SMOKE TEST OK" || { echo "SMOKE TEST FALLIDO"; exit 1; }
```

### 8.8 `ci/collect-logs.sh`

```bash
#!/usr/bin/env bash
set -uo pipefail
OUT=/tmp/lab5g-diag
rm -rf "$OUT"; mkdir -p "$OUT"

echo "[diag] recolectando en $OUT"

# Logs de cada contenedor
for c in $(docker compose ps --services 2>/dev/null); do
  docker compose logs --no-color --tail=500 "$c" > "$OUT/log-${c}.txt" 2>&1 || true
done

# Estado de red del host LXC
{ echo "=== ip addr ==="        ; ip addr
  echo "=== ip route ==="       ; ip route show table all
  echo "=== iptables filter ===" ; iptables -L -n -v
  echo "=== iptables nat ==="   ; iptables -t nat -L -n -v
  echo "=== iptables mangle ===" ; iptables -t mangle -L -n -v
  echo "=== sysctl ==="         ; sysctl net.ipv4.ip_forward \
                                          net.ipv4.conf.all.rp_filter
  echo "=== modulos ==="        ; grep -E '^(gtp5g|sctp)' /proc/modules
  echo "=== docker networks ===" ; docker network ls
} > "$OUT/host-network.txt" 2>&1

# Estado interno del UPF y del UE
docker compose exec -T upf sh -c \
  'ip addr; echo ---; ip route; echo ---; ip -d link show upfgtp; echo ---;
   iptables -t nat -L -n -v' > "$OUT/upf-state.txt" 2>&1 || true

docker compose exec -T ue sh -c \
  'ip addr; echo ---; ip route; echo ---; ip link show uesimtun0' \
  > "$OUT/ue-state.txt" 2>&1 || true

# Configuracion efectiva
docker compose config > "$OUT/compose-resolved.yml" 2>&1 || true
cp -r config ueransim "$OUT/" 2>/dev/null || true

# Captura corta de los tres planos
timeout 15 docker compose exec -T gnb sh -c \
  'tcpdump -i any -c 200 -w - "sctp port 38412 or udp port 2152"' \
  > "$OUT/capture-gnb.pcap" 2>/dev/null || true

timeout 15 docker compose exec -T upf sh -c \
  'tcpdump -i any -c 200 -w - "udp port 8805 or udp port 2152"' \
  > "$OUT/capture-upf.pcap" 2>/dev/null || true

echo "[diag] listo: $(du -sh $OUT | cut -f1)"
```

Los `.pcap` se suben como artefacto de la ejecución y se abren directamente en Wireshark. Eso convierte cada fallo de CI en un caso de estudio reproducible en lugar de en "otra vez no funciona".
---

## 9. Playbook de depuración — de `docker compose up` a `uesimtun0`

### 9.1 La secuencia que debe ocurrir

Depurar sin este mapa mental es adivinar. Ocho etapas, en este orden estricto. Cada una **falla de forma distinta** y tiene un punto de observación distinto.

| # | Etapa | Protocolo / puerto | Dónde mirar | Señal de éxito |
|---|---|---|---|---|
| 1 | Registro de NFs en el NRF | HTTP/2, TCP 8000 | logs NRF | `NF register success` por cada NF |
| 2 | **PFCP Association** SMF↔UPF | PFCP, UDP 8805 (N4) | logs SMF | `Association Setup Response` |
| 3 | SCTP INIT gNB→AMF | SCTP 38412 (N2) | `tcpdump` en gnb | 4-way handshake SCTP completo |
| 4 | **NG Setup** | NGAP sobre SCTP | logs gNB | `NG Setup procedure is successful` |
| 5 | Enlace radio emulado UE↔gNB | UDP 4997 (UERANSIM) | logs UE | `Connection setup for PLMN ...` |
| 6 | Registro NAS + 5G-AKA | NAS sobre NGAP | logs UE + AMF | `Registration is successful` |
| 7 | **PDU Session Establishment** | NAS-SM + PFCP + NGAP | logs SMF | `PDU Session Establishment Accept` |
| 8 | Túnel de datos | GTP-U, UDP 2152 (N3) | `ip addr` en ue | `uesimtun0` con IP de 10.60.0.0/16 |

Las tres etapas en negrita son donde se rompe el 90% de los laboratorios.

### 9.2 Árbol de decisión

```
¿Existe uesimtun0?
│
├─ NO ──> ¿El UE se registró? (log ue: "Registration is successful")
│         │
│         ├─ NO ──> ¿Hubo NG Setup? (log gnb: "NG Setup ... successful")
│         │         │
│         │         ├─ NO ──> ¿Llega SCTP al AMF?
│         │         │         tcpdump -i any sctp port 38412
│         │         │         ├─ Sin paquetes ──> [S1] conectividad N2
│         │         │         ├─ INIT sin ACK ──> [S2] módulo sctp / AMF no escucha
│         │         │         └─ NGSetupFailure ─> [S3] PLMN / TAC
│         │         │
│         │         └─ SÍ ──> ¿El UE ve al gNB? (UDP 4997)
│         │                   ├─ NO ──> [S4] gnbSearchList / radio_net
│         │                   └─ SÍ ──> [S5] fallo de autenticación / suscriptor
│         │
│         └─ SÍ ──> [S6] PDU Session Establishment Reject
│                   └─ ¿Asociación PFCP establecida?
│                      ├─ NO ──> [S7] nodeID PFCP / UPF caído
│                      └─ SÍ ──> [S8] DNN / S-NSSAI / pool de IPs
│
└─ SÍ ──> ¿ping -I uesimtun0 al DN funciona?
          ├─ NO ──> ¿Se ven paquetes GTP-U en N3?
          │         tcpdump -i any udp port 2152
          │         ├─ NO ──> [S9] endpoint N3 mal configurado
          │         └─ SÍ ──> [S10] NAT / forwarding en el UPF
          │
          └─ SÍ ──> ¿iperf3 grande funciona?
                    ├─ NO ──> [S11] MTU
                    └─ SÍ ──> laboratorio operativo
```

### 9.3 Diagnóstico por síntoma

---

**[S1] No llega ningún paquete SCTP al AMF**

```bash
# ¿Están en la misma red?
docker compose exec gnb ip -br addr
docker compose exec amf ip -br addr
docker compose exec gnb ping -c2 10.100.200.11

# ¿El gNB está usando la IP correcta para N2?
docker compose exec gnb grep ngapIp /ueransim/config/gnb.yaml
```

Causa habitual: `ngapIp` en `gnb.yaml` apunta a la IP de `n3_net` en lugar de la de `sbi_net`. Recuerda: el gNB tiene **tres** IPs y cada una tiene un propósito. Consulta la tabla de §2.3.

---

**[S2] SCTP INIT enviado, sin respuesta**

```bash
docker compose exec amf ss -lntu | grep 38412
docker compose logs amf | grep -i 'ngap\|sctp\|listen'
grep '^sctp ' /proc/modules       # en el LXC
```

Dos causas:
1. **Módulo `sctp` no cargado en el host Proxmox.** El AMF arranca sin quejarse, pero el `bind()` de SCTP falla silenciosamente o el paquete se descarta a nivel de kernel. `modprobe sctp` en el host.
2. **`ngapIpList` con `0.0.0.0`** en algunas versiones no bindea correctamente en un contenedor multihomed. Pon la IP explícita: `10.100.200.11`.

---

**[S3] `NGSetupFailure`**

```bash
docker compose logs amf | grep -i -A5 'ng setup'
docker compose logs gnb | grep -i -A5 'ng setup'
```

El AMF rechaza el NG Setup cuando el gNB anuncia un PLMN o TAC que no está en `supportTaiList`. Comprobación cruzada:

```bash
grep -A3 supportTaiList config/amfcfg.yaml     # mcc/mnc + tac hex
grep -E '^(mcc|mnc|tac):' ueransim/gnb.yaml    # mcc/mnc + tac decimal
```

Recuerda la conversión: `tac: '000001'` (free5GC, hex) ≡ `tac: 1` (UERANSIM, decimal). Si en `amfcfg` pusiste `tac: '000007'` y en `gnb.yaml` `tac: 7`, correcto. Si pusiste `tac: '000010'` y `tac: 10`, **incorrecto** — `0x000010` es 16 decimal, no 10. Es una trampa real.

`ci/check-plmn.py` detecta esto antes de desplegar.

---

**[S4] El UE no encuentra al gNB**

```bash
docker compose logs ue | grep -i 'plmn\|search\|cell'
docker compose exec ue ping -c2 10.100.203.30
docker compose exec gnb ss -lnu | grep 4997
```

`gnbSearchList` en `ue.yaml` debe contener el **`linkIp`** del gNB (`radio_net`), no su `ngapIp` ni su `gtpIp`. UERANSIM usa UDP 4997 para el enlace radio emulado; si apuntas a la IP equivocada, el UE queda buscando indefinidamente sin error explícito.

---

**[S5] Fallo de registro / autenticación**

```bash
docker compose logs ue   | grep -iE 'reject|cause|auth|failure'
docker compose logs amf  | grep -iE 'reject|cause|authentication'
docker compose logs ausf | tail -50
docker compose logs udm  | tail -50
```

Tabla de causas 5GMM (⚠️ contrasta con 3GPP TS 24.501 §9.11.3.2; los códigos son estables pero el texto varía):

| Causa | Significado | Origen probable en este lab |
|---|---|---|
| #3 | Illegal UE | Fallo de autenticación: K/OPc no coinciden con el suscriptor |
| #6 | Illegal ME | IMEI rechazado (raro en lab) |
| #7 | 5GS services not allowed | Suscriptor no existe en MongoDB, o IMSI no coincide |
| #9 | UE identity cannot be derived | SUCI mal formado; revisa `protectionScheme` y `routingIndicator` |
| #11 | PLMN not allowed | PLMN del UE fuera de `plmnSupportList` |
| #12 | Tracking area not allowed | TAC no está en `supportTaiList` |
| #15 | No suitable cells in TA | Combinación PLMN+TAC inconsistente |
| #22 | Congestion | UPF caído o AMF sin recursos |

Verificación directa del suscriptor:

```bash
docker compose exec -T mongodb mongosh --quiet free5gc --eval '
  const id="imsi-732101000000001";
  printjson(db.subscriptionData.authenticationData
              .authenticationSubscription.findOne({ueId:id}));
  printjson(db.subscriptionData.provisionedData.amData.findOne({ueId:id}));'
```

Si devuelve `null`, el suscriptor no está aprovisionado (o el `servingPlmnId` no coincide con el PLMN que anuncia el AMF).

Comprueba también `opType`: el valor `8e27b6af...` de ejemplo es un **OPc**, no un OP. Si en `ue.yaml` pones `opType: 'OP'`, la derivación de claves da un resultado distinto y la autenticación falla con causa #3, sin ninguna pista de que el problema sea ese campo.

---

**[S6] / [S8] PDU Session Establishment Reject**

```bash
docker compose logs ue  | grep -i -A5 'pdu session'
docker compose logs smf | grep -iE 'reject|cause|error' | tail -40
```

Causas 5GSM relevantes (⚠️ TS 24.501 §9.11.4.2):

| Causa | Significado | Corrección |
|---|---|---|
| #8 | Operator determined barring | Falta la política en `policyData.ues.smData` |
| #26 | Insufficient resources | Pool de IPs agotado o UPF sin capacidad |
| #27 | Missing or unknown DNN | `apn` en `ue.yaml` ≠ `dnn` en `smfcfg`/`upfcfg`/suscriptor |
| #29 | User auth/authorization failed | El suscriptor no tiene ese DNN en `smData` |
| #31 | Request rejected, unspecified | Genérico; ve a los logs del SMF |
| #33 | Requested service option not subscribed | S-NSSAI no suscrita |
| #67 | Insufficient resources for slice and DNN | Combinación S-NSSAI+DNN no configurada en el UPF |

La causa #67 es la más frecuente y casi siempre significa lo mismo: **el `sd` no coincide**. Verifica los cuatro sitios simultáneamente:

```bash
grep -A2 'sd' config/smfcfg.yaml       # '010203'  (string)
grep -A2 'sd' config/amfcfg.yaml       # '010203'  (string)
grep -A2 'sd' ueransim/ue.yaml         # 0x010203  (hex)
docker compose exec -T mongodb mongosh --quiet free5gc --eval '
  printjson(db.subscriptionData.provisionedData.smData.findOne())'
```

---

**[S7] La asociación PFCP nunca se establece**

Este es el fallo F3, y es el que más tiempo consume porque el UE se registra correctamente y todo *parece* bien hasta que pides una sesión.

```bash
# ¿El UPF está vivo?
docker compose ps upf
docker compose logs upf | tail -50

# ¿Existe la interfaz que crea gtp5g?
docker compose exec upf ip -d link show upfgtp

# ¿Hay tráfico PFCP en N4?
docker compose exec upf timeout 20 tcpdump -i any -n udp port 8805

# Comparación de nodeID — deben ser idénticos
grep -A3 'nodeID' config/upfcfg.yaml
grep -A3 'nodeID' config/smfcfg.yaml
```

Interpretación:

- **Sin paquetes en 8805** → el SMF no está intentando; revisa `userplaneInformation.upNodes.UPF.addr`.
- **Request sin Response** → el UPF recibe pero no responde: casi siempre está muerto porque `gtp5g` no se pudo inicializar. Mira `docker compose logs upf` desde la primera línea.
- **Response con `Cause: Request Rejected`** → `nodeID` desalineado.

Si `ip link show upfgtp` da `Device does not exist`:

```bash
grep '^gtp5g ' /proc/modules          # ¿cargado?
dmesg | grep -i gtp5g | tail -20      # ¿errores del módulo?
docker compose exec upf capsh --print | grep -o net_admin
```

---

**[S9] `uesimtun0` existe pero no pasa tráfico**

Este es el síntoma que quieres eliminar. La causa es casi siempre el **endpoint N3**.

```bash
# ¿Sale GTP-U del gNB?
docker compose exec gnb timeout 20 tcpdump -i any -n udp port 2152

# ¿Llega al UPF?
docker compose exec upf timeout 20 tcpdump -i any -n udp port 2152
```

Cuatro escenarios:

| gNB envía | UPF recibe | Diagnóstico |
|---|---|---|
| No | No | El UE no tiene sesión activa; vuelve a [S6] |
| Sí, a IP X | No | **`endpoints` de N3 en `smfcfg` apunta a la IP equivocada.** El gNB envía a donde el SMF le dijo. Debe ser `10.100.201.20`, la IP del UPF en `n3_net` |
| Sí | Sí | El GTP-U llega; el problema es post-desencapsulación → [S10] |
| Sí, ida y vuelta | Sí | Funciona; el problema está en el DN |

Comprobación cruzada de las tres IPs del UPF:

```bash
docker compose exec upf ip -br addr
# Debe mostrar 10.100.200.20 (N4), 10.100.201.20 (N3), 10.100.204.20 (DN)
grep -A4 'gtpu' config/upfcfg.yaml           # addr debe ser 10.100.201.20
grep -A6 'interfaceType: N3' config/smfcfg.yaml
```

---

**[S10] GTP-U llega al UPF pero el ping no vuelve**

```bash
docker compose exec upf ip -d link show upfgtp
docker compose exec upf ip route
docker compose exec upf iptables -t nat -L POSTROUTING -n -v
docker compose exec upf sysctl net.ipv4.ip_forward

# ¿Se ve tráfico desencapsulado saliendo por upfgtp?
docker compose exec upf timeout 15 tcpdump -i upfgtp -n

# ¿Y saliendo hacia el DN?
docker compose exec upf timeout 15 tcpdump -i any -n \
  'host 10.100.204.10 and icmp'
```

Lista de verificación:

1. `net.ipv4.ip_forward = 1` dentro del contenedor UPF (el `sysctls:` del compose lo fija).
2. Regla `MASQUERADE` para `10.60.0.0/16` presente, **y aplicada a la interfaz correcta**. Los contadores de `iptables -t nat -L -n -v` deben incrementarse al hacer ping. Si están a cero, la regla existe pero no coincide → interfaz equivocada. Aquí es donde `upf-nat.sh` gana sobre asumir `eth0`.
3. `iptables -L FORWARD -n -v` en el UPF no debe estar descartando.
4. El destino (`dn-iperf`) debe tener ruta de vuelta. Como haces MASQUERADE, la ve como si viniera de `10.100.204.20` y responde a la interfaz correcta automáticamente.

---

**[S11] Ping OK, transferencias grandes se cuelgan**

El fallo F7, MTU.

```bash
# Encuentra el MTU real con ping incremental y DF activado
for s in 1200 1300 1350 1364 1400 1450; do
  echo -n "size $s: "
  docker compose exec -T ue ping -I uesimtun0 -M do -s $s -c1 -W2 \
    10.100.204.10 >/dev/null 2>&1 && echo OK || echo "FRAGMENTACION"
done

docker compose exec ue ip link show uesimtun0    # MTU actual
ip link show tailscale0                          # 1280
docker network inspect n3_net -f \
  '{{index .Options "com.docker.network.driver.mtu"}}'
```

Corrección: MTU de `uesimtun0` = MTU de la red Docker − 36 bytes de overhead GTP-U. Con redes a 1400 → `uesimtun0` a **1364**. Ya lo hace `ue-entrypoint.sh`; si estás depurando a mano:

```bash
docker compose exec ue ip link set dev uesimtun0 mtu 1364
```

Y asegúrate de que el MSS clamping del LXC está activo (§5.5).

### 9.4 Referencia de captura de paquetes

| Interfaz | Filtro `tcpdump` | Contenedor | Qué revela |
|---|---|---|---|
| N2 (NGAP) | `sctp port 38412` | gnb o amf | NG Setup, NAS encapsulado |
| N3 (GTP-U) | `udp port 2152` | gnb o upf | Plano de usuario, TEIDs |
| N4 (PFCP) | `udp port 8805` | smf o upf | Association, Session Establishment, PDRs/FARs |
| Radio emulada | `udp port 4997` | ue o gnb | Enlace UERANSIM UE↔gNB |
| SBI | `tcp port 8000` | cualquier NF | HTTP/2 entre NFs |
| Post-túnel | `-i upfgtp` | upf | Paquetes IP del UE ya desencapsulados |

Captura a fichero para Wireshark:

```bash
docker compose exec -T upf timeout 60 tcpdump -i any -s0 -w - \
  'udp port 2152 or udp port 8805' > /tmp/upf.pcap
```

En Wireshark: `Analyze → Decode As` → puerto 38412 como **NGAP**, 8805 como **PFCP**, 2152 como **GTP**. Con `cipheringOrder: [NEA0]` en `amfcfg`, el NAS aparece en claro dentro del NGAP, lo que hace legibles los `Registration Request`, `Authentication Request` y los códigos de causa exactos.

### 9.5 Comandos de un vistazo

```bash
# Estado global
docker compose ps
docker compose logs -f --tail=50 amf smf upf gnb ue

# Todos los rechazos, en una sola pasada
docker compose logs 2>&1 | grep -iE 'reject|fail|error|cause' | tail -40

# Estado del UE (UERANSIM expone una CLI)
docker compose exec ue ./nr-cli --dump
docker compose exec ue ./nr-cli imsi-732101000000001 -e status
docker compose exec ue ./nr-cli imsi-732101000000001 -e ps-list

# Estado del gNB
docker compose exec gnb ./nr-cli --dump
docker compose exec gnb ./nr-cli UERANSIM-gnb-732-101-1 -e status

# Forzar re-registro del UE sin recrear el contenedor
docker compose exec ue ./nr-cli imsi-732101000000001 -e deregister normal
docker compose restart ue

# NFs registradas en el NRF
docker compose exec -T mongodb bash -c \
  'curl -s http://10.100.200.10:8000/nnrf-nfm/v1/nf-instances' | head -c 2000
```

### 9.6 Reinicio limpio

El orden importa. Levantar el gNB antes de que la asociación PFCP esté hecha produce fallos que parecen de configuración pero son de temporización:

```bash
docker compose down
docker compose up -d mongodb
sleep 10
docker compose up -d nrf udr udm ausf pcf nssf
sleep 10
docker compose up -d amf upf
sleep 10
docker compose up -d smf          # el SMF necesita al UPF vivo para PFCP
sleep 15
bash ci/provision-subscriber.sh
docker compose up -d gnb
sleep 10
docker compose up -d ue
docker compose up -d prometheus grafana cadvisor node-exporter \
                     pushgateway blackbox iperf-probe
```

El pipeline de §8.3 ya respeta este orden mediante los jobs separados y `ci/wait-ready.sh`.

---

## 10. Orden de trabajo sugerido

No intentes montarlo todo a la vez. Este es el camino con menos frustración:

1. **Host Proxmox**: `gtp5g` vía DKMS + `sctp` + sysctl. Verifica con `lsmod`. *(§3)*
2. **LXC**: config privilegiada, arranca, ejecuta `ci/preflight.sh`. No sigas hasta que pase entero. *(§4)*
3. **Núcleo 5G sin Tailscale ni observabilidad**: solo `mongodb` → NFs → `upf` → `smf`. Objetivo único: ver `Association Setup Response` en los logs del SMF. *(§6, §7)*
4. **Suscriptor**: créalo a mano en el WebConsole, vuelca el esquema de Mongo, calibra `provision-subscriber.sh`. *(§8.6)*
5. **RAN**: `gnb`, luego `ue`. Objetivo: `uesimtun0` con IP y ping al DN. *(§9)*
6. **Tailscale**: instala, anuncia rutas, apruébalas, aplica `lab5g-netrules.sh`. Verifica desde otro nodo del tailnet. *(§5)*
7. **Observabilidad**: Prometheus, Grafana, exporters, sondas. *(§7)*
8. **CI/CD**: runner + workflow, con el smoke test como puerta. *(§8)*

Cada paso deja el sistema en un estado verificable. Si el paso 5 falla, sabes que 1-4 estaban bien, y el espacio de búsqueda se reduce a la configuración de la RAN.

---

## 11. Resumen de cambios frente al despliegue anterior

| Área | Antes | Ahora | Fallo que elimina |
|---|---|---|---|
| PLMN | 208/93 (Francia) | 732/101 o 001/01, validado por CI | F4 |
| `gtp5g` | Compilado a mano | DKMS + `modules-load.d` + preflight | F1 |
| LXC | Config parcial | Privilegiado + AppArmor unconfined + `cap.drop` vacío | F2 |
| Redes Docker | Una sola bridge | 5 redes por plano (SBI/N3/radio/DN/obs) | F3, F9 |
| NAT del UPF | `-o eth0` fijo | Interfaz detectada por prefijo de IP | F6 |
| Tailscale | Sin reglas de firewall | `DOCKER-USER` + MSS clamp + unidad systemd | F5, F7 |
| MTU | Por defecto | 1400 en Docker, 1364 en `uesimtun0` | F7 |
| Suscriptor | Manual en WebConsole | Script idempotente en el pipeline | F4 |
| Validación | Ninguna | `preflight.sh` + `check-plmn.py` antes de desplegar | F1-F4 |
| Observabilidad | Dozzle (solo logs) | Prometheus + Grafana + cAdvisor + node-exporter + sondas en el netns del UE | — |
| Diagnóstico | Manual | `collect-logs.sh` + pcaps como artefacto de CI | — |

Dozzle sigue siendo útil y no compite con este stack: añádelo si quieres seguir viendo logs en vivo desde el navegador. Prometheus mide, Dozzle narra.