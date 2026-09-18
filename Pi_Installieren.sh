#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Das komplette Skript niemals mit sudo starten: sonst gehoeren .venv und
# State-Dateien root, waehrend systemd spaeter bewusst als normaler Benutzer
# laeuft. sudo wird nur fuer apt/systemd an den noetigen Stellen verwendet.
if [[ "$EUID" -eq 0 ]]; then
  echo "FEHLER: Bitte dieses Skript NICHT mit sudo starten."
  echo "Richtig: ./Pi_Installieren.sh"
  exit 78
fi
if [[ ! -w "$ROOT" ]]; then
  echo "FEHLER: Der aktuelle Benutzer kann im Bot-Ordner nicht schreiben: $ROOT"
  exit 78
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "FEHLER: Dieses Setup ist fuer Raspberry Pi OS / Linux gedacht."
  exit 78
fi
ARCH="$(uname -m)"
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
  echo "FEHLER: 64-Bit ARM erforderlich. Erkannt: $ARCH"
  echo "Bitte Raspberry Pi OS 64-Bit verwenden."
  exit 78
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "FEHLER: python3 fehlt."
  exit 78
fi

if ! python3 - <<'PYCHECK'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PYCHECK
then
  echo "FEHLER: Python >= 3.11 ist erforderlich. Erkannt: $(python3 --version 2>&1)"
  exit 78
fi

# Release-Vollstaendigkeit VOR apt/pip pruefen. So faellt ein fehlerhaft
# gepacktes ZIP sofort auf und nicht erst nach einer langen Installation.
REQUIRED_RELEASE_FILES=(
  broker_observation.py pulsar/diagnostics.py webui/diagnostics.py
  webui/static/execution_details.js webui/static/ai_diagnostics.js
  CHANGELOG.md IMPLEMENTATION_REPORT.md TEST_REPORT.md MIGRATION_NOTES.md KNOWN_ISSUES.md
  RELEASE_BUILD.txt release_unpack.py
  massive_service.py risk_basis_review.py etoro_protection_evidence.py etoro_protection_journal.py
  candle_observation.py decision_explanation.py pulsar/news_normalization.py NEXUS_Diagnose_Starten.sh NEXUS_10_Diagnose.py
  tests/test_v101_massive.py tests/test_v101_migration.py tests/test_v101_candle_observation.py
  tests/test_v101_halt_diagnostics.py tests/test_v101_fmp_context.py tests/test_v101_evidence_packet.py
  tests/test_v100_risk_persistence.py tests/test_v100_execution_recovery.py
  tests/test_v100_broker_health.py tests/test_v100_strategy_pulsar.py
  tests/test_v100_analysis_jobs.py tests/test_v100_webui_backend.py
  tests/test_v100_frontend.py tests/frontend_v100.test.js
  check_test_dependencies.py tests/test_v974_test_dependencies.py
  etoro_accounting_resolution.py broker_display_context.py Nexus_Buchungsabgleich.py
  tests/test_v972_accounting_projection.py tests/test_v972_webui_contexts.py tests/test_v972_optional_provider.py tests/test_v972_migration_and_fees.py
  ledger_result.py tests/test_v971_installation_regressions.py
  INSTALLATIONSANLEITUNG_NEXUS_9.7.4_DE.md CHANGELOG_v9.7.4_NEXUS.txt
  VERSION.txt live_trader.py gui_app.py news_sources.py news_check.py research_snapshot.py crypto_diagnose.py
  console_io.py tool_runner.py pi_preflight.py self_test.py
  tests_intelligence.py tests_integration_v560.py volltest.py requirements-lock.txt requirements-test.txt
  tradingbot-pi5.service.template tradingbot-webui.service.template
  nexus_start.py broker/okx.py broker/okx_stream.py webui_start.py webui_setup.py webui/app.py
  webui_network_setup.py webui_open.py broker_diagnostics.py Pi_WebUI_Aktivieren.sh Pi_Service_Unit_Pruefen.sh
  tests/test_v821_service_guard.py tests/test_v821_favorites_routing.py
  decision_snapshot.py tests/test_v830_audit_chain.py
  etoro_reconciliation.py core_volume_20.py tests/test_v831_etoro_reconciliation.py tests/test_v831_core_volume_20.py
  crypto_strategy_mode.py freqtrade_sample_strategy.py freqtrade_sample_backtest.py tests/test_v900_freqtrade_sample.py
  TradingBot_GUI.desktop.template TradingBot_Starten.desktop.template
  TradingBot_Stoppen.desktop.template TradingBot_Status.desktop.template TradingBot_WebUI.desktop.template
  README_V8_1_2_NEXUS_DE.md INSTALLATIONSANLEITUNG_NEXUS_8.1.2_DE.md INSTALLATIONSANLEITUNG_NEXUS_8.2_DE.md
  CHANGELOG_v8.2_NEXUS.txt MASTER_NOTIZ_NEXUS_8.2.txt
  CHANGELOG_v8.2.1_NEXUS.txt MASTER_NOTIZ_NEXUS_8.2.1.txt INSTALLATIONSANLEITUNG_NEXUS_8.2.1_DE.md
  CHANGELOG_v8.2.2_NEXUS.txt MASTER_NOTIZ_NEXUS_8.2.2.txt INSTALLATIONSANLEITUNG_NEXUS_8.2.2_DE.md
  CHANGELOG_v8.3.0_NEXUS.txt TEST_REPORT_v8.3.0_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_8.3.0_DE.md
  CHANGELOG_v8.3.1_NEXUS.txt TEST_REPORT_v8.3.1_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_8.3.1_DE.md
  CHANGELOG_v9.0_NEXUS.txt TEST_REPORT_v9.0_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_9.0_DE.md
  FREQTRADE_MODUS_NEXUS_9.0_DE.md RISIKOPRUEFUNG_NEXUS_9.0_DE.md
  CHANGELOG_v9.0.4_NEXUS.txt TEST_REPORT_v9.0.4_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_9.0.4_DE.md
  KORREKTUREN_NEXUS_9.0.4_DE.md tests/test_v904_critical_regressions.py
  CHANGELOG_v9.0.5_NEXUS.txt TEST_REPORT_v9.0.5_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_9.0.5_DE.md
  KORREKTUREN_NEXUS_9.0.5_DE.md tests/test_v905_okx_connection_resilience.py
  CHANGELOG_v9.0.6_NEXUS.txt TEST_REPORT_v9.0.6_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_9.0.6_DE.md
  KORREKTUREN_NEXUS_9.0.6_DE.md
  CHANGELOG_v9.0.7_NEXUS.txt TEST_REPORT_v9.0.7_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_9.0.7_DE.md
  KORREKTUREN_NEXUS_9.0.7_DE.md tests/test_v907_api_reconciliation_currency.py
  CHANGELOG_v9.0.8_NEXUS.txt TEST_REPORT_v9.0.8_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_9.0.8_DE.md
  KORREKTUREN_NEXUS_9.0.8_DE.md tests/test_v908_etoro_scanner_resilience.py
  CHANGELOG_v9.0.9_NEXUS.txt TEST_REPORT_v9.0.9_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_9.0.9_DE.md
  KORREKTUREN_NEXUS_9.0.9_DE.md tests/test_v909_broker_truth_universe_logbook.py
  CHANGELOG_v8.1.1_NEXUS.txt ARCHITEKTUR_V8_NEXUS_DE.md TEST_REPORT_v8.1.1_NEXUS.txt
  WIREGUARD_VPN_EINRICHTUNG_DE.md WireGuard_Status_Pruefen.sh
  webui/templates/login.html webui/templates/dashboard.html webui/templates/settings.html webui/templates/logbook.html
  webui/static/app.css webui/static/common.js webui/static/dashboard.js webui/static/settings.js webui/static/logbook.js
  CHANGELOG_v9.5_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_9.5_DE.md TEST_REPORT_v9.5_NEXUS.txt
  tests/test_v950_critical_release.py state_lock.py persistent_daily_budget.py broker_exit_journal.py
  crypto_analysis.py run_crypto_backtest.py run_crypto_walkforward.py
  CHANGELOG_v9.5.1_NEXUS.txt INSTALLATIONSANLEITUNG_NEXUS_9.5.1_DE.md TEST_REPORT_v9.5.1_NEXUS.txt
  tests/test_v951_etoro_rootfix.py tests/test_v951_etoro_price_cap.py
  tests/test_v951_ownership_domain.py tests/test_v951_etoro_closed_migration.py
  tests/test_v951_trade_ledger_exact_link.py tests/test_v951_actual_upgrade_replay.py
  tests/test_v951_etoro_close_broker_hardening.py tests/test_v951_fill_commit_pipeline.py
  tests/test_v951_post_buy_accounting_race.py tests/test_v951_trade_ledger_sell_replay.py
)
for required in "${REQUIRED_RELEASE_FILES[@]}"; do
  if [[ ! -f "$ROOT/$required" ]]; then
    echo "FEHLER: Release unvollstaendig - Datei fehlt: $required"
    exit 78
  fi
done

# Bookworm und neuer verwalten System-Python als extern; daher immer venv.
# Nicht nur `venv --help` pruefen: auf Debian kann das vorhanden sein, obwohl
# ensurepip fuer das tatsaechliche Erzeugen einer Umgebung fehlt.
NEED_VENV=0
CHECKDIR="$(mktemp -d)"
if ! python3 -m venv "$CHECKDIR/check" >/dev/null 2>&1; then NEED_VENV=1; fi
rm -rf "$CHECKDIR"
NEED_TK=0
if ! python3 -c 'import tkinter' >/dev/null 2>&1; then NEED_TK=1; fi
if [[ "$NEED_VENV" == "1" || "$NEED_TK" == "1" ]]; then
  echo "Installiere benoetigte Raspberry-Pi-OS-Basispakete ..."
  sudo apt-get update
  sudo apt-get install -y python3-venv python3-tk
fi

if [[ -d .venv && ! -x .venv/bin/python ]]; then
  echo "Unvollstaendige .venv erkannt – wird sauber neu erzeugt."
  rm -rf .venv
fi
if [[ ! -d .venv ]]; then
  echo "Erzeuge virtuelle Python-Umgebung ..."
  python3 -m venv .venv
fi

PY="$ROOT/.venv/bin/python"
PIP="$ROOT/.venv/bin/pip"

echo "Aktualisiere pip/wheel ..."
"$PY" -m pip install --upgrade --no-cache-dir pip setuptools wheel

echo "Installiere exakt getestete Python-Abhaengigkeiten ..."
# Auf dem Pi niemals stundenlang NumPy/Pandas/scikit-learn lokal kompilieren.
# Fehlt ein ARM64-Wheel, bricht das Setup sauber ab statt den Pi zu ueberlasten.
"$PIP" install --only-binary=:all: --no-cache-dir -r requirements-lock.txt

echo "Installiere Test-Abhaengigkeiten fuer den verpflichtenden Volltest ..."
"$PIP" install --only-binary=:all: --no-cache-dir -c requirements-lock.txt -r requirements-test.txt

echo "Pruefe installierte Abhaengigkeiten und echten WebUI-TestClient ..."
"$PY" -m pip check
"$PY" check_test_dependencies.py

# Linux-Secrets: nur fuer den aktuellen Benutzer lesbar.
# Wichtig: die Existenz jeder Datei einzeln pruefen. 'openai_ai_settings.json'
# ist kein Glob, deshalb greift nullglob dort nicht -- ohne die Pruefung
# meldete chmod bei einer Neuinstallation "Datei nicht gefunden".
shopt -s nullglob
for f in *_credentials.json openai_ai_settings.json ai_router_settings.json web_ui_settings.json; do
  [[ -e "$f" ]] || continue
  chmod 600 "$f" || true
done
shopt -u nullglob
chmod 700 "$ROOT" || true
chmod +x Pi_*.sh || true
chmod +x Nexus_*.sh 2>/dev/null || true
chmod +x WebUI_Starten.sh 2>/dev/null || true

echo
echo "Pi-Preflight ..."
TRADINGBOT_PI_MODE=1 TRADINGBOT_PI_TARGET=pi5-8gb "$PY" pi_preflight.py --service

echo
echo "Offline-Volltest ..."
# Wiederholbare Installation: Nach einem Update oder Teilabbruch duerfen im
# Arbeitsordner bereits WebUI-Zugang, Einstellungen und Laufzeitdaten liegen.
# Der normale Volltest bleibt ausserhalb des Installers weiterhin strikt und
# verweigert solche Dateien in einem auszuliefernden ZIP.
if ! TRADINGBOT_PI_MODE=1 TRADINGBOT_PI_TARGET=pi5-8gb TRADINGBOT_ALLOW_LOCAL_STATE=1 "$PY" volltest.py; then
  echo
  echo "===================================================================="
  echo "INSTALLATION ABGEBROCHEN: Der Volltest war nicht erfolgreich."
  echo "===================================================================="
  echo "Die systemd-Dienste und Desktop-Starter wurden NICHT auf diesen Ordner umgestellt."
  echo "Bitte keine vorhandenen TradingBot-Starter verwenden; sie koennen noch auf eine alte Version zeigen."
  echo "Erst den Testfehler beheben und danach ./Pi_Installieren.sh erneut ausfuehren."
  exit 1
fi

# Erst NACH dem strikten Quellcode-/Regressionstest eine lokale WebUI-Datei
# anlegen. Die sichere Adresse wird beibehalten; WireGuard, Keys, Peers und wg0
# werden vom TradingBot-Installer niemals angelegt, ersetzt oder neu gestartet.
echo
echo "WebUI-Netzmodus erkennen (WireGuard bleibt unveraendert) ..."
"$PY" webui_network_setup.py --mode "${NEXUS_WEBUI_ACCESS_MODE:-auto}"

# systemd-Datei fuer genau diesen Benutzer/Pfad erzeugen.
SERVICE_NAME="tradingbot-pi5.service"
SERVICE_TMP="$(mktemp)"
WEB_SERVICE_NAME="tradingbot-webui.service"
WEB_SERVICE_TMP="$(mktemp)"
USER_NAME="$(id -un)"
GROUP_NAME="$(id -gn "$USER_NAME")"
VERSION_RAW="$(tr -d '[:space:]' < VERSION.txt)"
VERSION_DISPLAY="${VERSION_RAW%-NEXUS}"
if [[ -z "$VERSION_DISPLAY" ]]; then
  echo "FEHLER: VERSION.txt ist leer."
  rm -f "$SERVICE_TMP" "$WEB_SERVICE_TMP"
  exit 78
fi
# systemd ExecStart-Pfade sind absichtlich einfach gehalten. Leerzeichen oder
# Shell-Sonderpfade lehnen wir ab, statt eine schwer diagnostizierbare Unit zu
# installieren. Der mitgelieferte Release-Ordnername ist kompatibel.
case "$ROOT" in
  *[[:space:]]*|*'"'*|*'\'*) echo "FEHLER: Bot-Pfad darf keine Leerzeichen, Anfuehrungszeichen oder Backslashes enthalten: $ROOT"; rm -f "$SERVICE_TMP"; exit 78 ;;
esac
BOT_ROOT="$ROOT" BOT_USER="$USER_NAME" BOT_GROUP="$GROUP_NAME" BOT_VERSION="$VERSION_DISPLAY" "$PY" - "$SERVICE_TMP" "$WEB_SERVICE_TMP" <<'PYRENDER'
import os, sys
from pathlib import Path
for source, destination in (
    ('tradingbot-pi5.service.template', sys.argv[1]),
    ('tradingbot-webui.service.template', sys.argv[2]),
):
    template = Path(source).read_text(encoding='utf-8')
    template = template.replace('__USER__', os.environ['BOT_USER'])
    template = template.replace('__GROUP__', os.environ['BOT_GROUP'])
    template = template.replace('__BOTDIR__', os.environ['BOT_ROOT'])
    template = template.replace('__VERSION__', os.environ['BOT_VERSION'])
    Path(destination).write_text(template, encoding='utf-8')
PYRENDER

# Ein eventuell noch laufender Dienst einer aelteren TradingBot-Version wird
# vor dem Umschalten auf die neue Unit gestoppt und deaktiviert. So kann nach
# erfolgreicher Installation zuerst die alte Konfiguration in Ruhe ueber die
# GUI uebernommen und kontrolliert werden.
echo "Stoppe/deaktiviere einen eventuell vorhandenen TradingBot-Dienst ..."
sudo systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
sudo systemctl disable --now "$WEB_SERVICE_NAME" >/dev/null 2>&1 || true
# Manuell nachgeruestete 8.0-Unit aus der Ersteinrichtung darf nicht parallel
# auf Port 8780 laufen. Nur die Unit wird abgeloest; WireGuard bleibt unberuehrt.
sudo systemctl disable --now nexus-webui.service >/dev/null 2>&1 || true

sudo install -m 0644 "$SERVICE_TMP" "/etc/systemd/system/$SERVICE_NAME"
sudo install -m 0644 "$WEB_SERVICE_TMP" "/etc/systemd/system/$WEB_SERVICE_NAME"
rm -f "$SERVICE_TMP" "$WEB_SERVICE_TMP"
sudo systemctl daemon-reload
./Pi_Service_Unit_Pruefen.sh all
# WICHTIG: Der neue Dienst bleibt absichtlich DEAKTIVIERT und GESTOPPT.
# Erst nach Einstellungsimport und manueller Kontrolle soll
# ./Pi_Service_Aktivieren.sh ausgefuehrt werden.
sudo systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
sudo systemctl disable "$WEB_SERVICE_NAME" >/dev/null 2>&1 || true

# Journal auf dem Pi begrenzen. Das ist eine globale journald-Obergrenze,
# verhindert aber gerade auf SD-Karten unbegrenztes Wachstum.
sudo mkdir -p /etc/systemd/journald.conf.d
printf '%s\n' '[Journal]' 'SystemMaxUse=250M' 'RuntimeMaxUse=100M' | sudo tee /etc/systemd/journald.conf.d/tradingbot-pi5.conf >/dev/null
sudo systemctl restart systemd-journald || true

# Desktop-Starter ohne Terminal. Der Benutzer erhaelt ausschliesslich fuer
# Start/Stop/Restart dieses einen Dienstes passwortfreies systemctl-Recht.
SUDOERS_TMP="$(mktemp)"
SYSTEMCTL_BIN="$(command -v systemctl)"
if [[ -z "$SYSTEMCTL_BIN" ]]; then
  echo "FEHLER: systemctl wurde nicht gefunden."
  rm -f "$SUDOERS_TMP"
  exit 78
fi
printf '%s\n' "$USER_NAME ALL=(root) NOPASSWD: $SYSTEMCTL_BIN start tradingbot-pi5.service, $SYSTEMCTL_BIN stop tradingbot-pi5.service, $SYSTEMCTL_BIN restart tradingbot-pi5.service, $SYSTEMCTL_BIN start tradingbot-webui.service, $SYSTEMCTL_BIN stop tradingbot-webui.service, $SYSTEMCTL_BIN restart tradingbot-webui.service" > "$SUDOERS_TMP"
sudo install -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/tradingbot-pi5
rm -f "$SUDOERS_TMP"
if command -v visudo >/dev/null 2>&1; then sudo visudo -cf /etc/sudoers.d/tradingbot-pi5 >/dev/null; fi

DESKTOP_DIR=""
if command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
if [[ -z "$DESKTOP_DIR" ]]; then
  if [[ -d "$HOME/Schreibtisch" ]]; then DESKTOP_DIR="$HOME/Schreibtisch"; else DESKTOP_DIR="$HOME/Desktop"; fi
fi
mkdir -p "$DESKTOP_DIR"
for item in TradingBot_GUI TradingBot_WebUI TradingBot_Starten TradingBot_Stoppen TradingBot_Status; do
  src="$ROOT/${item}.desktop.template"
  dst="$DESKTOP_DIR/${item}.desktop"
  BOT_ROOT="$ROOT" "$PY" - "$src" "$dst" <<'PYDESKTOP'
import os,sys
from pathlib import Path
src,dst=map(Path,sys.argv[1:3])
text=src.read_text(encoding='utf-8').replace('__BOTDIR__',os.environ['BOT_ROOT'])
dst.write_text(text,encoding='utf-8')
PYDESKTOP
  chmod +x "$dst"
  if command -v gio >/dev/null 2>&1; then gio set "$dst" metadata::trusted true >/dev/null 2>&1 || true; fi
done

# Kein automatischer Start und kein Autostart an dieser Stelle. Das ist
# absichtlich ein Human-Gate fuer Updates: erst GUI oeffnen, Einstellungen
# aus der alten Version uebernehmen/kontrollieren und danach den Dienst
# bewusst aktivieren.

echo
echo "===================================================================="
echo "INSTALLATION UND TESTS ERFOLGREICH - BOT NOCH NICHT GESTARTET"
echo "===================================================================="
echo "Der systemd-Dienst wurde installiert, ist aber DEAKTIVIERT und GESTOPPT."
echo "Desktop-Starter fuer WebUI, Tkinter-Fallback und Dienst wurden unter $DESKTOP_DIR angelegt."
echo
echo "Naechste Schritte fuer ein Update:"
echo "  1. Anleitung INSTALLATIONSANLEITUNG_NEXUS_9.7.4_DE.md beachten."
echo "     Bereits vorab migriert? Dann keine zweite automatische Uebernahme."
echo "     Sonst nur aus explizitem, gestopptem Quellordner: settings_migration.py --strict /pfad/zur/quelle"
echo "  2. WebUI-Zugang einrichten:     ./.venv/bin/python webui_setup.py"
echo "     WebUI rebootfest aktivieren: ./Pi_WebUI_Aktivieren.sh"
echo "     Grafische Einrichtung:       ./Pi_GUI_Starten.sh"
echo "  3. In der WebUI eToro, OKX, Telegram, Risikoprofil und KI kontrollieren."
echo "  4. In WebUI oder lokal OKX, MASSIVE und OpenAI eintragen:"
echo "                                 ./Nexus_Einrichten.sh"
echo "     Verbindungen pruefen:       ./Nexus_Einrichten.sh --test"
echo "     Kein --einmal als Verbindungstest: ein Zyklus kann Orders senden."
echo "  5. Erst danach Trading-Core + WebUI bewusst aktivieren:"
echo "                                 ./Pi_Service_Aktivieren.sh"
echo
echo "Bis Schritt 5 handelt der neue Bot NICHT und startet auch nach einem Reboot NICHT automatisch."
echo "Status:                          ./Pi_Service_Status.sh"
echo "Not-Aus:                         ./Pi_Neue_Kaeufe_Pausieren.sh"
