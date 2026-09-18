# NEXUS 10.1.5 – PEP-Abrechnung und eToro-Auftragskommunikation

Grundlage: Quellfassung 10.1.4, Diagnose vom 14.09.2026 21:45 UTC, Abschlussbelege vom 14.09.2026 17:55 UTC sowie die angehängten eToro-Spezifikationen für Historie, Close-Information, Kosten, asynchrones Eröffnen und Stornieren. Umsetzung vom 15.09.2026.

## PEP: konkrete Ursache und Korrektur

Die laufende Ergebnis-Kaufsperre betrifft den tatsächlichen geschlossenen Trade 54. Der alte Sonntagsauftrag und der spätere native TP-Abschluss sind unterschiedliche Vorgänge. `1043` ist die Instrument-ID, `380995258` die alte Schließorder und `3597440106` die Position. Die tatsächliche TP-Schließorder-ID ist in den vorliegenden Belegen nicht bekannt. Keine dieser Identitäten wird ersetzt oder aus ähnlichen Mengen geraten.

Die History meldet 244,16 USD `netProfit` und 0 USD `fees`. Bereits die reine Kursdifferenz von 109 × (138,59 − 136,35) ergibt 244,16 USD. Der bestätigte Einstiegsbeleg enthält zusätzlich 1 USD Kosten. Die Historie allein belegt deshalb keine vollständige Nettoabrechnung.

Die geprüften Barbestände ergeben eine Differenz von 1 USD gegenüber dem Bruttoverkaufserlös. Daraus folgt **unter Ausschluss weiterer Geldbewegungen** eine Abschlusskostenableitung von 1 USD und ein Netto von 242,16 USD. Die neue WebUI zeigt die Rechnung und verlangt die Bestätigung dieser Voraussetzung. Sie etikettiert die Ableitung als `USER_CONFIRMED`, getrennt von nativen Brokerbelegen.

Die Bestätigung ist konten-, umgebungs-, positions-, mengen- und beleggebunden. Eine geänderte Vorschau erfordert erneutes Prüfen. Der vorherige Trade und der komplette Prüfbeleg werden atomar in `decision_history.sqlite` gespeichert. Mehrfache Bestätigung, Neuladen oder Neustart buchen das Ergebnis nur einmal. Der Risikoabgleich liest die bestätigte Abrechnung erneut und übernimmt sie zum belegten Verkaufstag; bisherige Risikoperioden und andere unbekannte Ergebnisse bleiben erhalten.

## Aus den neuen API-Dokumenten übernommen

| Dokumentierter Punkt | Verhalten in 10.1.5 |
|---|---|
| Asynchroner v3-Create, HTTP 202 | Bestehende Long-Eröffnungen verwenden den v3-Endpunkt. Annahme ist keine Ausführung. |
| Eindeutige Parameter | Bestehender `limitIOC`-Preisdeckel, ein Instrument-Identifier, Größe in Units, expliziter Settlement-Typ und vorhandene SL/TP-Werte. |
| Antwortverlust | Persistierter Request-GUID; Nachlesen mit Original-Order-ID oder Referenz. Kein zweiter Create zur Fehlerbehebung. |
| v3-Storno, HTTP 202 | Dauerhaft vorgemerkte Anfrage; das leere Storno-`referenceId` wird nicht zum Lookup-Anker. |
| Lookup 6/7/9 | 6: Storno läuft; 7: storniert; 9: Teilmenge ausgeführt, Rest storniert. Bereits gekaufte Mengen bleiben bestehen. |
| Lookup 1/2/11/12 | Empfang/Platzierung/Warten auf Markt/Warten auf Trigger bleiben offen. |
| Lookup 3/4/5/10 | Voll ausgeführt/abgelehnt/Teilfill/Teilfill mit abgelehntem Rest getrennt. Status 5 beweist keinen beendeten Restauftrag. |
| Unbekannter oder widersprüchlicher Status | Keine Freigabe; Ausführungs- und Positionsbelege werden getrennt gehalten. Legacy-v1-Statuscodes werden nicht als v2-Codes interpretiert. |
| Gemeinsame Ausführungskontingente | v3-Create und v3-Delete verwenden denselben bereits persistent begrenzten Ausführungsbereich wie die übrigen eToro-Schreibzugriffe. |
| Close-`proceeds` | Antwort und Geldfelder werden dauerhaft erhalten. Bei eindeutigem USD-Scope werden Bruttoerlös und Differenz ausgewiesen; daraus allein entsteht keine Gebührenbuchung. |
| Zeitpunkt | `requestOccurred` wird nicht als Ausführungszeit verwendet. Ein Sonntagsbefehl erhält dadurch keinen erfundenen Sonntags-Fill. |

Die Spezifikation bezeichnet `proceeds` als Gesamterlös, bestätigt jedoch nicht ausdrücklich den Abzug aller Provisionen. Ein einzelner kleinerer Wert beweist den gesamten Gebührenumfang ebenfalls nicht. Die vorgeschlagene Differenzformel wird deshalb als prüfbare Beobachtung vorbereitet, nicht ohne unabhängigen Beleg als universelle Gebührenregel eingeführt.

Der v3-Create wird nur im bisherigen Eröffnungspfad verwendet. Schließungen bleiben beim dokumentierten positionsbezogenen Close-Endpunkt. Zusätzliche Ordertypen, Hebel oder Short-Strategien werden dadurch nicht aktiviert. Das Stornoangebot beschränkt sich auf eindeutig zugeordnete wartende NEXUS-Kaufaufträge; es verändert keine nativen Schutzorders.

## WebUI und Diagnose

- Handel: kostenbezogene Vorschau und bestätigter Barbestandsabgleich am tatsächlich geschlossenen Trade.
- Handel/Klärung: wartende Kaufaufträge, Stornoanfrage und belegter Bearbeitungsstatus.
- Altauftrag: ausdrücklicher Hinweis, dass ein abgeschlossener Positionsnachweis mit ungeklärtem altem Auftrag keine offene Position und keine aktive Mengen-Kaufsperre ist.
- Diagnose 1.6.0: getrennte Berichte zu Abrechnungen, Barbestandssammlung, Stornozuständen und `proceeds` ohne bestätigten Gebührenumfang. Kürzungen und fehlende Exporttabellen bleiben sichtbar.
- Nutzerabrechnungen werden als solche ausgewiesen; der abweichende Umfang des History-Ergebnisses bleibt dokumentiert.

## Grenzen und Abnahme

Ein allgemeines „tritt nie wieder auf“ ist bei fehlenden Brokerdaten nicht beweisbar. Vollständige native Kosten werden automatisch verarbeitet; beleglose Fälle erhalten einen prüfbaren Klärungsweg. Barbestandsmessungen allein garantieren bei parallelen Buchungen keine korrekte Zuordnung. Ein Update darf deshalb keinen angeblichen Nullkostenbeleg erzeugen.

Die Prüfung erfolgt offline mit simulierten Brokerantworten und den vorliegenden historischen PEP-Daten. Keine Installation auf Georgs Pi und kein realer Broker-/Telegram-/kostenpflichtiger Quellenaufruf wurden durchgeführt. Die bestätigte PEP-Abrechnung und ihre Übernahme durch den laufenden Handelskern sind nach Installation in der WebUI nachprüfbar. Die tatsächlichen Testergebnisse und Paketprüfsummen stehen im mitgelieferten Prüfbericht.

Offizielle Referenzen: [Asynchroner Auftrag](https://api-portal.etoro.com/api-reference/trading--demo/submit-an-order-for-asynchronous-processing), [Stornoanfrage](https://api-portal.etoro.com/api-reference/trading--demo/request-cancellation-of-a-pending-order). Die angehängten OpenAPI-Auszüge sind die Grundlage der hier ausgewerteten Felder.
