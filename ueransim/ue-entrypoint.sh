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
      # MTU del tunel = MTU de la bridge Docker - 36 bytes de cabecera GTP-U
      ip link set dev "$TUN_IFACE" mtu "${UE_MTU:-1464}"
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