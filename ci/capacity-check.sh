#!/usr/bin/env bash
# Comprueba que el host tiene margen para arrancar la pila. Falla RAPIDO y con
# mensajes accionables (mismo formato ok/bad/warn que ci/preflight.sh).
#
#   capacity-check.sh [--estricto]
#
# Codigos: 0 = todo bien, 1 = fallo duro, 2 = solo advertencias
# (con --estricto las advertencias tambien fallan, codigo 1).
set -uo pipefail

PROC="${LAB5G_PROC_DIR:-/proc}"
ENV_FILE="${LAB5G_ENV_FILE:-/etc/lab5g/lab5g.env}"
DOCKER_DIR="${LAB5G_DOCKER_DIR:-/var/lib/docker}"
GATE_LOAD="${GATE_LOAD:-4}"
GATE_IO="${GATE_IO:-40}"
MIN_MEM_MB="${MIN_MEM_MB:-600}"     # por debajo, mongod/Prometheus mueren por OOM al arrancar
MAX_SWAP_PCT="${MAX_SWAP_PCT:-70}"  # swap casi llena en disco mecanico = congelacion segura
MIN_DISK_GB="${MIN_DISK_GB:-2}"     # fallo duro: el LXC tiene 24 GB con ~7 GB libres; por debajo no caben ni los logs
WARN_DISK_GB="${WARN_DISK_GB:-4}"   # advertencia: el TSDB de Prometheus llega a 3 GB

ESTRICTO=0
[ "${1:-}" = "--estricto" ] && ESTRICTO=1

fail=0; warns=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; warns=$((warns+1)); }
lt()   { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a+0 < b+0)}'; }

# RAM
avail_kb=$(awk '/^MemAvailable:/ {print $2}' "$PROC/meminfo" 2>/dev/null)
avail_mb=$(( ${avail_kb:-0} / 1024 ))
if [ "$avail_mb" -ge "$MIN_MEM_MB" ]; then ok "RAM disponible ${avail_mb} MB (>= ${MIN_MEM_MB})"
else bad "RAM disponible ${avail_mb} MB (< ${MIN_MEM_MB}): detén servicios (lab5g.sh stop grafana) o libera memoria antes de arrancar"; fi

# Swap
swt=$(awk '/^SwapTotal:/ {print $2}' "$PROC/meminfo" 2>/dev/null); swf=$(awk '/^SwapFree:/ {print $2}' "$PROC/meminfo" 2>/dev/null)
if [ "${swt:-0}" -gt 0 ]; then
  pct=$(awk -v t="$swt" -v f="${swf:-0}" 'BEGIN{printf "%d", (t-f)*100/t}')
  if [ "$pct" -lt "$MAX_SWAP_PCT" ]; then ok "swap en uso ${pct} % (< ${MAX_SWAP_PCT})"
  else bad "swap en uso ${pct} % (>= ${MAX_SWAP_PCT}): el host esta paginando; reduce servicios o espera a que baje"; fi
else ok "sin swap configurada"; fi

# Carga: solo advertencia, porque 'lab5g.sh start' ya espera a la compuerta.
l=$(awk '{print $1}' "$PROC/loadavg" 2>/dev/null || echo 0)
if lt "$l" "$GATE_LOAD"; then ok "load1 $l (< $GATE_LOAD)"
else warn "load1 $l (>= $GATE_LOAD): el arranque esperara a que baje"; fi
io=$(sed -n 's/^some .*avg10=\([0-9.]*\).*/\1/p' "$PROC/pressure/io" 2>/dev/null | head -n1)
if [ -z "$io" ]; then warn "sin presion de I/O (PSI) disponible: no se puede medir"
elif lt "$io" "$GATE_IO"; then ok "presion de I/O ${io} % (< $GATE_IO)"
else warn "presion de I/O ${io} % (>= $GATE_IO): disco saturado; el arranque esperara"; fi

# Disco
dd_dir="$DOCKER_DIR"; [ -d "$dd_dir" ] || dd_dir=/
free_gb=$(df -Pk "$dd_dir" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}')
if [ "${free_gb:-0}" -lt "$MIN_DISK_GB" ]; then bad "espacio libre en $dd_dir: ${free_gb:-0} GB (< ${MIN_DISK_GB}): docker system df; borra imagenes/volumenes no usados"
elif [ "${free_gb:-0}" -lt "$WARN_DISK_GB" ]; then warn "espacio libre en $dd_dir: ${free_gb} GB (< ${WARN_DISK_GB}): justo; Prometheus puede crecer a 3 GB"
else ok "espacio libre en $dd_dir: ${free_gb} GB (>= ${WARN_DISK_GB})"; fi

# Modulos y dispositivos
if grep -q '^gtp5g ' "$PROC/modules" 2>/dev/null; then ok "modulo gtp5g cargado"
else bad "modulo gtp5g NO cargado: en el host del LXC ejecuta 'modprobe gtp5g' (sin el UPF no hay plano de usuario)"; fi
if grep -q '^sctp ' "$PROC/modules" 2>/dev/null || [ -d "$PROC/net/sctp" ]; then ok "SCTP disponible"
else bad "SCTP no disponible: en el host ejecuta 'modprobe sctp' (el AMF/gNB lo necesitan para NGAP)"; fi
if [ -c "${LAB5G_TUN:-/dev/net/tun}" ]; then ok "/dev/net/tun presente"
else bad "/dev/net/tun ausente: el LXC necesita el dispositivo tun (UE simulado)"; fi

# Entorno
if [ -r "$ENV_FILE" ]; then ok "entorno $ENV_FILE presente"
else bad "falta $ENV_FILE: ejecuta deploy/bootstrap-runner.sh (o crea el archivo desde .env.example)"; fi

echo
if [ "$fail" -ne 0 ]; then echo "Capacidad INSUFICIENTE"; exit 1; fi
if [ "$warns" -gt 0 ]; then
  if [ "$ESTRICTO" -eq 1 ]; then echo "Capacidad con advertencias (--estricto: fallo)"; exit 1; fi
  echo "Capacidad OK con $warns advertencia(s)"; exit 2
fi
echo "Capacidad OK"; exit 0
