#!/usr/bin/env bash
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
exec "$ROOT/.venv/bin/python" gui_app.py
