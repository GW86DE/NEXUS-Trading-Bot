#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
MODE="paper"
[[ -f handelsmodus.txt ]] && MODE="$(tr -d '[:space:]' < handelsmodus.txt | tr '[:upper:]' '[:lower:]')"
if [[ "$MODE" == "live" ]]; then
  echo "ACHTUNG: handelsmodus.txt steht auf LIVE."
  echo "Die separate, zeitlich begrenzte LIVE-Freigabe bleibt zwingend erforderlich."
  read -r -p "Dienst trotzdem starten? Tippe JA: " ANSWER
  [[ "$ANSWER" == "JA" ]] || { echo "Abgebrochen."; exit 1; }
fi
WEBUI_BEREIT=0
if [[ -x "$ROOT/.venv/bin/python" ]] && "$ROOT/.venv/bin/python" webui_setup.py --status >/dev/null 2>&1; then
  ./Pi_Service_Unit_Pruefen.sh webui
  WEBUI_BEREIT=1
fi
./Pi_Service_Unit_Pruefen.sh core
sudo systemctl start tradingbot-pi5.service
if [[ "$WEBUI_BEREIT" == "1" ]]; then
  sudo systemctl start tradingbot-webui.service
fi
sleep 2
sudo systemctl --no-pager --full status tradingbot-pi5.service || true
