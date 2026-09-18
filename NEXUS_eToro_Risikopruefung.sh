#!/usr/bin/env bash
# Lesender eToro-Kontoabgleich. Kein Dienststart und keine Handelsaktion.
set -euo pipefail
umask 077
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8:strict
nexus_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
nexus_python="$nexus_root/.venv/bin/python"
if [[ ! -x "$nexus_python" ]]; then
    nexus_python="$(command -v python3)"
fi
if [[ $# -eq 0 ]]; then
    nexus_export_dir="$nexus_root/diagnosen"
    mkdir -p -- "$nexus_export_dir"
    nexus_export_name="$("$nexus_python" -c 'from datetime import datetime, timezone; from uuid import uuid4; print("eToro_Risikobelege_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:12] + ".json")')"
    printf 'Lesender eToro-DEMO-Kontoabgleich. Keine Orders und keine Risikofreigabe.\n'
    printf 'Belegdatei: %s\n' "$nexus_export_dir/$nexus_export_name"
    exec "$nexus_python" "$nexus_root/etoro_risk_maintenance.py" \
        --collect --environment DEMO --output "$nexus_export_dir/$nexus_export_name"
fi
exec "$nexus_python" "$nexus_root/etoro_risk_maintenance.py" "$@"
