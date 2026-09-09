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
