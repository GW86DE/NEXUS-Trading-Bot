#!/usr/bin/env bash
set -euo pipefail
echo "WARNUNG: Das stoppt den gesamten Prozess."
echo "Damit endet die client-seitige Ueberwachung. Bestaetigte eToro- und OKX-Schutzorders bleiben brokerseitig aktiv."
read -r -p "Zum vollstaendigen Stoppen STOP eingeben: " ANSWER
[[ "$ANSWER" == "STOP" ]] || { echo "Abgebrochen."; exit 1; }
sudo systemctl stop tradingbot-pi5.service
