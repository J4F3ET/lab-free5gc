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
