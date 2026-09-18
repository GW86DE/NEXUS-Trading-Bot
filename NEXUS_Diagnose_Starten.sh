#!/usr/bin/env bash
# NEXUS 10.1.9 / Diagnose 1.8.0: passiv; Standard 30 Minuten.
set -euo pipefail
umask 077
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8:strict
exec python3 -u "$(dirname -- "${BASH_SOURCE[0]}")/NEXUS_10_Diagnose.py" "$@"
