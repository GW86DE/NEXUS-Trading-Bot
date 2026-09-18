# NEXUS 10.1.1 auf dem Raspberry Pi installieren

Diese Anleitung gilt für das Update deiner bestehenden NEXUS-Installation auf dem Raspberry Pi 5 mit 8 GB RAM. Voraussetzung sind Raspberry Pi OS in 64 Bit/ARM64, Python ab 3.11, eine vorhandene DEMO-/Paper-Konfiguration und die beiden eingerichteten NEXUS-Dienste.

**Der normale Installer übernimmt Einstellungen und Handelsdaten und startet anschließend WebUI und DEMO-/Paper-Core.** Er erstellt keine Echtgeldfreigabe. Die offene historische eToro-Risikobasis und eine noch nicht bestätigte PEP-Schutzübernahme bleiben gesondert zu prüfen.

## 1. Herunterladen und Paket prüfen

Speichere `NEXUS_10.1.1_Installieren.sh` im Ordner `Downloads` auf deinem Pi. Die Datei enthält das passende Release; ein separat entpacktes ZIP ist für diesen Installationsweg nicht erforderlich.

Öffne ein Terminal als dein normaler Pi-Benutzer:

```bash
cd ~/Downloads
bash NEXUS_10.1.1_Installieren.sh --paket-pruefen
```

Die Paketprüfung kontrolliert das eingebettete Paket und seine Prüfsummen. Sie startet keine Dienste und legt keinen neuen Installationsordner an. Die bestehende Installation kann dabei weiterlaufen.

**Den Installer nicht mit `sudo bash` starten.** Benötigte Administratorrechte fordert er selbst für die entsprechenden Systemschritte an.

## 2. Updateplan ansehen

```bash
cd ~/Downloads
bash NEXUS_10.1.1_Installieren.sh --plan
```

`--plan` entpackt das geprüfte Quellpaket nach `~/Georg/TradingBot_v10.1.1_NEXUS` und prüft Quelle und Ziel. Dabei können Zielverzeichnis und Installationsprotokoll angelegt werden. Es erfolgen noch keine Dienstumstellung, Handelsdatenübernahme oder Dienststarts.

Der Updater ermittelt die bisherige Quelle aus **beiden** Diensten: `tradingbot-pi5.service` für den Core und `tradingbot-webui.service` für die WebUI. Beide müssen auf denselben bisherigen Installationsordner zeigen; `WorkingDirectory` und `ExecStart` müssen zusammenpassen. Bei fehlenden oder widersprüchlichen Dienstpfaden wird abgebrochen. Die Option `--source` kann diese Prüfung nicht umgehen.

Die Dienstpfade kannst du bei Bedarf anzeigen:

```bash
systemctl show tradingbot-pi5.service tradingbot-webui.service --no-pager -p WorkingDirectory -p ExecStart -p ActiveState
```

## 3. Installieren und starten

Wenn Paket- und Planprüfung erfolgreich waren:

```bash
cd ~/Downloads
bash NEXUS_10.1.1_Installieren.sh
```

Lass das Terminal bis zur Abschlussmeldung geöffnet. Der Ablauf ist:

1. Quellpaket, Abhängigkeiten, Offline-Tests und Pi-Voraussetzungen prüfen.
2. Alten Core und alte WebUI kontrolliert stoppen; zusätzliche manuell gestartete Botprozesse erkennen.
3. Bisherigen Zustand und Dienstdateien sichern.
4. Einstellungen, Zugangsdaten und Handelszustände über den vorgesehenen Migrationsweg übernehmen. Der alte Versionsordner wird nicht überschrieben.
5. Beide Dienste auf den neuen Ordner umstellen, die WebUI starten und ihre Version prüfen.
6. DEMO-/Paper-Core starten und frische Verbindungs-/Laufzeitmeldungen der aktivierten Broker prüfen.

Fehlen eingerichtete WebUI-Zugangsdaten, kann der Installer deren Einrichtung im Terminal anfordern. Die normale Installation braucht deshalb ein interaktives Terminal. Eine aktive oder unklare LIVE-Konfiguration wird abgewiesen; Konten werden nicht eigenmächtig auf DEMO umgestellt. FMP-Tarifeinstellungen werden übernommen. Ein bereits korrekt eingestellter Starter-Tarif braucht keine erneute Vorgabe.

Die erwartete Erfolgsmeldung beginnt mit:

```text
UPDATE UND STARTKONTROLLE ERFOLGREICH:
```

Eine weiterhin angezeigte fachliche Kaufsperre kann trotz erfolgreicher Installation richtig sein. Insbesondere wird die alte falsche eToro-Risikobasis durch die Installation nicht gelöscht oder zurückgesetzt.

| Aufruf | Wirkung |
|---|---|
| Ohne Zusatzoption | Paket prüfen, Update durchführen, WebUI und DEMO-/Paper-Core starten und Startkontrolle durchführen |
| `--paket-pruefen` | Nur eingebettetes Paket prüfen; kein neuer Installationsordner und keine Dienstaktion |
| `--plan` | Quellpaket ins Ziel entpacken, Quelle/Ziel prüfen und Plan ausgeben; keine Dienstaktion und keine Handelsdatenmigration |
| `--nur-entpacken` | Geprüften Quellstand ins Ziel entpacken; keine Datenmigration, Dienstumstellung oder Dienststarts |

`--nur-entpacken` ist keine abgeschlossene Installation und ersetzt nicht die Startkontrolle. Für das normale Update dieser Anleitung genügt der eigenständige Installer. `Pi_Installieren.sh` ist ein anderer Ablauf und wird hier nicht zusätzlich ausgeführt.

## 4. Nach dem Update prüfen

```bash
systemctl status tradingbot-pi5.service tradingbot-webui.service --no-pager
systemctl show tradingbot-pi5.service tradingbot-webui.service --no-pager -p WorkingDirectory
```

Beide Dienste sollen nach erfolgreichem Update aktiv sein und auf `~/Georg/TradingBot_v10.1.1_NEXUS` zeigen. Öffne die vom Installer ausgegebene WebUI-Adresse und aktualisiere die Browserseite. Die Oberfläche soll NEXUS 10.1.1 anzeigen.

Prüfe anschließend getrennt:

- eToro: richtige Umgebung/Kontozuordnung, PEP-Position und Schutzstatus, Grund einer etwaigen Kontorisikosperre;
- OKX: vorhandene Positionen und deren Schutz; Ablehnungen wegen volumenloser DEMO-Kerzen bleiben zulässig;
- PULSAR: Aktienkandidaten und nachvollziehbare Bearbeitung; ETFs dürfen keine begrenzten Aktienplätze verbrauchen;
- Logbuch: hervorgehobene verständliche Gründe sowie getrennte technische Details und Ausführungszustände.

## 5. eToro-Kontorisiko lesend abgleichen

```bash
cd ~/Georg/TradingBot_v10.1.1_NEXUS
bash NEXUS_eToro_Risikopruefung.sh
```

Dieser Starter liest standardmäßig das eToro-DEMO-Konto. Er erzeugt eine eindeutig benannte JSON-Datei unter `diagnosen` und zeigt ihren Pfad an. Es werden keine Orders, Schutzänderungen oder Risikorücksetzungen durchgeführt. Bei einer während der Sammlung geänderten Risikodatei oder veralteten Belegen ist ein erneuter Abgleich erforderlich.

**Ein erfolgreicher aktueller Kontoabgleich bestätigt keine historische Tagesbasis.** Die bisherige eToro-Sperre bleibt bestehen, solange die unabhängigen Tages-/Kontobelege fehlen. Die Ausgabe nennt die erforderlichen Belege. Stelle diese JSON-Datei zusammen mit dem neuen Diagnoseexport zur Auswertung bereit. Einzelheiten stehen in [docs/implementation/repair_risk.md](docs/implementation/repair_risk.md).

## 6. PEP-Schutz als neue lokale Planentscheidung übernehmen

Dieser Schritt ist getrennt von der Installation. Die besprochenen Werte **Stop 135,90 USD und Take-Profit 138,52 USD** werden nur übernommen, wenn der frische Brokerzustand für die eindeutig zugeordnete Position genau passt. Der Wartungsweg sendet **keinen PATCH und keine Kauf-/Verkaufsorder**.

Zunächst die lokalen Positionsschlüssel anzeigen:

```bash
cd ~/Georg/TradingBot_v10.1.1_NEXUS
./.venv/bin/python etoro_protection_repair.py
```

Wähle den vollständigen `record_id` der PEP-Botposition. Das ist der ausgegebene interne Positionsschlüssel, nicht automatisch die alleinige eToro-Positionsnummer.

Vor Vorschau und Übernahme beide Dienste stoppen und zusätzlich geöffnete manuelle Botfenster schließen:

```bash
sudo systemctl stop tradingbot-pi5.service tradingbot-webui.service
```

Während dieses Wartungsfensters läuft die lokale Positionsüberwachung nicht. Vorhandene native Broker-Stops werden dadurch nicht gelöscht; ihren Zustand im Broker prüfen.

Die Vorschau erstellen und dabei den Platzhalter durch den vollständigen ausgegebenen Schlüssel ersetzen:

```bash
bash NEXUS_eToro_Schutzplan.sh --read-broker \
  --record-id 'RECORD_ID_AUS_DER_ANZEIGE' --stop 135.90 --take-profit 138.52 \
  --reason 'Vorhandenen PEP-Schutz als neue wirksame Planentscheidung uebernehmen' \
  --output pep_schutzplan.json
```

Die Ausgabedatei darf noch nicht existieren; verwende bei einer späteren neuen Vorschau einen anderen Dateinamen. Prüfe Konto, DEMO/LIVE, Positions-ID, Instrument, Restmenge, ursprünglichen Plan und neue Sollwerte. Nur bei `READY_TO_ADOPT` kann die konkrete Entscheidung innerhalb von 15 Minuten übernommen werden:

```bash
bash NEXUS_eToro_Schutzplan.sh --read-broker --apply \
  --plan pep_schutzplan.json --decision-id 'VOLLSTAENDIGE_DECISION_ID_AUS_DEM_PLAN' \
  --workers-stopped
```

Dabei erfolgt ein weiterer frischer Brokerabgleich. `APPLIED` bedeutet ausschließlich, dass die neue lokale Planentscheidung gesichert gespeichert wurde. Die ursprüngliche Kauf-/Schutzhistorie und die Herkunft der Botposition bleiben erhalten. Ein abweichender Brokerpreis, eine veränderte Menge oder fehlende Belege führen zu `BLOCKED`.

Nach erfolgreicher Übernahme die Dienste wieder starten:

```bash
sudo systemctl start tradingbot-webui.service tradingbot-pi5.service
```

Der laufende Core muss den Schutz anschließend erneut frisch bestätigen. Bis dahin darf `PENDING_CONFIRMATION` beziehungsweise `UNCONFIRMED` angezeigt werden. Auch nach erfolgreicher PEP-Bestätigung kann die separate eToro-Risikobasis neue Käufe weiterhin blockieren.

Bei `BLOCKED` den konkreten Grund auswerten, keine Zustandsdatei löschen und den Stop nicht bloß für eine grüne Anzeige verändern. Das Wartungstool startet angehaltene Dienste nicht automatisch wieder. Ein Wiederstart stellt die bisherige DEMO-Überwachung wieder her, beseitigt aber keine offene Schutz-/Risikosperre. Weitere Verträge: [docs/implementation/repair_pep.md](docs/implementation/repair_pep.md).

## 7. Frische Diagnose erstellen

Starte die mit 10.1.1 ausgelieferte Diagnose nach dem Update beziehungsweise nach abgeschlossener Wartung, während Core und WebUI laufen:

```bash
cd ~/Georg/TradingBot_v10.1.1_NEXUS
bash NEXUS_Diagnose_Starten.sh
```

Alternativ lässt sich der eigenständige passende Starter aus `Downloads` verwenden:

```bash
cd ~/Downloads
bash NEXUS_10.1.1_Diagnose_Starten.sh
```

Das mitgelieferte Diagnosewerkzeug hat die Toolversion 1.2.0 und beobachtet standardmäßig 30 Minuten. Es löst selbst keine zusätzlichen Broker-, FMP-, Massive-, GPT- oder Telegram-Aktionen aus. Der normal weiterlaufende Bot kann seine regulären Aufgaben ausführen. Die Diagnose trennt Startsammlung, Beobachtungsfenster und Gesamtzeitraum; fehlende Daten werden nicht als tatsächliche Null dargestellt.

Am Ende wird der Pfad zu einer eindeutig benannten Diagnose-ZIP ausgegeben. Diese ZIP zur Auswertung bereitstellen. Ein sofortiger Export vorhandener Daten ist mit `--sofort` möglich; er ersetzt keinen vollständigen Verlaufstest. Unterbrechungen werden soweit möglich als unvollständiger Teilbericht exportiert.

## Bei einem Installationsfehler

Bewahre neuen Ordner, alten Ordner, `nexus_update.log`, `nexus_update_state.json` und die ausgegebene Sicherung auf. Den Installer nicht wiederholt blind starten, wenn das Journal einen unterbrochenen Zustandsimport oder bereits gestartete neue Dienste meldet.

| Zeitpunkt des Fehlers | Verhalten und weiteres Vorgehen |
|---|---|
| Vor der Grenze zum ersten neuen Dienststart | Der Updater versucht, bisherige Dienstdateien und den vorherigen Betriebszustand wiederherzustellen. Der alte Quellzustand wurde nicht überschrieben. Bei zusätzlichem Rücknahmefehler Dienstpfade und Status prüfen und Protokolle erhalten. |
| Ab der Grenze zum ersten neuen Dienststart, einschließlich WebUI | Es könnten bereits neue Befehle oder Handelsdaten entstanden sein. Die neuen Dienste werden im Fehlerpfad gestoppt und deaktiviert; der aktuelle Zustand bleibt erhalten. Es erfolgt kein automatischer Rückfall auf alte Handelsdaten. |

**Nach neuen Fills niemals einfach eine alte Risikodatei, Positionsdatei oder Datenbank zurückkopieren.** Eine spätere Wiederherstellung muss zuerst den neuesten Brokerbestand, Orders, Fills, Schutz und die neuen lokalen Belege abgleichen. Das gilt auch für DEMO. Sind Dienste angehalten, laufen lokale Schutz-/Exitfunktionen nicht; vorhandenen nativen Brokerschutz direkt kontrollieren.

Die Sicherung liegt in einem vom Installer ausgegebenen Ordner `NEXUS_Sicherung_…` neben dem neuen Versionsordner. Sie enthält den alten Zustand als `alter_zustand.tar.gz` und Dienstbelege. Historische einzelne `.protection-plan-….bak`- und `.basis-review-….bak`-Dateien bleiben im alten Ordner und in dieser Vollsicherung; sie werden nicht separat in den neuen Ordner migriert. Bewahre beides auf.

## Zugehörige Berichte

- [TEST_REPORT.md](TEST_REPORT.md): konkrete abgeschlossene Tests und Grenzen der Abnahme;
- [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md): umgesetzte Änderungen;
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md): verbleibende fachliche Blockaden und Betriebsgrenzen;
- [MIGRATION_NOTES.md](MIGRATION_NOTES.md): Details zur Datenübernahme.

Eine erfolgreiche Installation ist keine uneingeschränkte Freigabe für Echtgeldhandel.
