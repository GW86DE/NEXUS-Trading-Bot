# NEXUS 9.7.4 – fehlende Testabhängigkeit korrigieren

Ausgangspunkt ist die ausgelieferte **9.7.3**. Diese Wartung ergänzt `httpx==0.28.1`
für die verpflichtenden FastAPI-/Starlette-Tests und prüft die Testumgebung vorab.
Die Kauf-/Verkaufslogik, das Ledger und sämtliche WebUI-Dateien sind unverändert.
Die Versionsanzeige kommt weiterhin aus `config.VERSION_NEXUS`.

**Hier zunächst nur die isolierte Prüfung ausführen.** Sie installiert Pakete in
 einer neuen lokalen `.venv`, prüft den neuen Code und schreibt ein Testprotokoll.
Sie schaltet keine Dienste um, startet keinen Trading-Core und migriert keine
Handelsdaten. Die bisherige Installation und deren `.venv` bleiben unverändert.

## 1. ZIP separat entpacken

`TradingBot_v9.7.4_NEXUS.zip` unter `/home/georg/Georg` speichern. Als Benutzer
`georg`, nicht mit `sudo`, in einem Bash-Terminal ausführen. Die Klammer umfasst
alle Schritte; bei einem Fehler wird innerhalb dieses Blocks abgebrochen:

```bash
(
  set -euo pipefail
  cd "$HOME/Georg"
  if [ -e TradingBot_v9.7.4_NEXUS ]; then
    echo 'STOPP: Ziel existiert bereits. Nicht darüber entpacken.'
    exit 1
  fi
  unzip TradingBot_v9.7.4_NEXUS.zip
  cd TradingBot_v9.7.4_NEXUS
  cat VERSION.txt
)
```

Erwartet: `9.7.4-NEXUS`. Nur in einem frisch entpackten, unveränderten Quellordner
weiterarbeiten. Vorhandene Datenordner nicht löschen, um den Test zu erzwingen.

## 2. Eigene Testumgebung erstellen und ALLE Pflichtpakete installieren

**Nicht mehr die alte 9.7.1-Umgebung ungeprüft wiederverwenden.** Die neue
`requirements-test.txt` enthält `pytest==9.0.2` und `httpx==0.28.1`.
Die zehn direkten Laufzeit-Pins in `requirements-lock.txt` bleiben unverändert.
Transitive Abhängigkeiten werden von pip anhand dieser Vorgaben aufgelöst;
die Datei ist kein vollständiger, hashgebundener Lock aller transitiven Pakete.

```bash
(
  set -euo pipefail
  cd "$HOME/Georg/TradingBot_v9.7.4_NEXUS"
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --only-binary=:all: \
    -r requirements-lock.txt -r requirements-test.txt
  ./.venv/bin/python -m pip check
  ./.venv/bin/python check_test_dependencies.py
)
```

Bei Paket-/Downloadfehlern nicht zum nächsten Schritt wechseln. Die Installation
hat dann keine Testfreigabe. Kein `sudo pip`, keine Dummybibliothek, kein Entfernen
des WebUI-Authentifizierungstests und kein Überspringen mittels `-k`.
`--only-binary=:all:` vermeidet eine langwierige lokale Kompilierung auf dem Pi.

Erwartet: `No broken requirements found` und `TESTABHAENGIGKEITEN OK`.
Der Helfer zeigt den tatsächlich verwendeten Interpreter und Paketversionen an.
Er ruft einen echten TestClient lokal im Prozess auf, ohne Brokerverbindungen.

## 3. Vollständigen Test mit sichtbarem Rückgabestatus ausführen

```bash
(
  set -euo pipefail
  cd "$HOME/Georg/TradingBot_v9.7.4_NEXUS"
  ./.venv/bin/python volltest.py 2>&1 | tee "$HOME/nexus_9.7.4_volltest.log"
)
```

Erwartet werden **OK in jeder Prüfgruppe** und abschließend **VOLLTEST OK**.
`pipefail` verhindert, dass das erfolgreiche Schreiben der Logdatei einen
fehlgeschlagenen Test verdeckt. Die Prüfung nennt nun auch Skip-Gründe.
Die Anzahl übersprungener GUI-/Plattformtests kann von der Buildumgebung abweichen;
übersprungen bedeutet nicht bestanden.

Das Originalproblem war der Test
`tests/test_v972_webui_contexts.py::test_read_endpoints_require_auth`.
Dieser Test bleibt unverändert aktiv und muss jetzt seine echten
Authentifizierungsprüfungen ausführen, statt schon beim HTTPX-Import zu scheitern.

## 4. Noch keine automatische Dienstumstellung

Die drei Schritte oben genügen zur Prüfung der Fehlerkorrektur. Danach nicht
nebenbei `Pi_Installieren.sh`, `Pi_Service_Aktivieren.sh` oder einen zusätzlichen
Core starten. `Pi_Installieren.sh` ist KEINE unabhängige Testinstallation: es kann
nach erfolgreichem Volltest die gemeinsamen systemd-Units umstellen.

Die Betriebsumstellung ist ein eigener Schritt: tatsächlichen alten Quellordner
mit `systemctl show` bestimmen, alle Writer stoppen, den vollständigen
zusammengehörigen Zustand sichern und danach kontrolliert migrieren. Nicht allein
wegen einer höheren Versionsnummer Daten aus irgendeiner früheren Kopie übernehmen.
Dieser Wartungsstand ändert das Schema oder den Migrationsweg nicht.

Bei der späteren normalen Installation installiert `Pi_Installieren.sh` HTTPX
bereits automatisch, prüft `pip check`, den TestClient und den vollständigen
Volltest, bevor es Dienste umstellt. Es gibt keinen erlaubten Test-Bypass.

## Grenzen und Sicherheit

Der Paketprüfbericht nennt die tatsächliche Buildumgebung. Die Neuinstallation
aller gepinnten Laufzeitpakete konnte dort wegen nicht erreichbarer Paketquellen
nicht ausgeführt werden. Die Funktionsprüfung verwendet echte vorhandene Pakete
in einer getrennten Umgebung; deren Versionen sind ausdrücklich dokumentiert.
Das ist kein Nachweis der ARM64-Installation. Deshalb ist Schritt 2 auf dem Pi
mit den tatsächlichen Release-Pins erforderlich.

Keine Datenbank, keinen Filltracker, keine Broker-Registry und keine Warnungsliste
löschen. Offene Positionen/Schutzorders bei einer bestehenden Störung direkt beim
Broker kontrollieren. Ein gestoppter Core führt keine Client-Stops aus. Nie zwei
Trading-Cores auf dasselbe Konto starten. Keine Echtgeldfreigabe allein aufgrund
dieses Wartungs- oder Offline-Testergebnisses.
