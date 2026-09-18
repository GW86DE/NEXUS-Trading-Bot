# NEXUS 9.9.0 FIX1 — eToro und PULSAR

Build: `9.9.0-FIX1-ETORO-PULSAR`, 13. September 2026.

Diese Korrektur baut auf 9.9.0 auf. Handelsstrategie, FMP-Tarifumschaltung,
Kerzenversorgung und täglicher Universumswechsel bleiben auf diesem Stand.

## Änderungen

- eToro übernimmt explizite Gebühren und Steuern aus dem passenden nativen
  Einstiegsbeleg. Ein neuer Hintergrundnachlauf erreicht auch bereits
  geschlossene, eindeutig kontogebundene Positionen. Er bearbeitet höchstens
  einen fälligen Auftrag je Schritt. Bei fehlenden externen Abschlusskosten
  erfolgt die nächste Prüfung nach sechs Stunden; Fehler werden nach 30 Minuten
  erneut geprüft. Konto, Umgebung, Order, Position, Ausführungszeit, Menge und
  Preis müssen übereinstimmen. Nicht passende Belege bleiben offen.
- Die bekannten sechs Einstiegsgebühren von jeweils 1 USD können damit
  automatisch nachgeführt werden. `fees=0` in der alten eToro-Handelshistorie
  beweist keine vollständigen externen Abschlusskosten. Die sechs Nettoergebnisse
  bleiben daher ungeklärt, bis diese Kosten belegt sind. Es wird kein Verkauf
  erneut ausgelöst. Frühere vorläufige Werte bleiben in `etoro_entry_cost_audit`
  erhalten. Der ausführliche Abrufstatus steht im jeweiligen `fee_recovery`-
  Eintrag von `etoro_reconciliation.json`.
- PULSAR bekommt einen eindeutigen Antwortvertrag je Kandidat mit genau drei
  Risiken und zulässigen Quellenreferenzen. Kurze Referenzen werden vor der
  Speicherung auf die vollständigen Belegkennungen zurückgeführt. Der tatsächliche
  Validierungsfehler ersetzt die irreführende Fehlermeldung „Standardrouting“.
  Die zuletzt abgelehnte Antwort wird begrenzt für sieben Tage zur Diagnose
  aufbewahrt. Eine fehlgeschlagene Vorprüfung erhält höchstens zwei zusätzliche
  Versuche nach jeweils 15 Minuten, innerhalb von zwei Stunden, auch am Wochenende.
  Bestehende Budgets und Modusprüfungen gelten weiterhin. Die Wiederholung nutzt
  die gespeicherten Marktbelege; sie startet keine neue Markt-Abfrageserie.
- SUI: Der alte fehlgeschlagene Verkaufsversuch wird nach exakter Ledgerzuordnung
  mit dem später erfolgreichen Abschluss verbunden. Der ursprüngliche Beleg wird
  nicht umgeschrieben.
- eToro-Anzeige: Tatsächlich gelesene SL/TP-Werte sind vom noch offenen Abgleich
  mit dem Plan getrennt. Bestätigte Einstiegskosten werden separat angezeigt.
  Alte Kurse behalten Datum und Quellenhinweis und werden nicht als aktuelle
  ausführbare Preise dargestellt.

## Installation auf dem Pi

Den bereitgestellten `NEXUS_9.9.0_FIX1_Installieren.sh` unter Downloads speichern
und als georg ohne `sudo` starten:

```bash
bash "$HOME/Downloads/NEXUS_9.9.0_FIX1_Installieren.sh"
```

Der Starter prüft das Paket und verwendet anschließend den vorhandenen
Updateablauf: aktiven Core-Dienst ermitteln, Daten sichern, Dienste kontrolliert
anhalten, Einstellungen und Zustand übernehmen, prüfen und starten. Er installiert
in einen eigenen FIX1-Ordner und überschreibt keinen fremden Quellstand. Der
automatische Start ist wie bisher auf den bestätigten Demo-Ausgangsstand begrenzt.
Ein Fehler vor der Freigabe bleibt ein Fehler; die Ausgabe nennt den betreffenden
Schritt. Vorhandene Konfiguration und API-Zugänge werden lokal übernommen und
sind nicht im Quellpaket enthalten.

Nur das Downloadpaket ohne Dienstaktion prüfen:

```bash
bash "$HOME/Downloads/NEXUS_9.9.0_FIX1_Installieren.sh" --paket-pruefen
```

Bei manueller ZIP-Installation das ZIP entpacken und im neuen Ordner
`bash Nexus_Update.sh` ausführen. Anschließend die Handelsseite im Browser neu
laden. Nach den ersten Hintergrundschritten sollten die Einstiegskosten der
sechs Aktien sichtbar sein; ihre offenen Abschlusskosten dürfen weiterhin in
„Klärung“ stehen. Nach einem PULSAR-Lauf muss entweder eine validierte Vorprüfung
oder ein konkreter Fehler erscheinen.

## Noch benötigter Beleg

Für ADBE, CRM, JPM, KO, TXN und AMD fehlen explizite externe Abschlussgebühren
bzw. deren bestätigte Gebührenfreiheit. Dafür ist ein eToro-Kontoauszug oder
eine Abrechnung mit Positionskennung und vollständigen Kosten vom 01. bis
10. September 2026 geeignet. Die bereits vorliegenden Übersichtslisten und
Einstiegs-Lookups müssen nicht erneut exportiert werden.

PEP: Broker-SL 135,90 und TP 138,52 USD weichen vom Plan 135,8946 und 138,5207
USD ab. Die gelieferten Instrumentdaten bestätigen keine zulässige Rundungsregel.
Deshalb wird keine Toleranz geraten und kein Brokerauftrag verändert. Der letzte
belegte Kurs stammt vom Freitag, 11. September, nicht vom Sonntag des Exports.

Community-Einzelbelege bleiben ein optionaler Zusatz. Aggregierte Erwähnungen
beweisen keine Zahl unabhängiger Autoren. Der Tradestie-Zertifikatsfehler wird
nicht durch Abschalten der TLS-Prüfung umgangen und verhindert keine GPT-Vorprüfung.

Die Tests verwenden isolierte Datenkopien und simulierte HTTP-Antworten. Eine
erfolgreiche echte GPT-Antwort nach Installation und der Betrieb auf deinem Pi
können erst dort bestätigt werden.
