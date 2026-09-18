# NEXUS 10.1.0 – Umsetzungsbericht

**Ergebnis:** gezielte, getestete Codekorrekturen gegenüber NEXUS 10.0.0 sowie das zusätzlich gewünschte verständliche Logbuch. Zwei historische eToro-Freigabeblocker bleiben mangels Originalbelegen offen. **Uneingeschränkte Live-Releaseentscheidung: NEIN.** Das neue Paket unterstützt das kontrollierte Demo/Paper-Update und die anschließende passive 30-Minuten-Diagnose.

## Grundlage und Nachweiskette

Verbindliche Grundlage war der tatsächliche NEXUS-10.0.0-Code sowie die neue Diagnose `NEXUS_10_Diagnose_2026-09-13_13-56-41_661552_UTC_9024084bae.zip` des Collectors1.0.1 und der daraus erstellte Maßnahmenkatalog. Botversion 10.0.0, Diagnoseversion 1.0.1 und neue Botversion 10.1.0 werden ausdrücklich getrennt.

Die unveränderte Ausgangsversion bestand 2371 Tests und 244 Untertests. Alle Änderungen wurden in einem neuen Quellordner entwickelt. Geld-/Brokerpfade, PULSAR/FMP, Massive, Kerzen, WebUI und Migration wurden fokussiert geprüft und anschließend gemeinsam getestet. Endgültige Zahlen, Gruppen und Grenzen stehen im TEST_REPORT.md; maschinenlesbare Belege in validation/.

## Endkontrolle des freigegebenen Maßnahmenkatalogs

✅ bedeutet hier: implementiertes Verhalten durch Offline-Tests bestätigt. Eine reale Broker-/Pi-Abnahme wird damit nicht behauptet.

| Maßnahme | Status | Tatsächlich umgesetzt | Verbleibende Grenze |
|---|---|---|---|
| D01 Risikobasis | 🟠 teilweise umgesetzt | Reine Reviewanzeige, alle lokalen Blockaden, gesicherte idempotente Metadatenmigration bei unabhängig identischem Checkpoint | Der alte eToro-Kontobezug/Tagesbeleg fehlt; Originalfall bleibt gesperrt |
| D02 Schutzpreise | 🟠 teilweise umgesetzt | Regelgebundene Normalisierung, Requested/Sent/Observed, Mengen-/Flag-/Identitätskontrolle, persistentes PATCH-Recovery | Echte PEP-Rundungsregel fehlt; keine angenommene Freigabe |
| D03 Massive Free | ✅ umgesetzt und getestet | Vier Abrufe/rollierenden 60 s, ≥15 s Abstand, gemeinsame Reservierung vor HTTP, Cache, 429/Retry-After, Prozess-/Neustart-/Uhrschutz, begrenzte Antworten | Fremde Key-Nutzung wird erst durch Anbieterantworten sichtbar |
| D04 Massive-News | ✅ umgesetzt und getestet | Beschreibung, Original-URL und Zeitpunkt normalisiert und bis ins Quellenpaket erhalten | Fehlende Providerfelder werden nicht ergänzt |
| D05 GPT-Paket | ✅ umgesetzt und getestet | Neueste vollständige OHLCV-Zeilen mit Volumen zuerst, verpflichtende Fehler-/Datumsbelege, harte Bytegrenzen, neue Cacheidentität | Neue echte Modellantworten erst nach regulärem Lauf |
| D06 FMP-Finanzdaten | ✅ umgesetzt und getestet | FY-Lücken blockieren unzulässige Jahresvergleiche, Stable-Feldalias-Mapping, datierte Bewertungs-/Finanzkennzahlen | NBIS-Quellkonflikt bleibt sichtbar; keine erfundene Bilanzkorrektur |
| D07 Markt-Kontext | ✅ umgesetzt und getestet | Eigenständige Fälligkeit, Komponentenstatus, Empfangszeit für Frischeprüfung | Externe Datenverfügbarkeit bleibt providerabhängig |
| D08 OKX-Kerzen | 🟠 teilweise umgesetzt | Je Instrument/Umgebung: Aktualität, Aktivität, Nullvolumen, synthetische Lücken und begrenzte Rohbelege; strikter Parser | Ursache der alten flachen DEMO-Reihen ohne alte Rohantworten nicht endgültig beweisbar |
| D09 Zeitraster | ✅ umgesetzt und getestet | Sample 5m, Standardkonfiguration sowie Start-/Setup-/Bereitschaftstexte konsistent; Sicherheits-Anlaufsperre separat | Schutzwartezeit bleibt absichtlich bestehen |
| D10 Statuswahrheit | 🟡 umgesetzt, aber nicht vollständig validiert | Risiko neben Marktöffnung, Quellenalias-Historie, Mengen-/Gebührenstatus, Schutzbelege, GPT-Eingabequellen und Massive-Zähler | Browser-Sichtprüfung auf drei Geräteklassen offen |
| D11 externe Quellen | 🟠 teilweise umgesetzt | Nasdaq-Zeilenklassifizierung mit gültigen Teilbelegen und ausdrücklicher Zeit-/Vollständigkeitsgrenze; vorhandene Pausen erhalten | GDELT-/Tradestie-Erreichbarkeit/Zertifikat und ursprüngliches Nasdaq-Rohformat nicht real geprüft |
| D12 FMP-Nutzwert | ✅ umgesetzt und getestet | Vorhandene Jahres-/Tages-/Cashflow-/Schuldendaten im Research und in Details, ETF-Frühfilter, FMP-News zuerst | Keine neue automatische Entry-/Exit-Strategie daraus |
| D13 Diagnose | ✅ umgesetzt und getestet | Tool 1.1.0: 30 Minuten Standard, Sofortmodus klar beschriftet, eindeutige ZIP, effiziente unveränderte PULSAR-Pakete, neue Belege, unbekannt statt falscher Null | Reale 30-Minuten-Abnahme nach Installation erforderlich |
| D14 deutsches Logbuch | ✅ umgesetzt und getestet | Hervorgehobener einfacher Satz aus gespeicherten Fakten, genaue Kauf-/Verkaufsgründe, aufklappbare Technik, kein zusätzlicher GPT-Aufruf | Nicht klassifizierbare Altfälle erhalten ehrlichen Hinweis |
| D15 zusätzliche Migration | ✅ umgesetzt und getestet | Fremde Zielkonten/Settings und SQLite-Sidecars abweisen, keine Trade-DB aufgrund leerer decisions-Tabelle überschreiben, neue Zustände mitnehmen | Absichtliches Mischen verschiedener Kontostände bleibt ausgeschlossen |

## Funktionale Änderungen

**Massive:** Der Free-Plan wird auf Ebene aller NEXUS-Prozesse begrenzt. Allgemeine News, PULSAR und Verbindungstest besitzen kein getrenntes unkoordiniertes Minutenbudget mehr. Fehlender Slot erzeugt eine datierte lokale Pause; die Handelsschleife wartet nicht minutenlang. Identische erfolgreiche Abfragen werden wiederverwendet. Ein Neustart vergisst weder Versuch noch 429-Pause. FMP liefert vorrangig vorhandene aktuelle Nachrichten; Massive ergänzt nur eine belegte Lücke oder liefert Cache.

**FMP/PULSAR/GPT:** Die bisherigen Daten werden inhaltlich besser genutzt. Der exakt rekonstruierte alte Precheck-Hash zeigte Kurse nur bis 08.09.2026, obwohl der 11.09. vorlag; neue Pakete enthalten den jüngsten Tag samt Volumen. Alle 15 betroffenen Massive-Artikel behalten Beschreibung/URL/Zeit. NBIS 2021→2025 wird nicht als jährliche Wachstumsrate behandelt. MU-EV/EBITDA und weitere vorhandene Kennzahlen werden korrekt zugeordnet. Jahresdaten bleiben ausdrücklich Jahresdaten, keine heutigen Bewertungswerte. ETF/Fonds erhalten keine sinnlosen fünf Unternehmensabschlussabfragen. Dokumentierte Inputquellen beweisen ihre Übergabe an das Modell, nicht welche Gewichtung das Modell ihnen gab.

**Broker/Sicherheit:** Risiko- und Schutzprüfung werden genauer belegbar. Ein Timeout bei Schutz-PATCH führt zu einer offenen Bestätigung statt erneutem Versand. Herkunft, Konto, Umgebung, Positions-ID, Menge und Schutzflags bleiben zwingend. Bei PEP ist die ursprüngliche Rundungsvermutung nicht belegt: 135,8946→135,90 ist kein gewöhnliches kaufmännisches Runden auf zwei Stellen. Der Trade wird deshalb nicht voreilig entsperrt. OKX wird durch eine lokale eToro-Prüfung nicht gesperrt.

**Kerzen/Strategie:** Keine Regel wurde gelockert, um Käufe zu erzeugen. Die neun Originalentscheidungen bleiben in Indikatoren, Kerzenschluss und Ergebnis reproduzierbar. Aktuelle Ticker können gleichzeitig mit flachen historischen Kerzen auftreten; diese verschiedenen Aussagen sind jetzt erkennbar. Sample nutzt5m für den Signalstatus; die konfigurierte Sicherheitswartezeit bleibt separat.

## WebUI – sichtbare Änderungen im vorhandenen Design

- **Logbuch:** Der deutsche Hauptgrund ist hervorgehoben. Beispiel: „Kein Kauf, weil für die geprüfte Kerze kein Handelsvolumen gemeldet wurde und die erforderliche Kurserholung noch nicht bestätigt ist.“ Messwerte und ausführliche Begründung sind aufklappbar. Genehmigt, teilweise ausgeführt, vollständig ausgeführt und unklar bleiben unterscheidbar. Grün steht für belegten vollständigen Fill.
- **Dashboard/Brokerdetails:** Alle gemeldeten Kaufhindernisse, insbesondere Risikobasis plus Markt geschlossen. Kerzenaktivität und Signaltakt werden getrennt von Verbindungsstatus und Sicherheitswartezeit gezeigt.
- **Positionen:** Gewünschter, gegebenenfalls normalisierter, gesendeter und beobachteter Schutz mit Herkunft und offener Bestätigung. Bestehender FMP-Unternehmenskontext nur, wenn tatsächlich gespeichert.
- **PULSAR/GPT:** Nächster planmäßiger Lauf, Provider-/Cache-/Pausegründe, Datenstand und tatsächlich übergebene Quellen. Kein künstlicher GPT-Aufruf nur für eine grüne Anzeige.
- **Universum:** Vorhandene FMP-Finanzdetails und Kerzenqualitätswarnungen ohne neue automatische Handelsregel.
- **Quellen/Einstellungen:** Massive-Minutenbudget, letzter Versuch/Erfolg, nächster Slot und Cache. Historischer FMP-News-Fehler wird von aktuellem FMP-Status getrennt. Tageslimit0 bedeutet keine zusätzliche lokale Tagesgrenze.
- **Ausführungsdetails/Performance:** Vollständige Mengenbuchung bedeutet nicht automatisch vollständige Gebühren oder korrektes bekanntes Nettoergebnis.

Branding, Logo, Grundnavigation und Farbwelt bleiben erhalten. Der vorhandene Panelstil wird ergänzt. Keine fremden Templates, keine neue Dashboardbibliothek, keine neue Hauptseite, kein neuer Microservice. Browser- und Touch-Abnahme bleibt ausdrücklich offen.

## Dateien, Risiken und Testzuordnung

Die detaillierten Teilberichte unter docs/implementation/ listen für jede Modulgruppe die konkrete Ursache, alte/neue Umsetzung, betroffene Dateien, Verhaltenstests, Zwischenergebnisse und verbleibende Risiken. SOURCE_CHANGESET.json vergleicht alle Programmdateien mit dem 10.0.0-Baseline-Manifest und prüft Bytegleichheit zum final getesteten Stand. Dadurch ist jede Paketänderung nachvollziehbar.

Wichtigste neue Module: massive_service.py, candle_observation.py, risk_basis_review.py, etoro_protection_evidence.py, etoro_protection_journal.py, decision_explanation.py, pulsar/news_normalization.py. Bestehende Broker-, Worker-, FMP-, UI- und Migrationsmodule wurden gezielt angepasst; Details siehe Teilberichte.

## Erkannte Regressionen und ihre Behandlung

- Zwischenzeitlicher Einrückungsfehler im Universums-Ausnahmepfad nach UI-Ergänzung: korrigiert, Kompilierung und folgende UI-/PULSAR-Tests grün.
- FMP-Frischeprüfung verwendete Zeitpunkt vor HTTP und konnte eine während der Anfrage aktualisierte Quote verwerfen: gegen Empfangszeit geprüft, mit virtueller Zeitverschiebung sowie stale/future-Fällen getestet.
- Ein stiller Diagnose-Schreibfehlerpfad verletzte die Release-Hygiene: auf begrenzte Debugmeldung ohne sensible Transportdaten geändert; Handelsverhalten bleibt unabhängig.
- Diagnose mit fehlender Massive-/FMP-Tabelle durfte nicht 0 Abrufe melden: jetzt unbekannt, gekürzte Exporte ausdrücklich Untergrenze; Verhaltenstest und unabhängiger Replay.
- Zwei schon vor 10.1 vorhandene Migrationsfehler wurden durch zusätzliche Prüfung reproduziert und mit 21 zusätzlichen P0-Fällen abgesichert.
- Versionsgebundene Release-Testannahmen wurden auf 10.1.0 aktualisiert; Schutz des vorhandenen 10.0.0-Ordners wird mitgeprüft. Alter Logbuch-Literaltest wurde an präzisere Zustände angepasst und durch echte JS-Zustandstests ergänzt.
- Massive-Crossreview ergänzte Schutz gegen Vorwärtssprünge der Uhr sowie Größen-/Laufzeitgrenzen für HTTP-Antworten.

## Bewusst nicht übernommen

Keine automatische neue Risikobasis ohne Kontobeleg; keine hypothetische PEP-Preisregel; keine pauschale Toleranzerweiterung; keine 0-Euro-Gebühren für unbekannte Kosten; keine Schwächung von Volumen/Stop/TakeProfit; keine Live-Kurse still in DEMO; keine neuen bezahlten FMP-Endpunkte ohne Berechtigung; kein GPT-Aufruf pro Asset; keine komplette UI-Neugestaltung. Verwendet wurden die bereits bewerteten Konzepte gemeinsamer Quoten-/Cacheverwaltung, expliziter Zustände, nachweisgebundener Normalisierung und Detailansichten im NEXUS-Stil.

## Nächster Nachweis

Nach dem kontrollierten Update Diagnose 1.1.0 ab Start 30 Minuten laufen lassen. Sie erfasst neue Massive-Versuche/Pausen, FMP-/PULSAR-Fortschritt, tatsächlich dokumentierte GPT-Verarbeitung, Kerzenbelege, Brokerblockaden und Systemzustand. Ein aufgrund Zeitfenster/Cache/Vorfilter ausgebliebener GPT-Request ist kein automatischer Fehler. Die anschließende ZIP erlaubt die reale Nachkontrolle; die aktuell fehlenden historischen/vertraglichen Brokerbelege ersetzt sie nicht automatisch.
