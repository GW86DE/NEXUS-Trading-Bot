# NEXUS 10.1.0

Gezielte Korrekturen aus Diagnose 1.0.1 vom 13.09.2026. Die NEXUS-Optik, Seiten und Handelsstrategien bleiben erhalten.

## Installation auf deinem Raspberry Pi 5

Die Datei **NEXUS_10.1.0_Installieren.sh** ist eigenständig. Speichere sie im Downloadordner und starte als normaler Benutzer (ohne sudo):

```bash
bash ~/Downloads/NEXUS_10.1.0_Installieren.sh
```

Der Installer erkennt die bisherige, von beiden NEXUS-Diensten verwendete Installation, sichert den Zustand, übernimmt die Daten in einen neuen Versionsordner, testet und startet die neue Demo/Paper-Version. Der alte Releaseordner wird nicht überschrieben. Bei mehreren/unpassenden Dienstpfaden bricht die Vorprüfung mit dem konkreten Grund ab. LIVE wird vom vorhandenen Updater abgewiesen; dieser Build hat keine Freigabe für unbeaufsichtigten Echtgeldhandel.

Vorab nur das Paket prüfen:

```bash
bash ~/Downloads/NEXUS_10.1.0_Installieren.sh --paket-pruefen
```

Nur den Updateplan prüfen, ohne Dienststart:

```bash
bash ~/Downloads/NEXUS_10.1.0_Installieren.sh --plan
```

Wenn FMP Starter bereits eingestellt ist, bleibt es erhalten. Nur falls dein tatsächlich gebuchter Starter-Tarif noch nicht ausgewählt ist, ist beim Installieren die vorhandene Option `--fmp-starter` verfügbar. Es werden keine neuen API-Zugangsdaten benötigt.

## Danach 30 Minuten beobachten

Lade **NEXUS_10_Diagnose_1.1.0_Starten.sh** herunter und starte:

```bash
bash ~/Downloads/NEXUS_10_Diagnose_1.1.0_Starten.sh
```

Sie beobachtet ab Start 30 Minuten passiv. Es werden keine zusätzlichen Broker-, Massive-, FMP-, GPT- oder Telegram-Anfragen ausgelöst. Am Ende zeigt sie den vollständigen Pfad zur ZIP mit Datum, Uhrzeit und Zufallskennung. Diese ZIP anschließend hier hochladen. Das Werkzeug liegt auch als `NEXUS_Diagnose_Starten.sh` im neuen NEXUS-Ordner.

Sofortexport vorhandener Belege: gleiche Datei mit `--sofort`. Das ersetzt keinen Verlaufstest. Ein Abbruch erzeugt soweit möglich einen ausdrücklich unvollständigen Teilbericht. Der Toolname 1.1.0 ist die Diagnoseversion; die Botversion lautet 10.1.0.

## Was weiterhin Aufmerksamkeit braucht

Die historischen eToro-Risikobasis-/Kontobelege und der genaue PEP-Schutzpreisvertrag fehlen weiterhin. Das Update hebt diese lokalen Sperren nicht durch angenommene Werte auf. Die Oberfläche benennt beide Ursachen genauer. Ebenso bleiben unbekannte historische Exitgebühren unbekannt. Details stehen in KNOWN_ISSUES.md und IMPLEMENTATION_REPORT.md.

## Berichte

- CHANGELOG.md: Änderungen gegenüber 10.0.0.
- IMPLEMENTATION_REPORT.md: Maßnahmen, Dateien, Wirkungen und Belege.
- TEST_REPORT.md: Offline-Tests und Grenzen der Validierung.
- MIGRATION_NOTES.md: Datenerhalt, Cacheversionen und Wiederholbarkeit.
- KNOWN_ISSUES.md: offene Punkte und Releaseentscheidung.

Frühere 10.0.0-Berichte liegen unter docs/history/10.0.0. Sie sind keine Testergebnisse für 10.1.0.
