# PULSAR 9.8.5: tatsächlicher Funktionsstand

PULSAR ist über den eigenen Hauptmenüpunkt erreichbar. Es bietet einen
Aufmerksamkeitsradar mit fünf Kandidaten, individuellen Analysen, Kursgrafiken,
Quellen und sichtbaren Datenlücken. Die vollständige Handelsfreigabe nach dem
vereinbarten Konzept ist mit den angeschlossenen Quellen weiterhin gesperrt.

## Daten und Analysen

1. ApeWisdom liefert bis zu 100 Aktien aus seinem Aggregatfeed. Die fünf
   Kandidaten werden nach wachsender Aufmerksamkeit mit Mindeststichprobe
   sortiert. Dies ist ein Aufmerksamkeitsrang, kein Belegscore oder Kaufsignal.
2. Tradestie kann zusätzlich aktiviert werden. Die überlappenden Reddit-Foren
   zählen nicht als unabhängige Bestätigung. Ein Ausfall wird angezeigt.
3. Bestehende FMP-, SEC- und MASSIVE-Zugänge ergänzen Unternehmensdaten, Kurse
   und Nachrichten. FMP liefert zusätzlich den passenden Earnings-Termin.
   Fehlende oder veraltete Daten werden nicht mit Annahmen ersetzt.
4. Luna erhält die Quellenpakete aller fünf Kandidaten. Für jeden werden eine
   individuelle These, drei Risiken, Datenlücken und OBSERVE/REVIEW/REJECT verlangt.
   Symbol und Quellenreferenzen werden formal geprüft. Eine Quellen-ID allein
   ist kein automatischer Nachweis, dass die KI eine Tatsache richtig ausgelegt hat.
5. REVIEW priorisiert vertiefte Terra-Recherche; REJECT verhindert die Auswahl.
   Pro Zyklus höchstens eine neue Vertiefung. Ein separater Terra-Gegencheck erhält
   nur die Quelldaten, nicht das Urteil der ersten Analyse. Vorhandene gültige
   Analysen der übrigen Kandidaten bleiben erhalten.
6. Im Beobachtungsmodus wird höchstens ein Recherchefavorit pro ISO-Woche in
   Europe/Berlin gespeichert. Er braucht Analyse und Gegenprüfung ohne Ablehnung.
   Fehlende strenge Pflichtbelege bleiben sichtbar. Das ist eine Simulation ohne
   Kauf, Brokerzugriff, Telegram-Freigabe oder gebuchte Performance.

Kandidaten ohne KI-Antwort zeigen eine Datenübersicht und den offenen Prüfstatus.
Es erscheinen keine vorgefertigten drei Risiken als vermeintliche KI-Analyse.
Die Kurszeiträume 1/5 Tage und 1/3 Monate verwenden gelieferte abgeschlossene
Kerzen. Ausführungsmarkierungen kommen ausschließlich aus dem Trade-Ledger.

## Grenzen und Verbrauch

Maximal 20 erstmals angereicherte Symbole pro Berliner Kalendertag; bekannte
Symbole verbrauchen keinen zweiten Neuaufnahmeplatz. Der Aufmerksamkeitsfeed
umfasst höchstens 100 Datensätze. Das ist keine Vollsuche über alle Reddit-Aktien.
Social-Abfragen werden stündlich zwischengespeichert. Der Worker prüft werktags
zwischen 07 und 19 Uhr New Yorker Zeit, am Wochenende höchstens zweimal täglich.

Die bisherigen Obergrenzen bleiben: 80 Social-/200 Datenaufrufe täglich und
400/1.000 wöchentlich; KI zusätzlich 1 USD täglich, 2 USD wöchentlich, 10 USD
monatlich anhand konfigurierter Preise. Kosten werden vorher reserviert und bei
unklarem Ausgang nicht zurückgebucht. Es wurde kein Abo gebucht, kein Anbieter
neu freigeschaltet und keine kostenpflichtige KI-Abfrage im Test ausgeführt.

Die dokumentierten Aggregatfelder von ApeWisdom und Tradestie enthalten keine
eindeutigen Autoren, Einzelbeiträge oder argumentbezogenen Nachweise.
[ApeWisdom API](https://apewisdom.io/api/),
[Tradestie API](https://tradestie.com/apps/reddit/api/).
Der Earnings-Anschluss verwendet den offiziellen FMP-Kalender.
[FMP Earnings Calendar](https://site.financialmodelingprep.com/developer/docs/stable/earnings-calendar).

## Was zur vollständigen Freigabe weiterhin fehlt

| Bedingung | Tatsächlicher Stand |
|---|---|
| 20 Autoren / 3 Argumentgruppen / 2 Communities | Aggregatquellen liefern die Belege nicht; kein zugelassener Einzelbeitragsadapter |
| Verifizierter wirtschaftlicher Primäranlass | Nachrichten/Firmendaten vorhanden, aber noch kein Adapter, der daraus den erforderlichen verifizierten Ereignisbeleg erstellt |
| 14-/28-Tage-Basis und verdoppelte Aktivität | Lokale Historie wird aufgebaut; bis dahin offen |
| Belegscore ≥80 und zwei Prüfungen ≥60 Minuten auseinander | Prüfmechanik vorhanden, Pflichtlücken verhindern die Zulassung |
| Passender Earnings-Termin | Jetzt über FMP möglich; leere, fremde oder >6 Stunden alte Antwort bleibt ungenügend |
| Konto, echte Aktie, Stückzahl, Kosten, frische Kurse, Risiko und Schutz | Weiterhin unmittelbar im bestehenden Core zu prüfen |

„Persönliche Freigabe“ allein hebt keine dieser Bedingungen auf. Für vollständige
Handelsnominierungen fehlt somit ein zugelassener Datenzugang für Beiträge/Autoren
und dessen geprüfte Anbindung sowie die Verifizierung wirtschaftlicher Ereignisse.
Die Pflichtbedingungen wurden nicht heimlich abgeschwächt.

Die bestehenden Freigabe-/Handelsregeln bleiben: eine echte Nominierung pro Woche,
maximal zwei offene oder schwebende Trades, zwei persönlich gebundene Telegram-
Bestätigungen, frische unveränderte Pläne, 0,25 % Kontorisiko, maximal 3 %
Positionskapital und 6 % PULSAR-Kapital. Neustart/Moduswechsel widerrufen alte
ungesendete Freigaben. Ein ungeklärter Orderausgang wird nicht erneut gesendet.

Das Earnings-Fenster berücksichtigt jetzt die nächsten zwei US-Handelstage
einschließlich Feiertagen. Der gesamte zweite Tag bleibt bei einem reinen
Kalenderdatum gesperrt. Bei widersprüchlichen Terminen gilt der nähere.

## Abnahme

Die Funktionsprüfungen verwenden lokale Daten, einen simulierten KI-Router und
Broker-Testdoubles. Das belegt Datenfluss und Sperrregeln, nicht die Qualität echter
KI-Texte oder die Erreichbarkeit der Anbieter auf dem Pi. Nach Installation:
WebUI neu laden, Version kontrollieren, PULSAR auf Beobachten stellen und den
ersten Datenstand sowie eventuelle Quellen-/Budgetmeldungen ansehen.
