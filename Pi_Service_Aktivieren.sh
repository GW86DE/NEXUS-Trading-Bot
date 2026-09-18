#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
MODE="paper"
[[ -f handelsmodus.txt ]] && MODE="$(tr -d '[:space:]' < handelsmodus.txt | tr '[:upper:]' '[:lower:]')"
if [[ "$MODE" == "live" ]]; then
  echo "ACHTUNG: handelsmodus.txt steht auf LIVE."
  echo "Die separate LIVE-Freigabe bleibt weiterhin zwingend erforderlich."
  read -r -p "Dienst trotzdem aktivieren? Tippe JA: " ANSWER
  [[ "$ANSWER" == "JA" ]] || { echo "Abgebrochen."; exit 1; }
fi
./Pi_Service_Unit_Pruefen.sh core
WEBUI_BEREIT=0
if [[ -x "$ROOT/.venv/bin/python" ]] && "$ROOT/.venv/bin/python" webui_setup.py --status >/dev/null 2>&1; then
  ./Pi_Service_Unit_Pruefen.sh webui
  WEBUI_BEREIT=1
fi
sudo systemctl enable --now tradingbot-pi5.service
if [[ "$WEBUI_BEREIT" == "1" ]]; then
  sudo systemctl enable --now tradingbot-webui.service
else
  echo "WebUI bleibt aus: zuerst ./.venv/bin/python webui_setup.py ausfuehren."
fi
sleep 2
sudo systemctl --no-pager --full status tradingbot-pi5.service || true
sudo systemctl --no-pager --full status tradingbot-webui.service || true
