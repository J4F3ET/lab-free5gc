#!/usr/bin/env bash
set -euo pipefail

UE_POOL="${UE_POOL:-10.60.0.0/16}"
DN_PREFIX="${DN_PREFIX:-10.100.204.}"

sysctl -w net.ipv4.ip_forward=1

# Resolver la interfaz del Data Network POR SU IP, no por nombre.
# Docker no garantiza que dn_net sea eth0/eth1/eth2 de forma estable.
DN_IF=$(ip -o -4 addr show | awk -v p="$DN_PREFIX" '$4 ~ "^"p {print $2; exit}')

if [ -z "$DN_IF" ]; then
  echo "[upf] ERROR: no encuentro interfaz en ${DN_PREFIX}0/24" >&2
  ip -o -4 addr show >&2
  exit 1
fi

echo "[upf] Data Network en interfaz ${DN_IF}"

iptables -t nat -C POSTROUTING -s "$UE_POOL" -o "$DN_IF" -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s "$UE_POOL" -o "$DN_IF" -j MASQUERADE

iptables -C FORWARD -i upfgtp -j ACCEPT 2>/dev/null \
  || iptables -A FORWARD -i upfgtp -j ACCEPT

echo "[upf] NAT configurado para ${UE_POOL} -> ${DN_IF}"