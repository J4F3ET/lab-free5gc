#!/usr/bin/env bash
set -uo pipefail
OUT=/tmp/lab5g-diag
rm -rf "$OUT"; mkdir -p "$OUT"

echo "[diag] recolectando en $OUT"

# El runner de CI corre como usuario 'deploy', no como root: iptables e
# ip route del host necesitan privilegios (ver /etc/sudoers.d/lab5g-deploy).
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo -n"; fi

# Logs de cada contenedor
for c in $(docker compose ps --services 2>/dev/null); do
  docker compose logs --no-color --tail=500 "$c" > "$OUT/log-${c}.txt" 2>&1 || true
done

# Estado de red del host LXC
{ echo "=== ip addr ==="        ; ip addr
  echo "=== ip route ==="       ; $SUDO ip route show table all
  echo "=== iptables filter ===" ; $SUDO iptables -L -n -v
  echo "=== iptables nat ==="   ; $SUDO iptables -t nat -L -n -v
  echo "=== iptables mangle ===" ; $SUDO iptables -t mangle -L -n -v
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
