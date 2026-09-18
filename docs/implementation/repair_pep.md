# D02: Bestehenden eToro-Schutz als neue Planentscheidung übernehmen

Arbeitsstand auf NEXUS 10.1.0, ohne neue Versionsnummer und ohne Brokeraktionen in der Entwicklung.

## Ergebnis und Grenze

`etoro_protection_repair.py` bietet einen ausführbaren Wartungsweg für bereits eindeutig belegte einzelne BOT-Aktienpositionen. Ein frischer Brokerzustand muss zu den ausdrücklich vorgegebenen neuen Preisen passen. Der Weg sendet **niemals PATCH, Kauf oder Verkauf**. Stimmen die Werte nicht, bleibt die Übernahme gesperrt. Das getrennte Problem der eToro-Risikobasis bleibt davon unberührt.

Die historischen PEP-Werte 135,8946/138,5207 und die beobachteten 135,90/138,52 wurden als Offline-Testfall verwendet. Es gibt keine Sonderfreigabe für PEP, keine angenommene Zwei-Dezimal-Regel und keine zusätzliche Preistoleranz. Die Positions-ID/Kontobindung sind Laufzeiteingaben, keine im Programm hinterlegte Freigabe.

## Ablauf auf dem Raspberry Pi nach einer später freigegebenen Installation

Die Dateien müssen zusammen mit dem geprüften Entwicklungsstand installiert sein. Im tatsächlichen NEXUS-Installationsordner ausführen. Das Tool verwendet dieselbe `config.POSITION_STATE_FILE` wie `PositionManager` (standardmäßig `position_state.json`). Die abgeleitete `stock_positions.json` ist keine Reparaturquelle.

Der ausführbare Starter `NEXUS_eToro_Schutzplan.sh` zeigt ohne Argumente die Hilfe und reicht konkrete Argumente an das Wartungstool weiter. Beispielsweise ersetzt `./NEXUS_eToro_Schutzplan.sh --read-broker ...` in den folgenden Befehlen den Python-Aufruf. `NEXUS_eToro_Risikopruefung.sh` behandelt die davon getrennte lesende Prüfung der Kontorisikobasis.

1. Nur lokalen Zustand anzeigen, ohne Brokerabruf:

   ```bash
   ./.venv/bin/python etoro_protection_repair.py
   ```

2. Vor der endgültigen Vorschau Core und WebUI stoppen; so bleibt die Positionsdatei zwischen Vorschau und Übernahme unverändert. Vorhandene native Broker-Stops bestehen weiter. Das Tool stoppt oder startet keine Dienste selbst.

   ```bash
   sudo systemctl stop tradingbot-pi5.service tradingbot-webui.service
   ```

3. Frische lesende Vorschau anfordern. `RECORD_ID_AUS_DER_ANZEIGE` durch den vollständigen Schlüssel aus Schritt 1 ersetzen. Die hier verwendeten neuen Preise sind der besprochene PEP-Plan, kein allgemeiner Instrumentstandard.

   ```bash
   ./.venv/bin/python etoro_protection_repair.py --read-broker \
     --record-id 'RECORD_ID_AUS_DER_ANZEIGE' --stop 135.90 --take-profit 138.52 \
     --reason 'Vorhandenen PEP-Schutz als neue wirksame Planentscheidung uebernehmen' \
     --output pep_schutzplan.json
   ```

4. Konto, DEMO/LIVE, Positions-ID, Instrument, Menge, Original-/Effektivplan und Quellenzeitpunkte in der Ausgabe prüfen. Nur bei `READY_TO_ADOPT` kann die angezeigte Entscheidung innerhalb von 15 Minuten ausdrücklich übernommen werden:

   ```bash
   ./.venv/bin/python etoro_protection_repair.py --read-broker --apply \
     --plan pep_schutzplan.json --decision-id 'VOLLSTAENDIGE_DECISION_ID_AUS_DEM_PLAN' \
     --workers-stopped
   ```

5. Bei `APPLIED` ist ausschließlich der lokale neue Schutzplan gespeichert. Nach einem gesonderten Start des bestehenden Core muss die nächste frische Schutzprüfung noch erfolgreich sein. Bis dahin bleiben `PENDING_CONFIRMATION` und `UNCONFIRMED`. Auch danach gelten Kontorisiko-, Daten-, Markt- und weitere Handelsgates. Bei `BLOCKED` den Grund klären; keine Statusdatei löschen oder umetikettieren.

Eine erneute Anwendung derselben bereits gespeicherten Entscheidung liefert `ALREADY_APPLIED`, führt keine erneute Zustandsänderung aus und behauptet keinen aktuellen Brokerschutz. Neue Kontozuordnung, neue Restmenge oder zwischenzeitlich abweichende Sollwerte verhindern diese Idempotenzbestätigung.

## Beweis- und Persistenzvertrag

- BOT/VERIFIED, bestätigte offene Kauf-/Positionskette, Konto-Fingerprint, DEMO/LIVE, einzelne owned/observed/broker-Positions-ID, Instrument, Long-Richtung und exakt unveränderte Restmenge sind Pflicht. Nutzerseitiges Beobachten bleibt bindend.
- Der Adapter bindet zunächst `/api/v1/me` an die echte CID. Er liest P&L frisch und ergänzt den Stoptyp über den CID-gebundenen Instrument-Breakdown. Beide Antworten müssen Positions-ID, Instrument, Long-Richtung, Menge und SL/TP exakt gleich darstellen. Beide Antworten müssen zeitlich frisch sein; fehlende oder unklare Felder blockieren.
- Beide Schutzflags müssen ausdrücklich `false`, der Stoptyp ausdrücklich `fixed` sein. Fehlende Flags werden nicht aus positiven Preisen oder der zusätzlichen Antwort erfunden.
- Der Breakdown muss die relevante Orderliste ausdrücklich als Liste enthalten. Fehlerhafte P&L-Orderlisten oder -zeilen dürfen nicht als leere Liste verschwinden. Offene relevante Orders sowie ungeklärte SUBMITTING/ACCEPTED-Schutzjournal-Einträge blockieren die Übernahme.
- Neuer Long-Stop darf gegenüber dem bisherigen Plan nicht niedriger, neues Ziel nicht höher sein. Das neue Ziel muss oberhalb des neuen Stops liegen. Auch diese eingeschränkte Änderung ist eine neue Entscheidung; sie garantiert keinen besseren Ausführungspreis oder Handelsgewinn.
- Die Vorschau enthält Prüfsummen von Zustandsdatei, Entscheidung und Stoptyp-Quelle sowie Quellenzeitpunkte. `--apply` akzeptiert nur die explizite Decision-ID, die unveränderte vollständige Positionsdatei und einen zweiten neueren Brokerabruf. Es gibt keinen CLI-Schalter zum Anwenden einer Offline-Snapshotdatei.
- Vor jeder Anwendung werden laufende Writer geprüft. Währenddessen hält das Tool den Core-Instanzlock und die identische kritische Positionsdateisperre. Direkt vor dem Schreiben werden Writer und ursprüngliche Dateibytes erneut geprüft. Ein geprüftes unverändertes Backup wird vor dem atomischen Schreiben erzeugt.
- `PositionRecord.protection_plan_history` speichert ursprüngliche und neue Planwerte, Grund, Identität, Quellenbelege, Zeitpunkte, Backup, ursprüngliche Dateiprüfsumme sowie `sent: null`. Kauforder, Fillhistorie, entry_reference_id, Herkunft und Eigentum bleiben erhalten. Es wird kein historischer Versandbeleg erfunden.
- `planned_stop`/`planned_take` enthalten danach die neuen wirksamen Werte; ursprüngliche Risikobeträge bleiben unverändert konservativ und zusätzlich im Beleg erhalten. Die Historie wird beim Laden/Speichern und in der Anzeigeprojektion erhalten.

## Laufender Schutz und PATCH-Journal

Reparierte Positionen werden im Wiederverbindungs- und normalen Verwaltungszyklus strikt gegen Richtung, explizite Flags und belegten festen Stoptyp geprüft. Der ergänzende Readback wird innerhalb einer P&L-Snapshotgeneration je Instrument wiederverwendet. Fehlender Stoptyp im Standard-P&L führt damit nicht zum Erraten des Stoptyps. Ein Ausfall oder Widerspruch des Zusatzendpunkts lässt den Schutzstatus offen.

PULSAR berücksichtigt bei späteren Client-Stop-Entscheidungen und nativen Stopänderungen den wirksamen Plan. Seine ursprünglichen Einstiegspreise, R-Maßstäbe und strategischen Regeln bleiben erhalten. Insbesondere wird nach einer Stopanhebung nicht wieder das ursprüngliche höhere Take-Profit angefordert.

Der vorhandene separate Adapter-PATCH-Pfad wurde bei Bestätigung und Wiederaufnahme gehärtet: Ein Intent wird vorher dauerhaft reserviert. Die Annahme muss `operationId`, passende `positionId` und passende `referenceId` enthalten; sie wird als ACCEPTED gespeichert. Eine fehlerhafte Antwort oder ein Timeout erlaubt keinen zweiten blinden PATCH. Das Journal verlangt bei neuen Intents einen nach dem Auftrag aufgenommenen passenden Positionsbeleg. Explicit false-Flags gehen beim Journal-Abgleich nicht mehr verloren. Ein alter noch sichtbarer Stop kann bei ausstehendem neuem Auftrag keine AUTO-Bestätigung bewirken. Ein später passender Readback kann das Journal auflösen, ohne zugleich einen davon abweichenden alten lokalen Plan zu bestätigen.

**Weiterhin offen und ausdrücklich außerhalb der neuen Übernahme-CLI:** Falls eine tatsächliche Änderung beim Broker benötigt wird, fehlt in diesem Wartungsweg die separat zu belegende aktuelle Eligibility-/Preisgrenzenprüfung. Deshalb wird keine solche Änderung versucht. Die allgemeine Preisbildung neuer Orders und eine pauschale Zwei-Dezimal-Normalisierung wurden nicht eingeführt. Die direkte praktische Abnahme von Käufen, Verkäufen oder Schutz-PATCH ist nicht erfolgt.

## Offline-Prüfung

Neue Tests: `tests/test_etoro_protection_plan_repair.py`. Sie prüfen echte PEP-Ausgangspreise, exakte Übernahme ohne Versand, Originalhistorie/BOT/VERIFIED, Neustart, Idempotenz, falsches Konto/Umgebung/Position/Instrument, geänderte Menge, unklare Flags/Stoptyp, alte Snapshots und Pläne, unerwartete Stateänderungen, unzulässige Planänderungen, Speicherfehler, laufende Writer, ungeklärte PATCH-Aufträge und spätere Bestätigung sowie PULSAR-Effektivplan-Verwendung.

Bestehende Tests wurden nur dort als vollständige synthetische Brokerantwort ergänzt, wo die neue Beweisprüfung nun die dokumentierten ACK-Korrelationsdaten und den von `_pnl()` erzeugten Zeitstempel erfordert. Sicherheits- oder Netzsperren wurden nicht abgeschaltet.

Zusätzliche Regressionstargets: `test_v101_etoro_protection_evidence.py`, `test_v940_etoro_identity.py`, `test_v930_etoro_zuordnung.py`, `test_v984_receipts_and_pulsar_lifecycle.py`. Der zentrale Testnachweis dokumentiert den abschließenden Stand und die Ergebnisse.

## Geprüfte Primärquellen

- [eToro: Instrument Breakdown DEMO](https://api-portal.etoro.com/api-reference/trading--demo/get-instrument-breakdown-demo) – CID-Bindung, gruppierte Positionen und Orders, Stoptyp.
- [eToro: Modify stop-loss and take-profit](https://api-portal.etoro.com/api-reference/trading--demo/modify-stop-loss-and-take-profit-settings-on-an-open-position) – asynchrone Annahme und Korrelationsdaten.
- [eToro: Account PnL and Portfolio Details](https://api-portal.etoro.com/api-reference/trading--demo/get-account-pnl-and-portfolio-details) – Standard-Positionsbelege.
- [eToro: Instrument Trading Eligibility](https://api-portal.etoro.com/api-reference/trading--demo/check-instrument-trading-eligibility) – separat zu prüfende Änderungsrechte und Grenzen.

Die Quellen wurden am 13.09.2026 gelesen. Dokumentationsbeispiele ersetzen keine echte Kontoantwort vom Raspberry Pi.
