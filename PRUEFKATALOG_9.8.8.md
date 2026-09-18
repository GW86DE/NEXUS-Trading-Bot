# NEXUS 9.8.8 – Prüfkatalog und Herkunft der Änderungen

Arbeitsgrundlage: tatsächliches 9.8.7-ZIP, Diagnoseexport vom 11.09.2026 und
originale DOGE-Antworten. Frühere Befunde 9.5.7 bis 9.8.7 wurden auf vorhandene
Identitäts-, Restart-, Gebühren-, Schutz- und Installerverträge abgeglichen.
Es wird kein vollständiger Freqtrade-Rewrite und keine garantierte Broker-
Ausführung behauptet. PULSAR 1.2 und Handelsstrategien bleiben fachlich unverändert.

## Neue Gegenproben

`test_v988_accounting.py`: unabhängiges Ledgergate; Konto-/Modustrennung;
Reservierung versus letzte Sendefreigabe; Restnachweis und Kostenverteilung;
kein Staub aus ungeklärtem Abgang; defekte oder fremde Belege; echte
Mindestmengen/Quotefrische; Erhalt fremder Bestände; Original-Schutzanker;
Neustart zwischen DB und JSON; Gebühren-, Währungs-, Mengen- und Füllduplikate.

`test_v988_import_migration_ui.py`: rein lesende Vorschau; vollständiger
Original-Import und Wiederholung; bereits anderweitig belegter Abschluss;
falsches Konto/Umgebung/Order/Kinderorder; ZIP-Pfad- und JSON-Duplikatabwehr;
WAL-Sicherung; tatsächliche Ausführung der unveränderten produktiven SVG-Datei
in Node für Tages-/Summenansicht in Geld und Prozent. Kein Browser-E2E-Ersatz.

`test_v988_staging_and_guard.py`: echte Staging-Migration/Import/Promotion mit
simulierten OS-Schritten; kein Überschreiben der Quelldaten; Stopgrenze;
Operatorpause; falsche Kontobelege; vollständiger SQLite-Checkpoint vor
Verschieben; keine transiente SHM-Datei als Nutzerdaten; kontrollierter
Lesefehler und geschlossene DB-Verbindungen.

## Präzisierte frühere Tests – keine stillen Abschwächungen

- `test_v70_crypto_engine`: Broker-Fake besitzt jetzt ausdrückliches Testkonto
  und DEMO/LIVE. Fehlende Identität darf in der Produktion keine Freigabe sein.
- `test_v814_positionsbuch` und `test_v954_staub_nach_verkauf`: Die alte Erwartung,
  nach bloßer Kontobalance zu verkleinern/zu löschen, war Teil des Fehlers.
  Gegenproben verlangen jetzt Erhalt und beleggebundenen Abgleich. Positive
  Restabnahme erfolgt mit vollständiger Kauf-/Verkaufsbelegkette.
- `test_v910_geldpfad`: Quellprüfung endet an einem semantischen Abschnitt statt
  nach einer starren Zeichenanzahl. Beide Konfliktrichtungen bleiben geprüft.
- `test_v976_release_and_diagnose`: aktueller Versionswert und zusätzliche
  tatsächlich ausgeführte Accounting-Stagingphase; Operatorzustände unverändert.
- `test_v981_performance`: Nicht mehr den falschen `complete ? value : null`-
  Ausdruck verlangen. Tatsächliche JavaScript-Ausführung prüft bekannte
  Teilsumme, Markierung und getrennten unbekannten Nullfall.
- `test_v982_acceptance`: Ein aggregierter Verkaufsgebührenwert ohne originale
  Kauf-/Verkaufskette darf einen geschätzten Einstand nicht bestätigen. Der alte
  positive Kurzschluss wird jetzt abgelehnt; kompletter positiver, idempotenter
  Nachlauf mit Rohbelegen ist in den neuen Tests enthalten.

## Prüfung an Nutzerkopien

Preview darf die Originaldatei nicht ändern. Vier belegte Restzeilen werden
abgetrennt, ihre Elterngebühren/Kostenbasis proportional zugeordnet. Der echte
DOGE-Abschluss erhält tatsächliche Fillzeiten, native USD-Werte und winzigen
Rest. Alle anderen bestätigten Brutto-/Nettowerte bleiben identisch. Zweiter
Import/migrationslauf verändert keine Tradezeile. Die EUR-Risikobuchhaltung darf
native USD-Ergebnisse ohne historischen Wechselkurs nicht als EUR ausgeben.
Private Datenkopien und Zugangsdaten gehören nicht in dieses Quellpaket.

## Endabnahme

Alle sieben `volltest.py`-Gruppen müssen aus dem fertigen Quellstand und erneut
aus dem ausgelieferten ZIP laufen. Code-/Manifest-/Versionsgleichheit des
selbstenthaltenden Installers separat prüfen. Frühere Fehlversuche und deren
Ursachen im externen Prüfbericht nicht verschweigen. Linux x86_64 und Mock-
systemd sind keine Raspberry-Pi-/Safari-/reale Broker-POST-Abnahme.

## Gefundene Altlast in der Testisolation

Der alte Dienst-Routingtest ohne OKX startete einen echten, endlosen
Aktien-Universumsworker, der nach 60 Sekunden in fremde Testzustände lief.
Der Test benutzt jetzt nur für diesen sachfremden Researchworker einen
begrenzten Testdouble und prüft dessen Aufruf und Terminierung. Der echte
Supervisor und der Aktienstart werden weiterhin ausgeführt. Produktive
Worker-, Strategie- und Startlogik sind dafür nicht geändert worden.
Ein abgebrochener Diagnoselauf wurde nicht als erfolgreicher Volltest gezählt.

## Kalter Kontoalias-Cache im Schreibpfad

Der Volltest deckte zusaetzlich einen verschachtelten Schema-Schreibzugriff auf:
Die Alias-Suche innerhalb einer offenen Verkaufstransaktion oeffnete bei kaltem
Cache eine weitere initialisierende Verbindung. Aliase werden innerhalb solcher
Transaktionen jetzt auf derselben Verbindung gelesen. Zwei neue Gegenproben
verbieten die verschachtelte Initialisierung und trennen Caches verschiedener
Datenbanken. Die Mengen-/Preis-/Konto-Pruefungen werden nicht gelockert.
