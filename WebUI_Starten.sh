#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  echo "Virtuelle Umgebung fehlt. Bitte zuerst Pi_Installieren.sh ausfuehren."
  exit 2
fi
exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/webui_start.py" "$@"

