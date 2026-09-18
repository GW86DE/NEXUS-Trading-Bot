# PULSAR 1.2 in NEXUS 9.8.7

Stand: 11. September 2026. Diese Beschreibung ersetzt die älteren PULSAR-Anleitungen für den aktuellen Regelstand. Sie beschreibt Programmverhalten, keine vollständige Abnahme der Quellen auf Georgs Pi.

## Entdecken ist nicht Handeln

Eine neue Aktie benötigt keine 14 vergangenen Beobachtungstage, um sichtbar zu werden oder eine Quellen-/GPT-Recherche zu erhalten. Das war grundsätzlich schon in 9.8.6 möglich. In 9.8.7 wird es ausdrücklich angezeigt und im Ranking abgesichert: Einer der höchstens fünf Rechercheplätze kann einen neu erfassten Kandidaten mit mindestens 40 Erwähnungen und unbekanntem Vortagsvergleich aufnehmen. Die übrige Sortierung bevorzugt gemessene Aufmerksamkeitszuwächse mit Mindestbasis 20.

Fehlt `mentions_24h_ago`, bleibt der Anstieg unbekannt. Eine vom Anbieter ausdrücklich gemeldete Null ist eine andere Information; auch sie erzeugt keinen unendlichen Wachstumsscore. Ein neuer Rechercheplatz ist kein zusätzlicher Trade, keine Ausnahme vom Risiko und kein Beweis einer Push-Kampagne.

Der Belegscore bleibt 30 Punkte wirtschaftlicher Originalanlass, 25 Aufmerksamkeit, 20 Kurs/Volumen, 15 passende Finanzdaten und 10 Gegenprüfung. Mindestens 80 Punkte und alle Pflichtbelege bleiben Voraussetzung einer Nominierungsprüfung. Ohne die 25 Aufmerksamkeits-Punkte sind höchstens 75 bekannte Punkte vorhanden. Das wird nicht auf 100 umgerechnet und bleibt keine Handelsfreigabe.

## Historie ohne erfundene Ruhephasen

Die Quellen liefern Toplisten mit begrenzter Reichweite, keine vollständige tägliche Zählung sämtlicher Aktien. Ein erfolgreicher Abruf von `all-stocks` beweist nicht, dass ein abwesendes Instrument dort grundsätzlich erkannt werden konnte. `stocks` und `investing` besitzen jeweils eigene Bezugsräume. Deshalb erzeugt dieses Release keine Abwesenheits-Nullen, keine gemeinsame Sichtbarkeitsschwelle aus gemischten Filtern und keine rückwirkenden 19 stillen Tage.

Die Datenbank speichert je Quelle, Ticker und UTC-Stunde die letzte tatsächlich erhaltene Messung. Die Vergleichsbasis ist der Median dieser beobachteten Stundenwerte pro UTC-Datum und danach der Median der Tageswerte. Das Fenster umfasst die letzten 28 Tage ohne die letzten 24 Stunden. Mindestens 14 tatsächlich beobachtete UTC-Daten bleiben für die Aufmerksamkeitspunkte erforderlich. Zahlreiche Messungen am selben Datum zählen nicht als zusätzliche Tage.

Diese Statistik beschreibt **beobachtete Toplistenwerte**, nicht die nachgewiesene Aufmerksamkeit während ganzer Tage. Auswahlverzerrung durch die Topliste bleibt eine ausdrücklich benannte Grenze. Ohne eine belastbare Abdeckungsaussage eines Anbieters wäre eine vermeintlich korrigierte stille Vergangenheit unzulässig. Neue Datenzugänge können diesen Vertrag später erweitern; ein API-Schlüssel allein ersetzt die Abdeckungsprüfung nicht.

## Datenpflege auf dem Pi

Rohbeobachtungen werden nach sieben Tagen in kompakte Stundenstichproben überführt. Diese bleiben 35 Tage erhalten und decken das 28-Tage-Vergleichsfenster ab. Je Wartungslauf werden höchstens 5.000 Rohzeilen bearbeitet. Ein alter großer Bestand wird über mehrere Läufe abgebaut; währenddessen kann die Baseline weiterhin die vorhandenen echten Rohbeobachtungen lesen. Ein fehlgeschlagener Schreibvorgang rollt Verdichtung und Löschung gemeinsam zurück. Ungültige Belege innerhalb des Fensters bleiben erhalten und werden als Problem gemeldet, statt als Null zu zählen.

Auch während PULSAR `AUS` ist, darf diese lokale Wartung laufen; sie ruft keine Quelle auf. Alte abgelaufene Quellencaches werden begrenzt aufgeräumt. Assessments, Verbrauchsreservierungen, persönliche Freigaben, Wochenverbräuche und sämtliche Handels-/Brokerjournale bleiben erhalten. Daher gibt es bewusst **keine Garantie einer konstanten Gesamtdateigröße**. Freie SQLite-Seiten können wiederverwendet werden. Es wird im laufenden Bot kein platz- und sperrintensives `VACUUM` ausgelöst.

## Quellen und Agent

ApeWisdom bleibt der öffentliche Aufmerksamkeitsanschluss, Tradestie optional eine überlappende Ergänzung. Überlappende Erwähnungen werden nicht addiert. Ein Quellenfehler bleibt Fehler beziehungsweise Abrufpause; er erzeugt keinen stillen Beobachtungstag. Die Oberfläche nennt abgerufene Filter/Seiten und fehlgeschlagene Abrufe, nicht eine angeblich vollständige Reddit-Abdeckung.

Der bestehende begrenzte GPT-Agent bleibt erhalten: individuelle Luna-Vorauswahl, gezielte Webrecherche eines vorausgewählten Kandidaten, eigener Abruf unterstützter Originaldokumente, gebundene Ereignisprüfung, Terra-Vertiefung und separate Gegenprüfung. Nur tatsächlich vom Webwerkzeug gelieferte Quellen-URLs dürfen Originalabrufe auslösen. Freier Modelltext und Suchsnippets sind keine Originalbelege. SEC-/unterstützte Unternehmensmeldungen können Primärbelege liefern; FDA-Suchtreffer, externe IR-Sonderfälle und PDF-Originale werden nicht pauschal als vollständig angebunden ausgegeben.

Die bisherigen Grenzen bleiben bestehen: höchstens zwei neue Webrecherchen pro Tag und acht pro Woche, maximal zwei Suchwerkzeugaufrufe je Recherche. KI-Budget einschließlich Reservierungen: 1 USD täglich, 2 USD wöchentlich, 10 USD monatlich. Unklarer Verbrauch behält seine Reservierung. Modellpreise und Werkzeugpreisannahmen sind anwendungsseitige Budgetwerte, keine garantierte Anbieterrechnung. Ein neues Abo wird nicht abgeschlossen, ein Reddit-Zugang nicht vorausgesetzt oder umgangen.

## Community und Finanzdaten

„Community-Breite nicht prüfbar“ bedeutet: Geeignete Einzelbelege fehlen. Das ist kein negatives Messergebnis. „Geprüft, Kriterien nicht erfüllt“ setzt dagegen einen passenden Einzelbeleg voraus. Auch erfüllte Kriterien belegen Accounts und Stichprobenmerkmale, nicht automatisch unabhängige Menschen oder organisch entstandene Aufmerksamkeit. Aggregat- und SEC-Belege können keine Autorenbreite attestieren. Es ist kein eigener Reddit-/LunarCrush-/Stocktwits-Rohdatenadapter in diesem Release aktiviert.

USD-Mindestwerte für Kurs, Marktkapitalisierung und Handelsumsatz werden nur mit einem passenden USD-Unternehmensprofil verwendet. Ein anderes oder fehlendes Währungsfeld bleibt eine Qualifizierungslücke. SEC-Finanzdaten benötigen eine passende Ticker-/CIK-Zuordnung, die echte Einheit des ausgewählten Wertes und zueinander passende Start-/Enddaten für Gewinn und operativen Cashflow. Zukünftige oder unplausible Berichtsangaben werden nicht zugelassen. Unterschiedliche Perioden werden nicht durch bloßes Vorzeichenvergleichen passend gemacht.

## Freigaben und weiter bestehende Grenzen

Persönliche Telegram-Doppelbestätigung, eindeutige Kontobindung, zeitlich getrennte Bewertungen, Wochenlimit, Risiko-/Kosten-/Liquiditätsregeln und die erneute Prüfung durch den Handelskern bleiben unverändert. GPT erhält keine Orderrechte. Alte 1.1-Bewertungen zählen nicht als 1.2-Freigaben. Bestehende Trades, Wochenverbrauch und Budgetbuchungen werden deshalb nicht zurückgesetzt.

Die Oberfläche zeigt Recherchephasen wie Quellen, Marktdaten, Luna, Originalquellen und Terra. Bei einem langen blockierten Abruf wird kein künstlicher Heartbeat erzeugt, um eine Freigabefrische vorzutäuschen. Der anschließende echte Pi-Test muss Zugang, Antwortalter, Modellunterstützung und Kostenbelege der tatsächlich eingerichteten Anbieter prüfen. Fehlende Autoren, alte Finanzbelege oder unbekannte historische Tage sind durch lokale Tests nicht hergestellt worden.

### Offizielle Nachschlagestellen

ApeWisdom API: https://apewisdom.io/api/

ApeWisdom Methodik: https://apewisdom.io/methodology

SEC Company Facts: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

OpenAI Websuche: https://platform.openai.com/docs/guides/tools-web-search

SQLite-Dateipflege: https://www.sqlite.org/lang_vacuum.html
