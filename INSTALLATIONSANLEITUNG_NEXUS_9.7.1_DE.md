# NEXUS 9.7.1 – Korrektur der 9.7-Installationsregressionen

Stand: 07.09.2026. Ausgangsbasis ist die ausgelieferte 9.7-ZIP mit SHA-256
`f7e3d196d3aa8f0a3581cbe12148560aae95695463dbc3570c07484f21e79bdc`.

## Prüfstatus vorab

Die 14 auf dem Raspberry Pi gezeigten Fehlschläge wurden reproduziert und in
den betroffenen Testfällen korrigiert. 94 gezielte Prüfungen bestehen,
einschließlich 24 neuer Grenzfalltests. Der vollständige lokale Prüflauf des
Erstellers hat weiterhin 8 Importfehler, weil dort das echte Paket `yfinance`
nicht installiert ist und die Paketquelle nicht erreichbar war. Diese 8 Fehler
sind von den 14 gemeldeten Fehlern getrennt und bestanden schon vor der Änderung.
Die tatsächlichen Protokolle stehen im Prüfbericht. Es gibt daher noch keine
abschließende Freigabe für deinen Pi und keine Echtgeldfreigabe.

## 1. Separat entpacken – noch keinen Dienst umstellen

Speichere `TradingBot_v9.7.1_NEXUS.zip` unter `/home/georg/Georg`.
Verwende ein Terminal als Benutzer `georg`, nicht als root.

```bash
cd ~/Georg
unzip -n TradingBot_v9.7.1_NEXUS.zip
cd TradingBot_v9.7.1_NEXUS
cat VERSION.txt
```

Erwartet: `9.7.1-NEXUS`. Falls dieser Zielordner bereits eine veränderte Kopie
enthält, nicht blind darüber entpacken; zuerst einen separaten sauberen Ordner
verwenden. Nicht die 9.7-ZIP oder den 9.6-Arbeitsstand verwechseln.

## 2. Zunächst die bereits vorhandene Python-Umgebung der 9.7 verwenden

Die fehlgeschlagene 9.7-Installation hat die Python-Umgebung bereits angelegt.
Damit lässt sich die Korrektur prüfen, ohne noch einmal Abhängigkeiten zu laden
und ohne systemd-Dienste oder Desktop-Starter zu ändern.

```bash
cd ~/Georg/TradingBot_v9.7.1_NEXUS
ls -l ../TradingBot_v9.7_NEXUS/.venv/bin/python
../TradingBot_v9.7_NEXUS/.venv/bin/python -c 'import sys, yfinance; print(sys.version); print("yfinance", yfinance.__version__)'
set -o pipefail
../TradingBot_v9.7_NEXUS/.venv/bin/python volltest.py 2>&1 | tee ~/nexus_9.7.1_volltest.log
```

Nur falls der `ls`-Befehl bestätigt, dass diese Python-Datei existiert, den
Prüflauf starten. Fehlt die Datei, nicht durch ein beliebiges System-Python
ersetzen und nicht Tests überspringen. Die exakte verwendete Python-Umgebung
und deren Abhängigkeiten sind für die Abnahme wichtig.

`pipefail` verhindert, dass das erfolgreiche Schreiben der Logdatei einen
fehlgeschlagenen Volltest als Erfolg erscheinen lässt. Erwartet wird am Ende
`VOLLTEST OK` mit `OK` in jeder Prüfgruppe. `FEHLER(1)` ist ein Fehlerstatus,
keine Erlaubnis zum Fortfahren. Die Anzahl übersprungener Plattformtests kann
von der Buildumgebung abweichen; die Skip-Gründe müssen sichtbar bleiben.

## 3. An diesem Punkt noch nicht migrieren oder starten

Die Logdatei liegt unter `/home/georg/nexus_9.7.1_volltest.log` und kann zur
Kontrolle hochgeladen werden. Diese Schritte ändern keine Handelsdaten und
starten keinen zusätzlichen Bot. Keine `fill_progress.json` löschen, keine
SQLite-Datei aus einem beliebigen alten Ordner übernehmen und keine
Sicherheitsprüfung auskommentieren.

`Pi_Installieren.sh` ist kein isolierter Test: nach erfolgreichem Volltest kann
es die gemeinsamen systemd-Units ändern. Deshalb jetzt noch nicht ausführen.
Nach bestandenem Pi-Volltest muss vor einer Umstellung der tatsächlich aktive
Quellordner der bisherigen Dienste ermittelt und der zugehörige zusammengehörige
Zustand gesichert werden. Niemals zwei Cores auf dasselbe Konto starten.

Solange die alte Instanz gestört ist, offene Positionen und vorhandene Schutzorders
direkt im Brokerkonto kontrollieren. Eine erreichbare WebUI ist allein kein
Beleg, dass der Handelsworker läuft. Der Offline-Test ersetzt keine Broker-Demo-
Abnahme und keine Prüfung des tatsächlichen systemd-Lebenszyklus auf dem Pi.
