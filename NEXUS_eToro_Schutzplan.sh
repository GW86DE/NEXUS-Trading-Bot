#!/usr/bin/env bash
# Explizite Schutzplan-Wartung; ohne Optionen wird nur die Hilfe angezeigt.
set -euo pipefail
umask 077
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8:strict
nexus_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
nexus_python="$nexus_root/.venv/bin/python"
if [[ ! -x "$nexus_python" ]]; then
    nexus_python="$(command -v python3)"
fi
if [[ $# -eq 0 ]]; then
    set -- --help
fi
exec "$nexus_python" "$nexus_root/etoro_protection_repair.py" "$@"
