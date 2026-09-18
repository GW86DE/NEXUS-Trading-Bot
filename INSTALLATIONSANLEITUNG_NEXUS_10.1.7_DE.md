# NEXUS 10.1.7 – OKX-Kontotrennung und Bestandsklärung

Diese Version basiert auf dem wiedergefundenen vollständigen 10.1.6-Paket. Der im abgebrochenen Chat erwähnte 10.1.7-Zwischenstand war nicht verfügbar; die Änderungen wurden neu umgesetzt und geprüft.

## Installation mit neuem OKX-Demounterkonto

Den selbstenthaltenden Installer auf dem Raspberry Pi speichern. Im Ordner der heruntergeladenen Datei ausführen:

```bash
bash NEXUS_10.1.7_Installieren.sh --paket-pruefen
bash NEXUS_10.1.7_Installieren.sh --okx-neues-konto
```

Als normaler Pi-Benutzer ausführen, nicht mit `sudo`. Der Installer fragt bei Bedarf selbst nach den nötigen Systemrechten. Er ermittelt die vorhandene Installation anhand der beiden Dienste, sichert sie, stoppt Core und WebUI, übernimmt die Einstellungen in einen Arbeitsordner und führt die Prüfungen vor dem Dienststart aus. Ziel: `~/Georg/TradingBot_v10.1.7_NEXUS`.

Beim Kontowechsel die **drei API-Zugangsdaten des neuen OKX-Demounterkontos** eingeben. Die Eingaben sind verborgen. Nicht die Zugangsdaten des alten Hauptkontos verwenden. Das neue Konto muss in den abrufbaren Historien frei von Orders/Fills und aktuellen Positionen sein. Vorhandenes Demo-Startguthaben ist erlaubt; daraus werden keine NEXUS-Positionen erzeugt.

Die Vorprüfung verwendet ausschließlich GET-Abfragen. Sie verändert weder OKX-Guthaben noch Kontomodus, Freigaben oder Orders. Ist eine erforderliche Abfrage nicht verfügbar, bricht der Wechsel ab. Die API-Historien sind zeitlich begrenzt: Ihre Leere ist kein Beweis, dass ein Konto niemals gehandelt hat. Ein NEXUS bereits bekanntes Konto darf deshalb auch bei leeren API-Historien nicht als neu zurückgesetzt werden.

Ohne `--okx-neues-konto` erfolgt ein normales Update. Ein abweichendes Konto wird dabei mit `OKX_ACCOUNT_CONTEXT_MISMATCH` blockiert. Bei ungebundenen oder widersprüchlichen Altbelegen ist eine explizite Klärung erforderlich. NEXUS übernimmt sie nicht aufgrund eines neu eingetragenen Schlüssels.

Weitere Optionen des Installers bleiben erhalten, etwa `--plan`, `--source` und `--nur-entpacken`. `--plan` führt den Kontowechsel nicht aus. Ein gebuchter FMP-Tarif wird nur mit der bereits vorhandenen Option `--fmp-starter` eingerichtet; der Kontowechsel verändert keinen Tarif.

## Was archiviert wird

Der alte OKX-Zustand liegt anschließend als `okx_account_archive_<Zeit>_<Kennung>.zip` im neuen Projektordner. Enthalten sind das alte Positionsbuch, die alte Risikobasis und relevante Belegdateien. SQLite-Datenbanken werden einschließlich bestätigter WAL-Inhalte gesichert und auf Integrität geprüft. Gemeinsame Belegdatenbanken bleiben auch in der aktiven Installation erhalten; sie werden nicht geleert.

`ACCOUNT_ARCHIVE.json` enthält Dateiprüfsummen und Reaktivierungskandidaten aus offenen Bestandslücken. Auch wenn das alte Positionsbuch schon leer ist, bleiben damit die fünf Lücken und zugehörige Ledgerbelege erhalten. Ein Kandidat ist keine Freigabe zur automatischen Wiederherstellung.

Nicht archiviert werden Zugangsschlüssel in diesem zusätzlichen Belegarchiv. Die vollständige Sicherung des bestehenden Installers bleibt davon unabhängig. Das spätere Aktivieren des alten Kontos benötigt seine richtigen Zugangsdaten und einen geprüften Wiederherstellungsplan. Keine Stops oder Take-Profits werden aus fehlenden Daten erfunden.

Das neue Konto erhält ein leeres NEXUS-Positionsbuch und einen neuen OKX-Risikozustand. eToro, Strategieauswahl einschließlich Freqtrade, PULSAR, Quellenhistorien und KI-Budgets bleiben erhalten. Alte kontolose Ledgerzeilen werden dem ausdrücklich neuen Konto nicht zugeordnet. Kontogebundene 54092-Meldungen bleiben beim verursachenden Konto.

## Verhalten bei fehlendem Bestand

- Fehlender oder wesentlich reduzierter Bestand ohne passenden Ausführungsbeleg bleibt `BROKER_STATE_UNKNOWN`, auch nach beliebig vielen Wiederholungen.
- Position, ursprüngliche Menge, Stop-/Zielwerte und offene Ledgerzeile bleiben erhalten.
- Der neue Kontosnapshot wird vor der Equity-Aktualisierung geprüft. Ein inkonsistenter Snapshot ersetzt die letzte bestätigte Risikobasis nicht; neue Käufe sind blockiert.
- Der Schutzstatus lautet aktuell `UNKNOWN_BROKER_STATE`. Ein vorheriger Status bleibt separat als historische Information erhalten.
- Leeres Guthaben erlaubt keine automatische Stornierung vermeintlich verwaister Schutzorders. Reguläre Ausstiege behalten ihre konkreten, positionsgebundenen Stornowege.
- Ein echter zugeordneter SELL-Beleg kann weiter verarbeitet werden. Das bloße Wiederauftauchen von Guthaben hebt eine gespeicherte Bestandslücke nicht automatisch auf.

## Nach der Installation

In der WebUI prüfen, dass das neue OKX-Demokonto verbunden ist, null NEXUS-Kryptopositionen geführt werden und kein alter Verlust/54092-Zustand auf das neue Konto übertragen wurde. Demo-Startbestände dürfen als Kontobestand erscheinen. Die eToro-Seite und bisherigen Strategieeinstellungen sollten unverändert verfügbar sein.

Das Release repariert NEXUS. Es stellt die bei OKX verschwundenen Demo-Bestände nicht wieder her. Das bleibt Gegenstand der Supportklärung.

## Unterbrochener Kontowechsel

Bei einem Stromausfall während des lokalen Schreibens bleibt `okx_account_switch_pending.json` als Startblockade bestehen. Diese Datei nicht einfach löschen und den Bot starten. Beim Installer gelten dessen Sicherungs- und Wiederanlaufregeln; das Belegarchiv und der Updatebericht ermöglichen die Prüfung des unterbrochenen Zustands. Es gibt bewusst keine automatische Reaktivierung alter, ungeklärter Positionen.

## Grenzen der Prüfung

Die Releaseprüfungen verwenden simulierte Brokerantworten und lokalen Zustand. Sie senden keine echten Orders, Nachrichten oder KI-Anfragen. Ein Test mit einer Kopie der Originaldaten prüft zusätzlich die Archivierung und Kontotrennung. Live-OKX-Zugriff, echte Dienstumschaltung und ARM64-Hardware werden hier nicht ausgeführt. Die tatsächlich ausgeführten Tests und Ergebnisse stehen im separaten `NEXUS_10.1.7_Pruefbericht.md`.
