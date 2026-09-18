# NEXUS 10.1.0 – D04/D05 Evidenzpakete

## Geänderte Dateien

- `pulsar/ai_packet.py`
- `pulsar/news_normalization.py`
- `tests/test_v101_evidence_packet.py`
- `tests/fixtures/v101_evidence_packet.py`

Worker-Integrierung und Finanznormalisierung wurden vom übergeordneten Agenten durchgeführt. Dieser Teilauftrag hat `worker.py` nicht bearbeitet.

## Problem → Umsetzung

**D04:** Die Diagnose enthält für fünf PULSAR-Kandidaten jeweils drei Massive-Nachrichten mit `description`, `article_url`, `published_utc`. Der bisherige Worker las ausschließlich `text`, `url`, `publishedDate`; deshalb gingen bei allen 15 ausgewählten Nachrichten die vorhandenen Belege verloren.

`normalize_news(items, provider)` erzeugt reine tiefe Kopien. Bei Massive/Polygon ergänzt es kanonische Felder aus den vorhandenen Providerfeldern; bestehende nichtleere kanonische Werte haben Vorrang. Originalfelder inklusive Publisher-/Artikelmetadaten bleiben erhalten. Fehlende Werte werden nicht erfunden; eine Beschreibung wird nicht als vollständiger Artikel ausgegeben. Der Worker integriert den Helper bei seiner bestehenden Auswahl von drei Nachrichten und kürzt nur Titel/Text. Ursprüngliche Rohquellen bleiben unverändert.

`project()` kürzt Nachrichten ebenfalls gezielt: URL und Publikationszeit bleiben unverändert, Titel und Text können gekürzt werden; vollständige Nachrichtenobjekte werden nach Platz ausgewählt. Ein Beleg, dessen minimale Identität nicht passt, wird ausdrücklich als ausgelassen markiert. Leere Quellen bleiben leere Quellen.

**D05:** Die bisherigen 4.300-Byte-Ansichten enthielten nach generischer Präfix-/Schlüsselkürzung je Kandidat nur Kurstage 14.08., 04.09. und 08.09. und nur Felder `date`, `close`, `high`. Tatsächlich lagen Kurse bis 11.09. mit Volumen vor.

Die neue Projektion sortiert datierte Tageskerzen absteigend. Sie reserviert mindestens die neueste verfügbare vollständige OHLCV-Zeile, entfernt weitere Zeilen nur als Ganzes und schneidet niemals einzelne OHLCV-Felder für das Budget ab. Nicht vorhandenes Volumen bleibt nicht vorhanden; tatsächlich gemeldetes Volumen 0 bleibt 0. Nicht datierte Zeilen werden gezählt, nicht als aktuelle Kurse ausgegeben. Metadaten: `data_order`, `available_rows`, `included_rows`, `undated_rows`, `latest_available_date`, `as_of`; Paket-/Datenhash und `view_truncated` bleiben vorhanden. Wenn nicht einmal der Mindestbeleg ins Budget passt, wird die GPT-Ansicht blockiert.

**Finanz-Belegschutz:** Vorhandene `schema_version`, `symbol`, `symbol_identity_verified`, `available`, `currency`, `reason`, `errors` und `trend_period` werden bei Jahresfinanzdaten als vollständiger Mindestbeleg reserviert. Periodenlücken/Fehler können dadurch nicht mit den kürzbaren Kennzahldetails verschwinden. Der reale NBIS-Vergleich 2021→2025 bleibt im GPT-Paket ausdrücklich nicht vergleichbar.

Revision: `evidence-view-2`. Keine Order-, Broker- oder SQLite-Migration in diesem Teilauftrag. Alte GPT-Projektionscaches müssen wegen geänderter Evidenzansicht über Revision/Paketidentität getrennt werden; übergeordnete Integration aktualisiert zusätzlich die Analyse-/Paketrevision.

## Reale Replay-Belege

Fixture ausschließlich aus öffentlichen Markt-/Nachrichtenbelegen der Diagnose `NEXUS_10_Diagnose_2026-09-13_13-56-41_661552_UTC_9024084bae.zip`. Enthält fünf Originalpakete, die ausgewählten 15 Massive-Rohartikel und ursprüngliche Aufmerksamkeitsdaten. Keine Konten oder Zugangsdaten.

Die alte reine Projektionsfunktion wurde separat aus NEXUS 10.0.0 per AST geladen, ohne Anwendung/Worker zu importieren. Der aus den fünf Originalpaketen erneut berechnete gemeinsame GPT-Precheck-Hash stimmt exakt mit dem tatsächlich gespeicherten Request überein:

`7a37b86ee26da7b49fb4f2ae1a60d023d174cd74e1d5cfe49d92cf6a59f1443c`

| Kandidat | Alt: letzter GPT-Kurstag | Alt: Felder | Neu: erster/aktuellster GPT-Kurstag | Neu: Felder |
|---|---|---|---|---|
| MU | 2026-09-08 | date, close, high | 2026-09-11 | date, open, high, low, close, volume |
| SPY | 2026-09-08 | date, close, high | 2026-09-11 | date, open, high, low, close, volume |
| ORCL | 2026-09-08 | date, close, high | 2026-09-11 | date, open, high, low, close, volume |
| NVDA | 2026-09-08 | date, close, high | 2026-09-11 | date, open, high, low, close, volume |
| NBIS | 2026-09-08 | date, close, high | 2026-09-11 | date, open, high, low, close, volume |

Zusätzlich wird der tatsächliche integrierte `worker.build_card` für alle fünf Märkte getestet: alle 15 Nachrichten besitzen Beschreibungsauszug, identische URL und Publikationszeit; Rohquellen werden nicht verändert. Projektionsbudget bleibt jeweils ≤4.300 UTF-8-Bytes. Aktuelle Jahres-FMP-Normalisierung wird mit dem echten NBIS-Fixture bis zur GPT-Projektion geprüft.

## Tests

Letzter bestätigter isolierter Lauf:

`implementation/test_runs/v101-packet-integration-confirmed_a14293f2`

`94 bestanden, 0 fehlgeschlagen; network_events=false`.

| Gruppe | Ergebnis |
|---|---:|
| Neue Packet-/News-/Kerzen-/Finanzmetadaten-Regressionen | 27/27 |
| Neue FMP-Kontext-Regressionen | 16/16 |
| Bestehende V9.9.0-Research-/Display-Tests | 9/9 |
| Bestehende V9.8.5-PULSAR-Flow-/Seitentests | 15/15 |
| Bestehende V9.8.6-PULSAR-Research-Tests | 27/27 |

Command: `python3 implementation/run_isolated_tests.py --source implementation/TradingBot_v10.1.0_NEXUS --label v101-packet-integration-confirmed tests/test_v101_evidence_packet.py tests/test_v101_fmp_context.py tests/test_v990_research_display.py tests/test_v985_pulsar_flow_and_pages.py tests/test_v986_pulsar_research.py`.

Nachweise: `junit.xml`, `result.json`, `output.txt` im Laufverzeichnis. Eine bestehende Starlette/AnyIO-Deprecation-Warnung, kein Fehler.

Während der Testentwicklung wurden Fixtureannahmen berichtigt: ein Budget enthielt entgegen Testannahme noch beide Kerzen; kompaktierte Aufmerksamkeitsdaten enthielten keine Worker-Identitäten, deshalb echte vollständige Aufmerksamkeitszeilen ergänzt; Publisher-Metadaten gehören zur unveränderten Rohquelle, nicht zur bewusst kompakten Worker-Newsansicht. Diese anfänglichen Testfehler betrafen die Fixture-/Testannahmen, nicht neue Laufzeitregressionen. Der dokumentierte bestätigte Lauf umfasst alle Korrekturen und tatsächliche Worker-Integration.

## Grenzen

Kein realer GPT-, FMP-, Massive- oder Brokeraufruf; die Tests belegen Datenübergabe, Budgettreue und Verarbeitung der vorhandenen Diagnosequellen. Sie belegen keine bessere Marktprognose und keine Live-Handelsfreigabe. Sehr viele/übergroße verpflichtende Qualitätsbelege können den Precheck bewusst blockieren. Neuberechnete Ansichten können einen neuen regulären GPT-Request statt eines alten Caches auslösen; bestehende Budget-/Vorfilterregeln bleiben dafür zuständig.
