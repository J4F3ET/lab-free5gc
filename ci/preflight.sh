#!/usr/bin/env bash
# Verifica que el entorno LXC/host puede soportar free5GC.
# Falla RAPIDO y con mensajes accionables.
set -uo pipefail

fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }

echo "=== Preflight lab-free5gc ==="

# --- Capa 0: modulos de kernel (compartidos con el host Proxmox) ---
if grep -q '^gtp5g ' /proc/modules; then
  ok "modulo gtp5g cargado"
else
  bad "gtp5g NO cargado. En el HOST Proxmox: modprobe gtp5g"
  bad "  (si falla tras un upgrade de kernel, recompila via DKMS)"
fi

if grep -q '^sctp ' /proc/modules; then
  ok "modulo sctp cargado"
else
  bad "sctp NO cargado -> N2/NGAP fallara. En el HOST: modprobe sctp"
fi

# --- Capa 1: devices y capabilities ---
[ -c /dev/net/tun ] \
  && ok "/dev/net/tun presente" \
  || bad "/dev/net/tun ausente -> revisa lxc.mount.entry en la config del LXC"

if capsh --print 2>/dev/null | grep -q 'cap_net_admin'; then
  ok "CAP_NET_ADMIN disponible"
else
  bad "sin CAP_NET_ADMIN -> el LXC no es privilegiado o hay cap.drop"
fi

# Prueba real: crear y borrar un netdev dummy
if ip link add lab5gtest type dummy 2>/dev/null; then
  ip link del lab5gtest
  ok "puedo crear interfaces de red"
else
  bad "no puedo crear netdevs -> falta lxc.apparmor.profile: unconfined"
fi

# --- Capa 2: sysctl ---
[ "$(sysctl -n net.ipv4.ip_forward)" = "1" ] \
  && ok "ip_forward activo" \
  || bad "ip_forward=0 -> sysctl -w net.ipv4.ip_forward=1"

rp=$(sysctl -n net.ipv4.conf.all.rp_filter)
[ "$rp" = "2" ] && ok "rp_filter en modo loose (2)" \
                || warn "rp_filter=$rp; recomendado 2 con multiples redes Docker"

# --- Capa 2: Docker ---
docker info >/dev/null 2>&1 \
  && ok "docker operativo" \
  || bad "docker no responde"

mtu=$(docker network inspect bridge -f '{{index .Options "com.docker.network.driver.mtu"}}' 2>/dev/null)
[ -n "$mtu" ] && ok "MTU por defecto de Docker: $mtu" \
              || warn "MTU de Docker sin fijar; con Tailscale puede dar problemas de TCP"

# --- Capa 3: Tailscale ---
if command -v tailscale >/dev/null 2>&1; then
  if tailscale status >/dev/null 2>&1; then
    ok "tailscale conectado"
    routes=$(tailscale status --json 2>/dev/null \
             | grep -o '"AdvertisedRoutes":\[[^]]*\]' || true)
    [ -n "$routes" ] && ok "rutas anunciadas: $routes" \
                     || warn "sin rutas anunciadas"
  else
    warn "tailscale instalado pero no conectado"
  fi

  if iptables -C DOCKER-USER -i tailscale0 -d 10.100.200.0/24 -j ACCEPT 2>/dev/null \
     || iptables -C DOCKER-USER -i tailscale0 -j ACCEPT 2>/dev/null; then
    ok "regla DOCKER-USER para tailscale0 presente"
  else
    bad "falta ACCEPT en DOCKER-USER para tailscale0"
    bad "  -> ejecuta /usr/local/sbin/lab5g-netrules.sh"
  fi
fi

# --- Recursos ---
mem=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
[ "$mem" -ge 4096 ] && ok "memoria: ${mem} MiB" \
                    || warn "solo ${mem} MiB; recomendado >= 4096"

echo
[ $fail -eq 0 ] && echo "Preflight OK" || { echo "Preflight FALLIDO"; exit 1; }
