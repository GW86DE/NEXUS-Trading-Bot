#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  echo "FEHLER: .venv fehlt. Zuerst ./Pi_Installieren.sh ausfuehren."
  exit 2
fi
if ! ./.venv/bin/python webui_setup.py --status >/dev/null 2>&1; then
  echo "FEHLER: Zuerst ./.venv/bin/python webui_setup.py ausfuehren."
  exit 2
fi
if [[ ! -x ./Pi_Service_Unit_Pruefen.sh ]]; then
  echo "FEHLER: Pi_Service_Unit_Pruefen.sh fehlt. Bitte ./Pi_Installieren.sh erneut ausfuehren."
  exit 78
fi

# Eine fehlende Unit kann nach einer manuellen Bereinigung oder einem sehr
# alten Upgrade vorkommen. In diesem einen Fall wird sie aus DER Vorlage dieses
# Release-Ordners neu erzeugt. Existiert dagegen bereits eine Unit, wird sie
# nicht ueberschrieben: Pi_Service_Unit_Pruefen.sh muss dann erst beweisen,
# dass sie auf genau diesen Ordner zeigt. So kann diese Hilfe niemals still
# einen noch vorhandenen 8.1.x-Dienst starten.
SERVICE_NAME="tradingbot-webui.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
SERVICE_TEMPLATE="$ROOT/tradingbot-webui.service.template"
if ! systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
  if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
    echo "FEHLER: Vorlage fehlt: $SERVICE_TEMPLATE"
    exit 78
  fi
  case "$ROOT" in
    *[[:space:]]*|*'"'*|*'\\'*)
      echo "FEHLER: Bot-Pfad darf keine Leerzeichen, Anfuehrungszeichen oder Backslashes enthalten: $ROOT"
      exit 78
      ;;
  esac
  USER_NAME="$(id -un)"
  GROUP_NAME="$(id -gn "$USER_NAME")"
  VERSION_RAW="$(tr -d '[:space:]' < VERSION.txt)"
  VERSION_DISPLAY="${VERSION_RAW%-NEXUS}"
  if [[ -z "$VERSION_DISPLAY" ]]; then
    echo "FEHLER: VERSION.txt ist leer."
    exit 78
  fi
  SERVICE_TMP="$(mktemp)"
  trap 'rm -f "$SERVICE_TMP"' EXIT
  BOT_ROOT="$ROOT" BOT_USER="$USER_NAME" BOT_GROUP="$GROUP_NAME" BOT_VERSION="$VERSION_DISPLAY" \
    ./.venv/bin/python - "$SERVICE_TMP" <<'PYRENDER'
import os
import sys
from pathlib import Path

template = Path("tradingbot-webui.service.template").read_text(encoding="utf-8")
template = template.replace("__USER__", os.environ["BOT_USER"])
template = template.replace("__GROUP__", os.environ["BOT_GROUP"])
template = template.replace("__BOTDIR__", os.environ["BOT_ROOT"])
template = template.replace("__VERSION__", os.environ["BOT_VERSION"])
Path(sys.argv[1]).write_text(template, encoding="utf-8")
PYRENDER
  sudo install -m 0644 "$SERVICE_TMP" "$SERVICE_PATH"
  sudo systemctl daemon-reload
  echo "WebUI-Service-Unit wurde aus tradingbot-webui.service.template fuer diesen Release-Ordner erstellt."
fi

./Pi_Service_Unit_Pruefen.sh webui
./.venv/bin/python webui_network_setup.py --mode "${NEXUS_WEBUI_ACCESS_MODE:-auto}"
sudo systemctl enable --now "$SERVICE_NAME"
sleep 2
sudo systemctl --no-pager --full status "$SERVICE_NAME"
