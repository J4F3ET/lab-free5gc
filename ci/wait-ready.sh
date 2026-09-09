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
