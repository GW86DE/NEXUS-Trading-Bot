# NEXUS 9.8.1 — Befunde, Korrekturen und Abnahme

Stand: 9. September 2026. Vollständige Weiterentwicklung des vorhandenen 9.8.0-Stands, keine Neuinstallation mit leeren Handelsdaten. Schwerpunkt ist BTC; der vom Nutzer bestätigte manuelle SUI-Verkauf ist ein eigener Buchhaltungsfall.

## Ergebnis und Reichweite

Die belegten BTC-Preisband- und TXN-Instrumentfehler sind im Code korrigiert. Die Verkaufszuordnung wurde gegen unklare Orders, widersprüchliche Fill-IDs und erfundene Teilmengen abgesichert. Historische Fremdwährungsverkäufe erhalten einen überprüfbaren nativen Erlösbeleg. Neu ist eine kontogetrennte Tages-/Gesamtergebnisgrafik.

Das ist ein offline getesteter Release-Kandidat für die DEMO-Abnahme, kein Nachweis eines bereits erfolgreichen Verkaufs auf Georgs Pi und keine Zusicherung, dass jede Börsenorder ausgeführt wird. Es wurden keine Orders an OKX/eToro gesendet, keine Dienste auf dem Pi geändert und keine hochgeladenen Originaldaten überschrieben.

## Belegte Fehler und Zuordnung

| Fall | Beleg und Ursache | Korrektur | Noch offen |
| --- | --- | --- | --- |
| BTC-USDC, DEMO | 09.09., 10:03:33 CEST: Best Bid 79.419, Limit 78.624, OKX 51138 mit Mindestverkaufspreis 79.027. Die eigene 1%-Grenze lag außerhalb des Börsenpreisbandes. | Aktuelles `sellLmt` berücksichtigen, tickgenau nach oben begrenzen; eigenes Risiko nicht lockern. Nach Bestandsfreigabe erneut prüfen. | Tatsächliche Ausführung nach Installation beobachten. Historischer Mindestwert ist kein konstanter Kurs. |
| BTC-Wiederholungen | Derselbe Fehlertyp führte zu langen allgemeinen Wiederholungsabständen bis 40 Minuten. | Eindeutige Preisbandablehnung als No-Fill behandeln; frühestens nach 30 Sekunden, tatsächlich im nächsten regulären Takt neu prüfen. Altwartezeit nur mit exakt passendem abgelehntem Journaleintrag verkürzen. | Ohne Journalbeweis bleibt ein alter Auftrag gesperrt; kein JSON-Reset. |
| SUI extern verkauft | Nutzer bestätigt manuellen Verkauf. Entry SUI-USDC, Verkauf SUI-EUR, zwei echte Fills über 124,77 SUI. Alter Ledgertext behauptete ausgelösten Brokerschutz. | Originalbeleg/ursprüngliche Zeile erhalten; echte Exit-ID, Menge, EUR-Erlös und manuellen Grund ergänzen; wiederholbare Vorschau-/Anwenden-Funktion. | Historischer USDC/EUR-Wechselkurs fehlt. Kein berechenbarer bestätigter Gesamtgewinn. Historische Korrektur muss auf dem Pi ausdrücklich angewandt werden. |
| eToro TXN | Schutzabgleich bekam ein untypisiertes Vertragsobjekt; „Asset-Typ ?“. Positions-ID 3596233679 war kein Instrument; TXN-Instrument ist 1634. | Typisiertes Aktieninstrument verwenden; Instrument-, Order- und Positions-ID getrennt halten und Widersprüche abweisen. | Historische Ein-/Ausstiegsgebühren fehlen; -161,31 USD aus Kursdifferenz nicht als bestätigtes Netto ausgeben. |
| Order-/Fill-Zuordnung | Ein Archivfill ist keine Bestätigung, dass eine Order beendet ist. Wiederholte IDs konnten widersprüchliche Inhalte überdecken; physische Fills wurden auf passende Mengen gekürzt. | Terminalstatus separat abfragen; kein fremder Abschluss bei unklarer eigener Order; widersprüchliche IDs abweisen, keine proportional umgeschriebenen Fills. | Historisch beschädigte Aliase ohne eindeutigen Gegenbeleg werden nicht pauschal gelöscht oder neu zugeordnet. |

Die erste SUI-FOK-Störung und die später eindeutig belegte BTC-Preisbandablehnung sind unterschiedliche Fehlerbilder. Die BTC-Ursache ist anhand des Zahlenvergleichs bewiesen. Eine vollständige historische Rekonstruktion des damaligen SUI-Matchingbuchs ist damit nicht behauptet.

## Kauf und Verkauf: Sicherheitsregeln

Das seit 9.8.0 gemeinsame dauerhafte Auftragsbuch bleibt erhalten: Absicht vor dem API-Aufruf speichern; Konto, Umgebung, Instrument, Seite und Client-ID binden; einzelne unveränderliche Fills verarbeiten; terminalen Brokerzustand von abgeschlossener Buchhaltung trennen. Ungeklärte Aufträge werden nicht durch einen neuen POST ersetzt.

9.8.1 ergänzt aktuelle Börsenpreisgrenzen für beide Seiten. SELL muss die höhere Untergrenze aus NEXUS und OKX einhalten, BUY die niedrigere Obergrenze. Die gewählte Grenze muss zum aktuellen ausführbaren Buch passen. Ungültige, alte oder fehlende Antworten sperren den Auftrag. Nach Freigabe zuvor gebundenen Guthabens wird neu gelesen. Bei eindeutigem Fehlschlag wird der Schutz abgeglichen; bei unbekanntem Verkauf wird keine konkurrierende Schutz-SELL-Order für möglicherweise bereits verkaufte Menge erzeugt.

Käufe bleiben FOK, Verkäufe preisbegrenzte IOC. FOK-Kauf vermeidet unvollständige Einstiege; IOC-Verkauf kann eine belegte Teilmenge ausführen. Der Rest muss korrekt gebucht und geschützt werden. Es gibt keinen automatischen unlimitierten Market-Notverkauf und keine Aufweichung der eingestellten Verlust-/Slippagegrenzen.

Auch eine nicht handelbare Restmenge ist nicht einfach ein weiterer Trade. Ein reiner Bestandsrückgang beweist weder Fillpreis noch manuellen Verkäufer noch ausgelösten Stop. Fehlende Daten werden nicht durch aktuelle Kurse ersetzt.

## Was von Freqtrade und Hummingbot übernommen wurde

Es wurden Verhaltensmuster in NEXUS implementiert, nicht ganze fremde Engines eingefügt. Ein bloßes Kopieren einer `sell()`-Funktion ersetzt keine passende Zustandsführung oder Buchhaltung.

- Freqtrade beschreibt für abgelaufene Exit-Orders Storno und Neuansatz zum aktuellen Preis bei weiterhin bestehendem Signal. NEXUS nutzt dieses Prinzip erst nach nachgewiesenem terminalem Ausgang; seine eigenen Preis- und Kontoschranken bleiben bestehen. [Freqtrade-Konfiguration](https://www.freqtrade.io/en/stable/configuration/#configuration-parameters).
- Hummingbot beginnt das Tracking vor dem Order-API-Aufruf und verfolgt Zustandsänderungen bis zum Abschluss, Storno oder Fehlschlag. Das ist die Grundlage der dauerhaften NEXUS-Auftragsidentität; ein Timeout bedeutet keinen sicheren Fehlschlag. [Hummingbot Order Lifecycle](https://hummingbot.org/connectors/connectors/architecture/order_lifecycle/).
- OKX stellt `buyLmt`, `sellLmt`, `enabled` und einen Zeitstempel pro Instrument bereit; 51137/51138 benennen überschrittene Kauf-/Verkaufsgrenzen. NEXUS liest diese strukturierten Daten statt einen alten Grenzpreis aus Fehlermeldungstext dauerhaft zu übernehmen. [OKX API: Get limit price](https://my.okx.com/docs-v5/en/#public-data-rest-api-get-limit-price).

Zusätzlich wurden die vorhandenen Freqtrade- und Hummingbot-Quellen zur Orderverfolgung und Fill-Verarbeitung verglichen. Keine Behauptung, dass Referenzbots keine Ausführungsfehler haben oder deren Standardkonfiguration auf gemischt manuell/botverwaltete Konten unverändert passt.

## Neue WebUI-Ergebnisanzeige

„Übersicht“ zeigt kompakte Karten für Heute und Gesamt seit Aufzeichnungsbeginn. Umschaltbar sind Tagesbalken/kumulierte Linie, Geld/Prozent sowie 30/90/365 sichtbare Tage. Gesamtwerte verwenden trotzdem alle vorhandenen Trades, nicht nur das Chartfenster oder die 500 neuesten Historienzeilen.

Eine Auswahl entspricht genau Broker + Konto + DEMO/LIVE + Währung. EUR, USDC und USD werden nicht ohne Wechselkursbeleg addiert. Es handelt sich um realisierte Ergebnisse abgeschlossener Trades nach bestätigten Gebühren; offene Buchgewinne/-verluste bleiben auf der Handelsseite separat geschätzt.

Prozent = 100 × bestätigtes Nettoergebnis / Einstiegskapital derselben bewerteten abgeschlossenen Trades einschließlich zugehöriger Einstiegsgebühr. Die einzelnen Trade-Prozente werden nicht addiert. Dies ist keine Kontorendite, keine zeitgewichtete Rendite und keine Rendite einschließlich Ein-/Auszahlungen.

Fehlende Gebühren/FX oder nicht belegte Abschlüsse sind unbekannte Ergebnisse. Karten kennzeichnen bestätigte Teilsummen; Chartwerte mit unvollständigem Ergebnis werden als Lücke angezeigt. Ein Tag ohne Abschlüsse hat Geldwert 0, aber ohne Kapitalbasis keine Prozentzahl. Tagesgrenze ist Europe/Berlin, einschließlich Sommer-/Winterzeit.

Die Wertetabelle und Methodenerklärung sind ausklappbar. Mobile Layoutregeln, 44-px-Bedienelemente und ein größenangepasstes SVG sind vorhanden. Die seit 9.8.0 gemeinsame Seite „Handel“, der Kerzenchart, Konto-/Verwaltungstrennung und die bestehenden Einstellungsfunktionen bleiben erhalten. Es wurden keine Handels- oder Risikofunktionen zugunsten der Optik entfernt.

## Ausgeführte Prüfungen

Der vollständige isolierte Testlauf nutzt `volltest.py`, nicht einen direkten pytest-Aufruf mit möglichen produktiven Umgebungsvariablen. Er prüft Release-Hygiene, Abhängigkeiten/WebUI-Client, alle Python-Regressionen, Selbsttest, Kompilierbarkeit, hostneutrales Pi-Preflight und Shellsyntax. Der Quellstand ist während des Laufs unverändert. Die vollständigen Rohprotokolle gehören zum separaten Prüfnachweis-Paket.

Abschlussstand: 1.669 bestandene Python-Tests und 216 bestandene Untertests. Eine vorhandene Starlette/AnyIO-Abkündigungswarnung, kein Testfehler. Python 3.12.14, Linux x86_64; das ist nicht die ARM64/Python-3.13.5-Umgebung des Pi. Neue Fachprüfungen umfassen unter anderem:

- Tatsächliche BTC-Zahlen 79.419 / 78.624 / 79.027, Tickrundung, BUY-Obergrenze, fehlendes/ungültiges/veraltetes Preisband, Umgebungskonflikt, Erneuerung nach Freigabe.
- 51138 eindeutig abgelehnt gegenüber HTTP 503/Timeout unklar; kein doppelter POST; Schutz und getrennte Wiederholungszustände.
- Eigene Order nicht terminal oder Fillbeleg unvollständig: kein Fremdverkaufsfallback und kein Vergessen wegen fehlenden Guthabens.
- Eindeutige Instrument-/Order-/Client-ID, widersprüchliche Duplikate, Mengenabweichung über Lot-Staub, keine Fill-Kürzung.
- TXN-Instrument 1634 getrennt von Positions-ID 3596233679 und typisierte eToro-Auflösung.
- SUI-Verkauf in EUR: echte Mengen/Gebühren, atomare Fill-Claims, Rollback bei Konflikt, Wiederholung ohne Doppelbuchung, falsches Konto/Umgebung/Entry und unvollständige Historie gesperrt.
- Tagesgrenzen/Zeitzonen, unbekannte Ergebnisse, gewichtete Teilverkaufsbasis, mehr als 500 Trades, getrennte Konten/Währungen, API-Sitzung und Parametergrenzen.

Ein erneuter Entwicklungslauf deckte außerdem eine sporadische `database is locked`-Kollision bei der ersten gleichzeitigen Orderreservierung auf. Ursache war das bedingungslose Umschalten auf WAL bei jedem Öffnen der gemeinsamen Datenbank. 9.8.1 lässt vorhandenen WAL-Modus unverändert, serialisiert lokale Verbindungsinitialisierung und wiederholt ausschließlich vorübergehende SQLite-Sperren mit Zeitgrenze. Fehlerhafte Handles werden geschlossen, I/O-Fehler nicht verschluckt. Das ist keine Wiederholung eines Order-POSTs. Zusätzliche Tests: zehn frische Datenbanken mit je 16 Threads, sechs unabhängige Prozesse, Sperrfrist und Disk-I/O-Fehler; jeweils höchstens eine zulässige Reservierung.

Zusätzlich: 56 bestandene JavaScript-Logikprüfungen mit einem kleinen DOM-Testmodell (Breiteneingaben 320, 390, 834, 1440), Syntaxprüfung der neuen/geänderten JS-Dateien, nullwerttreue Diagrammlücken, Kontoauswahl, Wechsel Geld/Prozent, Schutz vor veralteten API-Antworten, Leeren veralteter Werte bei Fehler und Text-Escaping. Das ist ausdrücklich kein Browser-/Layouttest.

## Probe mit der bereitgestellten Datenbank

Die hochgeladene Datenbank wurde nur lesend geöffnet und in eine temporäre, konsistente SQLite-Kopie übernommen. Auf dieser Kopie wurde der tatsächliche SUI-Bericht angewandt und identisch wiederholt.

Ergebnis: weiterhin 52 Trades, ausschließlich Trade 45 nachgepflegt; die bisherigen Werte aller anderen 51 Trades unverändert; zwei exakte neue Fill-Claims, keine Doppelbuchung; SQLite `quick_check` erfolgreich. Einstiegswährung USDC bleibt stehen, native Verkaufswährung ist EUR; `ausstieg_preis` und `netto_pnl` in der ursprünglichen USDC-Rechnung bleiben NULL. Netto-Verkaufserlös 84,9180658695 EUR ist kein Gewinn.

SHA-256 der unveränderten hochgeladenen Originaldatenbank: `d253b134adfc86eb8fdf276eabb54f27025ce640327c500e3da608500957a820`. Die Kopie wird nicht als fertige Ersatzdatenbank ausgeliefert: Seit dem Export kann der Pi weitergehandelt haben.

## Grenzen und nächste Abnahme

Der bereitgestellte Browser blockierte den lokalen Testseitenaufruf mit `ERR_BLOCKED_BY_CLIENT`. Daher keine bestätigte visuelle Prüfung in Safari, auf iPhone/iPad oder auf einem tatsächlichen PC-Browser. Responsive Regeln und JavaScript sind geprüft, echte Layout-/Touchkontrolle bleibt offen. Die Fehlermeldung wurde nicht durch einen anderen Browserweg umgangen.

Ebenso offen: tatsächliches OKX-/eToro-Matching, Broker-Netzwerkstörungen unter Last, Pi-Hardware und native Abhängigkeiten. Die Test-Transporte sind synthetisch; erfolgreiche Simulation beweist keine vorhandene Liquidität. Der Installer führt seine Prüfungen nochmals auf dem Pi aus, ersetzt aber keinen beobachteten DEMO-Handelszyklus.

Historisch fehlende Daten werden nicht erfindbar: SUI-FX und TXN-Gebühren bleiben offen. Alte, beschädigte Registry-Aliase werden nicht ohne exakte Gegenbelege pauschal entfernt. Die vorhandenen belegsicheren Migrationsregeln bleiben bestehen. Neue Geschäftsvorfälle werden durch strengere Identitäten geschützt; die gesamte Vergangenheit ist damit nicht rückwirkend vollständig bewiesen.

Vor Livebetrieb erforderlich: erfolgreiche Installation beider Dienste in 9.8.1, beobachteter DEMO-Kauf und -Verkauf samt Fill-/Ledgerabgleich, geprüfter Restbestand und Schutz, ein Wiederanlauf ohne Doppelorder und die Geräteprüfung. Bei Abweichungen keine Bestände löschen und keine weitere Coreinstanz starten; BTC-Diagnose und relevante Logs verwenden.
