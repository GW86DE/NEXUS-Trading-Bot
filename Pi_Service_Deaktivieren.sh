#!/usr/bin/env bash
set -euo pipefail
# Nur Autostart abschalten. Einen laufenden Trader hier absichtlich NICHT
# beenden, damit Schutz-/Exit-Ueberwachung nicht durch einen harmlosen
# Verwaltungsbefehl verschwindet. Fuer Prozess-Stopp: Pi_Service_Stoppen.sh.
sudo systemctl disable tradingbot-pi5.service
sudo systemctl disable tradingbot-webui.service >/dev/null 2>&1 || true
echo "Autostart fuer Trading-Core und WebUI deaktiviert. Laufende Prozesse wurden NICHT gestoppt."
echo "Zum kontrollierten Prozess-Stopp: ./Pi_Service_Stoppen.sh"
