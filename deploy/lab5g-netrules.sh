#!/usr/bin/env bash
# Reglas de red del LXC para publicar el lab en la LAN.
# Sustituye a la version basada en tailscale0.
# Instalar en /usr/local/sbin/lab5g-netrules.sh y ejecutar despues de docker.service.
set -euo pipefail

LAN_SUBNET="${LAN_SUBNET:-192.168.1.0/24}"
LAN_IF="$(ip -o -4 route show to default | awk '{print $5; exit}')"
LAN_IF="${LAN_IF:-eth0}"

# Redes internas del lab que deben ser alcanzables desde la LAN.
NETS=(10.100.200.0/24 10.100.205.0/24 10.60.0.0/16)

add() {  # idempotente: no duplica reglas en cada reinicio
  iptables -C "$@" 2>/dev/null || iptables -I "$@"
}

# Docker deja FORWARD en DROP y encadena DOCKER-USER primero. Sin estos
# ACCEPT, el trafico que entra por la LAN hacia las bridges se descarta
# en silencio: dentro del LXC todo responde, desde la LAN nada.
for NET in "${NETS[@]}"; do
  add DOCKER-USER -i "$LAN_IF" -d "$NET" -j ACCEPT
done
add DOCKER-USER -o "$LAN_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

# rp_filter en modo loose: con varias bridges Docker el filtrado estricto
# descarta retornos asimetricos ("el ping sale pero no vuelve").
sysctl -qw net.ipv4.conf.all.rp_filter=2
sysctl -qw net.ipv4.ip_forward=1

echo "[netrules] LAN=${LAN_SUBNET} iface=${LAN_IF}; reglas DOCKER-USER aplicadas"
