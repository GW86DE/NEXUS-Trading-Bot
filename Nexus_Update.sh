#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ "$EUID" -eq 0 ]]; then
  echo 'Nicht mit sudo starten; sudo wird nur fuer Systemschritte verwendet.'
  exit 78
fi
# State isolation is not a test bypass: all groups run from a verified copy.
export TRADINGBOT_ALLOW_LOCAL_STATE=1
python3 nexus_update.py "$@" 2>&1 | tee -a "$ROOT/nexus_update.log"
