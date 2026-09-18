# NEXUS 10.1.0 – WebUI / verständliches Logbuch

## Umgesetzt

- `decision_explanation.py`: deterministische deutsche Hauptbegründung aus gespeicherten Status-/Check-/Reason-Feldern; keine GPT-Aufrufe oder Handelsfreigaben. NO_SIGNAL benennt gemessenes Nullvolumen und fehlende Kurserholung getrennt. Fehlende Messwerte werden nicht als Nullwerte ausgegeben. Freigabe, Teilfüllung, vollständige Ausführung, Ablehnung, Storno und unklarer Ausgang bleiben getrennt; Verkaufsseite wird berücksichtigt. Bei unbekannten Gründen ausdrücklich keine erfundene Erklärung.
- `webui/state.py`: read-only Anreicherung vorhandener und alter Entscheidungszeilen ohne DB-Migration. Fehlerhaftes Payloadformat fällt auf leere Fakten zurück. Neue Aktiensichten für bereits gespeicherten FMP-Kontext; keine zusätzlichen Provideraufrufe. Alte FMP-Jahresschemata bleiben unvalidiert. Schutzbelege sind nur bei exakt gleichem Konto, Umgebung und Position-ID-Menge zugeordnet.
- `webui/static/logbook.js`, `webui/templates/logbook.html`, `webui/static/nexus.css`: hervorgehobener deutscher Satz vor aufklappbaren technischen Begründungen/Messwerten. Alle Texte HTML-escaped. Bestehende Navigation, Farben, Filter, Seitennavigation bleiben. Tastatur-/Touchzugriff auf Tabellen und Details.
- `webui/diagnostics.py`, `webui/static/dashboard.js`: alle gemeldeten Kaufhindernisse einschließlich Risikobasis neben Marktöffnung; Risiko bleibt brokerlokal. Kerzenqualität und Signal-Zeitraster getrennt von zusätzlicher Sicherheitswartezeit in Brokerdetails. Historischer FMP-Fehler erhält keinen aktiven Verbindungstest-Button.
- `webui/diagnostics.py`, `webui/static/execution_details.js`: Ausführungsmengenbuchung und Gebührenbelege getrennt. Vollständige Mengenbelege reichen nicht für vollständige Gebühren oder Netto-P&L. Begrenzte read-only Sammelabfrage, maximal 5000 Fill-Zeilen pro Ansichtsseite.
- `webui/static/common.js`, `webui/static/positions.js`, `webui/static/trades.js`, `webui/app.py`: gewünschte/gesendete/beobachtete Schutzpreise, Präzisionsquelle, Stand und offene Bestätigung in Positionsdetails. Keine pauschale Freigabe von Rundungsabweichungen.
- `webui/static/common.js`, `webui/static/pulsar.js`, `webui/static/universe.js`: bestehende PULSAR-/Positions-/Universumsdetails zeigen vorhandenen FMP-Firmenkontext, Tagesdaten, Cashflow, Schulden und jährlich datierte Kennzahlen. Jährliche Kennzahlen sind ausdrücklich keine heutige Bewertung. Nicht vergleichbare Jahre werden markiert. Keine strategischen Zusatz-Gates.
- `webui/static/pulsar.js`: nächste planmäßige Suche (lokale Anzeige mit Planungszeitzone), Anbieterabrufstatus (FMP ausreichend/Cache/Lücke/Pause), Fehler und vorhandener FMP-Kontext.
- `webui/static/ai_diagnostics.js`: tatsächlich übergebene Quellen, Datenstand, Kürzung und Paketversion aus neuen input_sources-Belegen; kein Beweis einer bestimmten Modellgewichtung. Ältere Aufrufe ohne Feldbeleg bleiben unbekannt.
- `webui/settings_store.py`, `webui/static/settings.js`, `webui/templates/settings.html`: Massive-Free-Zähler, vier lokale/fünf Anbieterabrufe pro Minute, nächster Zeitpunkt, letzter Versuch/Erfolg und Cache-Wiederverwendung über `massive_service.readonly_status`; keine DB-Erstellung oder API-Aufrufe durch diese Statusanzeige. Tageslimit 0 als „keine zusätzliche Tagesgrenze“ beschriftet. FMP-Nutzungsbelege werden nicht mit tatsächlicher Gesamtnutzung gleichgesetzt.

## Verifiziert

Finaler Lauf `implementation/test_runs/v101_webui_final_d4f8af6a`: **104 Tests bestanden**, keine Network Events, eine bestehende Starlette/AnyIO-DeprecationWarning. Enthält 25 neue WebUI-Tests und die v930-Ausführungsstatusregressionen. Positive Kaufchecks und konkrete Verkaufsgründe (Stop-Loss, Take-Profit, Trailing, ROI, manuell) werden jetzt gezielt erklärt; fehlende Prüfbelege nicht ergänzt. Grün ausschließlich bei vollständig ausgeführtem Auftrag, niemals für eine bloße Genehmigung. Der alte v930-Textvergleich wurde an die präzisere Unterscheidung „teilweise ausgeführt / Ausführung prüfen“ angepasst; zusätzlich prüft der tatsächliche JS-Renderer alle Ausführungszustände.

`implementation/test_runs/v101_webui_complete_b8a52d36`: 88 Tests bestanden, keine Network Events, eine bestehende Starlette/AnyIO-DeprecationWarning.

Gruppen: neue 18 Tests (Entscheidungserklärung, Screenshotfall, gemessene Null versus fehlende Messung, Genehmigung/Teilfüllung/Storno/Unknown, Risiko/Brokertrennung, Gebührenvollständigkeit, read-only API-Anreicherung, echter JS-Renderer mit Escaping/Details, Schutzidentität, FMP-Altbestand); v100 WebUI-Backend, v972 Brokerkontext, v980 WebUI, v814 WebUI/Diagnose.

Vorheriger Fokuslauf `v101_webui_37cc533d`: 39 bestanden. Zwischenzeitliche IndentationError-Regression im Universums-Exceptionpfad aus einer Texterweiterung wurde gefunden und behoben; `py_compile` sowie folgende 88 Tests grün.

Alle geänderten JavaScript-Dateien mit `node --check` geprüft. Tatsächliche Renderer wurden unter Node/vm ausgeführt; HTML-Injektion und unbekannte Werte getestet.

## Sichtprüfung / Grenzen

Echte Browser-Sichtprüfung für Desktop/Tablet/Smartphone ist offen: Browser meldet `net::ERR_BLOCKED_BY_CLIENT`, danach verbietet die URL-Policy weitere Versuche/Umgehungen. Keine Screenshots oder Pixelprüfung behaupten.

`implementation/ui_preview_v101/` enthält außerhalb des Releases eine reproduzierbare Offline-Prüfseite mit echten Templates/CSS/JS und fiktiven Daten; Iframes 390/820/1440 px. Keine Zugangsdaten, keine schreibenden HTTP-Aktionen, keine Brokeraufrufe. Generator `build_preview.py` kann nach einem Asset-Update erneut ausgeführt werden. Die Vorschau ist kein Nachweis der echten Geräte-Sichtprüfung.

Keine reale Ausführung, kein Broker-/GPT-Aufruf und kein produktiver Zustand wurden für diese Prüfungen verwendet. Nicht bekannte Fehlercodes bleiben mit ehrlicher allgemeiner Erklärung plus vollständigem technischem Detail sichtbar. Die API-Anreicherung verändert historische Entscheidungsdaten nicht.
