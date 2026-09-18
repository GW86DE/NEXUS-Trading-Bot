# Unabhängiger Read-only-Review der WebUI-Integration

Geprüft: `webui/auth.py`, neue Routen in `webui/app.py`, `webui/diagnostics.py`, relevante Dashboard-/Logprojektionen in `webui/state.py` und `tests/test_v100_webui_backend.py`. Keine Änderungen an diesen Modulen vorgenommen. Vier Gegenproben wurden in einem eigenen temporären Zustand ausgeführt; keine Broker-/GPT-Anfragen.

## Konkrete Befunde zur Korrektur

| ID | Priorität | Codepfad | Reproduzierter Befund | Geeignete enge Korrektur |
|---|---|---|---|---|
| UI-R01 | P1 | diagnostics._connection → Import decision_analytics | Erster `execution_history()`-Aufruf legt in leerem Zustand `decision_history.sqlite`, WAL und SHM an. `decision_analytics` führt beim Import `init_db()` aus. Der neue Pfad ist deshalb noch nicht vollständig read-only. | Dateipfad ohne initialisierenden Modulimport auflösen; erster Prozessaufruf muss Zustand unverändert lassen. |
| UI-R02 | P1 | state._runtime | Heartbeat einen Tag in der Zukunft wird mit Alter 0 versehen; `online` und `worker_alive` werden wahr. Die neue Operationsprojektion lehnt denselben Zeitstempel ab. | Zukunftszeit explizit unbekannt/veraltet behandeln, keine Nullkappung. Alle Anzeigen sollen dieselbe Frischedefinition benutzen. |
| UI-R03 | P1 | diagnostics.operations_snapshot | Runtime und Kontext DEMO; `okx_detail.modus=LIVE` bei gleicher Kontokennung und positiver Bereitschaft. Ergebnis trotzdem DEMO `buy.state=ALLOWED`. | Auch Umgebung/Modus des Detailbelegs auf Widersprüche prüfen. Bei fehlendem Grund einer positiven Bereitschaft außerdem keinen Text „Keine aktuelle Kaufbereitschaft übermittelt“ ausgeben. |
| UI-R04 | P2 | auth.configured / authenticate | Syntaktisch gültige Credentialdatei mit Array statt Objekt löst `AttributeError` aus. Sitzungsdecodierung bleibt fail closed; Loginanzeige bricht jedoch mit HTTP 500 ab. | Credentialform zentral als Objekt mit erwarteten Feldtypen prüfen; ungültige Konfiguration als nicht eingerichtet behandeln. |

Maschinenlesbare Belege: `implementation/evidence/webui_independent_review.jsonl`; ausführbares isoliertes Skript: `implementation/evidence/webui_review_probe.py`. Die Belege sind synthetische Gegenfälle. Es wird kein realer Kontowechsel, Ausfall oder unbefugter Zugriff behauptet.

## Positiv geprüft

- Passworteinrichtung rotiert den Sitzungsschlüssel. Vorherige Cookies werden tatsächlich ungültig; nicht nur ein lokales Cookie gelöscht.
- Fehlende Credentials können keine Sitzung mit leerem bekannten HMAC-Schlüssel mehr begründen. Sitzung braucht aktuelle Benutzerzuordnung, Ablaufzeit, CSRF-Wert und Nonce.
- Neue Diagnoserouten verlangen echte Sitzung und bieten keinen POST-Schreibpfad. Steuerung verlangt zusätzlich CSRF und Aktionsbestätigung.
- Orderdetail bindet Broker, Konto, Umgebung und Client-ID gemeinsam. Fremder Scope wird nicht über Symbol oder heutige Konfiguration ergänzt.
- Order- und Fill-/Eventlisten sind mengenbegrenzt; SQLite-Lesevorgänge haben feste Zeitbudgets und lesen innerhalb eines Snapshot-Transactions. Fehler einer fehlenden oder defekten Ausführungstabelle werden nicht als gesunde leere Orderliste dargestellt.
- Order-Request-/Result-Rohpayloads werden nicht an die neue Detailansicht ausgegeben. Detailfehler liefern kontrollierte 400/404/503-Texte ohne rohe Datenbankausnahme.
- REST und WebSocket werden separat dargestellt; erfolgreicher Transport mit fachlicher Ablehnung wird nicht automatisch zum Verbindungsabbruch. Kein globaler Verkaufsfreibrief wird aus einem positiven REST-Status erzeugt.
- Eine leere Reconciliation-Liste wird nicht mit vollständigem Depotabgleich gleichgesetzt. Brokerprobleme können getrennt angezeigt werden.
- Die neue Literal-Suche bindet SQL-Parameter und maskiert `%`, `_` und Backslash; sie eröffnet keine SQL-Injektion.

## Grenzen

Der Review betrifft den tatsächlichen Code und isolierte Gegenproben, keinen Browser-/Pi-/Livebrokerlauf. Bereits vorhandene Dashboardimporte und ältere Zusammenfassungen sind nicht pauschal frei von Initialisierungseffekten; die neue, ausdrücklich als read-only bezeichnete Orderdiagnose braucht insbesondere UI-R01. Keine Behauptung eines vollständigen Penetrationstests.
