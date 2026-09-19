#!/usr/bin/env bash
# NEXUS 10.8.1 / Diagnose 1.9.0: passiv, keine Brokeraktion. Standard 30 Minuten.
set -euo pipefail
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8:strict
exec python3 -u "$(dirname -- "${BASH_SOURCE[0]}")/NEXUS_10_8_1_Diagnose.py" "$@"
