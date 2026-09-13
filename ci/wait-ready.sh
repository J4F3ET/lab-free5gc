#!/usr/bin/env bash
set -uo pipefail

# Presupuesto POR comprobacion, no global. Antes 'deadline' se calculaba una
# sola vez aqui arriba y las once esperas lo compartian: la primera se comia
# el presupuesto entero y las demas reportaban TIMEOUT sin haber esperado ni
# un segundo. En este host mongod tarda ~90 s solo en arrancar, asi que eso
# pasaba siempre.
TIMEOUT="${TIMEOUT:-180}"

wait_for() {
  local label="$1" cmd="$2" budget="${3:-$TIMEOUT}"
  local deadline=$(( $(date +%s) + budget ))
  printf '  esperando %-28s' "$label"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if eval "$cmd" >/dev/null 2>&1; then echo "OK"; return 0; fi
    sleep 2
  done
  echo "TIMEOUT (${budget}s)"; return 1
}

fail=0

# Presupuestos generosos donde el arranque es lento de verdad.
wait_for "MongoDB" \
  "docker compose exec -T mongodb mongosh --quiet --eval \"db.adminCommand('ping')\"" \
  300 || fail=1

wait_for "NRF (SBI)" \
  "docker compose exec -T mongodb bash -c 'exec 3<>/dev/tcp/10.100.200.10/8000'" \
  240 || fail=1

# El log del AMF puede tener miles de lineas acumuladas de reinicios previos,
# y la linea "Listen on ..." casi siempre esta muy cerca del principio. Con
# 'grep -q' el grep corta la lectura en cuanto encuentra esa linea, y el
# 'docker compose logs' que sigue escribiendo el resto recibe SIGPIPE y
# devuelve un error (255) aunque el grep si haya encontrado lo que buscaba.
# Con 'pipefail' activo eso se propaga como fallo del pipe completo y este
# chequeo daba TIMEOUT (240s) siempre, aun con el AMF sano y escuchando.
# La sustitucion de proceso evita el problema: el exit code de
# 'docker compose logs' dentro de <(...) no forma parte del pipe que mira
# pipefail.
wait_for "AMF NGAP (SCTP 38412)" \
  "docker compose exec -T amf ss -lntu 2>/dev/null | grep -q 38412 \
   || grep -qi 'ngap.*listen' < <(docker compose logs amf 2>&1)" \
  240 || fail=1

# El registro en el NRF es la senal real de que la NF esta viva. Se consulta
# el log del NRF una sola vez por NF, con presupuesto corto: si el NRF ya
# esta arriba, o el registro aparecio o no, y no vale la pena esperar 180 s
# por cada uno de los ocho.
for nf in AMF SMF AUSF UDM UDR PCF NSSF UPF; do
  wait_for "registro de ${nf} en NRF" \
    "docker compose logs nrf 2>&1 | grep -qi '${nf}.*register'" \
    60 || true
done

# La asociacion PFCP es el punto critico (fallo F3). Mismo riesgo de SIGPIPE
# que el chequeo del AMF si el log de smf acumula muchos reinicios y la linea
# buscada queda cerca del principio: se usa sustitucion de proceso por la
# misma razon (ver comentario en "AMF NGAP" mas arriba).
wait_for "asociacion PFCP SMF<->UPF" \
  "grep -qiE 'association setup accepted response|pfcp association.*success' < <(docker compose logs smf 2>&1)" \
  300 \
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
