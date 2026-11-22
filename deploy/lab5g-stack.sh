#!/usr/bin/env bash
# Dueno unico del arranque y de la recuperacion de la pila del lab 5G.
#
#   lab5g-stack.sh run              arranque escalonado + bucle de recuperacion
#                                   (lo usa lab5g-stack.service)
#   lab5g-stack.sh start [sel...]   arranque escalonado (completo o parcial)
#   lab5g-stack.sh stop             apagado ordenado inverso
#   lab5g-stack.sh status           tabla de servicios
#   lab5g-stack.sh gate             exit 0 si la compuerta de carga pasa
#
# POR QUE existe: el host (4 nucleos, 3.8 GB, disco MECANICO) se congelo y
# reinicio porque Docker levantaba ~25 contenedores a la vez (load average 28
# con I/O al 99 %, medido en el arranque en frio del 21 nov; en la estampida
# original llego a 105). El compose ya no tiene politica de reinicio ('restart: "no"'):
# aqui se arranca UN servicio cada vez, solo cuando el host esta tranquilo
# (compuerta de carga), y se espera a que cada uno este sano antes del
# siguiente. Tardar 20-30 min esta bien; congelar el host no.
#
# Tambien se puede 'source'-ar (lab5g-backup.sh reutiliza la compuerta y los
# tests usan las funciones puras). Variables LAB5G_* permiten probarlo sin
# docker ni root: LAB5G_DRYRUN=1, LAB5G_PROC_DIR, LAB5G_STATE_DIR, ...
set -euo pipefail

# ---------------------------------------------------------------- contrato
LAB5G_DIR="${LAB5G_DIR:-/opt/lab5g}"
ENV_FILE="${LAB5G_ENV_FILE:-/etc/lab5g/lab5g.env}"
PROJECT="${LAB5G_PROJECT:-lab5g}"
STATE_DIR="${LAB5G_STATE_DIR:-/var/lib/lab5g}"
RUN_DIR="${LAB5G_RUN_DIR:-/run/lab5g}"
PROC="${LAB5G_PROC_DIR:-/proc}"
CGROUP_DIR="${LAB5G_CGROUP_DIR:-/sys/fs/cgroup}"
DRYRUN="${LAB5G_DRYRUN:-0}"
PUSH_BASE="${LAB5G_PUSH_BASE:-http://10.100.205.14:9091}"
PUSH_URL="${LAB5G_PUSH_URL:-$PUSH_BASE/metrics/job/lab5g_stack}"

# ------------------------------------------------------------------ umbrales
# Compuerta: load1 < 4 en un host de 4 nucleos = como mucho "1 tarea por
# nucleo"; por encima el arranque de otro contenedor solo empeora la cola.
# Presion de I/O (PSI 'some avg10') < 40 %: con disco mecanico a partir de ahi
# las lecturas de arranque de los NF compiten entre si y todo se alarga (I/O al
# 99 % en el arranque en frio del 21 nov).
GATE_LOAD="${GATE_LOAD:-4}"
GATE_IO="${GATE_IO:-40}"
# MemAvailable >= 300 MB: con 3.8 GB y swap en disco mecanico, arrancar otro
# contenedor sin memoria libre es la via directa a paginar y congelar el host.
GATE_MEM="${GATE_MEM:-300}"
GATE_POLL="${GATE_POLL:-15}"             # sondeo de la compuerta (s)
GATE_LOG_EVERY="${GATE_LOG_EVERY:-300}"  # un log cada 5 min, no uno por sondeo
# Tras 45 min en la misma compuerta se avanza con UN servicio: mejor degradado
# que bloqueado para siempre (p. ej. un proceso ajeno que mantiene load alto).
GATE_MAX_WAIT="${GATE_MAX_WAIT:-2700}"
HEALTH_POLL="${HEALTH_POLL:-5}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"        # tope por servicio
HEALTH_TIMEOUT_MONGO="${HEALTH_TIMEOUT_MONGO:-600}"  # mongod tarda ~90 s solo en arrancar con este disco
SETTLE="${SETTLE:-30}"                   # pausa tras cada servicio sano: el load1 es una media de 1 min y 5 s no dejaban verlo subir
RECOVERY_PERIOD="${RECOVERY_PERIOD:-60}"
COOLDOWN="${COOLDOWN:-120}"              # entre reinicios del MISMO servicio
MAX_RESTARTS="${MAX_RESTARTS:-5}"        # por servicio y ventana
WINDOW="${WINDOW:-3600}"                 # ventana deslizante (s)
METRICS_PERIOD="${METRICS_PERIOD:-30}"
STOP_TIMEOUT="${STOP_TIMEOUT:-30}"

# ------------------------------------------------------------ topologia
# Cada elemento es una etapa; su indice (1..) es el valor de lab5g_stack_etapa.
# prometheus y pushgateway van en la etapa 1: no dependen de nada (red obs) y
# asi se registra y se mide el propio arranque en frio (siempre con compuerta).
STAGES=(
  "prometheus pushgateway"
  "mongodb"
  "nrf"
  "udr udm ausf pcf nssf"
  "amf"
  "upf"
  "smf"
  "gnb"
  "ue"
  "dn-iperf"
  "cmp-dn"
  "cmp-ue"
  "envio envio-eco"
  "cmp-gen"
)
# Bajo demanda / sondas: no se arrancan solos. Los de red compartida solo se
# reinician si estan corriendo y quedaron obsoletos.
ON_DEMAND=(grafana)
EXTRAS=(blackbox iperf-probe phy-sim)   # solo arrancan si se nombran: start blackbox|iperf-probe|phy-sim

# Dependencias de namespace de red: hijo -> padres.
declare -A PARENTS=(
  [cmp-ue]="ue"
  [envio-eco]="ue cmp-ue"
  [cmp-gen]="ue cmp-ue"
  [envio]="cmp-dn"
  [blackbox]="ue"
  [iperf-probe]="ue"
)

MANAGED=()
for _st in "${STAGES[@]}"; do
  for _s in $_st; do MANAGED+=("$_s"); done
done
ALL_NAMES=("${MANAGED[@]}" "${ON_DEMAND[@]}" "${EXTRAS[@]}")

# ---------------------------------------------------------------- utilidades
log() { printf '%s [lab5g] %s\n' "$(date '+%F %T')" "$*"; }
now() { echo "${LAB5G_NOW:-$(date +%s)}"; }

# 'sleep' interrumpible: con 'sleep N' en primer plano bash no atiende SIGTERM
# hasta que termina; en segundo plano + 'wait' el trap actua al instante.
nap() { sleep "$1" & wait $! 2>/dev/null || true; }

# Todas las llamadas a docker pasan por aqui: con tope de tiempo (el CLI se
# cuelga bajo carga) y con modo de prueba en seco.
dk() {
  if [ "$DRYRUN" = 1 ]; then
    echo "[dry-run] docker $*" >&2
    if [ "${1:-}" = inspect ] && [ -n "${LAB5G_FAKE_INSPECT:-}" ] && [ -r "$LAB5G_FAKE_INSPECT" ]; then
      cat "$LAB5G_FAKE_INSPECT"
    fi
    return 0
  fi
  timeout "${DK_TIMEOUT:-60}" docker "$@"
}

# compose: siempre env-file + proyecto + cwd=$LAB5G_DIR y SIEMPRE con nombres
# de servicio (un 'up' o 'stop' sin nombres tocaria los ~25 a la vez).
dc() {
  if [ "$DRYRUN" = 1 ]; then dk compose "$@"; return; fi
  (
    cd "$LAB5G_DIR" || exit 1
    DK_TIMEOUT="${DK_TIMEOUT:-600}" dk compose --env-file "$ENV_FILE" -p "$PROJECT" \
      --profile ran --profile compresion --profile obs --profile grafana \
      --profile obs-extra --profile probes "$@"
  )
}

ensure_dirs() { mkdir -p "$STATE_DIR" "$RUN_DIR"; }
abort_requested() { [ -e "$RUN_DIR/parado" ]; }
# Servicios detenidos A MANO con 'stop <svc>': la recuperacion no los toca.
manual_stopped() { [ -r "$RUN_DIR/parados" ] && grep -qxF -- "$1" "$RUN_DIR/parados"; }
manual_stop_add() { manual_stopped "$1" || echo "$1" >> "$RUN_DIR/parados"; }
manual_stop_del() {   # sin argumentos vacia la lista
  [ -e "$RUN_DIR/parados" ] || return 0
  if [ "$#" -eq 0 ]; then rm -f "$RUN_DIR/parados"; return 0; fi
  local a tmp="$RUN_DIR/parados.tmp"
  cp "$RUN_DIR/parados" "$tmp"
  for a in "$@"; do grep -vxF -- "$a" "$tmp" > "$tmp.2" || true; mv "$tmp.2" "$tmp"; done
  mv "$tmp" "$RUN_DIR/parados"
}

lock_acquire() {   # bloqueante con tope; sin flock se sigue sin cerrojo
  command -v flock >/dev/null 2>&1 || return 0
  exec 9>"$RUN_DIR/ops.lock"
  flock -w "${1:-3600}" 9 || log "aviso: no obtuve el cerrojo de operaciones, sigo igualmente"
}
lock_try() {
  command -v flock >/dev/null 2>&1 || return 0
  exec 9>"$RUN_DIR/ops.lock"
  flock -n 9
}
lock_release() { command -v flock >/dev/null 2>&1 && { flock -u 9 2>/dev/null || true; }; return 0; }

# ------------------------------------------------- funciones puras (probables)
# loadavg: "0.52 0.58 0.59 1/345 12345" -> primer campo.
read_load1() {
  local f="$PROC/loadavg" v=0
  [ -r "$f" ] && read -r v _ < "$f" || true
  echo "${v:-0}"
}
# PSI: "some avg10=12.34 avg60=... total=..." -> 12.34. Si el kernel/LXC no
# expone PSI se asume 0 (no se puede medir; el load1 sigue protegiendo).
read_io_pct() {
  local f="$PROC/pressure/io" v=""
  [ -r "$f" ] && v=$(sed -n 's/^some .*avg10=\([0-9.]*\).*/\1/p' "$f" | head -n1) || true
  echo "${v:-0}"
}
read_mem_avail_mb() {
  local f="$PROC/meminfo" kb=0
  [ -r "$f" ] && kb=$(awk '/^MemAvailable:/ {print $2}' "$f") || true
  awk -v k="${kb:-0}" 'BEGIN{printf "%d", k/1024}'
}
num_lt() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a+0 < b+0)}'; }

GATE_MSG=""
gate_ok() {
  local l io mem ok=0
  l=$(read_load1); io=$(read_io_pct)
  GATE_MSG="load1=$l (<$GATE_LOAD) io=${io}% (<$GATE_IO)"
  num_lt "$l" "$GATE_LOAD" && num_lt "$io" "$GATE_IO" || ok=1
  # Memoria: solo si el kernel la expone (en pruebas con /proc simulado puede faltar).
  if [ -r "$PROC/meminfo" ]; then
    mem=$(read_mem_avail_mb)
    GATE_MSG="$GATE_MSG mem=${mem}MB (>=$GATE_MEM)"
    [ "$mem" -ge "$GATE_MEM" ] || ok=1
  fi
  return "$ok"
}

# Ventana deslizante de reinicios: lineas "epoch servicio".
RESTART_LOG="$STATE_DIR/reinicios.log"
window_count() {   # reinicios de $1 dentro de la ventana
  [ -r "$RESTART_LOG" ] || { echo 0; return; }
  awk -v s="$1" -v lim="$(( $(now) - WINDOW ))" '$2==s && $1>lim {n++} END{print n+0}' "$RESTART_LOG"
}
last_restart() {
  [ -r "$RESTART_LOG" ] || { echo 0; return; }
  awk -v s="$1" '$2==s {t=$1} END{print t+0}' "$RESTART_LOG"
}
total_restarts() {
  [ -r "$RESTART_LOG" ] || { echo 0; return; }
  awk -v s="$1" '$2==s {n++} END{print n+0}' "$RESTART_LOG"
}
record_restart() { echo "$(now) $1" >> "$RESTART_LOG"; }
prune_restart_log() {   # conserva 24 h para que el archivo no crezca sin limite
  [ -r "$RESTART_LOG" ] || return 0
  local tmp="$RESTART_LOG.tmp"
  awk -v lim="$(( $(now) - 86400 ))" '$1>lim' "$RESTART_LOG" > "$tmp" && mv "$tmp" "$RESTART_LOG"
}
in_cooldown() { local t; t=$(last_restart "$1"); [ "$t" -gt 0 ] && [ $(( $(now) - t )) -lt "$COOLDOWN" ]; }
is_exhausted() { [ "$(window_count "$1")" -ge "$MAX_RESTARTS" ]; }

fmt_dur() {
  awk -v s="$1" 'BEGIN{ if(s<60) printf "%ds",s; else if(s<3600) printf "%dm%02ds",s/60,s%60;
    else if(s<86400) printf "%dh%02dm",s/3600,(s%3600)/60; else printf "%dd%02dh",s/86400,(s%86400)/3600 }'
}

# Metrica etapa: indice de etapa, 0 al terminar, negativo si degradado.
set_etapa() {
  local v="$1" deg="${2:-0}"
  [ "$deg" = 1 ] && [ "$v" -gt 0 ] && v=$(( -v ))
  echo "$v" > "$STATE_DIR/etapa.tmp" && mv "$STATE_DIR/etapa.tmp" "$STATE_DIR/etapa"
}
get_etapa() { cat "$STATE_DIR/etapa" 2>/dev/null || echo 0; }

# -------------------------------------------------- estado de contenedores
declare -A ST=() ID=() SA=() HL=()
SNAP_N=0
# OJO (docker 29): .State es un mapa y '{{if .State.Health}}' ABORTA con "map has no entry" en los
# contenedores sin healthcheck, dejando la instantanea vacia. Por eso se usa 'index .State "Health"'.
# UNA sola llamada a docker inspect para todos (varias por servicio cada 30 s
# bajo carga serian parte del problema). Los ausentes simplemente no salen.
snapshot() {
  ST=(); ID=(); SA=(); HL=(); SNAP_N=0
  local out n s i a h
  out=$(dk inspect -f '{{.Name}}|{{.State.Status}}|{{.Id}}|{{.State.StartedAt}}|{{with index .State "Health"}}{{.Status}}{{end}}' \
        "${ALL_NAMES[@]}" 2>/dev/null || true)
  while IFS='|' read -r n s i a h; do
    [ -n "$n" ] || continue
    n="${n#/}"; ST[$n]="$s"; ID[$n]="$i"; SA[$n]="$a"; HL[$n]="$h"; SNAP_N=$((SNAP_N+1))
  done <<< "$out"
}
is_running() { [ "${ST[$1]:-}" = running ]; }

# Padre mas nuevo que el hijo => el hijo comparte un namespace ya muerto.
# StartedAt es ISO-8601 UTC con el mismo formato: la comparacion de cadenas basta.
is_stale() {
  local c="$1" p
  [ -n "${SA[$c]:-}" ] || return 1
  for p in ${PARENTS[$c]:-}; do
    [ -n "${SA[$p]:-}" ] || continue
    [[ "${SA[$p]}" > "${SA[$c]}" ]] && return 0
  done
  return 1
}
parents_running() {
  local p
  for p in ${PARENTS[$1]:-}; do is_running "$p" || return 1; done
  return 0
}

# --------------------------------------------------------------- salud
tcp_ok() { timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }
gtp5g_loaded() { grep -q '^gtp5g ' "$PROC/modules" 2>/dev/null; }

probe_extra() {   # $1 servicio, $2 estado de health ('' si no tiene)
  case "$1" in
    nrf) tcp_ok 10.100.200.10 8000 ;;
    amf) tcp_ok 10.100.200.11 8000 ;;
    smf) tcp_ok 10.100.200.12 8000 ;;
    mongodb) [ -n "$2" ] || tcp_ok 10.100.200.40 27017 ;;
    ue) [ -n "$(dk exec ue ip -o addr show uesimtun0 2>/dev/null)" ] ;;
    *) return 0 ;;
  esac
}

# 0 = sano, 1 = fallo/tope, 99 = parada solicitada. SIN 'docker logs'.
wait_health() {
  local svc="$1" budget="${2:-}" deadline out s h
  if [ -z "$budget" ]; then
    budget="$HEALTH_TIMEOUT"; [ "$svc" = mongodb ] && budget="$HEALTH_TIMEOUT_MONGO"
  fi
  [ "$DRYRUN" = 1 ] && return 0
  deadline=$(( $(now) + budget ))
  while :; do
    abort_requested && return 99
    out=$(dk inspect -f '{{.State.Status}}|{{with index .State "Health"}}{{.Status}}{{end}}' "$svc" 2>/dev/null || echo "ausente|")
    s="${out%%|*}"; h="${out#*|}"
    case "$s" in
      exited|dead|ausente) log "FALLO: $svc esta '$s'"; return 1 ;;
      running)
        # 'unhealthy' puede recuperarse dentro del start_period: se sigue esperando.
        if { [ -z "$h" ] || [ "$h" = healthy ]; } && probe_extra "$svc" "$h"; then return 0; fi ;;
    esac
    if [ "$(now)" -ge "$deadline" ]; then log "FALLO: $svc no llego a estar sano en ${budget}s (estado=$s health=${h:-n/a})"; return 1; fi
    nap "$HEALTH_POLL"
  done
}

# 0 = compuerta abierta, 1 = tope de espera agotado (avanzar degradado), 99 = parada.
wait_gate() {
  local label="$1" t0 last now_
  gate_ok && return 0
  t0=$(now); last=$t0
  log "compuerta cerrada antes de '$label' ($GATE_MSG); espero"
  while ! gate_ok; do
    abort_requested && return 99
    nap "$GATE_POLL"
    now_=$(now)
    if [ $(( now_ - t0 )) -ge "$GATE_MAX_WAIT" ]; then
      log "compuerta cerrada $(( (now_ - t0) / 60 )) min ($GATE_MSG): avanzo con '$label' en modo degradado"
      return 1
    fi
    if [ $(( now_ - last )) -ge "$GATE_LOG_EVERY" ]; then
      log "sigo esperando la compuerta para '$label' ($GATE_MSG), $(( (now_ - t0) / 60 )) min"
      last=$now_
    fi
  done
  log "compuerta abierta ($GATE_MSG)"
  return 0
}

# --------------------------------------------------------------- arranque
# Huerfanos conocidos de composes anteriores (lista CERRADA, nombre exacto,
# nunca por patron). '5f3e1c9dd437_grafana' es un contenedor renombrado por un
# 'up' interrumpido.
ORPHANS=(dozzle webui cadvisor node-exporter 5f3e1c9dd437_grafana)
ORPHANS_DONE=0
FIRST_UP=1

# Todos los contenedores gestionados: un 'restart: unless-stopped' heredado de
# un compose viejo los resucitaria en el arranque de docker, justo la
# estampida que se quiere evitar. Se normaliza a "no" (no recrea nada).
normalize_restart() {
  [ "$DRYRUN" = 1 ] && return 0
  local pol
  pol=$(dk inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$1" 2>/dev/null || true)
  if [ -n "$pol" ] && [ "$pol" != "no" ]; then
    log "$1 tenia restart='$pol': lo dejo en 'no'"
    dk update --restart=no "$1" >/dev/null 2>&1 || log "aviso: no pude normalizar la politica de $1"
  fi
}
purge_orphans() {
  [ "$ORPHANS_DONE" = 1 ] && return 0
  ORPHANS_DONE=1
  local o
  for o in "${ORPHANS[@]}"; do dk rm -f "$o" >/dev/null 2>&1 || true; done
}

# Un contenedor con el nombre del servicio creado a mano ('docker run', sin etiquetas de
# compose) hace que 'up' falle con "container name already in use" y el servicio nunca
# arranque (visto en el primer arranque en frio con cmp-dn y envio, del 18 nov).
# Si esta parado se elimina: es sin estado (el codigo va montado) y compose lo recrea.
# Si esta CORRIENDO no se toca: alguien lo lanzo a proposito y se avisa.
clear_foreign() {   # $1 nombre del servicio/contenedor
  local c="$1" proj st
  proj=$(dk inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$c" 2>/dev/null) || return 0
  [ "$proj" = "$PROJECT" ] && return 0
  st=$(dk inspect -f '{{.State.Status}}' "$c" 2>/dev/null || true)
  if [ "$st" = running ]; then
    log "ERROR: $c corre y no pertenece al proyecto $PROJECT (lanzado a mano): no lo toco"
    return 1
  fi
  log "$c: contenedor viejo sin proyecto compose ($st): lo elimino para que compose lo recree"
  dk rm "$c" >/dev/null 2>&1 || return 1
}

# 'up -d --no-deps' es idempotente: solo recrea si cambio la configuracion
# (hash). Asi un 'start' tras un 'sync' aplica el compose nuevo aunque el
# servicio ya corra. Los hijos con network_mode "service:X" se RECREAN: un
# 'restart' falla si X fue recreado (el namespace al que apuntan ya no existe).
apply_svc() {   # $1 servicio, $2 "force" para recrear
  local extra=()
  [ "$FIRST_UP" = 1 ] && extra+=(--remove-orphans)
  [ "${2:-}" = force ] && extra+=(--force-recreate)
  clear_foreign "$1" || return 1
  dc up -d --no-deps "${extra[@]}" "$1" || return 1
  FIRST_UP=0
  normalize_restart "$1"
  purge_orphans
}

restart_svc() {   # recuperacion: con padres => recrear siempre; sin padres => up
  if [ -n "${PARENTS[$1]:-}" ]; then apply_svc "$1" force
  else apply_svc "$1"; fi
}

# Arranca UN servicio y espera su salud. 0 ok, 1 fallo, 99 parada.
start_one() {
  local svc="$1" mode="" before="" after=""
  if [ "$svc" = upf ] && ! gtp5g_loaded; then
    log "ERROR: modulo gtp5g no cargado: omito upf (sin plano de usuario). Cargalo en el host del LXC: modprobe gtp5g"
    return 1
  fi
  snapshot
  if ! parents_running "$svc"; then
    log "ERROR: omito $svc: su servicio padre (${PARENTS[$svc]}) no esta corriendo"
    return 1
  fi
  if [ -n "${PARENTS[$svc]:-}" ]; then
    if is_stale "$svc" || ! is_running "$svc"; then mode=force; fi
    is_stale "$svc" && log "$svc: su padre es mas nuevo (namespace obsoleto): recreo"
  fi
  is_running "$svc" && before="${ID[$svc]:-}"
  log "aplicando $svc${mode:+ (recreate)}"
  apply_svc "$svc" "$mode" || { log "FALLO: 'up' de $svc"; return 1; }
  # Un servicio que ya corria pasa igualmente por 'up -d --no-deps' (idempotente: compose
  # solo lo recrea si cambio su configuracion). Se deja constancia de lo ocurrido, porque
  # "Container X Running" en la salida de compose no dice si hubo o no cambio de configuracion.
  if [ -n "$before" ] && [ -z "$mode" ] && [ "$DRYRUN" != 1 ]; then
    after=$(dk inspect -f '{{.Id}}' "$svc" 2>/dev/null || true)
    if [ -z "$after" ]; then :
    elif [ "$after" = "$before" ]; then
      log "$svc: ya corria y compose no vio cambios de configuracion (mismo contenedor); si esperabas un valor nuevo del entorno, verifica con 'docker exec $svc env' o usa 'stop $svc' y luego 'start $svc'"
    else
      log "$svc: recreado con la configuracion nueva"
    fi
  fi
  wait_health "$svc"
}

declare -A SELECT=()
resolve_selection() {   # aditiva: el llamador pone SELECT=() antes
  local a s
  for a in "$@"; do
    case "$a" in
      nucleo)    for s in mongodb nrf udr udm ausf pcf nssf amf upf smf; do SELECT[$s]=1; done ;;
      ran)       for s in gnb ue dn-iperf; do SELECT[$s]=1; done ;;
      compresion) for s in cmp-dn cmp-ue envio envio-eco cmp-gen; do SELECT[$s]=1; done ;;
      obs)       for s in prometheus pushgateway; do SELECT[$s]=1; done ;;
      *)
        local found=0
        for s in "${MANAGED[@]}" "${EXTRAS[@]}"; do [ "$s" = "$a" ] && found=1; done
        if [ "$found" = 0 ]; then
          echo "etapa/servicio desconocido: '$a' (grafana: usa 'lab5g.sh grafana up')" >&2
          return 2
        fi
        SELECT[$a]=1 ;;
    esac
  done
}

http_get() {   # GET con tope de 5 s; curl -> wget -> python3
  [ -n "$PUSH_TOOL" ] || detect_push_tool
  case "$PUSH_TOOL" in
    curl)    timeout 6 curl -s -m 5 "$1" ;;
    wget)    timeout 6 wget -q -T 5 -t 1 -O - "$1" ;;
    python3) timeout 7 python3 -c 'import sys,urllib.request;sys.stdout.write(urllib.request.urlopen(sys.argv[1],timeout=5).read().decode())' "$1" ;;
    *) return 1 ;;
  esac
}

# Si el pushgateway no tiene series lab5g_cmp_eval (se pierden si se vacio),
# se republican con el evaluador. Maximo UN intento por arranque; un fallo se
# registra y no aborta.
ensure_cmp_eval() {
  snapshot
  is_running cmp-dn && is_running pushgateway || return 0
  [ "${HL[cmp-dn]:-}" = healthy ] || [ -z "${HL[cmp-dn]:-}" ] || return 0
  if [ "$DRYRUN" != 1 ] && http_get "$PUSH_BASE/metrics" 2>/dev/null | grep -q '^lab5g_cmp_eval'; then
    log "pushgateway ya tiene lab5g_cmp_eval"; return 0
  fi
  local rc=0; GATE_MAX_WAIT=600 wait_gate "publicar lab5g_cmp_eval" || rc=$?
  [ "$rc" = 0 ] || { log "no publico lab5g_cmp_eval (compuerta cerrada/parada)"; return 0; }
  log "publicando lab5g_cmp_eval (informacion --sin-archivos --publicar)"
  # En un contenedor aparte y efimero (256 MB): dentro de cmp-dn (64 MB) un OOM
  # mataria el shim y cortaria el tunel. La imagen ya existe por cmp-dn.
  # Un solo intento, tope de 900 s.
  DK_TIMEOUT=900 dk run --rm --network obs_net --memory 256m \
      -v "$LAB5G_DIR/compresion:/cmp:ro" -e PYTHONDONTWRITEBYTECODE=1 \
      python:3.14-alpine python3 /cmp/evaluar.py informacion --sin-archivos --publicar "$PUSH_BASE" \
    || log "aviso: la publicacion de lab5g_cmp_eval fallo (no aborta)"
  return 0
}

FAILED=()
start_services() {
  FAILED=()
  local idx=0 stage svc rc deg=0 list
  # Orden: las etapas y, al final, los extras nombrados explicitamente.
  local stages=("${STAGES[@]}")
  for svc in "${EXTRAS[@]}"; do
    if [ -n "${SELECT[$svc]:-}" ]; then stages+=("$svc"); fi
  done
  for stage in "${stages[@]}"; do
    idx=$((idx+1))
    for svc in $stage; do
      if [ "${#SELECT[@]}" -gt 0 ] && [ -z "${SELECT[$svc]:-}" ]; then continue; fi
      abort_requested && { log "parada solicitada: abandono el arranque"; return 99; }
      deg=0
      set_etapa "$idx" 0
      rc=0; wait_gate "$svc" || rc=$?
      [ "$rc" = 99 ] && { log "parada solicitada: abandono el arranque"; return 99; }
      if [ "$rc" = 1 ]; then deg=1; set_etapa "$idx" 1; fi   # un solo servicio degradado; la siguiente vuelta exige la compuerta otra vez
      rc=0; start_one "$svc" || rc=$?
      [ "$rc" = 99 ] && { log "parada solicitada: abandono el arranque"; return 99; }
      if [ "$rc" != 0 ]; then FAILED+=("$svc"); fi
      nap "$SETTLE"
    done
  done
  # Politica de reinicio "no" tambien en bajo demanda y sondas, si existen.
  snapshot
  local _n; for _n in "${ALL_NAMES[@]}"; do [ -n "${ST[$_n]:-}" ] && normalize_restart "$_n"; done
  set_etapa 0 0
  if [ "${#SELECT[@]}" -eq 0 ] || [ -n "${SELECT[cmp-dn]:-}" ]; then ensure_cmp_eval || true; fi
  if [ "${#FAILED[@]}" -gt 0 ]; then
    log "arranque terminado con fallos en: ${FAILED[*]}"
    return 1
  fi
  log "arranque terminado: todo en pie"
}

# ---------------------------------------------------------- recuperacion
LAST_GATE_LOG=0
recovery_cycle() {
  abort_requested && return 0
  snapshot
  # Si docker no respondio (fallo silencioso bajo carga) no se decide nada:
  # tomar "todo caido" por un timeout provocaria reinicios en cascada.
  if [ "$SNAP_N" -eq 0 ] && [ "$DRYRUN" != 1 ]; then log "docker no responde; salto el ciclo"; return 0; fi
  local svc cand="" why=""
  for svc in "${MANAGED[@]}" "${EXTRAS[@]}"; do
    local managed=1 down=0 stale=0
    for _e in "${EXTRAS[@]}"; do [ "$_e" = "$svc" ] && managed=0; done
    if is_running "$svc"; then
      is_stale "$svc" && stale=1
    else
      [ "$managed" = 1 ] && down=1
    fi
    [ "$down" = 0 ] && [ "$stale" = 0 ] && continue
    manual_stopped "$svc" && continue           # detenido a mano
    parents_running "$svc" || continue          # sin padre no hay a que unirse; el padre va antes
    if [ "$svc" = upf ] && ! gtp5g_loaded; then continue; fi
    in_cooldown "$svc" && continue
    is_exhausted "$svc" && continue             # 'agotado': no se insiste
    cand="$svc"; why=$([ "$down" = 1 ] && echo caido || echo obsoleto)
    break
  done
  [ -n "$cand" ] || return 0
  if ! gate_ok; then
    if [ $(( $(now) - LAST_GATE_LOG )) -ge "$GATE_LOG_EVERY" ]; then
      log "recuperacion: $cand $why pero la compuerta esta cerrada ($GATE_MSG)"; LAST_GATE_LOG=$(now)
    fi
    return 0
  fi
  lock_try || return 0
  log "recuperacion: reinicio $cand ($why; reinicios en ventana: $(window_count "$cand")/$MAX_RESTARTS)"
  record_restart "$cand"
  restart_svc "$cand" || log "FALLO: no pude reiniciar $cand"
  wait_health "$cand" || true     # los hijos de namespace esperan al siguiente ciclo
  lock_release
}

report_exhausted() {
  local svc
  for svc in "${MANAGED[@]}" "${EXTRAS[@]}"; do
    is_exhausted "$svc" && ! is_running "$svc" && log "AGOTADO: $svc (>= $MAX_RESTARTS reinicios en $((WINDOW/60)) min); no insisto"
  done
  return 0
}

# --------------------------------------------------------------- metricas
cg_mem_bytes() {   # $1 id completo; vacio si no se puede leer
  local id="$1" f
  for f in "$CGROUP_DIR/system.slice/docker-$id.scope/memory.current" \
           "$CGROUP_DIR/docker/$id/memory.current" \
           "$CGROUP_DIR/memory/docker/$id/memory.usage_in_bytes"; do
    [ -r "$f" ] && { cat "$f" 2>/dev/null && return 0; }
  done
  return 0
}

build_metrics() {   # usa ST/ID de un snapshot reciente
  local svc up b
  echo "# TYPE lab5g_stack_servicio_up gauge"
  for svc in "${MANAGED[@]}"; do
    up=0; is_running "$svc" && up=1
    echo "lab5g_stack_servicio_up{servicio=\"$svc\"} $up"
  done
  echo "# TYPE lab5g_stack_reinicios_total counter"
  for svc in "${MANAGED[@]}" "${EXTRAS[@]}"; do
    echo "lab5g_stack_reinicios_total{servicio=\"$svc\"} $(total_restarts "$svc")"
  done
  # Extra (no esta en el contrato): 1 si agotó los reinicios y sigue caido.
  echo "# TYPE lab5g_stack_agotado gauge"
  for svc in "${MANAGED[@]}"; do
    up=0; { is_exhausted "$svc" && ! is_running "$svc"; } && up=1
    echo "lab5g_stack_agotado{servicio=\"$svc\"} $up"
  done
  echo "# TYPE lab5g_stack_etapa gauge"
  echo "lab5g_stack_etapa $(get_etapa)"
  echo "lab5g_stack_latido_segundos $(now)"
  echo "lab5g_host_load1 $(read_load1)"
  echo "lab5g_host_io_presion_pct $(read_io_pct)"
  echo "lab5g_host_mem_disponible_mb $(read_mem_avail_mb)"
  for svc in "${MANAGED[@]}"; do
    [ -n "${ID[$svc]:-}" ] || continue
    b=$(cg_mem_bytes "${ID[$svc]}")
    [ -n "$b" ] && awk -v s="$svc" -v b="$b" 'BEGIN{printf "lab5g_ct_mem_mb{servicio=\"%s\"} %.1f\n", s, b/1048576}'
  done
  return 0
}

PUSH_TOOL=""
detect_push_tool() {
  if command -v curl >/dev/null 2>&1; then PUSH_TOOL=curl
  elif command -v wget >/dev/null 2>&1 && wget --help 2>&1 | grep -q -- '--body-file'; then PUSH_TOOL=wget
  elif command -v python3 >/dev/null 2>&1; then PUSH_TOOL=python3
  else PUSH_TOOL=none; fi
}
# PUT (reemplaza el grupo completo: las series de servicios que desaparecen
# no quedan fantasma). Nunca bloquea mas de ~5 s ni falla.
push_file() {
  local f="$1"
  if [ "$DRYRUN" = 1 ]; then echo "[dry-run] PUT $PUSH_URL ($(wc -l < "$f") lineas)" >&2; return 0; fi
  [ -n "$PUSH_TOOL" ] || detect_push_tool
  case "$PUSH_TOOL" in
    curl)    timeout 6 curl -s -m 5 -o /dev/null -f -X PUT --data-binary @"$f" "$PUSH_URL" ;;
    wget)    timeout 6 wget -q -T 5 -t 1 -O /dev/null --method=PUT --body-file="$f" "$PUSH_URL" ;;
    python3) timeout 7 python3 - "$f" "$PUSH_URL" <<'PY'
import sys, urllib.request
data = open(sys.argv[1], 'rb').read()
req = urllib.request.Request(sys.argv[2], data=data, method='PUT')
urllib.request.urlopen(req, timeout=5).read()
PY
    ;;
    *) return 1 ;;
  esac
}

metrics_loop() {
  local f="$STATE_DIR/metricas.prom" ok_prev=1 ok
  while :; do
    snapshot || true
    build_metrics > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f" || true
    ok=1; push_file "$f" 2>/dev/null || ok=0
    if [ "$ok" != "$ok_prev" ]; then
      [ "$ok" = 0 ] && log "pushgateway no responde; sigo sin metricas" || log "pushgateway vuelve a responder"
      ok_prev=$ok
    fi
    nap "$METRICS_PERIOD"
  done
}

# ------------------------------------------------------------- comandos
METRICS_PID=""
cmd_run() {
  ensure_dirs
  rm -f "$RUN_DIR/parado" "$RUN_DIR/parados"
  trap 'log "SIGTERM/SIGINT: termino"; exit 0' TERM INT
  trap '[ -n "$METRICS_PID" ] && kill "$METRICS_PID" 2>/dev/null || true' EXIT
  detect_push_tool
  log "iniciando (LAB5G_DIR=$LAB5G_DIR, compuerta load1<$GATE_LOAD io<$GATE_IO)"
  metrics_loop & METRICS_PID=$!
  lock_acquire
  SELECT=()
  start_services || true
  lock_release
  log "bucle de recuperacion cada ${RECOVERY_PERIOD}s"
  while :; do
    nap "$RECOVERY_PERIOD"
    prune_restart_log || true
    recovery_cycle || true
    report_exhausted
    [ "${LAB5G_ONESHOT:-0}" = 1 ] && break
  done
}

cmd_start() {
  ensure_dirs
  rm -f "$RUN_DIR/parado"
  SELECT=()
  resolve_selection "$@" || exit 2
  # 'start' sin argumentos = todo (nucleo, ran, compresion, obs; NO grafana).
  if [ "$#" -eq 0 ]; then manual_stop_del; else manual_stop_del "${!SELECT[@]}"; fi
  trap 'log "interrumpido"; exit 130' TERM INT
  lock_acquire
  local rc=0
  start_services || rc=$?
  lock_release
  [ "$rc" = 99 ] && rc=1
  exit "$rc"
}

cmd_stop() {
  ensure_dirs
  local i svc partial=0 targets=() done_=() left=()
  if [ "$#" -gt 0 ]; then
    partial=1; SELECT=()
    for svc in "$@"; do   # grafana y sondas tambien se pueden nombrar
      case "$svc" in
        grafana|blackbox|iperf-probe) SELECT[$svc]=1 ;;
        *) resolve_selection "$svc" || exit 2 ;;
      esac
    done
  else
    : > "$RUN_DIR/parado"   # antes que nada: frena arranque y recuperacion
  fi
  lock_acquire 120
  # Orden inverso: bajo demanda/sondas primero, luego la lista al reves.
  for svc in "${ON_DEMAND[@]}" "${EXTRAS[@]}"; do targets+=("$svc"); done
  for (( i=${#MANAGED[@]}-1; i>=0; i-- )); do targets+=("${MANAGED[$i]}"); done
  log "apagado ordenado (inverso, uno a uno, -t $STOP_TIMEOUT)"
  # A ciegas, sin depender de 'inspect': bajo carga el CLI falla en silencio y
  # saltarse un servicio "porque parecia parado" lo dejaria vivo.
  for svc in "${targets[@]}"; do
    if [ "$partial" = 1 ] && [ -z "${SELECT[$svc]:-}" ]; then continue; fi
    # Anotado ANTES de parar: la recuperacion no debe adelantarse y revivirlo.
    if [ "$partial" = 1 ]; then manual_stop_add "$svc"; fi
    log "deteniendo $svc"
    dc stop -t "$STOP_TIMEOUT" "$svc" >/dev/null 2>&1 || log "aviso: 'stop $svc' devolvio error"
    done_+=("$svc")
  done
  if [ "$partial" = 0 ]; then set_etapa 0 0; fi
  snapshot
  for svc in "${done_[@]}"; do if is_running "$svc"; then left+=("$svc"); fi; done
  lock_release
  if [ "${#left[@]}" -gt 0 ]; then log "ATENCION: siguen corriendo: ${left[*]}"; exit 1; fi
  if [ "$partial" = 1 ]; then
    log "detenidos: ${done_[*]} (anotados en /run/lab5g/parados; 'start <svc>' los reanuda)"
  else
    log "pila detenida (/run/lab5g/parado creado; 'lab5g.sh start' reanuda la recuperacion)"
  fi
}

cmd_status() {
  ensure_dirs
  snapshot
  printf '%-14s %-10s %-9s %8s\n' SERVICIO ESTADO UPTIME MEM_MB
  local svc st up m b t0
  for svc in "${MANAGED[@]}" "${ON_DEMAND[@]}" "${EXTRAS[@]}"; do
    st="${ST[$svc]:-ausente}"; up="-"; m="-"
    if [ "$st" = running ]; then
      t0=$(date -d "${SA[$svc]}" +%s 2>/dev/null || echo 0)
      [ "$t0" -gt 0 ] && up=$(fmt_dur $(( $(now) - t0 )))
      b=$(cg_mem_bytes "${ID[$svc]}")
      [ -n "$b" ] && m=$(awk -v b="$b" 'BEGIN{printf "%.0f", b/1048576}')
      [ -n "${HL[$svc]:-}" ] && st="running($(echo "${HL[$svc]}" | cut -c1-9))"
    fi
    is_exhausted "$svc" && [ "$st" != running ] && st="agotado"
    printf '%-14s %-10s %-9s %8s\n' "$svc" "$st" "$up" "$m"
  done
  echo
  echo "carga: load1=$(read_load1) io=$(read_io_pct)% memdisp=$(read_mem_avail_mb)MB | etapa=$(get_etapa)"
  [ -e "$RUN_DIR/parado" ] && echo "parado: la recuperacion automatica esta SUSPENDIDA (/run/lab5g/parado)"
  return 0
}

main() {
  case "${1:-}" in
    run)    cmd_run ;;
    start)  shift; cmd_start "$@" ;;
    stop)   shift; cmd_stop "$@" ;;
    status) cmd_status ;;
    gate)   if gate_ok; then echo "abierta: $GATE_MSG"; else echo "cerrada: $GATE_MSG"; exit 1; fi ;;
    *) echo "uso: ${0##*/} run|start [etapa|servicio...]|stop [servicio...]|status|gate" >&2; exit 2 ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
