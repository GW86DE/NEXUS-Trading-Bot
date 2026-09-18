# X Market Intelligence in NEXUS 10.1.3

X dient als zusätzliche Recherchequelle. Ein gemeinsamer Hintergrundsammler liefert
PULSAR und dem NEXUS-Core dieselben Daten. Die Funktion ist bei Installation ausgeschaltet.
Sie erzeugt keine Orders und verändert keine Kauf-, Verkaufs- oder Krisensperren.
Die Preisannahmen wurden am 14.09.2026 anhand der offiziellen X-Dokumentation geprüft.

## Kosten und Einrichtung

In der WebUI werden Bearer Token, maximal zwölf Fokusaktien und optional höchstens fünf
bevorzugte X-Accounts eingetragen. Vor Aktivierung werden die aktuellen Anbieterpreise und
das zusätzliche Ausgabenlimit in der X-Konsole bestätigt. Die Bestätigung verfällt nach
30 Tagen. Ändert X den Preis, muss die Preisbasis der Integration angepasst werden.
Eine Bestätigung allein ändert die hier festgelegten Stückpreise nicht.

Die offizielle Preisseite nennt 0,005 USD je Recent-Counts-Abfrage und 0,005 USD je
zurückgegebenem Post. Benutzererweiterungen, Streams, zusätzliche Profilabrufe und das
Vollarchiv werden nicht verwendet. Die von X beschriebene Deduplizierung wird nicht als
garantierte Kostenersparnis vorausgesetzt. [X-Preise](https://docs.x.com/x-api/getting-started/pricing)

| Sparplan, maximal | Pro 31 Tage | USD-Oberansatz | EUR-Reserveansatz |
|---|---:|---:|---:|
| 12 Aktien und 1 passende Markt-Kontrollquery, täglich | 403 Counts | 2,015 | 2,6195 |
| 3 Stichproben à höchstens 10 Posts täglich | 930 Post-Ressourcen | 4,65 | 6,045 |
| Gesamt | | 6,665 | **8,6645** |

Für die lokale Reservierung wird 1 USD vorsichtig mit 1 EUR bewertet, darauf kommen
30 Prozent Reserve für Umrechnung/Abgaben. Das ist keine Abrechnung oder Garantie eines
tatsächlichen Wechselkurses. Eine niedrigere nutzerseitige Grenze führt früher zur Pause;
die Software erlaubt höchstens **15 EUR je UTC-Kalendermonat**. Gebühren anderer X-Clients,
eines separaten Diagnoseskripts und GPT werden hier nicht mitgezählt. Das zusätzliche
X-Ausgabenlimit begrenzt das gesamte Entwicklerprojekt. Automatisches Aufladen dort nicht
als Ersatz für ein Ausgabenlimit verwenden.

Vor jeder Anfrage reserviert eine SQLite-Transaktion deren maximalen Preis. Bei Absturz,
Timeout, Fehlerantwort oder unbekannter Abrechnung bleibt die volle Summe gebunden.
Neustart, Tokenwechsel und Einstellungsänderung setzen diesen Zähler nicht zurück.
Es gibt keine automatische Suche mit weiteren Ergebnisseiten. Eine gestoppte oder
unvollständige Abfrage wird entsprechend gekennzeichnet.

## Messung und Verarbeitung

Counts erheben sechs vollständig abgeschlossene UTC-Tage innerhalb des zulässigen
Recent-Fensters. Identische Tage werden überschrieben, nicht addiert. Die längerfristige
28-Tage-Basis wächst aus diesen echten Beobachtungen; fehlende Tage werden niemals mit
Nullen aufgefüllt. Ein ausdrücklich gelieferter Nullwert ist dagegen ein echter Messwert.
Der 28-Tage-Quotient bleibt bis zu einer lückenlosen Basis unbekannt.
[Recent-Counts](https://docs.x.com/x-api/posts/get-count-of-recent-posts)

Jede Zeitreihe hat ihren eigenen Query-Hash. Für den Vergleich werden UTC-Wochentage und
Wochenenden getrennt; Feiertage werden in diesem ersten Messmodus nicht als eigener
Börsenkalender behauptet. Die Kontrollquery umfasst dieselben ausgewählten Cashtags
mit denselben Sprach-/Retweet-Regeln. Ihr Anteil ist ausdrücklich kein Anteil am
gesamten X-Verkehr. Änderungen der Symbolgruppe erzeugen eine andere Kontrollbasis.

Alle acht Stunden wird höchstens eine Suchstichprobe geladen: abwechselnd ausgewählte
Quellen, Makrothemen oder eine täglich rotierende Fokusaktie. Ohne ausgewählte Accounts
wird dieser Slot für eine Fokusaktie genutzt. Die Stichprobe liefert Text, Autor-ID,
Zeitpunkt, Cashtags und verlinkte Domains. Sie liefert weder vollständige Diskussions-
abdeckung noch einen Beweis für unabhängige Menschen. Es gibt keinen pauschalen X-Spike-
Score; dafür fehlen noch kalibrierte reale Beobachtungen.

Lokale, transparente Regeln gruppieren Themen wie Ergebnisse, Zölle, Sanktionen,
Zentralbanken, Ausfälle oder Cyberangriffe. Exakte/nahe Textduplikate, Autorenkonzentration
und Häufung in 30-Minuten-Fenstern werden als Stichprobenmerkmale gezählt. Daraus wird
weder „Botnetz“ noch „organisch“ abgeleitet. Gruppen verfallen nach 24 Stunden.

Die bestehende Luna-Vorprüfung bekommt lediglich abgeleitete Themen, Counts und
Beleghashes mit dem Status `UNCONFIRMED_RESEARCH_ONLY`. Es wird dadurch kein zusätzlicher
GPT-Aufruf eingeführt. Rohtexte gelangen nicht in `news_filter.marktlage()` oder
`global_crisis_score()`. Der Core erhält nur unbestätigte Hinweise zur Anzeige und
weiteren Forschung; automatische Risikoänderungen aufgrund von X sind ausgeschlossen.

## Diagnose und Datenschutz

`market_intelligence_status.json` enthält Abrufstatus, HTTP-Code, Query-/Antwort-Hashes,
gelieferte/verarbeitete Postzahlen, Duplikate, Budget und konsumierende Komponenten.
Damit sind „abgerufen“, „verarbeitet“ und „von PULSAR/Core gelesen“ unterscheidbar.
Die Datei enthält keine Posttexte oder Zugangsdaten. Auch der öffentliche API-Status
und die integrierte Diagnose enthalten keine Rohtexte und keine Autor-IDs.

Das Bearer Token liegt im bestehenden Credential Store: auf Linux Dateimodus 0600,
unter Windows nach dem dortigen DPAPI-Verfahren. Die getrennte X-Datenbank hat Modus 0600.
Rohposts werden spätestens sieben Tage nach ihrer letzten Erfassung entfernt. Post-
oder Autor-Löschungen entfernen Rohbelege und Zuordnungen; aktuelle Themen werden aus
den verbliebenen Posts neu berechnet. Tombstones verhindern erneutes Einlesen derselben
gelöschten Belege. Frühere PULSAR-Rechercheprotokolle können abgeleitete Kategorien,
Zähler und Hashes enthalten, aber keine von dieser Integration exportierten Rohtexte.

401/403 pausieren den X-Zugang, 429 setzt eine begrenzte Wartezeit, Netzwerkfehler werden
isoliert behandelt. Der Handels-Core läuft unabhängig weiter. Die Anzeige kennzeichnet
fehlende und veraltete Abdeckung; Budgetende bedeutet nicht „keine Nachrichten“.

## Abnahmestand

Offline getestet: Budgetkonkurrenz, Neustart, Fehlerfälle, geheime Werte, eingeschränkte
Endpunkte, Query-Versionen, Count-Abdeckung, Deduplizierung, zwei Symbolkonsumenten,
Löschen/Retention und fehlende Handelswirkung. Es wurden keine kostenpflichtigen
X-Abfragen durchgeführt. Ein echter Lauf nach bewusster Aktivierung muss Erreichbarkeit,
accountbezogene Berechtigung, Abdeckung und Anbieterrechnung noch bestätigen.
