#!/usr/bin/env bash
# Verifica que el entorno LXC/host puede soportar free5GC.
# Falla RAPIDO y con mensajes accionables.
set -uo pipefail

fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }

# El runner de CI corre como usuario 'deploy', no como root. Las
# comprobaciones de iptables y de netdevs necesitan privilegios: se elevan
# solo esas, via sudo -n (ver /etc/sudoers.d/lab5g-deploy).
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo -n"; fi
if [ -n "$SUDO" ] && ! $SUDO true 2>/dev/null; then
  bad "sin sudo sin contrasena para $(id -un) -> instala /etc/sudoers.d/lab5g-deploy"
  SUDO=""
fi

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
if $SUDO ip link add lab5gtest type dummy 2>/dev/null; then
  $SUDO ip link del lab5gtest
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
              || warn "MTU de Docker sin fijar (informativo; en LAN pura no es critico)"

# --- Capa 3: LAN (192.168.1.0/24) ---
# El lab se publica directamente en la LAN; ya no hay capa Tailscale.
LAN_SUBNET="${LAN_SUBNET:-192.168.1.0/24}"
LAN_GW="${LAN_GW:-192.168.1.1}"

lan_if=$(ip -o -4 route show to default | awk '{print $5; exit}')
lan_ip=$(ip -o -4 addr show dev "${lan_if:-eth0}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1)

if [ -n "$lan_ip" ]; then
  ok "IP en la LAN: ${lan_ip} (iface ${lan_if})"
else
  bad "sin IP en la LAN -> revisa net0 en la config del LXC"
fi

if ping -c1 -W2 "$LAN_GW" >/dev/null 2>&1; then
  ok "gateway ${LAN_GW} alcanzable"
else
  bad "gateway ${LAN_GW} NO responde -> el lab quedara aislado de la red"
fi

# Los puertos publicados por Docker (3000/5000/9090) deben ser accesibles
# desde el resto de la LAN. Docker pone FORWARD en DROP: sin estas reglas,
# el trafico entrante desde 192.168.1.0/24 hacia las bridges se descarta.
if $SUDO iptables -C DOCKER-USER -s "$LAN_SUBNET" -d 10.100.200.0/24 -j ACCEPT 2>/dev/null \
   || $SUDO iptables -C DOCKER-USER -i "${lan_if:-eth0}" -j ACCEPT 2>/dev/null; then
  ok "reglas DOCKER-USER para la LAN presentes"
else
  bad "faltan ACCEPT en DOCKER-USER para ${LAN_SUBNET}"
  bad "  -> ejecuta /usr/local/sbin/lab5g-netrules.sh"
fi

# --- Recursos ---
mem=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
[ "$mem" -ge 4096 ] && ok "memoria: ${mem} MiB" \
                    || warn "solo ${mem} MiB; recomendado >= 4096"

echo
[ $fail -eq 0 ] && echo "Preflight OK" || { echo "Preflight FALLIDO"; exit 1; }
