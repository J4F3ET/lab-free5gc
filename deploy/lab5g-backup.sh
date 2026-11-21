#!/usr/bin/env bash
# Copia de Prometheus (snapshot de la API admin) -> /var/backups/lab5g.
#
#   lab5g-backup.sh                 hace la copia (rota a las 3 ultimas)
#   lab5g-backup.sh restore ARCHIVO [--si]
#
# COPIA AL HOST: desde dentro del LXC no se puede escribir fuera de el. Si se
# quiere una copia fuera del contenedor, hacerlo desde el host Proxmox, p. ej.:
#   pct pull 110 /var/backups/lab5g/prometheus-AAAAmmdd-HHMM.tar.gz /mnt/backup/
# (o un cron del host con "pct exec 110 -- ls -1t /var/backups/lab5g" + pct pull).
#
# Requiere que prometheus corra con --web.enable-admin-api. No corre si la
# compuerta de carga esta cerrada: una copia sobre disco mecanico en un host
# saturado es justo lo que lo congelo.
set -euo pipefail

BACKUP_DIR="${LAB5G_BACKUP_DIR:-/var/backups/lab5g}"
# Solo 2 copias: el LXC tiene 24 GB con ~7 GB libres y el TSDB puede llegar a 2 GB.
KEEP="${BACKUP_KEEP:-2}"
PROM_DATA="${PROM_DATA:-/prometheus}"   # ruta del TSDB dentro del contenedor
LIB="${LAB5G_STACK_LIB:-$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lab5g-stack.sh}"
# shellcheck source=lab5g-stack.sh
. "$LIB"

# Disco mecanico: I/O ocioso y prioridad minima; gzip -1 gasta poca CPU.
lowprio() { if command -v ionice >/dev/null 2>&1; then ionice -c3 nice -n 19 "$@"; else nice -n 19 "$@"; fi; }

do_backup() {
  mkdir -p "$BACKUP_DIR"
  # Espera (con tope, 20 min por defecto) a que el host se calme en vez de
  # rendirse al primer intento: el timer corre a las 03:30 y 'Persistent' no
  # repite si la unidad termina. Si no se abre, 75 = EX_TEMPFAIL (visible).
  local grc=0
  GATE_MAX_WAIT="${BACKUP_GATE_WAIT:-1200}" wait_gate backup || grc=$?
  if [ "$grc" != 0 ]; then
    log "backup omitido: compuerta cerrada tras la espera ($GATE_MSG)"
    exit 75
  fi
  snapshot
  is_running prometheus || { log "ERROR: prometheus no esta corriendo"; exit 1; }
  # Espacio: el snapshot son hardlinks (casi gratis) pero el .tar.gz no. Se
  # exige libre >= 2x el tamano del TSDB + 2 GB antes de tocar nada.
  local size_kb free_kb need_kb
  size_kb=$(dk exec prometheus du -sk "$PROM_DATA" 2>/dev/null | awk '{print $1}' || true)
  [[ "${size_kb:-}" =~ ^[0-9]+$ ]] || { log "ERROR: no pude medir el tamano de $PROM_DATA; no copio nada"; exit 1; }
  free_kb=$(df -Pk "$BACKUP_DIR" | awk 'NR==2 {print $4}')
  need_kb=$(( size_kb * 2 + 2 * 1048576 ))
  if [ "$free_kb" -lt "$need_kb" ]; then
    log "ERROR: poco espacio en $BACKUP_DIR: libre $((free_kb/1024)) MB, necesito $((need_kb/1024)) MB (2x TSDB de $((size_kb/1024)) MB + 2 GB). No copio nada."
    exit 1
  fi
  local resp name ts out
  resp=$(dk exec prometheus wget -q -O - --post-data='' http://localhost:9090/api/v1/admin/tsdb/snapshot 2>/dev/null || true)
  name=$(printf '%s' "$resp" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p')
  if [ -z "$name" ]; then
    log "ERROR: la API de snapshot no respondio (respuesta: ${resp:-vacia}). ¿Prometheus lleva --web.enable-admin-api?"
    exit 1
  fi
  ts=$(date +%Y%m%d-%H%M)
  out="$BACKUP_DIR/prometheus-$ts.tar.gz"
  log "snapshot $name -> $out"
  # 'docker cp' emite un tar; no hace falta tar dentro de la imagen de Prometheus.
  if dk cp "prometheus:$PROM_DATA/snapshots/$name" - | lowprio gzip -1 -c > "$out.tmp"; then
    mv "$out.tmp" "$out"
  else
    rm -f "$out.tmp"; dk exec prometheus rm -rf "$PROM_DATA/snapshots/$name" || true
    log "ERROR: no pude empaquetar el snapshot"; exit 1
  fi
  dk exec prometheus rm -rf "$PROM_DATA/snapshots/$name" || log "aviso: no pude borrar el snapshot temporal $name"
  # Rotacion: se conservan las $KEEP mas recientes.
  # shellcheck disable=SC2012
  ls -1t "$BACKUP_DIR"/prometheus-*.tar.gz 2>/dev/null | tail -n +"$((KEEP+1))" | while read -r old; do
    rm -f -- "$old"; log "rotado: $old"
  done
  log "backup listo: $out ($(du -h "$out" | cut -f1))"
}

do_restore() {
  local file="${1:-}" confirm="${2:-}"
  [ -r "$file" ] || { echo "restore: archivo ilegible: '$file'" >&2; exit 2; }
  if [ "$confirm" != "--si" ]; then
    [ -t 0 ] || { echo "restore BORRA los datos actuales de Prometheus; repite con --si para confirmar" >&2; exit 2; }
    read -r -p "Esto BORRA los datos actuales de Prometheus y los reemplaza por $file. ¿Seguir? [escribe si] " a
    [ "$a" = si ] || { echo "cancelado"; exit 1; }
  fi
  local vol img dir base
  vol=$(dk inspect -f '{{range .Mounts}}{{if eq .Destination "'"$PROM_DATA"'"}}{{.Name}}{{end}}{{end}}' prometheus 2>/dev/null || true)
  img=$(dk inspect -f '{{.Config.Image}}' prometheus 2>/dev/null || true)
  [ -n "$vol" ] && [ -n "$img" ] || { log "ERROR: no encuentro el contenedor/volumen de prometheus"; exit 1; }
  # La recuperacion automatica competiria con el restore (reiniciaria prometheus
  # a mitad de la restauracion): cerrojo + anotar como parado a mano. Se libera
  # SIEMPRE (EXIT, tambien tras un error).
  lock_acquire 600
  manual_stop_add prometheus
  trap 'manual_stop_del prometheus; lock_release' EXIT
  log "deteniendo prometheus"
  dc stop -t "$STOP_TIMEOUT" prometheus
  dir=$(dirname "$(readlink -f "$file")"); base=$(basename "$file")
  log "restaurando $base en el volumen $vol"
  # Contenedor auxiliar con la misma imagen (busybox incluido), como root para
  # poder escribir y devolver la propiedad a 'nobody' (65534), que es quien
  # corre prometheus.
  DK_TIMEOUT=1800 dk run --rm -u 0 --entrypoint sh -v "$vol:$PROM_DATA" -v "$dir:/restore:ro" "$img" -c "
    set -e
    rm -rf $PROM_DATA/*
    mkdir /tmp/r && tar xzf /restore/'$base' -C /tmp/r
    cp -a /tmp/r/*/. $PROM_DATA/
    chown -R 65534:65534 $PROM_DATA"
  log "restauracion hecha. La recuperacion automatica volvera a levantar prometheus en ~1 min; o ya: lab5g.sh start prometheus"
}

case "${1:-backup}" in
  backup)  ensure_dirs; do_backup ;;
  restore) shift; ensure_dirs; do_restore "$@" ;;
  *) echo "uso: ${0##*/} [backup|restore ARCHIVO [--si]]" >&2; exit 2 ;;
esac
