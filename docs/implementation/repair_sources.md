# D18 / D22 – Quellenbelege und Fehlerwirkung

Arbeitsstand auf Basis NEXUS 10.1.0. Diese Arbeiten erzeugen keine neue
Versionsnummer und führen keine Broker- oder Provideraktionen aus.

## D18: Nasdaq-Teilbelege erreichen den Kaufverbraucher

`nasdaq_halt_feed.trading_evidence()` trennt nutzbare positive Haltzeilen von der
Vollständigkeit des RSS-Ausschnitts. Der bisherige strikte `parse()`-Vertrag bleibt
erhalten. Ein unvollständiger Feed verliert seine gültigen Zeilen nicht mehr.
`NewsBundle.source_coverage` enthält die Vollständigkeit und konkrete Zeilenfehler;
`sources_partial` weist Teilversorgung aus. Für vorhandene Verbraucher bleibt die
Warnung zusätzlich in `sources_failed` erhalten. Die Quelle wird bei Teilversorgung
nicht als vollständig erfolgreiche Quelle gezählt.

Die Diagnose vom 13.09.2026 enthält 23 strukturierte Zeilen: 8 gültige aktive
Haltbelege und 15 ältere Mehrtageshalte ohne nachgewiesen frischen Feedzeitpunkt.
`tests/fixtures/repair_nasdaq_diagnostic.json` enthält diese öffentlichen
Strukturbelege samt Herkunft und SHA256 des Diagnosearchivs. Der Test rekonstruiert
daraus RSS-Zeilen; die Original-RSS-Bytes lagen im Export nicht vor. Der Nachtest
belegt 8 nutzbare positive Zeilen und 15 weiterhin ungeklärte Zeilen.

Frische und Wiederaufnahme:

- Ein ausdrücklich alter oder in der Zukunft liegender Feedzeitpunkt wird nicht
  durch einen neuen Download zu einem aktuellen Haltbeleg.
- Ohne Feedzeitpunkt bleiben zeitlich gültige junge Haltzeilen nutzbar. Ältere
  Mehrtageszeilen benötigen einen aktuellen Feedzeitpunkt.
- Explizite Handelswiederaufnahme entfernt denselben Haltvorgang. Ein späterer
  neuer Halt bleibt erhalten. Eine Kursquotierungs-Wiederaufnahme allein gilt
  nicht als Handelswiederaufnahme.
- Markt- und Einzelwertabfrage teilen einen 60-Sekunden-Cache. Positive Belege
  verfallen spätestens nach 90 Sekunden; bekannte Wiederaufnahmezeitpunkte gelten
  auch innerhalb dieses Fensters. Der Nachrichtenfilter verwendet für Nasdaq
  höchstens 60 Sekunden statt des allgemeinen 30-Minuten-Caches.
- Strukturbelege werden nicht mit beliebigen ähnlichen Überschriften vermischt.
  Artikelobergrenzen entfernen keine aktiven Börsenhalte.

`Nachrichtenlage.aktiver_halt` kontrolliert positive amtliche Belege für genau das
betroffene Symbol einschließlich Beobachtungs- und Wiederaufnahmezeit. Dieser
Beleg blockiert einen neuen Kauf unabhängig von der konfigurierten allgemeinen
Nachrichtenpunkteschwelle. Das ist ein Ausführungsschutz, keine Änderung an der
Freqtrade-Strategie. Fehlende Symbole erzeugen keine Haltbestätigung und keine
Vollständigkeits-/Entwarnungsaussage. Bei einer unvollständigen Quelle lautet die
Zusammenfassung ausdrücklich nicht einfach „unauffällig“.

## D22: Aktuelle Wirkung und historische Fehler getrennt

`MultiSourceNews.health_snapshot()` erhält seine bisherigen Felder und ergänzt:

| Feld | Bedeutung |
| --- | --- |
| `error_at`, `error_age_seconds` | Zeitpunkt und Alter des letzten belegten Fehlers |
| `error_scope` | `CURRENT`, `HISTORICAL` oder `NONE` |
| `last_error_detail` | Letzter Fehler bleibt auch nach erfolgreichem Abruf nachvollziehbar |
| `current_effect` | `SOURCE_PARTIAL`, `SOURCE_PAUSED`, `SOURCE_UNAVAILABLE`, `UNKNOWN` oder `NONE` |
| `impact` | Deutsche Erklärung der aktuellen Auswirkung |
| `coverage` | Nasdaq-Vollständigkeit, nutzbare Zeilen, ungeklärte Gründe |

Ein alter Fehler ohne neuen Abruf bedeutet unbekannte aktuelle Verfügbarkeit,
nicht wiederhergestellte Gesundheit. Nach erfolgreichem Abruf bleibt der Fehler
als Historie sichtbar. Alte `FMP News`-Alias-Einträge bleiben getrennt von heutigen
FMP-Endpunkten. Das bestehende persistente GDELT-429-Backoff bleibt erhalten
(standardmäßig zwei Stunden, vorhandenes `Retry-After` wird respektiert). Ein neuer
Client und passive Statusabfragen führen während der Pause keinen Abruf aus.

`pulsar.research.social_source_status()` liefert denselben Zeit-/Wirkungsvertrag
für ApeWisdom und Tradestie aus der lokalen SQLite-Persistenz. TLS-Fehler führen
bei Socialabrufen zuerst zu einer Stunde Pause, bei Wiederholung exponentiell bis
maximal 24 Stunden. Der Status zeigt die fehlenden optionalen Stimmungsdaten und
den Weiterbetrieb anderer Quellen/Core. Pausen verbrauchen kein zusätzliches
Abfragebudget und verschieben den Zeitstempel des echten Fehlers nicht. Die
Zertifikatsprüfung bleibt aktiv. Bei Erfolg bleibt die Fehlerhistorie erhalten.

Massive-Limiter, FMP-Endpunktnutzung und GPT-Aufruffrequenzen wurden nicht verändert.

## Offline-Nachweis

Die Tests verwenden simulierte Providerantworten und die strukturierte
Nasdaq-Diagnosefixture. Das Projekt-Netzschutzsystem war aktiv.

- `sources_f7399867ce`: 76 Tests bestanden, keine Netzwerkversuche.
  Ziele: neue Quellentests, vorhandene Halt-Diagnosetests, v9.8.2-Akzeptanztests.
- `sources_regression_837f5434ff`: 116 Tests bestanden, keine Netzwerkversuche.
  Ziele: neue Quellentests sowie FMP-, Massive- und PULSAR-Abruf-/Flussregressionen.
- Der zweite Lauf enthält eine bestehende Starlette/AnyIO-DeprecationWarning;
  kein Testfehler.
- Abschließender gemeinsamer Lauf `sources_final_0533689c46`: **181 Tests
  bestanden**, keine Netzwerkversuche, eine bestehende Starlette/AnyIO-Warnung.
  Enthält zusätzlich den Ablauf von Nasdaq-Teilbelegen im Quellenstatus nach
  90 Sekunden. `result.json` und JUnit-Nachweis liegen im zugehörigen
  `implementation/test_runs`-Verzeichnis.

Die Nachweise prüfen unter anderem Wiederaufnahme während eines Cachefensters,
neues Einlesen nach 60 Sekunden, exakte Symboltrennung, veränderte News-Schwellen,
unvollständige Abdeckung, fehlende/stale Feedzeiten, Artikelgrenzen, persistente
GDELT-Pausen, TLS-Pausen, Budgets und Fehlerhistorie nach Wiederherstellung.

Nicht praktisch abgenommen sind tatsächliche Erreichbarkeit/TLS-Reparatur von
Tradestie, reale GDELT-Erholung oder die spätere Nasdaq-Vollständigkeit auf dem Pi.
