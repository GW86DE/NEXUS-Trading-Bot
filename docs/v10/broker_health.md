# NEXUS 10 – Brokerverträge und fachliche Laufzeitbeobachtung

Stand: 13.09.2026. Grundlage: extrahierter vollständiger NEXUS-9.9.0-FIX1-Code sowie N04/N08 und die N02-Wechselwirkung des technischen Vergleichs. Keine Brokeranfrage, Order, Kontoaktivierung oder GPT-Anfrage durchgeführt. Die folgenden Anpassungen liegen ausschließlich im neuen Arbeitsbaum.

## Änderungen und Nachweise

| Maßnahme | Konkretes Problem / Ursache | Änderung | Dateien | Prüfung / Status |
|---|---|---|---|---|
| NEXUS-IMP-B01 / N04 | Ein HTTP-200 mit `code=0`, aber fehlendem oder falschem `data`-Feld wurde über `or []` als leere Antwort behandelt. | Erfolgsantwort verlangt ein Objekt mit expliziter Liste. Fehlerhafte Orderantwort bleibt `OrderStatusUnklar`; keine Wiederholung. | `broker/okx.py` | Vier Schema-Gegenproben und Ordergegenprobe; umgesetzt und getestet. |
| NEXUS-IMP-B02 / N04 | Zwei Instrumentabfragen konnten wegen HTTP außerhalb des Cachelocks in umgekehrter Reihenfolge veröffentlichen; fehlgeschlagener Refresh ließ vorherigen Cache scheinbar frisch. | Lokale Anfragengeneration; überholte Antwort wird verworfen und meldet fachlichen Fehler. Neuester fehlgeschlagener Refresh invalidiert Frische. Private Regeln bleiben einzige Ausführungsquelle. | `broker/okx.py` | Zwei echte Reader-Threads mit kontrolliertem Antwortablauf sowie Refreshfehler; umgesetzt und getestet. |
| NEXUS-IMP-B03 / N04a | Septemberankündigung kann bestehende USD-Routen betreffen; Konto-/Regionsbetroffenheit ist unbekannt. | Rein lesende Projektion des vorhandenen privaten Instrumentcaches, Frische/Quelle/Qualität, beobachtete USD-Routen und zulässige Abrechnungswährungen. Betroffenheit bleibt `null`. | `broker/okx.py` | Keine Anfrage durch Preflight; öffentlicher/veralteter Katalog bleibt UNKNOWN; alte IDs unverändert. Umsetzung getestet, tatsächliches Konto nicht validiert. |
| NEXUS-IMP-B04 / N04 | eToro-Transport erlaubte aufgrund des generischen Defaults theoretisch Wiederholungen von POSTs, obwohl aktuelle Orderpfade bereits `safe_retry=False` übergeben. | Transportgrenze wiederholt ausschließlich GET/HEAD. Auch ein späterer irrtümlicher POST-Aufruf mit Default darf nicht blind wiederholen. V3-Create/V2-Referenzlookup/exakter Positionsclose bleiben bestehen. | `broker/etoro.py` | Timeout mit Default und exakt einem POST; bestehende Adapter-, Identitäts- und Close-Tests bestanden. |
| NEXUS-IMP-B05 / N08 | Healthcheckzeit, tatsächliche HTTP-Ausführung, REST-Erfolg und WS-Verbindung waren nicht einheitlich getrennt. | Kleiner pro Client gehaltener Beobachter mit Public/Private-Kanal, tatsächlichen Transportversuchen, Zeitstempeln, Dauer, Antwort-/Fehlerstatus und lokalen Sequenzen. Keine Secrets/Payloads/Querystrings. | `broker_observation.py`, `broker/okx.py`, `broker/etoro.py`, `broker_health.py` | Timeout nach Erfolg, ältere erfolgreiche Antwort nach jüngerem Fehler, Nichtversand wegen Limiter, Brokertrennung, Ablehnung bei weiterhin erreichbarem Transport; umgesetzt und getestet. |
| NEXUS-IMP-B06 / N08 | Ein Prozessheartbeat beweist keinen abgeschlossenen Schutzzyklus. Abgefangene Fehler konnten in einer allgemeinen OK-Meldung verschwinden. | Beginn, Ende, Laufzeit, Alter, Zahl durchlaufener Positionen und konkrete Diagnosecodes am tatsächlichen Positionsprüfpfad. WARN bei erkannten Fehlern trotz abgeschlossenem Durchlauf. | `crypto_engine.py`, `broker_observation.py` | Kontrollierte monotone Uhr, laufender/langer Zyklus, fehlender Broker und Fehlerdurchlauf. Keine Pi-Latenzwerte behauptet. |
| NEXUS-IMP-B07 / N02-Kopplung | Neu propagierte fsync-Fehler nach bewiesenem Fill würden den bereits bestehenden, späteren Schutzversuch überspringen. | Persistenzfehler nach bewiesenem Fill halten eine lokale OKX-Einstiegssperre; exakte Fill-/Orderlineage bleibt bestehen. Der normale idempotente Schutzpfad wird dennoch versucht. Pending-Intent wird erst nach vollständig erfolgreicher Projektion entfernt. Originalfehler wird weitergegeben. | `crypto_engine.py` | Vier EIO-Gegenproben an Ledger, Registry, Position und Schutzprojektion; reale SQLite/Registry/Engine-Methoden, nur Schutzantwort synthetisch. Jeweils exakter Schutzversuch und Sperre nachgewiesen. |
| NEXUS-IMP-B08 / N04 | Der bestehende eToro-Schemavertrag war korrekt, seine aktuelle Fehlerqualität aber nicht separat sichtbar. | Snapshotqualität mit eigener letzter Prüfung, letztem validierten Stand, Fehlercode und `complete=False` bei neuerem Fehler. Letzter erfolgreicher Snapshot wird nicht als neue leere Vollsicht ausgegeben. | `broker/etoro.py` | Gültiger explizit leerer Snapshot, anschließend fehlendes `positions`-Array: Fehler bleibt Fehler; umgesetzt und getestet. |

## API für die bestehende WebUI

`broker.connection_components()` führt keine Netzwerkanfragen aus. Beide Broker liefern additiv `environment`, `account_fingerprint` sowie `observations`:

```json
{
  "schema_version": 1,
  "broker": "okx",
  "environment": "DEMO",
  "observed_at": "UTC-Zeit der Projektion",
  "request_sequence_is_broker_sequence": false,
  "rest": {
    "private": {
      "state": "OK | ERROR | UNKNOWN",
      "transport_state": "OK | ERROR | NOT_SENT | UNKNOWN",
      "last_attempt_at": null,
      "last_started_at": null,
      "last_response_at": null,
      "last_success_at": null,
      "last_error_at": null,
      "http_status": null,
      "error_code": "",
      "latency_ms": null,
      "request_generation": 0,
      "completed_generation": 0,
      "in_flight": 0,
      "wire_attempts": 0,
      "success_age_seconds": null,
      "attempt_age_seconds": null
    }
  }
}
```

Die Felder sind ein Schema-Beispiel, keine erfundenen Betriebsdaten. Ein Kanal erscheint erst nach einem tatsächlichen Aufruf dieses Clients. `last_attempt_at` ist der Eintritt in die REST-Funktion, `last_started_at` unmittelbar vor dem HTTP-Aufruf; vor dem Versand gescheiterte Limits/Authentifizierung haben keinen Start-/Erfolgsbeleg. `wire_attempts` zählt echte Transportaufrufe. Bei einer fachlichen Ablehnung kann `state=ERROR` und gleichzeitig `transport_state=OK` gelten. Daraus darf die UI keinen Verbindungsverlust erfinden. Der öffentliche OKX-Kanal macht den privaten nicht gesund.

Die Sequenzen dienen nur der lokalen Beobachtungsreihenfolge. Eine verspätete erfolgreiche Antwort aktualisiert den realen letzten Antwort-/Erfolgszeitpunkt, löscht aber keinen neueren Fehlerstatus. Sie beweist insbesondere keinen vollständigen WS-/REST-Abgleich.

eToro liefert seinen vorhandenen WS-Zustand einschließlich `connected_since` und `last_private_event` sowie den unveränderten Workerstatus getrennt. `runtime_services_status.connection_components` ist zusätzlich enthalten; der zentrale Runtime-Schreiber kann die Komponenten direkt übernehmen. OKX liefert die bisherigen Account-/Order-/Pong-Zeitpunkte getrennt sowie `instrument_contract`.

`CryptoEngine.status()` / `runtime_status_okx.json` enthalten `functional_health.protection`:

* `state`: UNKNOWN, OK, WARN oder ERROR;
* `running`, `last_started_at`, `last_completed_at`, `last_success_at`;
* `duration_ms`, `age_seconds`, `running_seconds`, `checked_positions`, `errors`, `cycles`;
* explizite Bedeutung: abgeschlossener Positionsprüfdurchlauf, kein pauschaler Beweis jeder nativen Schutzorder.

Die UI muss das Alter aus dem UTC-Zeitstempel bei jedem Lesen neu berechnen, wenn der Runtime-Snapshot selbst älter geworden ist. Ein ruhender WS-Orderkanal ist nicht automatisch ein Defekt; die Eventzeit bleibt eine eigene Information. Diese Beobachtungen erteilen selbst keine Kauf- oder Verkaufsrechte.

## Tests

* **23/23 neue Tests bestanden**: `broker-final-health_8db92180`, 0,57 Sekunden. Aktueller neuer Testbestand einschließlich Transport-/Fachablehnungsunterscheidung und Retrygegenprobe.
* **94/94 gezielter gemeinsamer Zwischenstand bestanden**: `broker-final_0911f1fe`, 2,28 Sekunden; neue Tests plus `test_v975_execution_and_repair.py`, `test_v910_okx_api.py`, `test_v990_guards.py`. Danach kamen nur zwei zusätzliche Transportbeobachtungstests und deren kleine Metadatenpräzisierung hinzu.
* **153/153 bestehende Regressionen bestanden**: `broker-regression_4f4aa693`, 6,22 Sekunden; zehn Module zu eToro-Rootfix/Identität/Close, OKX, Schutz, Buchhaltung, Status und API-Reconciliation.
* Alle genannten Läufe verwendeten isolierte Quellkopien, Testzustandsordner, den vollständigen Prüf-Harness und Netzwerksperre. **Keine Netzwerkereignisse**. Die Zahlen sind überlappende Läufe und dürfen nicht zu einer Zahl einzigartiger Tests addiert werden.
* Ein erster Lauf hatte einen reinen Testimport-Tippfehler (`EToroBroker` statt bestehendem `EtoroBroker`): korrigiert und erneut bestanden. Kein Produktfehler dadurch verdeckt.

## Risiken, Grenzen und bewusste Nichtänderungen

1. Kein zusätzlicher Orderwriter, Readerdienst, Message Broker oder Hintergrundthread eingeführt. Die Schutzmetrik behebt keine langsamen Scans; sie macht diese später messbar. P95/P99 und Grenzwerte müssen auf Georgs Raspberry Pi 5 gemessen werden.
2. Der kritische Persistenzfehler bleibt im laufenden OKX-Engineprozess gesperrt. Nach Neustart gelten die bestehenden Startup-/Registry-/Ledger-/Kontobindungsregeln; die gehaltenen Pending-Intents erlauben den exakten Replay. Ein Neustart ist keine Zusage reparierter Hardware. Die Sperre eines allgemeinen Buchungsfehlers darf die zusätzliche Persistenzlatch nicht versehentlich löschen.
3. Auch der Schutzpfad darf bei defekter Festplatte/SD-Karte seine eigene dauerhafte Submit-Reservierung nicht umgehen. Wenn sein Journal keinen sicheren Write zulässt, bleibt eine neue native Schutzorder unbestätigt und der ursprüngliche Persistenzfehler sichtbar. Die Tests beweisen den erhaltenen Schutzversuch, keine Schutzgarantie bei defekter Hardware oder ausgefallenem Broker.
4. Eine lokale Requestgeneration ist kein Exchange-Wasserstand. Eine umfassende Neuordnung von REST-/WS-Deltas, History-Pagination und Vollständigkeitsverträgen aller Endpunkte wurde bewusst nicht behauptet oder neu gebaut. eToro besitzt bereits den serialisierten Snapshotpfad; er wurde nicht parallelisiert.
5. Das Instrumentcache-Verwerfen kann bei tatsächlich gleichzeitigen Readern einen zusätzlichen fachlichen Abbruch auslösen. Das ist beabsichtigt: ein neuerer Fehler darf einen älteren Katalog nicht erneut autoritativ machen. Kein öffentlicher Fallback wurde eingeführt.
6. Auf dem echten Demo-/Live-Konto, auf ARM64 und mit realen Disconnects/Netzwerkverlusten wurden diese Änderungen nicht ausgeführt. Bestehende SUI- und eToro-Fremdbelege werden nicht umgeschrieben; es werden keine fehlenden Gebühren oder Ausführungen erfunden.
7. Keine Datenbankschemamigration durch diesen Teil erforderlich. Beobachtungen sind additive, rekonstruierbare Laufzeitdaten. Originalrelease, Installation, Versionsdateien und Manifest wurden nicht geändert.

## Anbieterbeleg zur Septemberprüfung

Offizielle Quelle, am 13.09.2026 erneut gelesen: [OKX API Change Log](https://www.okx.com/docs-v5/log_en/), Ankündigung „OKX to migrate USD spot trading pairs“, letzter Anbieterstand 02.09.2026. Relevantes paralleles Zeitfenster: 23.09.2026 08:00 UTC bis 30.09.2026 08:00 UTC. Betroffene alte Instrumentkennungen werden nicht automatisch abgebildet; Abrechnungswährungen sind dem privaten `tradeQuoteCcyList`-Vertrag zu entnehmen. Der neue Preflight aktiviert kein Kontofeature und benennt keine gespeicherte Position um. Die konkrete regionale Kontobetroffenheit bleibt zu prüfen.

## Ergänzung aus der unabhängigen Gegenprüfung

Die Änderungen an Lifecycle, Filltracker und Ledger wurden anschließend unabhängig überprüft. Keine neue implizite Wiedereröffnung oder Trennung der gemeinsamen Ledger-/Lifecycle-SQL-Transaktion gefunden. Konto-/Umgebungstrennung der Quittungen blieb erhalten. Die Prüfung fand folgende zusätzliche Punkte:

* Der Filltracker akzeptierte strukturell falsche, aber syntaktisch gültige JSON-Felder (`seen_ids` als String und `initialized` als String). An die zuständige Implementierung zurückgegeben; dort wurden strenge Typprüfungen und Gegenproben ergänzt. Der Verlustschutz wird nicht mehr durch lexikografisches Löschen von 5.000 IDs erkauft. Die dadurch langfristig wachsende Checkpointdatei benötigt später eine beleggebundene Archivierung.
* Die neue WebUI-Projektion muss Konto und Umgebung von Komponenten/Runtime gegen den Anzeigenkontext prüfen, weil bisher getrennte Dateilesungen während eines Kontowechsels verschiedene Generationen liefern können. An die zentrale WebUI-Implementierung zurückgegeben.
* Empfang einer HTTP-Antwort beweist keine fachliche Ablehnung: HTTP503 und fehlerhafte Erfolgsschemata benötigen eine neutrale Fehlerbeschreibung. An die zentrale WebUI-Implementierung zurückgegeben.

### NEXUS-IMP-B09 – eToro-Risikobuchung nach Ledgerabschluss

**Problem:** `capture_new_fills()` schrieb den exakten Ledgerabschluss, anschließend den Tagesrisikobeleg. Ausnahmen aus `register_realized_pnl()` beziehungsweise Alias-/Unknown-/Kostenwrites fielen jedoch in den generischen per-Fill-Handler. Dieser quittierte den Fill zwar nicht, ließ den Aufrufer aber grundsätzlich weiter in den Kaufpfad gehen. Ein späterer erfolgreich gespeicherter Positionszähler konnte den früheren Persistenzfehler zusätzlich verdecken; die separate RiskState-Implementierung härtet deshalb ihre operationsbezogenen Sperren.

**Änderung in `live_trader.py`, ausschließlich innerhalb `capture_new_fills()`:**

1. Risiko-, Alias- und Kostenwrites erhalten einen kritischen Fehlerrahmen. Ein Fehler propagiert als `_CriticalFillAccountingError`; Journal-, Tracker- und History-Quittierungen werden nicht als erfolgreich vorweggenommen.
2. Ein bestätigtes Nettoergebnis muss zum Broker, Kontofingerabdruck, Demo-/Live-Modus und zur exakten Position des Fills gehören. Widersprüchliche Belege führen zu `ETORO_RISK_RECEIPT_SCOPE_MISMATCH`.
3. Nach bestehender Aliasübernahme wird zuerst ein idempotenter `UNKNOWN`-Beleg `ledger:<trade_id>` mit vorhandenem Abschlusszeitpunkt gespeichert. Erst anschließend wird das bestätigte Ergebnis gebucht. Damit findet die vorhandene `risk_result_recovery` diesen Ledgerbeleg auch nach Neustart mit leerem nächsten Fillpoll. Aliasübernahme bleibt davor, damit bereits gezählte ältere Belege nicht doppelt gezählt werden.
4. Nach dem Fillloop wird die verbleibende Risikopersistenzsperre geprüft. Exakte Fills dürfen vorher ihre eigenen Operationen reparieren; ein leerer Poll darf bei weiterhin offener Risikobuchung nicht in den Kaufpfad zurückkehren.

**Tests:** `tests/test_v100_etoro_risk_projection.py` enthält neun Gegenproben: echter dateibasierter RiskState mit EIO beim bestätigten Ergebniswrite, unverändert offener UNKNOWN-Beleg, Positionszähler darf Sperre nicht heilen, exakter Replay bucht einmalig und löst Sperre; Alias-/Unknown-Fehler; Neustart mit leerem Brokerpoll; vier widersprüchliche Broker-/Konto-/Umgebungs-/Positionskontexte; offene Sperre bei leerem Poll. Der letzte gezielte Lauf `capture-risk-final_c47022bc` bestand **63/63 Tests** in 3,14 Sekunden einschließlich bestehender Fillpipeline-, Buchhaltungs-, Schutz- und RiskState-Tests; keine Netzwerkereignisse.

**Testkorrektur:** Der alte Test in `tests/test_v910_geldpfad.py` suchte Text direkt in `pruefe_positionen()` und scheiterte am neuen Messwrapper. Sein impliziter IDLE-Vertrag war zudem bereits durch die sichere Exit-Reconciliation überholt: ein unbekannter Submit darf nicht allein durch Schutzbestätigung freigegeben werden. Der Ersatz treibt die wirkliche öffentliche Positionsprüfung mit einem belegten terminalen Nullfill an und verifiziert persistierten `RETRY_WAIT`, Retryzeitpunkt, bestätigten nativen Schutz und den sichtbaren Abschlussbeleg. Es wurde keine produktive Handelsregel für einen Quelltexttest abgeschwächt.

**Grenze:** Scheitert bereits der erste UNKNOWN-Write, kann dieser Marker nicht auf Disk stehen. Dann bleiben der unquittierte Fill und die vorhandenen History-/Exitjournal-Replaypfade für die Wiederholung verantwortlich. Diese Änderung ist keine neu eingeführte transaktionale Outbox über Ledger und Risk-JSON und keine Garantie verfügbarer Brokerhistorie bei dauerhaftem Datenträger- oder Brokerausfall.
