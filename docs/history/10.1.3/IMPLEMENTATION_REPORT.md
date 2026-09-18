# NEXUS 10.1.3 – Umsetzung

Stand 14.09.2026. Grundlage: NEXUS 10.1.2, die PEP-Abschlussbelege, die Diagnose vom 14.09.2026 sowie beide X-Konzept-/Prüfdokumente. Auftrag: eToro/PEP zuerst, danach begrenzte X-Recherche und nachvollziehbare WebUI-/Diagnoseanzeigen.

## PEP: Position und Schließauftrag auseinanderhalten

Ein Positions-Historienbeleg ohne Schließorder-ID konnte bisher den alten Schließauftrag fälschlich als ausgeführt markieren. Das neue `confirm_from_fill` verlangt eine tatsächlich passende nichtleere Order-ID. Konto, Umgebung, Instrument und Position werden mitgeführt. Generisches `orderId` aus der Positionshistorie ist kein Nachweis der Schließorder.

Ein unabhängiger vollständiger Positionsabschluss kann den alten Zustand zu `POSITION_CLOSED_ORDER_UNPROVEN` fortschreiben. Dafür müssen Depot und Historie frisch, vollständig und identisch zugeordnet sein, die Position im Depot fehlen und passende ausgeführte Mengen/Preise/Zeitpunkte vorliegen. Der vorherige Zustand bleibt im Audit. Die Mengen des Positionsabschlusses erscheinen nicht als künstliche Ausführungen des alten Auftrags. Beide Datenbankprojektionen werden nach Unterbrechung erneut abgeglichen. Eine doppelte Schließorder für diese Position wird verhindert. Numerisches `statusID=7` wird nicht als Stornierung geraten. Ein Kurs nahe SL oder TP wird nur als Hinweis gespeichert; der tatsächliche Auslöser wird erst durch einen ausdrücklichen Brokergrund bestätigt.

## Kaufprüfung und Gebühren

Die historische Ergebnisperiode wird anhand ihres ursprünglichen Archivs und Kontobelegs abgegrenzt. Alle alten Beträge, Zähler und unbekannten Ergebnisse bleiben erhalten. Der tatsächliche BUY-Prüfgrund und die Zahl aktueller/historischer ungeklärter Ergebnisse werden auch im Dashboard veröffentlicht. Ein grüner Verbindungsstatus bedeutet dadurch nicht automatisch Kaufbereitschaft.

v2-Einstiegsbelege werden direkt verarbeitet; ein v1-Fallback erzeugt keine irreführende v2-Identitätsfehlermeldung mehr. Vollständige native v2-Abschlusskosten können ein Ergebnis nachträglich bestätigen, wenn tatsächliche Order, Position, Konto, Währung, vollständige Menge und Ausführungen zusammenpassen. Die Kostenprojektion ist protokolliert und wiederholbar. Schon im Einstiegskurs enthaltene Marktspreads werden nicht erneut abgezogen.

**Die Zuordnungslogik ist korrigiert. Die Übernahme auf dem Pi erfolgt erst nach frischem vollständigem Brokerabgleich; für ein bestätigtes PEP-Nettoergebnis fehlt weiterhin der tatsächliche Abschlusskostenbeleg.** Die Software darf diese Lücke nicht durch einen geschätzten Nullbetrag schließen. Details in KNOWN_ISSUES.md.

### Was die gelieferten PEP-Belege tatsächlich zeigen

Der native Positionsbeleg nennt für Position 3597440106 einen vollständigen Abschluss am 14.09.2026 um 13:32:26,867 UTC: 109 Stück zu 138,59 USD, Bruttoergebnis 244,16 USD. Die tatsächliche Schließorder-ID fehlt. Das generische `orderId=380127623` ist die Einstiegsorder. Der alte Sonntagsauftrag 380995258 hat im neueren Abruf keine Ausführungen und liefert keinen nutzbaren Abschlusskostenbeleg. Seine Stornierung wird deshalb nicht allein aus der Statusnummer abgeleitet.

Im isolierten Replay korrigiert die neue Logik den alten falschen Zustand bei simuliert frisch erneut gelesener Historie genau einmal und wiederholbar. Die archivierte Historie selbst ist für den späteren Depotabruf zu alt und wird zurecht zurückgewiesen. Die sechs historischen Kostenlücken können anhand der hochgeladenen Dateien noch nicht endgültig abgegrenzt werden, da das ursprüngliche Risikoarchiv und der zugehörige Periodenbeleg nur auf dem Pi erwartet werden. Fehlen diese Dateien auch dort, bleiben alle sieben Ergebnisse konservativ in der Kaufprüfung. Die Diagnose exportiert jetzt gezielt diese referenzierten Originalbelege.

## Ausstiege außerhalb der regulären Sitzung

Die Positionsüberwachung verwendet frische Brokerkurse und den Broker-Handelbarkeitszustand. Kaufpause und US-Zeitfenster sind keine pauschale Verkaufssperre. Schutz-/Risikoausstiege werden vor einer möglicherweise fehlenden historischen Signalkerze geprüft. Ein nachgewiesener nativer Broker-Schutz bleibt brokerseitig; NEXUS erzeugt keinen konkurrierenden zweiten Schutzverkauf.

Explizite softwareseitige Gewinnmitnahmen prüfen aktuellen Verkaufskurs, belegte anteilige Einstiegskosten, aktuelle belegte Verkaufskostenschätzung und Ausführungspuffer. Fehlende oder veraltete Werte bleiben unbekannt. Sicherheitsausstiege und technische Risikoausstiege warten nicht auf positive Nettogewinne. Vorhandene native TP-/SL-Preise bleiben unverändert; es wird kein neues 1-%-Ziel eingeführt. Native TP-Auslösung bleibt unabhängig von einer Software-Kostenprüfung.

## X für NEXUS und PULSAR

Ein gemeinsamer Hintergrundsammler mit persistenten Reservierungen verhindert konkurrierende Ausgaben. Standard: ausgeschaltet. Nach Einrichtung höchstens zwölf Aktien plus ein gemeinsamer Kontrollwert täglich und drei Suchstichproben mit je höchstens zehn Posts pro Tag. Keine bezahlten Nutzererweiterungen, keine Suche im vollständigen Archiv, kein unkontrolliertes Paging. Fehlversuche werden vorsichtig mit dem reservierten Höchstbetrag bewertet. Timeouts, Authentifizierungsfehler und Ratengrenzen führen zu Pausen; der Handel läuft ohne X weiter.

Veröffentlichter Preis am 14.09.2026: 0,005 USD je Recent-Counts-Anfrage, 0,005 USD je geliefertem Post. Maximaler geplanter 31-Tage-Monat: (13 × 31 × 0,005 + 3 × 10 × 31 × 0,005) × 1,30 = **8,6645 EUR** bei vorsichtiger Bewertung USD 1 = EUR 1. Einstellbare lokale Grenze höchstens 15 EUR. Nicht mit einem garantierten Anbieterrechnungsbetrag verwechseln. Quelle: [X-Preise](https://docs.x.com/x-api/getting-started/pricing).

Counts unterscheiden vollständige Tage, Teilbelege, Fehler und fehlende Abfragen. 28-Tage-Basis wächst aus echten Beobachtungen; Suchänderungen haben andere Prüfsummen und Vergleichsgruppen. Kontrollnormalisierung bleibt ein Proxy innerhalb der gewählten Aktien. Posts werden nach ID/Ähnlichkeit zugeordnet; mehrere Suchkontexte bleiben erhalten. Themenhinweise haben Ablaufzeiten und bleiben unbestätigt. Rohtexte fließen nicht in die bestehende Schlagwort-Krisensperre. X kann weder Orders noch Universumsfreigaben erzeugen.

PULSAR erhält abgeleitete Themen-/Aufmerksamkeitsdaten im bestehenden Luna-Paket. Standardmäßig entsteht kein zusätzlicher GPT-Aufruf. Luna kostet in der veröffentlichten Standardtabelle 0,20 USD je Million Eingabetoken und 1,20 USD je Million Ausgabetoken; Websuche zusätzlich 10 USD je 1.000 Aufrufe plus Inhaltstoken. Zwei zusätzliche Suchaufrufe täglich wären rechnerisch etwa 0,62 USD/31 Tage zuzüglich Token. Diese neue Suchserie wird nicht automatisch eingeschaltet: zunächst Nutzen der vorhandenen Luna-Aufrufe und X-Belege beobachten. GPT bleibt außerhalb des X-Budgets und innerhalb der bestehenden GPT-Steuerung. Quelle: [OpenAI API-Preise](https://developers.openai.com/api/docs/pricing).

## WebUI und Diagnose

Eigene Menüpunkte **Quellen & X** und **Diagnose**, auch mobil sichtbar. X-Anzeige: Einrichtung/Verbindung, letzter Erfolg/Fehler, Anfragen, empfangene/eindeutige/verarbeitete Posts, Duplikate, Themenhinweise, Übergaben an Recherche, Monatsreservierungen und Restbudget. Prüfkennungen verbinden Anfrage, Antwort und verarbeitetes Paket. Historische Providerfehler werden vom aktuellen Zustand getrennt.

News- und Reddit-Anzeigen unterscheiden tatsächlich belegte Provider-/Aggregatdaten von unbekannten Einzelposts. ApeWisdom und Tradestie werden nicht als zwei unabhängige soziale Plattformen ausgegeben. Die Diagnose bleibt passiv: Sofortaufnahme oder 30 Minuten, lokale ZIP plus optionaler Telegram-Versand. X-Token, private Rohtext-Datenbank und Rohposts werden nicht exportiert. Quellenausfälle werden im Bericht sichtbar, ohne die Diagnose künstlich mit kostenpflichtigen Probeabrufen zu verändern.

## Einordnung der beiden X-Dokumente

Übernommen: gemeinsame Quelle, Budgetreservierung, getrennte Plattformbasis, Counts mit Abdeckungsbeleg, keine gefälschte 28-Tage-Basis, abgeleitete Themenhinweise, Prozess-/Verarbeitungsbelege, Aufbewahrung/Löschung und Trennung von Recherche und Handel.

Für diese erste Integration bewusst begrenzt: zwölf Aktien, drei Zehnerstichproben täglich, keine automatische Handelswirkung unbestätigter Ereignisse, keine Behauptung unabhängiger Menschen oder bewiesener Manipulation, kein bezahltes Vollarchiv. Breitere Abdeckung und zusätzliche Luna-Webrecherche brauchen zunächst einen gemessenen Nutzen; die bestehende Pipeline liefert dafür die Diagnosewerte.
