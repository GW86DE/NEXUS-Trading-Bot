#!/usr/bin/env bash
# TradingBot NEXUS 9.0 starten -- Aktien (eToro) und Krypto (OKX) gleichzeitig.
#
# Dieses Skript benutzt IMMER die Python-Umgebung des Bots. Ein Aufruf mit
# dem System-Python endet sonst mit "No module named 'pandas'", weil der
# Installer alle Pakete ausschliesslich in .venv installiert.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "FEHLER: Python-Umgebung fehlt. Bitte zuerst ./Pi_Installieren.sh ausfuehren."
  exit 78
fi

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1

exec "$ROOT/.venv/bin/python" nexus_start.py "$@"
