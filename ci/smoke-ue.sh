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
