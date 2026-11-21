#!/usr/bin/env bash
# Bootstrap del runner self-hosted dentro del LXC. Se ejecuta UNA VEZ como
# root; el pipeline de CI no puede hacerlo por si mismo porque justamente
# lo que instala son los permisos que le faltan.
#
#   ./deploy/bootstrap-runner.sh
#
# Idempotente: se puede repetir sin efectos secundarios.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Ejecutalo como root dentro del LXC." >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER_USER="${RUNNER_USER:-deploy}"

echo "[bootstrap] repo: $REPO_DIR  usuario del runner: $RUNNER_USER"

# --- 1. Reglas de red del lab (LAN) -----------------------------------
install -m 755 "$REPO_DIR/deploy/lab5g-netrules.sh" /usr/local/sbin/lab5g-netrules.sh
install -m 644 "$REPO_DIR/deploy/lab5g-netrules.service" \
               /etc/systemd/system/lab5g-netrules.service
systemctl daemon-reload
systemctl enable lab5g-netrules.service >/dev/null
echo "[bootstrap] lab5g-netrules.service instalado y habilitado"

# --- 2. Privilegios del runner ----------------------------------------
# Se valida ANTES de instalar: un sudoers malformado deja el sistema sin sudo.
if visudo -cf "$REPO_DIR/deploy/sudoers-lab5g-deploy" >/dev/null; then
  install -m 440 "$REPO_DIR/deploy/sudoers-lab5g-deploy" /etc/sudoers.d/lab5g-deploy
  echo "[bootstrap] /etc/sudoers.d/lab5g-deploy instalado"
else
  echo "[bootstrap] ERROR: sudoers malformado, no se instala" >&2
  exit 1
fi

# --- 2b. Pila escalonada del lab (entorno, directorios, scripts, unidades) ---
# El compose deja todos los servicios con 'restart: "no"': el unico que los
# arranca y repone es lab5g-stack.service. Aqui solo se INSTALA y se HABILITA;
# no se arranca (un arranque en frio tarda 20-30 min y lo decide el operador
# o el pipeline con 'lab5g.sh start').
install -d -m 755 -o root -g root /etc/lab5g
if [ -e /etc/lab5g/lab5g.env ]; then
  echo "[bootstrap] /etc/lab5g/lab5g.env ya existe: no se toca"
else
  install -m 640 -o root -g "$RUNNER_USER" "$REPO_DIR/.env.example" /etc/lab5g/lab5g.env
  echo "[bootstrap] /etc/lab5g/lab5g.env creado desde .env.example"
  echo "[bootstrap] AVISO: GRAFANA_ADMIN_PASSWORD tiene el valor de ejemplo;" \
       "fijala con: printf '%s' 'TU_CLAVE' | lab5g.sh env-set GRAFANA_ADMIN_PASSWORD" >&2
fi
# Se reafirman dueno y modo aunque ya existiera (sin tocar el contenido).
chown "root:$RUNNER_USER" /etc/lab5g/lab5g.env
chmod 640 /etc/lab5g/lab5g.env

install -d -m 755 -o "$RUNNER_USER" -g "$RUNNER_USER" /opt/lab5g
# .env es un enlace al entorno real: sync lo excluye (sobrevive) y asi
# 'docker compose' a mano tambien funciona desde /opt/lab5g.
ln -sfn /etc/lab5g/lab5g.env /opt/lab5g/.env
install -d -m 755 /var/lib/lab5g /var/backups/lab5g

for s in lab5g.sh lab5g-stack.sh lab5g-backup.sh; do
  install -m 755 "$REPO_DIR/deploy/$s" "/usr/local/sbin/$s"
done
for u in lab5g-stack.service lab5g-backup.service lab5g-backup.timer; do
  install -m 644 "$REPO_DIR/deploy/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable lab5g-stack.service lab5g-backup.timer >/dev/null
echo "[bootstrap] lab5g-stack.service y lab5g-backup.timer instalados y habilitados (NO arrancados)"
echo "[bootstrap] siguiente: lab5g.sh sync && lab5g.sh start"

# --- 3. Dependencias de los checks de CI -------------------------------
# ci/check-plmn.py necesita pyyaml. Se instala como paquete del sistema:
# Debian 13 marca el entorno como externally-managed (PEP 668) y en este
# runner pip no esta instalado, asi que 'pip install --user' no es opcion.
if python3 -c 'import yaml' 2>/dev/null; then
  echo "[bootstrap] OK: python3-yaml disponible"
else
  echo "[bootstrap] instalando python3-yaml..."
  apt-get update -qq
  apt-get install -y -qq python3-yaml
  python3 -c 'import yaml' 2>/dev/null \
    && echo "[bootstrap] OK: python3-yaml instalado" \
    || { echo "[bootstrap] ERROR: no pude instalar python3-yaml" >&2; exit 1; }
fi

# --- 4. Comprobaciones -------------------------------------------------
if id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -qx docker; then
  echo "[bootstrap] OK: $RUNNER_USER pertenece al grupo docker"
else
  echo "[bootstrap] AVISO: $RUNNER_USER no esta en el grupo docker;" \
       "el pipeline no podra usar docker compose" >&2
fi

if sudo -u "$RUNNER_USER" sudo -n /usr/local/sbin/lab5g-netrules.sh >/dev/null 2>&1; then
  echo "[bootstrap] OK: $RUNNER_USER puede aplicar las reglas de red"
else
  echo "[bootstrap] AVISO: sudo -n sigue fallando para $RUNNER_USER" >&2
fi

echo "[bootstrap] listo"
