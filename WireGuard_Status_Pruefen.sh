#!/usr/bin/env bash
set -u
echo "=== WireGuard-Interface ==="
if command -v wg >/dev/null 2>&1; then
  sudo wg show || true
else
  echo "WireGuard ist noch nicht installiert."
fi
echo
echo "=== Adresse wg0 ==="
ip -brief address show wg0 2>/dev/null || echo "wg0 ist nicht aktiv."
echo
echo "=== WebUI-Listener 8780 ==="
ss -ltn 2>/dev/null | awk 'NR==1 || /:8780[[:space:]]/' || true
echo
echo "Sicherheitsziel: 8780 nur auf 127.0.0.1, einer privaten LAN-IP oder der WireGuard-IP."
echo "Nie auf 0.0.0.0/:: und nie TCP 8780 in der FRITZ!Box freigeben."
