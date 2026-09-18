# NEXUS 10.1.0 — FMP/PULSAR Implementierung und Verifikation

Status: implementiert, fokussierte isolierte Offline-Abnahme bestanden. Kein realer Provider-/GPT-/Brokeraufruf. Dateien im neuen Releasebaum, Baseline 10.0.0 und Originaldiagnosen unverändert.

## Umgesetzte Maßnahmen

| ID | Problem | Konkrete Änderung | Testbeleg |
|---|---|---|---|
| D04 | 15 ausgewählte Massive-Artikel verloren Beschreibung/Datum/URL | Pure Provider-Normalisierung vor Worker-Verdichtung; kanonische Felder und Publisher vorhanden, Originalquellen unverändert; Projektion schneidet keine URL/Zeitstempel ab | Fünf tatsächliche Worker-Marktpakete/15 Nachrichten |
| D05 | Luna erhielt alte Tage und verlor Volumen | Datierte OHLCV-Zeilen neueste zuerst; vollständige neueste Zeile reserviert, nur ganze alte Zeilen entfallen; as_of/Anzahl/Auslassung sichtbar, harte Bytegrenze | Original-Input-Hash der alten fünf Pakete reproduziert; neue Pakete alle 11.09.2026 mit OHLCV statt 08.09. ohne Volumen |
| D06 | NBIS FY2025/FY2021 unqualifizierter Wachstumstrend; Stable-EV/EBITDA verworfen | FMP-ANNUAL-2; ausschließlich benachbarte vergleichbare FY-Perioden für Jahresänderung; trend_period/Fehler erhalten; versioniertes Alias-Mapping; negative EV-Multiples nicht als günstige Bewertung | Original NBIS-/MU-Antworten; Alias-Widerspruch, falsche Währung, negative Multiple, Schema1-Revalidierung |
| D07 | Holdings-Timer übersprang gerade noch gültigen Context, bis zu1h fehlender gültiger Markt-Kontext | Eigener fälliger Context-Termin anhand Cache-expiry; unabhängig von Holdings; individuelle Komponentenstatus und Quellensymbole bei Fehlern | Virtuelle Uhr vor/bei/nach Cacheablauf, nur bei Fälligkeit Abruf; stale FX blendet frischen BTC-Kontext nicht aus |
| D11 Teil | Nächster PULSAR-Lauf/Modellinput nicht klar | Status.schedule mit letzter Discovery, nächstem erlaubten Termin, NY-Zeitfenster; successful stage.input_sources listet die tatsächlich im validierten Modellrequest enthaltenen Quellen/Datum/Hashes | Werktags-/Wochenend-/Freitagübergang; enthaltene/ausgelassene Inputquellen und echte Source-ID-Auflösung |
| D12 | Bereits bezahlte FMP-Fakten schlecht zugänglich; ETFs verschwenden Abschlussaufrufe | Additiver fmp_context in Karten und Jahreskontext in Universumsreferenz; mehrere vorhandene Verschuldungs-/Cashflow-/Kapitalrenditefelder; ETF/Fonds vor Unternehmensabfragen; keine neue Strategie/GPT-Callklasse | Echte normalisierte Fakten bis Karte/Packet; SPY/QQQ/Fonds erzeugen keine fünf Unternehmensendpunkte |
| D03 Integration | PULSAR konnte Massive parallel unnötig belasten und lokale15s-Pausen1h cachen | Mindestens3 aktuelle eindeutige vollständige FMP-News → Massive cache_only, sonst begrenzte Ergänzung; lokale MassivePaused übernimmt retry_after, ohne HTTP-Versuch actualBudget0; echter429 bleibt verbraucht | FMP-Genügend/Cachemiss/echterCache/Datenlücke; Duplikate/Future/Stale/Title-only; lokale15s-Pause |

## Dateien

- `fmp_data.py`: FMP-ANNUAL-2, jährliche Vergleichbarkeit, Stable-Feldmapping, `annual_context`, ETF/Fonds-Filter, eigenständige Context-Fälligkeit/-Komponenten.
- `fmp_reference.py`: vorhandener Universums-/Referenzcache enthält validierten Jahreskontext.
- `pulsar/worker.py`: Newsnormalisierung, FMP-first/Massive-Ergänzung, ETF-Frühfilter, Kontext-/Quellenstatus, Schedule, unabhängiger Context-Timer, Versionsschutz alter Retryjobs.
- `pulsar/ai_packet.py`: bounded dedicated OHLCV/News/Finanzansichten mit verpflichtendem Qualitätskontext.
- `pulsar/news_normalization.py`: neue reine Normalisierung mit erhaltenen Rohfeldern.
- `pulsar/analysis.py`: `pulsar-cited-reviews-v7`, explizite historischeFY-/Lückeninstruktion, `input_sources` als Eingabebeleg nach validierter Antwort.
- `pulsar/financials.py`: jüngste valide Jahresbelege in Schema1/2 weiterhin kompatibel; gefährliche Schema1-Trends werden nicht als neuer Kontext ausgegeben.
- `tests/test_v101_fmp_context.py`, `tests/v101_financial_fixture.py`.
- `tests/test_v101_evidence_packet.py`, `tests/fixtures/v101_evidence_packet.py`.

Zusammenarbeit: Massive-Client/Service durch anderen Agenten (`news(cache_only=True)`, `MassivePaused`), WebUI durch anderen Agenten. UI erhielt genaue additive Schemata direkt.

## Daten und Migration

Rohantworten, FMP-Cache, Research-Historie, alte GPT-Antworten und alte Retryjobs werden nicht gelöscht. Neue Financials werden aus vorhandenen roh gecachten Antworten erneut normalisiert; abgeleitete Struktur jetzt FMP-ANNUAL-2. Cache-only-Leser kennzeichnen Schema1-Jahresableitungen als revalidierungsbedürftig statt sie still weiterzuverwenden. Normale folgende FMP-Verarbeitung kann sie ersetzen, ohne Rohbelege zu verwerfen. Bestehende gültige reine Finanzbelegverwendung des jüngsten Jahres bleibt kompatibel.

GPT-Cacheidentität enthält `pulsar-cited-reviews-v7`, Quellenpacket `pulsar-evidence-2`, Modellansicht `evidence-view-2`. Alte erfolgreiche Analysen bleiben Historie; sie werden nicht fälschlich als Analyse neuer Inputs ausgegeben. Alte persistierte Text-Retryjobs ohne aktuelle Review-Version bleiben erhalten, starten aber keinen kostenpflichtigen Request mit veralteten Inputs. Regulärer Zyklus erstellt neue validierte Inputs. Keine zusätzliche GPT-Aufrufschleife, bestehende Budgets und maximaler vertiefter Kandidat bleiben bestehen.

`input_sources` bedeutet nur: in der Eingabe eines erfolgreich validierten Modellrequests enthalten. Es behauptet weder, dass das Modell jedes Feld semantisch nutzte, noch dass diese Information den Handel verbesserte. Bestehende spezielle FMP-Wirkungszähler bleiben unverändert.

## Abschließender fokussierter Testlauf

`implementation/test_runs/v101-fmp-final_58189f54/`

**223/223 bestanden, 2,47s, keine Netzwerkereignisse.** `junit.xml`, `result.json`, `output.txt` enthalten die tatsächlichen Ergebnisse. Ein bestehender Starlette/AnyIO-DeprecationWarning, keine Produktausnahme.

Ausgeführt über `implementation/run_isolated_tests.py` mit eigener kopierter Sourceprojektion, separatem Zustand und Netzwerk-Sperre:

- Neue FMP-/Context-/Massive-Integrationsfälle (test_v101_fmp_context.py).
- Neue Originaldaten-/Packet-/Worker-Integrationsreplays (test_v101_evidence_packet.py).
- Bestehende v989-FMP-Tests.
- Bestehende v100-Strategy/PULSAR-Tests.
- Bestehende v990FIX1-Recovery-Tests.
- Bestehende v985-PULSAR-Seiten-/Flow-Tests.
- Bestehende v986-PULSAR-Research-Tests.
- Bestehende v987-PULSAR-Qualitätstests.
- Bestehende fix3-PULSAR-Timeout-Tests.

Vorheriger Integrationslauf fand eine bereits parallel behobene WebUI-Einrückungsregression. Nach Hinweis an den UI-Agenten und Korrektur besteht der obige vollständige fokussierte Lauf. Keine Ausblendung fehlgeschlagener Tests.

## Grenzen

Dies ist keine Livefreigabe. Keine zusätzliche API-Berechtigung geprüft, keine neuen Providerdaten abgerufen, keine realen GPT-Antworten der neuen Pakete produziert. Der Umfang ist deterministisch offline mit echten früheren Antworten und Fakes verifiziert. Root führt anschließend die gesamte Release-Suite und Artefaktprüfung aus. FMP-Planlimits und Broker-/Order-/P&L-/Strategieentscheidungen wurden durch diese Änderungen nicht umgestellt.
# Ergänzung: unabhängiger Zeitreview vor Paketierung

- Brokerreview fand eine neu entstandene FMP-Kontextregression: `refresh_market_context` prüfte einen Kurszeitstempel gegen den Zeitpunkt vor HTTP. Ein während der Anfrage neu erzeugter legitimer Quote konnte dadurch als zukünftig abgelehnt werden.
- Gezielt behoben: Kursfrische wird direkt nach Empfang gegen `time.time()` geprüft; der getrennte Startzeitpunkt für Scheduler/Cachefälligkeit bleibt bestehen. Keine Provider-, Strategie- oder Intervalländerung.
- Neuer deterministischer Test mit zwei Sekunden Zeitvorschub je Quote beweist Annahme neuer Antworten sowie weiterhin Ablehnung von 901 Sekunden alten bzw. wirklich zukünftig datierten Antworten.
- Isolierter Lauf `implementation/test_runs/v101-fmp-receipt-clock_ab341532`: **68/68 bestanden**, 0,68 Sekunden, `network_events=false` (24 Kontextfälle + 44 bestehende FMP-Fälle). Nur `fmp_data.py` und `tests/test_v101_fmp_context.py` wurden für diesen Fix verändert.
