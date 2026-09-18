#!/usr/bin/env bash
# Lokales Fallback zum Einrichten von v8.2 NEXUS.
#
# Hinweis zur Eingabe: Secret, Passphrase und API-Schluessel werden verdeckt
# eingelesen. Auf dem Bildschirm erscheint waehrend des Tippens NICHTS --
# das ist Absicht. Nach jeder Eingabe bestaetigt das Werkzeug die Zeichenzahl.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "FEHLER: Python-Umgebung fehlt. Bitte zuerst ./Pi_Installieren.sh ausfuehren."
  exit 78
fi

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1

exec "$ROOT/.venv/bin/python" nexus_setup.py "$@"
