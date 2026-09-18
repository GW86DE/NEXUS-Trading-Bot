# NEXUS 9.7.5 – ein Aufruf für das gesicherte Demo-Update

Stand: 08.09.2026. Basis: 9.7.4 **einschließlich Testisolation FIX1**.
Diese Version korrigiert die belegten lokalen SUI-/Orderfehler. Sie garantiert
nicht, dass OKX jede preisbegrenzte FOK-Order ausführt. Ein bestätigter No-Fill
ist weiterhin kein Verkauf und erzeugt keinen Gewinn.

## Empfohlener Weg: selbstenthaltender Installer

Die Datei **`NEXUS_9.7.5_Installieren.sh`** enthält bereits die gesamte Release-ZIP.
Sie unter `/home/georg/Georg` speichern und **als georg, nicht als root** ausführen:

```bash
bash "$HOME/Georg/NEXUS_9.7.5_Installieren.sh"
```

Kein separates Entpacken, keine erneute manuelle Migration, kein separater
WebUI-/Core-Start, keine ausgelassenen Tests. Nicht gleichzeitig noch
`Pi_Installieren.sh`, ältere Starter oder eine zweite Core-Instanz ausführen.
Der alte 9.7.4-FIX1 bleibt als Quelle erhalten. Den alten Ordner nicht löschen.

Voraussetzungen: bestehende gemeinsame Core-/WebUI-Dienste auf dem Pi 5,
64-Bit Raspberry Pi OS/ARM64, Python ab 3.11, funktionierender Paketindex und
sudo-Berechtigung. Der Quellstand muss ausdrücklich eToro Paper und OKX Demo
konfiguriert haben. LIVE oder unbekannte Modi werden **vor dem Stoppen abgelehnt**,
nicht still umgeschaltet. Diese Automatik ist ein Upgrade, kein Erstinstallations-
assistent für einen Pi ohne konfigurierte Dienste.

Der Installer kann ein sudo-Passwort benötigen. Nur falls kein migrierter
WebUI-Zugang vorhanden ist, werden Benutzername/Passwort interaktiv abgefragt.
Vorhandene Anmeldedaten werden nicht unnötig ersetzt. Die neuen Laufzeitpakete
werden in der **eigenen** `.venv` installiert; die alte `.venv` bleibt erhalten.

## Was der Aufruf automatisch macht

1. Eingebettete ZIP-Prüfsumme und jeden manifestierten Quellhash prüfen. In den
   eigenen Ordner `TradingBot_v9.7.5_NEXUS` entpacken. Bestehenden Quellcode nicht
   überschreiben; bei bereits identischem Stand nur fortsetzen.
2. Tatsächliche WorkingDirectory-/ExecStart-Pfade beider bestehenden Dienste
   abgleichen. Nicht anhand der höchsten Versionsnummer nach Handelsdaten suchen.
3. Eigene Python-Umgebung erstellen, echte Release-Pins und Testpakete installieren,
   `pip check`, reale Runtime-Imports, TestClient, Volltest und Pi-Preflight ausführen.
   Fehlt Node, wird es für die JavaScriptprüfungen installiert. Es erfolgt kein
   Betriebssystem-Upgrade. Bis zum bestandenen Vorabtest läuft der bisherige
   Dienstzustand weiter.
4. Beide bisherigen Dienste stoppen, nach zusätzlichen Botprozessen suchen und
   einen privaten vollständigen Zustandssicherungsordner erstellen. SQLite-WAL-
   Dateien bleiben erhalten. Kopieren über die vorhandene SQLite-Backup-Schnittstelle.
5. Zusammengehörigen Zustand strikt in einen Zwischenordner migrieren. Die
   Order-Reparatur verwendet exakte Ledger-/Fillbelege, erzeugt keine Brokerorder
   und verändert keine finanziellen Tradezeilen. Unbelegte Alt-Datensätze bleiben
   erhalten. Der Quellordner wird nicht reparierend beschrieben.
6. Zustand in das freie Ziel übernehmen und **nochmals** den vollständigen,
   isolierten Volltest ausführen. Persönliche Zugangsdaten/Profile werden nicht
   zu Regressionstest-Eingaben. Die individuelle Konfiguration wird anschließend
   getrennt geprüft.
7. Dienstdateien und Desktop-Starter auf 9.7.5 umstellen, vorhandenen WebUI-Zugang
   verwenden, WebUI starten und die neue Versionsseite lokal prüfen. Danach den
   Demo-Core starten. Ab diesem Schritt können **Demo-Orders** entstehen.
8. Bis zu sechs Minuten auf frische Heartbeats/Verbindungen jedes aktivierten
   Brokers warten. Ein gesunder OKX-Worker genügt nicht für eine tote eToro-Domäne.
   Fachliche Kauf-/Buchungssperren werden nicht zum Erreichen eines grünen Starts
   gelöscht. Die WebUI-Adresse wird am Ende ausgegeben.

Eine fehlende / veraltete Statusdatei beweist keine aktive Brokerverbindung.
Eine bestandene Startkontrolle ist keine Garantie, dass jede zukünftige
Brokerabfrage oder Ausführung gelingt, und keine Echtgeldfreigabe.

## Sicherung und Protokoll

- Sicherung: `~/Georg/NEXUS_Sicherung_<UTC-Zeit>/`
- Zusätzlicher Reparaturbeleg: `~/Georg/nexus_repair_backup_<UTC-Zeit>/`
- Installerprotokoll: `~/Georg/TradingBot_v9.7.5_NEXUS/nexus_update.log`
- Phasenjournal: `nexus_update_state.json` im Zielordner
- Belegte Datenreparatur: `order_repair_report.json` im Zielordner

Sicherungen enthalten möglicherweise Zugangsdaten; nicht öffentlich teilen.
Alte Risiko-/Kontowerte werden nicht zurückgesetzt, Warnungslisten nicht pauschal
geleert. Die Reparatur ist idempotent. Ein bereits erfolgreiches Update wird bei
einem erneuten Aufruf nicht nochmals migriert oder neu gestartet.

## Bei einem Fehler

**Nicht auf eigene Faust Dateien löschen oder Tests überspringen.**
Vor dem ersten Start neuer Dienste kann der Installer seine eigenen Änderungen
zurücknehmen und vorher aktive alte Dienste wieder starten. Die Quelle bleibt
unverändert. Eine unvollständige / abgebrochene Migration wird nicht blind erneut
in belegte Zielzustände kopiert.

**Nach dem ersten Start eines neuen Dienstes gibt es keinen automatischen Rückfall
auf alte Handelsdaten.** Es könnten inzwischen neue Kommandos oder Fills vorliegen.
Bei einer dann fehlgeschlagenen Kontrolle werden die neuen Dienste gestoppt und
ihr Autostart deaktiviert. Die neuesten Daten, das Phasenjournal und Sicherungen
bleiben erhalten. Diese Situation braucht einen kontrollierten Abgleich.

Beim Stoppen laufen lokale Client-Stops nicht. Vor dem Update offene Positionen
und tatsächlich aktive Schutzorders direkt beim Broker kontrollieren. Bestehende
Live-Positionen werden durch einen Demo-Bot nicht verwaltet.

## Alternative: bereits separat entpackte ZIP

Wer nur `TradingBot_v9.7.5_NEXUS.zip` verwendet, entpackt sie in einen neuen
Ordner und startet anschließend genau denselben Upgrade-Ablauf mit:

```bash
bash "$HOME/Georg/TradingBot_v9.7.5_NEXUS/Nexus_Update.sh"
```

Die selbstenthaltende Datei und die normale ZIP sind **derselbe Build**.
Nicht zwei verschiedene Installationen starten. Das technische `--plan` am
`Nexus_Update.sh` zeigt Quelle/Ziel, führt aber keine Installation durch.

## Prüfgrenzen des mitgelieferten Builds

Vollständige Offline-Suite und neue Verhaltensprüfungen, echte lokale SQLite-/JSON-
Persistenz, synthetische Brokerantworten, Installer-Störfälle und Browser-
Komponenten wurden in der Buildumgebung geprüft. Die komplette Prüfung wird aus
frisch entpackter ZIP wiederholt; konkrete Ergebnisse stehen im separaten
Paketprüfbericht. Kein realer Broker-POST, kein ARM64-/systemd-Dauerlauf hier.
Der Pi-Installer prüft die echten Release-Pins und Imports selbst; er ersetzt
fehlende Pakete nicht durch Attrappen. Keine umfassende Freqtrade-Neuentwicklung:
Strategien bleiben, Order-Identitäten und Wiederholungsverhalten werden gehärtet.
