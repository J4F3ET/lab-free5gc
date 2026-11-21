#!/usr/bin/env bash
# CLI unico del lab 5G (root, o el usuario 'deploy' via 'sudo -n').
#
#   lab5g.sh sync                    checkout actual -> $LAB5G_DIR
#   lab5g.sh check [--estricto]      comprobacion de capacidad del host
#   lab5g.sh start [etapa|svc...]    arranque escalonado (sin args: todo menos grafana)
#   lab5g.sh stop [svc...]           apagado ordenado inverso (sin args: todo)
#   lab5g.sh status                  tabla de servicios + carga
#   lab5g.sh grafana up|down         grafana bajo demanda
#   lab5g.sh backup                  copia de Prometheus
#   lab5g.sh restore ARCHIVO [--si]  restaura una copia
#   lab5g.sh env-set CLAVE           fija un valor del entorno (el valor entra por stdin)
#   lab5g.sh marcar-desplegado SHA / ultimo-desplegado
set -euo pipefail

LAB5G_DIR="${LAB5G_DIR:-/opt/lab5g}"
ENV_FILE="${LAB5G_ENV_FILE:-/etc/lab5g/lab5g.env}"
SELF_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
STACK="${LAB5G_STACK:-$SELF_DIR/lab5g-stack.sh}"
BACKUP="${LAB5G_BACKUP:-$SELF_DIR/lab5g-backup.sh}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"

die() { echo "lab5g: $*" >&2; exit 1; }

# --- sync --------------------------------------------------------------
# Copia SOLO los archivos versionados (git ls-files) a un directorio estable:
# el checkout del runner se limpia con 'git clean -ffdx' y no sirve de lugar
# de trabajo. Se excluyen .git, .env (secretos locales) y muestras_bandido
# (datos generados por el lab).
cmd_sync() {
  local src rc=0
  STAGE_TMP=""
  # safe.directory: root leyendo un checkout de 'deploy' daria "dubious ownership".
  src=$(git -c safe.directory='*' rev-parse --show-toplevel 2>/dev/null) \
    || die "sync: ejecutalo dentro del checkout (no es un repo git: $PWD)"
  [ -f "$src/docker-compose.yaml" ] || die "sync: $src no parece el repo del lab (falta docker-compose.yaml)"
  if [ ! -d "$LAB5G_DIR" ]; then
    install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$LAB5G_DIR" 2>/dev/null || install -d "$LAB5G_DIR"
  fi
  local stage; stage=$(mktemp -d); STAGE_TMP="$stage"
  trap 'rm -rf "$STAGE_TMP" "$STAGE_TMP.lista"' EXIT
  # Lista NUL-separada de archivos versionados, sin los excluidos.
  git -c safe.directory='*' -C "$src" ls-files -z \
    | { grep -zvE '^(\.env$|muestras_bandido/)' || true; } \
    | while IFS= read -r -d '' f; do [ -e "$src/$f" ] || [ -L "$src/$f" ] && printf '%s\0' "$f"; done > "$stage.lista"
  ( cd "$src" && xargs -0 -a "$stage.lista" cp -a --parents -t "$stage" ) || rc=$?
  [ "$rc" = 0 ] || { rm -f "$stage.lista"; die "sync: fallo al preparar la copia"; }
  # --inplace / cp -a sobre el existente conservan el inode: los archivos de
  # config montados como bind-mount de UN solo archivo en contenedores vivos
  # verian el inode viejo si se reemplazaran por rename.
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --inplace --delete --exclude=/.git --exclude=/.env --exclude=/muestras_bandido \
      "$stage"/ "$LAB5G_DIR"/
  else
    # Sin rsync: borrar lo que ya no esta versionado y copiar encima.
    local viejos
    viejos=$(mktemp)
    ( cd "$LAB5G_DIR" && find . \( -path ./.git -o -path ./muestras_bandido -o -path ./.env \) -prune -o \
        \( -type f -o -type l \) -print0 | sed -z 's|^\./||' | sort -z ) > "$viejos"
    sort -z "$stage.lista" | comm -z -23 "$viejos" - | ( cd "$LAB5G_DIR" && xargs -0 -r rm -f -- )
    rm -f "$viejos"
    cp -a "$stage"/. "$LAB5G_DIR"/
    find "$LAB5G_DIR" -mindepth 1 -type d -empty -not -path "$LAB5G_DIR/muestras_bandido*" -not -path "$LAB5G_DIR/.git*" -delete 2>/dev/null || true
  fi
  rm -f "$stage.lista"
  echo "sync: $(wc -l < <(git -c safe.directory='*' -C "$src" ls-files)) archivos de $src -> $LAB5G_DIR"
}

# --- check -------------------------------------------------------------
cmd_check() {
  local c
  for c in "$LAB5G_DIR/ci/capacity-check.sh" "$(git rev-parse --show-toplevel 2>/dev/null || echo .)/ci/capacity-check.sh"; do
    [ -f "$c" ] && exec bash "$c" "$@"
  done
  die "check: no encuentro ci/capacity-check.sh (¿hiciste 'sync'?)"
}

# --- grafana -----------------------------------------------------------
cmd_grafana() {
  # shellcheck source=lab5g-stack.sh
  . "$STACK"
  ensure_dirs
  case "${1:-}" in
    up)
      snapshot
      is_running prometheus || die "grafana up: prometheus no esta corriendo; 'lab5g.sh start prometheus' primero"
      # Datos recientes: la muestra mas nueva de 'up' no debe tener mas de 2 min.
      local resp ts age
      resp=$(dk exec prometheus wget -q -O - 'http://localhost:9090/api/v1/query?query=max(timestamp(up))' 2>/dev/null || true)
      ts=$(printf '%s' "$resp" | sed -n 's/.*"value":\[[0-9.]*,"\([0-9.e+]*\)"\].*/\1/p')
      [ -n "$ts" ] || die "grafana up: prometheus no devolvio datos"
      age=$(awk -v t="$ts" -v n="$(date +%s)" 'BEGIN{printf "%d", n-t}')
      [ "$age" -le 120 ] || die "grafana up: los datos de prometheus tienen ${age}s de antiguedad (>120s)"
      manual_stop_del grafana
      gate_ok || die "grafana up: el host no tiene margen ($GATE_MSG); reintenta cuando baje la carga"
      log "arrancando grafana"
      dc up -d --no-deps grafana
      normalize_restart grafana
      wait_health grafana 300 || die "grafana no llego a estar sana"
      ;;
    down)
      dc stop -t "$STOP_TIMEOUT" grafana ;;
    *) die "uso: lab5g.sh grafana up|down" ;;
  esac
}

# --- env-set -----------------------------------------------------------
# El valor entra por stdin: un argumento quedaria en 'ps' y en el historial.
cmd_env_set() {
  local key="${1:-}" val ex tmp
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "env-set: clave invalida"
  ex="$LAB5G_DIR/.env.example"
  [ -r "$ex" ] || die "env-set: no encuentro $ex (¿hiciste 'sync'?)"
  grep -q "^${key}=" "$ex" || die "env-set: la clave $key no existe en .env.example"
  [ -w "$ENV_FILE" ] || die "env-set: no puedo escribir $ENV_FILE"
  IFS= read -r val || true
  [ -n "$val" ] || die "env-set: valor vacio (pasalo por stdin)"
  # compose-go no desescapa \' dentro de comillas simples: un valor con ' no se
  # puede guardar de forma fiable, asi que se rechaza (igual que \ al final).
  case "$val" in *\'*) die "env-set: el valor no puede contener comillas simples (')" ;; esac
  case "$val" in *"\\") die "env-set: el valor no puede terminar en barra invertida" ;; esac
  # Comillas simples: compose las lee literales (no expande $ ni interpreta
  # barras ni &).
  tmp=$(mktemp "$(dirname "$ENV_FILE")/.envset.XXXXXX")
  chmod 600 "$tmp"
  # awk con ENVIRON: sin interpretar barras ni '&' del valor (\047 = comilla simple).
  K="$key" V="$val" awk '
    BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"]; hecho = 0 }
    index($0, k "=") == 1 { print k "=\047" v "\047"; hecho = 1; next }
    { print }
    END { if (!hecho) print k "=\047" v "\047" }' "$ENV_FILE" > "$tmp"
  # 'cat >' conserva dueno, grupo y modo (640 root:deploy) del original.
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
  echo "env-set: $key actualizada"
}

# --- desplegado -----------------------------------------------------------
# Ultimo sha desplegado (lo usa el workflow para saber que hay en el lab).
DEPLOYED_FILE="${LAB5G_STATE_DIR:-/var/lib/lab5g}/desplegado"
cmd_marcar_desplegado() {
  local sha="${1:-}"
  [[ "$sha" =~ ^[0-9a-f]{7,40}$ ]] || die "marcar-desplegado: sha invalido"
  mkdir -p "$(dirname "$DEPLOYED_FILE")"
  echo "$sha" > "$DEPLOYED_FILE"
  echo "marcado: $sha"
}
cmd_ultimo_desplegado() { [ -r "$DEPLOYED_FILE" ] && cat "$DEPLOYED_FILE" || true; }

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    sync)    cmd_sync ;;
    check)   cmd_check "$@" ;;
    start)   exec "$STACK" start "$@" ;;
    stop)    exec "$STACK" stop "$@" ;;
    status)  exec "$STACK" status ;;
    grafana) cmd_grafana "$@" ;;
    backup)  exec "$BACKUP" backup ;;
    restore) exec "$BACKUP" restore "$@" ;;
    env-set) cmd_env_set "$@" ;;
    marcar-desplegado) cmd_marcar_desplegado "$@" ;;
    ultimo-desplegado) cmd_ultimo_desplegado ;;
    *) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
  esac
}
main "$@"
