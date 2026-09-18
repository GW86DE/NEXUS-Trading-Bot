#!/usr/bin/env bash
# Prueft fail-closed, dass systemd wirklich den Ordner dieser Release-Version
# startet. Ohne diese Pruefung kann ein Desktop-Starter nach einem abgebrochenen
# Update unbemerkt wieder eine alte Version aktivieren.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-all}"

case "$MODE" in
  core|webui|all) ;;
  *) echo "Verwendung: $0 [core|webui|all]"; exit 64 ;;
esac

check_unit() {
  local unit="$1"
  local entry="$2"
  local expected="ExecStart=$ROOT/.venv/bin/python $ROOT/$entry"
  local unit_text=""

  unit_text="$(systemctl cat "$unit" 2>/dev/null || true)"
  if [[ -z "$unit_text" ]]; then
    echo "FEHLER: $unit ist nicht installiert. Bitte zuerst ./Pi_Installieren.sh erfolgreich beenden."
    return 78
  fi
  if ! grep -Fqx "$expected" <<<"$unit_text"; then
    echo "FEHLER: $unit zeigt nicht auf diesen Release-Ordner:"
    echo "  erwartet: $expected"
    systemctl show "$unit" --property=ExecStart --no-pager 2>/dev/null || true
    echo "Der Dienst wird aus Sicherheitsgruenden NICHT gestartet."
    echo "Bitte ./Pi_Installieren.sh in diesem Ordner erfolgreich beenden."
    return 78
  fi
  echo "OK: $unit startet diesen Release-Ordner."
}

if [[ "$MODE" == "core" || "$MODE" == "all" ]]; then
  check_unit "tradingbot-pi5.service" "pi_service.py"
fi
if [[ "$MODE" == "webui" || "$MODE" == "all" ]]; then
  check_unit "tradingbot-webui.service" "webui_start.py"
fi
