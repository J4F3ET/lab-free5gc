#!/usr/bin/env bash
# medir.sh — Con y sin compresor, sobre la red 5G del lab. Correr DENTRO del LXC, en el
# checkout del repo, con el núcleo + ran + compresion arriba:
#
#   export COMPOSE_PROJECT_NAME=lab5g COMPOSE_PROFILES=ran,compresion
#   docker compose up -d
#   bash compresion/medir.sh
#
# Mide los bytes que cruzan uesimtun0 (la sesión PDU: lo que el UE pone en la radio y que
# después viaja por N3 dentro de GTP-U). Mismo tráfico dos veces: directo a la IP real de
# cmp-dn y por el túnel compresor.
set -uo pipefail
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-lab5g}"
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-ran,compresion}"
N="${N:-200}"

contadores() {
  docker compose exec -T ue sh -c \
    'cat /sys/class/net/uesimtun0/statistics/tx_bytes /sys/class/net/uesimtun0/statistics/rx_bytes' \
    | tr '\n' ' '
}

correr() {  # $1 = etiqueta, $2 = destino
  read -r tx0 rx0 <<<"$(contadores)"
  docker compose exec -T cmp-ue python3 /cmp/generar.py --destino "$2" --paquetes "$N" | sed 's/^/    /'
  sleep 2
  read -r tx1 rx1 <<<"$(contadores)"
  echo "$1 $((tx1 - tx0)) $((rx1 - rx0))"
}

echo ">>> sin compresor (directo a 10.100.204.30)"
read -r _ tx_d rx_d < <(correr directo 10.100.204.30 | tee /dev/stderr | tail -1)
echo ">>> con compresor (túnel cmp0 -> 10.99.0.2)"
read -r _ tx_c rx_c < <(correr comprimido 10.99.0.2 | tee /dev/stderr | tail -1)

printf '\n%-28s %12s %12s\n' "" "subida (B)" "bajada (B)"
printf '%-28s %12d %12d\n' "sin compresor" "$tx_d" "$rx_d"
printf '%-28s %12d %12d\n' "con compresor" "$tx_c" "$rx_c"
awk -v a="$tx_d" -v b="$tx_c" 'BEGIN { printf "ahorro en subida por uesimtun0: %.2f %%\n", 100 * (1 - b / a) }'
echo
echo "Detalle por códec y errores: docker compose logs --tail=5 cmp-ue cmp-dn"
