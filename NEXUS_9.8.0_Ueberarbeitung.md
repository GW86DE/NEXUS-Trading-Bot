# NEXUS 9.8.0: Ausführung und Oberfläche

Stand: 8. September 2026. Grundlage sind der vollständige übergebene Quellstand 9.7.5, die Übergabe mit Statusdatei, frühere Kauf-/Verkaufsbefunde, Backlog, Prüfprotokolle, API-Beispiele und das Freqtrade-Quellarchiv. Die bereits erarbeiteten Korrekturen aus 9.7.6 sind in dieser vollständigen Version enthalten.

## Was jetzt anders läuft

Kauf und Verkauf haben ein gemeinsames dauerhaftes Auftragsbuch. Ein Auftrag wird vor seiner Übermittlung reserviert, mit echten Brokerbelegen abgeglichen und erst nach der zugehörigen Mengenbuchung abgeschlossen. OKX-Verkäufe verwenden eine preisbegrenzte IOC-Order. Damit können verfügbare Teilmengen verkauft werden; der Rest bleibt nachvollziehbar und wird erneut geschützt. Ein ungeklärter Auftrag gibt keinen zweiten Verkauf frei.

Die Handelsstrategie ist weiterhin an die jeweilige Position gebunden. Ihre Ausstiegssignale entscheiden, **wann** verkauft werden soll. Das Ausführungsmodul entscheidet anhand von Orderbuch, erlaubtem Preis, Brokerstatus und Fills, **was tatsächlich ausgeführt und gebucht wurde**. Diese Trennung ist für den SUI-Fall entscheidend: Ein korrektes ROI-Signal ist noch kein erfolgreicher Verkauf.

## Der SUI-Befund

Im Screenshot wurde für 124,77 SUI ein FOK-Verkauf mit Limit 0,8093 bei sichtbarem besten Gebot 0,8175 protokolliert. OKX meldete `cancelSource=13`, ausgeführte Menge null. Nach Versuch vier folgte eine Wartezeit von rund 40 Minuten. Die zuvor belegte Kaufmenge beträgt brutto 125,21 SUI, Basisgebühr 0,438235 SUI und damit netto 124,771765 SUI. Die Differenz zur handelbaren Menge 124,77 ist Rundungsrest, kein zusätzlicher Verkauf.

Belegt war außerdem im Ausgangscode: öffentliche OKX-Marktdaten bekamen nicht dieselbe Demo-Kennung wie die privaten Demo-Orders. Diese Abweichung ist korrigiert; Buch, Ticker und Kerzen werden in der passenden Umgebung angefordert. Ein Fehler beim Demo-Buch führt nicht zum Rückfall auf Live-Preise. Die tatsächlich damalige Demo-Buchtiefe liegt hier nicht vor. Deshalb wäre die Aussage „der historische SUI-Verkauf scheiterte sicher nur daran“ nicht belegt.

FOK verlangt die vollständige unmittelbare Ausführung; IOC erlaubt die unmittelbare Teilfüllung und storniert den nicht ausführbaren Rest. Das neue Verhalten beseitigt die zusätzliche Alles-oder-nichts-Bedingung des Verkaufs. Der Preisdeckel und die vorhandene Prüfung der Orderbuchtiefe bleiben wirksam. Bei schon vorab unzureichender Tiefe wird weiterhin kein Schutz zum Zweck eines aussichtslosen Verkaufs entfernt. [OKX: Ordertypen](https://www.okx.com/help/xi-strategy-order-types)

Ein bewiesener terminaler Nullfill wartet standardmäßig 30 Sekunden bis zur nächsten regulären Möglichkeit im Handelskern. Ein Takt kann länger dauern. Der Retry prüft Preis, Menge und Schutz erneut; er erweitert nicht eigenständig die konfigurierte Preisgrenze. Eine unbekannte Übermittlung bleibt dagegen im Abgleich. Ein Markt ohne passende Gegenorders kann auch mit IOC nicht zu einem garantierten Preis verkauft werden.

## Was wir von Freqtrade und Hummingbot übernehmen

Wir können ihre bewährten Abläufe als Vorbild nutzen. Einzelne Funktionen hängen dort jedoch an eigenen Ordermodellen, Wallets, Datenbanken, Ereignissystemen und Connectoren. Für NEXUS müssen außerdem eToro-Positions-IDs, OKX-Spotmengen, die Kontotrennung und die vorhandenen offenen Positionen erhalten bleiben. Deshalb ist die neue Schicht eine eigene Anpassung dieser Prinzipien; sie ist keine bloße Umbenennung einer fremden Verkaufsfunktion.

| Vorbild | Umsetzung in NEXUS |
|---|---|
| Freqtrade: Trade mit zugehörigen Orders und Ausführungen | Auftragsbuch plus bestehender Trade-Ledger; der Brokerauftrag und sein Buchungsbeleg bleiben getrennt nachvollziehbar. |
| Freqtrade: explizite Behandlung nicht ausgeführter Aufträge | Belegter Nullfill, Teilfüllung, offener Auftrag und unbekannte Annahme besitzen verschiedene Folgeaktionen. |
| Hummingbot: Auftrag vor Übermittlung verfolgen | Dauerhafte Reservierung vor jedem primären Kauf-/Verkaufs-POST. |
| Hummingbot: Status- und Fillereignisse getrennt behandeln | Terminaler Brokerstatus allein erzeugt weder eine erfundene Ausführung noch ein bestätigtes Netto. |
| Beide: erneute Meldungen einer Order sind normal | Unveränderliche Fill-IDs, persistente Buchungsbelege und Wiederanlauf ohne zweiten POST. |

Freqtrade hält die zugehörigen gefüllten und stornierten Orders am Trade und besitzt konfigurierbare Regeln für offene Aufträge. Hummingbot beschreibt getrennte Lebenszyklusereignisse und das Tracking vor dem Connector-Aufruf. Diese Quellen begründen die Architekturentscheidung; sie sind kein Nachweis, dass irgendein Bot jeden Auftrag immer ausführen kann. [Freqtrade: Trade-Objekt](https://www.freqtrade.io/en/stable/trade-object/), [Freqtrade: Konfiguration](https://www.freqtrade.io/en/stable/configuration/), [Hummingbot: Order Lifecycle](https://hummingbot.org/connectors/connectors/architecture/order_lifecycle/)

## Ablauf und feste Regeln

1. **Vorbereiten:** Identität, Konto, DEMO/LIVE, Instrument, handelbare Menge und Preisgrenze prüfen. Bestehende Risikogates, Positionsstrategie und KI-Freigaben für Einstiege bleiben erhalten.
2. **Reservieren:** Auftrag mit Client-/Request-ID dauerhaft in SQLite speichern. Konkurrierende gleiche Kauf-/Verkaufsversuche werden blockiert. Alte Reservierungen verfallen nicht allein durch Zeitablauf.
3. **Übermitteln:** Der Brokeradapter sendet den Auftrag. Eine erfolgreiche HTTP-Antwort beweist zunächst nur die Annahme. Timeout, widersprüchliche Antwort und fehlgeschlagene Speicherung nach dem POST führen zu einem ungeklärten Zustand.
4. **Abgleichen:** Status und Einzelfills müssen zur Identität passen. Wiederholte gleiche Fills werden nicht doppelt gezählt. Widersprechende Menge, Preis oder Identität werden zurückgewiesen. Ein verschwundener Auftrag in einem begrenzten Archiv ist kein Nullfill-Beweis.
5. **Buchen:** Tatsächlich verbrauchte bzw. erhaltene Menge im Ledger verbuchen. Bei Teilverkauf bleibt eine Restzeile mit Einstandszuordnung. Erst ein passender dauerhafter Ledgerbeleg bestätigt die Mengenbuchung im Auftragsbuch.
6. **Ergebnis und Schutz:** Das Risikobuch verwendet den bestätigten Ledgerabschluss. Restmengen werden weiter abgeglichen und geschützt. Nicht handelbarer Rundungsrest bleibt im Ledger nachvollziehbar.

Das neue Modul `execution_lifecycle.py` legt `execution_orders`, `execution_fills` und `execution_events` in der bereits übernommenen `decision_history.sqlite` an. Die vorhandenen Registry-, eToro-Reconciliation- und Exit-Journal-Dateien bleiben als notwendige Wiederanlaufbelege für ältere Vorgänge erhalten. Ein Upgrade darf deren Historie nicht wegwerfen.

Das Auftragsbuch und das Positions-/Risikobuch sind keine einzige atomare Transaktion über alle Dateien. Der Ablauf ist deshalb auf wiederholbare Verarbeitung nach einem Abbruch ausgelegt: erst der dauerhafte Beleg, dann die bestätigte Weiterverarbeitung. Die Prüfungen injizieren Ausfälle vor der Übermittlung, nach Brokerannahme und vor vollständiger Buchung.

## Mengen und Geld dürfen nicht geschätzt bestätigt werden

- Basisgebühren verändern die verfügbare Coinmenge. Bei OKX-Käufen wird die Nettomenge verwendet; bei einer Basisgebühr auf den Verkauf wird auch deren Bestandsverbrauch berücksichtigt.
- Ein ausdrücklich gemeldeter Gebührenwert null ist etwas anderes als ein fehlender Gebührenwert. Fehlende Angaben bleiben unbekannt. Fehlt dadurch auch der Nachweis der verbrauchten Basismenge, bleibt der Vorgang im Abgleich.
- Eine Gebühr in einer dritten Währung wird ohne belegten Umrechnungskurs nicht als Null oder 1:1 zur Handelswährung gerechnet. Mengen können bestätigt sein, während das Netto noch unbekannt ist. Rabatte bleiben als solche erhalten.
- Risikobuchungen verwenden permanente Abschlussbelege. Ein Neustart oder Tageswechsel darf denselben neuen Abschluss nicht erneut zählen. Bereits bekannte alte Tagesbelege werden bei passender Identität übernommen.
- Eine spätere Bestätigung eines unbekannten Risikobelegs ist einmalig möglich. Betrifft sie einen vorherigen Tag, erhöht sie nicht den Gewinn des heutigen Tages. **Eine vollständige automatische Rekonstruktion aller historischen fehlenden Gebühren ist nicht enthalten.** Unbelegte alte Ergebnisse bleiben offen gekennzeichnet.
- Weder ein Coinname noch eine Entscheidungs-ID begründet Eigentum. Konto, Umgebung und echte Order-/Positionsanker bleiben erforderlich. Guthaben, externe Depotpositionen und Botpositionen werden nicht gleichgesetzt.

## Die neue WebUI

| Seite | Sichtbarer Schwerpunkt |
|---|---|
| Übersicht | Zustand beider Handelskern-Verbindungen, Kaufbereitschaft, Störungen und letzte fünf Kaufentscheidungen. Weitere System-/Providerdaten ausklappbar. |
| Handel → Offen | Bestätigte Botpositionen mit Restmenge, Einstand, aktuellem Kurs, Ergebnis und Schutzstatus. |
| Handel → Verkäufe | Gebuchte Abschlüsse, Zeitraumfilter, Gebühren-/Ergebnisstatus und ausklappbare Auswertungen je Kontokontext. |
| Handel → Klärung | Neue noch offene Aufträge und ältere ungeklärte Bestände. Ein Lesefehler erscheint auch oben als Warnung. |
| Handel → Guthaben & Verwaltung | Kontoguthaben, externe Depotpositionen, Übernahme/Beobachtung sowie manuelle Aufträge und Sperren. |
| Analyse | Vorhandene Prüfwerkzeuge in ausklappbaren Gruppen; Universum als untergeordneter Zugang. |
| Einstellungen | Alle 51 bisherigen Felder und alle Laufzeitaktionen, verteilt auf zehn durchsuchbare Gruppen. |
| Logbuch | Technische Meldungen bei Bedarf; kein Ersatz für die verständlichen Zustandsanzeigen. |

„Trades“ und „Positionen“ sind zusammengeführt. Die alte URL `/positions` leitet nach Anmeldung auf Handel weiter. Die vorhandenen Verwaltungs-APIs bleiben erhalten. Ein Konto-Asset wird dadurch weiterhin nicht zu einer Botposition erklärt.

Karten zeigen zwölf Einträge je Seite. IDs, Strategiedetails und manuelle Aktionen liegen in aufklappbaren Bereichen. Der OKX-Kerzenchart bleibt mit Kauf, Verkauf, Stop, Brokerziel und ROI-Markierungen erhalten. Die bereits bestehende Beschränkung auf OKX-Kerzen bleibt bestehen.

Das Layout verwendet auf breiten Bildschirmen zwei Spalten, auf kleinen Geräten eine Spalte, gut erreichbare Eingaben und kompakte Reiter. Die Navigation wechselt auf Tablet und Handy zum Auswahlmenü. Tastatursteuerung und reduzierte Bewegung werden berücksichtigt. Hintergrundaktualisierung überschreibt kein geöffnetes Formular. Ein manueller OKX-Auftrag erhält einen zusammenhängenden Bestätigungsdialog; die eigentliche Ausführung bleibt Aufgabe des Handelskerns mit erneuter Identitäts- und Bestandsprüfung.

Die WebUI ruft keine Brokerorder direkt ab. Authentifizierung, CSRF-Schutz und die bestehende manuelle Auftragswarteschlange bleiben erhalten. Dashboard-Freigaben setzen aktuellen Worker, bekannte Umgebung, Kontobindung, passenden Modus, frische Bereitschaft und aktive Kaufsteuerung voraus.

## Prüfung, Installation und verbleibende Abnahme

Die konkreten Endergebnisse stehen in `TEST_REPORT_v9.8.0_NEXUS.txt`; Rohlogs und Codeänderungen liegen im separaten Nachweispaket. Der komplette Bot wird als ZIP und als selbstenthaltender Installer ausgeliefert. Es genügt einer der beiden Installationswege. Die Anleitung beschreibt den vorhandenen abgesicherten Updateprozess einschließlich Zustandsübernahme.

Der SUI-Test verwendet die übergebene Nettomenge und einen künstlichen Brokertransport: Das Buch ist zunächst ausreichend, bei Ankunft sind nur 40 SUI ausführbar. Anschließend werden 84,77 SUI verkauft; 0,001765 SUI bleiben als Ledgerrest. Geprüft werden Ordertyp, Schutz der Restposition, Verkaufsmenge und Summe der tatsächlichen Ergebnisbuchungen. Das ist eine Regression des neuen Ablaufs und keine nachträgliche Rekonstruktion des historischen Demo-Matchings.

Hier wurden keine echten Brokerorders gesendet, keine Dienste auf dem Nutzer-Pi geändert und keine aktuellen Nutzer-Laufzeitdaten migriert. Die Prüfung läuft auf Linux x86_64/Python 3.12.13. Pi 5/ARM64/Python 3.13.5 und die tatsächliche Broker-Demo sind getrennte Abnahmeumgebungen. JavaScript-Funktionen, HTML-Routen, Authentifizierung und responsive Regeln wurden geprüft; eine Darstellungskontrolle in echtem iPhone-/iPad-Safari bzw. Desktopbrowser steht aus.

Nach dem Update sind deshalb der erfolgreiche lokale Volltest, beide Dienste im neuen Ordner sowie Konto/DEMO, SUI-Restmenge, Schutz und nächste konkrete Ausführung in Handel zu prüfen. Ein weiterhin ungeklärter Altauftrag muss anhand seiner Brokerbelege geklärt werden. Warnungen oder Daten zu löschen wäre keine Reparatur. Der enthaltene ausschließlich lesende SUI-Export liefert dafür auch das neue Auftragsbuch.
