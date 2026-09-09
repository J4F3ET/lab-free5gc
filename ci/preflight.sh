#!/usr/bin/env bash
# Verifica que el entorno LXC/host puede soportar free5GC.
# Falla RAPIDO y con mensajes accionables.
set -uo pipefail

fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }

# El PATH del runner es /usr/local/bin:/usr/bin:/bin y no incluye /sbin.
# "command -v ip" resolvia a /usr/bin/ip, que NO esta en el sudoers, asi que
# el sudo daba denegado. Se fijan las rutas administrativas.
for c in /usr/sbin/ip /sbin/ip; do [ -x "$c" ] && IP_BIN="$c" && break; done
IP_BIN="${IP_BIN:-$(command -v ip || echo /sbin/ip)}"
for c in /usr/sbin/iptables /sbin/iptables; do [ -x "$c" ] && IPT_BIN="$c" && break; done
IPT_BIN="${IPT_BIN:-$(command -v iptables || echo /sbin/iptables)}"

# El runner de CI corre como usuario 'deploy', no como root. Solo las
# comprobaciones de iptables y de netdevs necesitan privilegios: se elevan
# esas, via sudo -n (ver /etc/sudoers.d/lab5g-deploy).
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo -n"
  # Se prueba con un comando que SI esta en el sudoers ('ip -V' es inocuo).
  # Probar con 'true' daba un falso negativo: no esta en la lista permitida.
  if ! $SUDO "$IP_BIN" -V >/dev/null 2>&1; then
    bad "sin sudo sin contrasena para $(id -un) -> ejecuta deploy/bootstrap-runner.sh"
    SUDO=""
  fi
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
  bad "  (persistelo en /etc/modules-load.d/ o se pierde al reiniciar)"
fi

# --- Capa 1: devices y capabilities ---
[ -c /dev/net/tun ] \
  && ok "/dev/net/tun presente" \
  || bad "/dev/net/tun ausente -> revisa lxc.mount.entry en la config del LXC"

# Se consulta el bounding set de PID 1, no las caps del usuario actual:
# 'deploy' es un usuario sin privilegios y capsh siempre daria negativo,
# lo que hacia parecer que el LXC no tenia la capability.
capbnd=$(awk '/^CapBnd:/{print $2}' /proc/1/status 2>/dev/null)
if [ -n "$capbnd" ] && [ $(( 0x$capbnd & (1 << 12) )) -ne 0 ]; then
  ok "CAP_NET_ADMIN en el bounding set del contenedor"
else
  bad "sin CAP_NET_ADMIN -> el LXC no es privilegiado o hay cap.drop"
fi

# Prueba real: crear y borrar un netdev dummy
if $SUDO "$IP_BIN" link add lab5gtest type dummy 2>/dev/null; then
  $SUDO "$IP_BIN" link del lab5gtest
  ok "puedo crear interfaces de red"
else
  bad "no puedo crear netdevs -> falta lxc.apparmor.profile: unconfined"
fi

# --- Capa 2: sysctl ---
# Se leen de /proc/sys directamente: no depende del binario sysctl ni del PATH.
fwd=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)
[ "$fwd" = "1" ] \
  && ok "ip_forward activo" \
  || bad "ip_forward=${fwd:-?} -> sysctl -w net.ipv4.ip_forward=1"

rp=$(cat /proc/sys/net/ipv4/conf/all/rp_filter 2>/dev/null)
[ "$rp" = "2" ] && ok "rp_filter en modo loose (2)" \
                || warn "rp_filter=${rp:-?}; recomendado 2 con multiples redes Docker"

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

lan_if=$("$IP_BIN" -o -4 route show to default | awk '{print $5; exit}')
lan_ip=$("$IP_BIN" -o -4 addr show dev "${lan_if:-eth0}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1)

if [ -n "$lan_ip" ]; then
  ok "IP en la LAN: ${lan_ip} (iface ${lan_if})"
else
  bad "sin IP en la LAN -> revisa net0 en la config del LXC"
fi

# ICMP no esta permitido a usuarios sin privilegios en este contenedor
# (ping_group_range), asi que un ping fallido NO implica falta de red.
# Se cae hacia la tabla de vecinos y la ruta por defecto, legibles sin sudo.
if ping -c1 -W2 "$LAN_GW" >/dev/null 2>&1; then
  ok "gateway ${LAN_GW} alcanzable (ICMP)"
elif "$IP_BIN" neigh show "$LAN_GW" 2>/dev/null | grep -qE 'REACHABLE|STALE|DELAY'; then
  ok "gateway ${LAN_GW} presente en la tabla de vecinos"
elif "$IP_BIN" route show default 2>/dev/null | grep -q "$LAN_GW"; then
  warn "gateway ${LAN_GW} en la ruta por defecto, sin confirmar por ICMP ni ARP"
else
  bad "gateway ${LAN_GW} inalcanzable -> el lab quedara aislado de la red"
fi

# Los puertos publicados por Docker (3000/5000/8888/9090) deben ser
# accesibles desde el resto de la LAN. Docker pone FORWARD en DROP: sin
# estas reglas el trafico entrante hacia las bridges se descarta en
# silencio (dentro del LXC todo responde, desde la LAN nada).
#
# Se comprueba la MISMA forma de regla que crea lab5g-netrules.sh
# (-i <iface> -d <subred> -j ACCEPT). Antes se buscaba '-s 192.168.1.0/24',
# que ese script nunca genera: el check fallaba con las reglas ya puestas.
missing=""
for net in 10.100.200.0/24 10.100.205.0/24 10.60.0.0/16; do
  $SUDO "$IPT_BIN" -C DOCKER-USER -i "${lan_if:-eth0}" -d "$net" -j ACCEPT 2>/dev/null \
    || missing="$missing $net"
done
if [ -z "$missing" ]; then
  ok "reglas DOCKER-USER para la LAN presentes (SBI, observabilidad, pool de UE)"
else
  bad "faltan ACCEPT en DOCKER-USER para:$missing"
  bad "  -> ejecuta sudo /usr/local/sbin/lab5g-netrules.sh"
fi

# --- Recursos ---
mem=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
[ "$mem" -ge 4096 ] && ok "memoria: ${mem} MiB" \
                    || warn "solo ${mem} MiB; recomendado >= 4096"

echo
[ $fail -eq 0 ] && echo "Preflight OK" || { echo "Preflight FALLIDO"; exit 1; }
