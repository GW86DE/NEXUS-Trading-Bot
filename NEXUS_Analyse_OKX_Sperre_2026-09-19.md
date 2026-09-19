# NEXUS – Analyse „OKX wieder gesperrt" (Diagnose 19.09.2026 12:01–12:42 UTC, Stand 10.8.0)

Grundlage: `NEXUS_10_Diagnose_2026-09-19_12-01-47_240593_UTC_3592866b92.zip` (10.8.0 auf dem Pi,
1007 Dateien MATCH, 30 Minuten Beobachtung, 61 Messpunkte), Bot-Log ab dem 10.8.0-Start
(05:46 Uhr Berlin), Ledger-/Buchhaltungs-Exporte aus `decision_history.sqlite`, Quelltext 10.8.0.
Zeiten im Log sind Berlin (UTC+2); Zeiten in den Datenbanken sind UTC.

## 1. Kurzfassung

1. **OKX ist nicht als Konto gesperrt.** Handelsbereitschaft `trading_ready: true`, `kaeufe_erlaubt: true`,
   Risikozustand `darf_kaufen: true`, eine offene BTC-Position mit aktivem Broker-Schutz, ETH-Verkauf
   um 10:21 Uhr sauber ausgeführt (+0,96 EUR). Gesperrt ist **genau ein Coin: XRP** (Reichweite SYMBOL).
2. **Warum es sich trotzdem wie „gesperrt" anfühlt:** XRP ist derzeit der einzige Coin mit Kaufsignal.
   1.055 Kaufwünsche für XRP in 2,6 Stunden – alle am RISK_GATE abgewiesen, alle 16 anderen Coins
   `NO_SIGNAL` (Demo-Kerzen ohne Volumen), SOL zusätzlich an der Orderbuchtiefe gescheitert.
   Ergebnis: kein einziger OKX-Kauf im Fenster.
3. **Ursache der XRP-Sperre: eine vierminütige OKX-Verbindungsstörung (07:20–07:24 Uhr).** In den zwei
   Zyklen, in denen der Kontostand nicht lesbar war, hat der Ledger-Abgleich für die Lot-Rest-Zeile
   **Trade 85 (0,0000532 XRP)** einen `BALANCE_REDUCTION`-Beleg mit `observed_balance = 0` gebucht –
   obwohl das Konto die ganze Zeit 50.000,0000532 XRP hielt. Der Beleg ist dauerhaft (`PENDING`) und
   löst sich nicht von selbst, weil die Selbstheilung nur für Positionen im Positionsbuch existiert.
4. **Zwei weitere Defekte, die dieselbe Wurzel haben (Lot-Rest-Zeilen aus 10.7.0 bleiben ewig offen):**
   ETH-Endlosschleife seit 10:21 Uhr (alle 8 s ein Traceback: Fill-Historie will den heutigen
   ETH-Verkauf auf die ALTE ETH-Restzeile 86 buchen) und die Journal-Flut (XRP-Kaufwunsch alle 8,35 s
   als vollständiger Entscheidungssatz; `decision_journal.jsonl` ist 833 MB groß).
5. **Nichts davon stammt aus dem 10.8.0-Umbau.** Die beteiligten Pfade sind seit 10.6.0/10.7.0
   unverändert; auf 10.7.1 wäre exakt dasselbe passiert. Die 10.8.0-Neuerungen haben funktioniert:
   Einsatzstufe „erhöht" ist nach dem Umzug von `risk_levels` erhalten, Volltest auf dem Pi grün,
   Update und Startkontrolle erfolgreich.

Fix erfordert eine neue Version (Vorschlag **10.8.1**, Abschnitt 7). Es gibt keinen Weg über die
Oberfläche, den Beleg für eine Restzeile zu lösen; ein Neustart hilft nicht (Beleg steht in der Datenbank).

## 2. Zustand OKX laut Diagnose (Endsammlung 12:41 UTC)

| Punkt | Wert |
|---|---|
| Verbindung | VERBUNDEN, REST OK, Privatstream SUBSCRIBED, 0 REST-Fehler seit 07:24 Uhr |
| Handelsbereitschaft | `trading_ready: true`, Grund „handelsbereit", keine offene Bedingung |
| Risiko | `darf_kaufen: true`, Kontowert 172.317,89 EUR, Tages-P&L +0,96, Einsatzstufe **erhöht** (1,2 % je Trade, Deckel 20 %) |
| Sperren | `domaene_gesperrt: false`; **1 Sperre: XRP, BALANCE_REDUCTION, Trade 85, Reichweite SYMBOL, Ablauf BELEG** |
| Positionen | BTC-EUR 0,00117908 @ 70.488,50 (seit 00:20 UTC, 10.7.1), Stop 63.439,65, TP 73.639,16, Schutz ACTIVE, Eigentum verifiziert |
| Letzter Zyklus | Scan: 1 Instrument geprüft (XRP) → abgelehnt „offener Bestands-/Buchungsbeleg; kein Neueinstieg in diesen Coin, andere Käufe frei" |
| Guthaben | XRP 50.000,0000532 · ETH 10,00000024 · BTC 1,00117909 · EUR 611,06 · USDC 99.595,22 · USD 100.000 |
| Entscheidungen 10:00–12:37 UTC | 1.055 × XRP BLOCKED (RISK_GATE) · 30 × SOL BLOCKED (Orderbuchtiefe) · 16 Coins × 31–32 NO_SIGNAL |

Die ETORO-Zeile „Käufe GESPERRT: US-Markt geschlossen" ist das Wochenende (Samstag), keine Sperre.

## 3. Zeitlinie 19.09.2026 (Berlin)

| Zeit | Ereignis | Beleg |
|---|---|---|
| 02:20 / 03:30 | BTC-Kauf (Trade 89) und ETH-Kauf (Trade 90) noch auf 10.7.1 | Ledger-Export |
| 05:41–05:46 | Installation 10.8.0: Sicherung, Migration, Reparaturlauf `repair_okx_accounting` (Konto 244895ca: **keine Lücken**), Volltest OK, Dienste neu gestartet | `nexus_update.log`, `okx_accounting_repair_report.json` (03:43 UTC) |
| 07:20:04 | OKX antwortet nicht mehr (ConnectionError): Verkaufsfills, Lotgrößen, Schutzorders nicht abrufbar; Klassifizierung ohne Kurs → XRP-Rest vorübergehend RESIDUAL_EXPOSURE | Log Z. 124–138 |
| **07:20:10 / 07:20:17** | „Guthabenstand für diesen Durchlauf nicht lesbar" – zwei Zyklen mit leerem Kontoschnappschuss. In genau diesen Zyklen entsteht der Beleg: `okx_balance_gaps` Trade 85, `first_seen 05:20:10.87 UTC`, `last_seen 05:20:17.61 UTC`, `observed_balance "0"`, `tracked_quantity 0.0000532` | Log Z. 155/186, Tabelle `okx_balance_gaps` |
| 07:20:23 | REST-Prüfung 3/3 fehlgeschlagen → OKX OFFLINE, Telegram-Meldung | Log Z. 213 |
| 07:24:23 | OKX wieder verbunden. Sofort: „Neueinstiege nur für XRP gesperrt (offener Bestands-/Buchungsbeleg dieses Coins)" | Log Z. 285–289 |
| 10:21:05–10:21:11 | Freqtrade-ROI-Ausgang ETH: Verkauf 0,0321828 ETH @ 2.308,10, +0,96 EUR, Trade 90 CLOSED, Restmenge 8,49e-08 als neue Restzeile 91 | Log Z. 455–463, Ledger |
| ab 10:21:11 | Alle ~8 s: „Verkauf okx ETH ist keiner Ledgerzeile zuzuordnen: Broker-Exitanker gehört zu einer anderen expliziten trade_id" + Traceback (bis Diagnoseende 1.515 Meldungen / 3.032 Tracebacks im gekürzten Log) | Log ab Z. 464 |
| 10:00–12:37 UTC | XRP-Kaufwunsch alle 8,35 s (Median) als BLOCKED journaliert: 1.055 Sätze | `decisions`-Export |
| 12:01–12:42 UTC | Diagnose | – |

## 4. Befund A – die XRP-Sperre (Trade 85)

### Was Trade 85 ist
Die Lot-Rest-Zeile aus 10.7.0/10.7.1: Beim Verkauf von Trade 81 (18,3284 XRP) blieben 0,0000532 XRP unter der
Lotgröße übrig; nach der 10.7.0-Regel „Rest läuft als eigene Zeile weiter" ist das eine eigene offene
Ledgerzeile (`accounting_kind TRADE`, Status `RESIDUAL_EXPOSURE`, kein Eintrag im Positionsbuch). Ebenso
existieren Restzeile 86 (ETH 5,26e-08, aus Trade 83), 84 (BTC 8,06e-09) und seit heute 91 (ETH 8,49e-08).

### Wie der Beleg entstand (Code 10.8.0, unverändert seit 10.6.0/10.7.0)
1. `crypto_engine._pruefe_positionen`: `broker.guthaben_schnappschuss()` wirft `VerbindungVerloren`.
   Der Fehler wird nur protokolliert (`OKX_BALANCE_SNAPSHOT_UNAVAILABLE`), der Zyklus **läuft mit leerem
   Schnappschuss `{}` weiter**.
2. `broker.positionen(guthaben_snapshot={})` liefert eine **leere Liste ohne Fehler** (kein Guthaben → keine
   Positionen), also `bestaende = {}`.
3. Für die Positionen im Buch (BTC, ETH) greift der 10.6.0-Schutz: „Bestand fehlt im Schnappschuss (1./2./3.
   Messung)" – zwei Messungen mit 30 s Mindestabstand wären nötig, die Störung war mit 13 s zu kurz.
   **Die Positionen blieben unversehrt.**
4. Für offene Ledgerzeilen **ohne** Positionsbuch-Eintrag (`_offene_ledger_abgleichen`) gibt es diesen
   Schutz nicht: `konto = 0` → sofort `mark_balance_gap(trade, 0, 0.0000532)` → Beleg `BALANCE_REDUCTION`,
   Status `BROKER_STATE_UNKNOWN`. Einmalige Messung, keine Prüfung, ob der Schnappschuss gültig war.
   Die Tabelle zeigt es exakt: `observed_balance "0"` zu den beiden Zeitpunkten der fehlgeschlagenen
   Kontoabfragen.

### Warum sich der Beleg nicht selbst löst
- `resolve_balance_gap_restored` (10.6.0, „Bestand wieder da nach zwei Messungen") wird nur aus der
  Positionsschleife aufgerufen (`_bestand_zurueckgewonnen(position, …)`) – Restzeilen ohne Position
  erreichen ihn nie.
- `repair_gaps_explained_by_lineage` (10.7.0) erklärt Lücken **geschlossener** Trades über ihre
  Restzeilen; hier hat die Restzeile selbst die Lücke.
- Die Staub-Schließung (`okx_residual_inventory.classify` bei „Kontobestand × Kurs ≤ 1 EUR") greift nie,
  weil sie den **gesamten Kontobestand** (50.000 XRP Demo-Startguthaben) statt der Restmenge bewertet.
  Auf einem Konto ohne Fremdbestände wäre die Restzeile längst geschlossen. Dieselbe Rechnung hält auch
  die ETH-Restzeilen (10 ETH Startguthaben) und BTC (1 BTC) offen.
- Auflösung laut Sperrliste: „Verkaufsbeleg (auch mit Lot-Rest) oder Bestand wieder da" – ein Verkauf
  von 0,0000532 XRP ist unter der Lotgröße unmöglich, „Bestand wieder da" hat für Restzeilen keinen Pfad.

### Wirkung
Nur XRP-Neueinstiege sind gesperrt (korrekte Reichweite SYMBOL nach 10.7.0). Aber XRP ist im Moment der
einzige Coin mit Kaufsignal, deshalb wirkt es wie eine Gesamtsperre.

## 5. Befund B – ETH-Endlosschleife seit dem Verkauf 10:21 Uhr

`historical_exit` (Nachladen unverbuchter SELL-Fills) läuft je Zyklus über **alle** offenen Ledgerzeilen ohne
Position – auch über die alte ETH-Restzeile 86 (Lot-Rest von Trade 83, 18.09.). Es holt die Fill-Historie
seit deren Einstieg (18.09. 00:55 UTC) und hält jede Bot-Verkaufsorder desselben Symbols für zuordenbar:
Der heutige ETH-Verkauf (Order 3936057951182520320) ist eine registrierte Bot-Exitorder → nicht in der
Fill-Liste der Gruppe von Trade 83 → „unverbucht" → `trade_close(trade_id=86, exit_order_id=…)`. Der
Ledger lehnt korrekt ab (der Exitanker gehört zu Trade 90). Es gibt aber weder ein Merken der Ablehnung
noch eine Begrenzung → alle ~8 s ein neuer Versuch mit zwei Tracebacks. Für die heutige Restzeile 91
passiert das nicht (der Fill steht in der Gruppe von Trade 90). Der Ledger ist konsistent; Wirkung: Log-
und CPU-Last, ~700 Meldungen pro Stunde, keine Sperre. **Beim nächsten XRP-Verkauf passiert dasselbe für
Restzeile 85.**

## 6. Befund C – Entscheidungsjournal-Flut

Im FREQTRADE-Modus merkt sich der Scan-Cursor je Kerze, welche Coins bewertet wurden – aber erst nach der
Signalauswertung. Ein Coin, der **vor** der Signalauswertung an der Symbolsperre scheitert, wird nie als
„gesehen" markiert und deshalb in jedem 8-Sekunden-Zyklus erneut geprüft und als vollständiger BLOCKED-
Entscheidungssatz journaliert (Marktdaten leer, `price: null`). 1.055 Sätze in 2,6 h; am 18.09. waren es
1.072 in 30 Minuten für drei Coins. `decision_journal.jsonl` hat inzwischen 833 MB – der Diagnose-Export
konnte deshalb mehrere Dateien nur gekürzt aufnehmen (`AUSLASSUNGEN.json`). Nebeneffekt: Die XRP-EUR-
Kerzen im Signalcache sind seit 05:15 UTC nicht mehr erneuert worden, weil die Ablehnung vor dem Abruf greift.

## 7. Nebenbefunde (keine Sperren, aber relevant)

- **SOL:** 30 Kaufwünsche 11:30–11:35 UTC blockiert: „Kein geeigneter Kostenpfad: SOL-USD: Orderbuchtiefe
  reicht für 107 SOL nicht". Die EUR-Lane hat nur noch 611 EUR (BTC-Kauf heute Nacht), deshalb wird über
  USD geroutet (100.000 USD); mit Einsatzstufe „erhöht" ergibt das ~21.000 USD je Trade, dafür ist das
  Demo-Orderbuch von SOL-USD zu dünn. Das ist die Kehrseite der USD-Freigabe plus Stufe „erhöht", kein Fehler.
- **Demo-Kerzenqualität:** 6 von 8 geprüften Instrumenten ohne jüngstes Kerzenvolumen (Zero-Volume-Anteil
  0,67–1,0; DOGE-EUR komplett flach) → `NO_SIGNAL` ist dort systembedingt.
- **OKX-Verbindung:** zwei Störungen seit dem Start (05:50 ReadTimeout, 07:20–07:24 ConnectionError),
  beide korrekt behandelt (DEGRADED → OFFLINE → Wiederverbindung, Telegram-Meldungen).
- **eToro:** 26 UNMATCHED Ereignisse im privaten Stream (bekannter Altbefund), 12 Nutzerabrechnungen,
  historische Kostenlücken unverändert; Markt zu (Samstag).
- **10.8.0 im Betrieb:** Einsatzstufe „erhöht" erhalten (Umzug von `risk_levels` ohne Zustandsverlust),
  Volltest inkl. Architektur-Suite auf dem Pi grün, PULSAR 5 Karten exportiert (Sitzung geschlossen,
  deshalb heute keine Quote-Nachladung/Kerzen zu erwarten). Die Luna-Migration ist im Export nicht sichtbar
  (`ai_router_settings.json` wegen Ausgabelimit ausgelassen) – am Montag in den Einstellungen prüfen.

## 8. Vorschlag 10.8.1 (WARTET AUF FREIGABE)

Kein Umbau, fünf gezielte Korrekturen an der Wurzel „Restzeile ohne Position" plus Aufräumen:

1. **Kein Beleg aus einem ungültigen Schnappschuss.** Schlägt `guthaben_schnappschuss` fehl, überspringt der
   Zyklus den Ledger-Abgleich und die Klassifizierung (Bereitschaft „reconciliation" bleibt offen); es wird
   nichts gebucht und nichts gesperrt. `broker.positionen()` mit leerem Schnappschuss meldet den Fehler
   statt einer leeren Liste.
2. **Gleiche Bestätigungsregel für Ledgerzeilen wie für Positionen:** `BALANCE_REDUCTION` für eine Zeile
   ohne Positionsbuch-Eintrag erst nach zwei gültigen Messungen mit ≥ 30 s Abstand (der vorhandene Zähler
   `_fehlender_ledger_bestand` wird dafür benutzt, er ist heute wirkungslos).
3. **Selbstheilung „Bestand wieder da" auch für Restzeilen** (`resolve_balance_gap_restored` aus dem
   Ledger-Abgleich, zwei gültige Messungen, ≥ 120 s) – löst Trade 85 im ersten stabilen Zyklus nach dem
   Update, mit Beleg (Schnappschuss, Zeit, Menge), ohne Handarbeit.
4. **Staubregel auf die Restmenge beziehen:** Restzeile × Kurs ≤ 1 EUR → Schließung über
   `okx_residual_inventory.classify` wie vorgesehen, unabhängig davon, wie viel Fremdbestand (Demo-Startguthaben)
   das Konto sonst hält. Damit verschwinden 84/85/86/91 als Dauerbaustellen, jede künftige Restzeile schließt
   sich innerhalb eines Zyklus.
5. **Fill-Historie nur in der eigenen Abstammungslinie:** `historical_exit` darf einen Verkauf nur einer Zeile
   zuordnen, deren Einstiegskette (Entry-Order) zum Fill passt; bereits an einen anderen Trade gebundene
   Exitanker werden vor dem Buchungsversuch ausgeschlossen; eine Ablehnung wird je (Trade, Order) gemerkt,
   nicht alle 8 s wiederholt.
6. **Journal-Flut:** ein an einer Symbolsperre gescheiterter Coin wird für die laufende Kerze als geprüft
   markiert (eine BLOCKED-Entscheidung je Kerze statt alle 8 s); dazu eine Kompaktierung des
   `decision_journal.jsonl` (heute 833 MB) im Installer mit Sicherung.

Tests: die Datenlage vom 19.09. wird 1:1 nachgestellt (Verbindungsabbruch mit leerem Schnappschuss → kein
Beleg; Restzeile 85 mit Beleg + Bestand 50.000,0000532 → Selbstheilung; ETH-Verkauf mit alter Restzeile →
keine Fehlbuchung, kein zweiter Versuch; Staub-Schließung mit Fremdbestand). Bau wie immer mit komplettem
Testbestand, Je-Test-Vergleich gegen das 10.8.0-Paket und Installer-Selbsttest.

**Bis dahin:** Es besteht kein Handlungsdruck – die BTC-Position ist geschützt, alle anderen Coins sind frei,
XRP-Käufe sind gesperrt, sonst nichts. Ein Neustart ändert nichts (Beleg in der Datenbank), ein manueller
XRP-Verkauf ist unmöglich (0,0000532 XRP unter der Lotgröße).
