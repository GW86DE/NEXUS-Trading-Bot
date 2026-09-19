# NEXUS 10.8.1 – Installation auf dem Raspberry Pi 5

**Build:** `10.8.1-RESTZEILEN-SCHNAPPSCHUSS-UND-KERZENCURSOR` · Basis 10.8.0 · Diagnose 1.9.0

10.8.1 behebt die XRP-Sperre vom 19.09.2026 an der Wurzel (Analyse:
`NEXUS_Analyse_OKX_Sperre_2026-09-19.md`): Ein nicht lesbarer Guthabenstand ist
keine Messung mehr; Ledgerzeilen ohne Position bekommen dieselbe
Zwei-Messungen-Regel wie Positionen im Buch; ein offener Bestandsbeleg einer
Restzeile löst sich, sobald der Bestand die gebuchte Menge in zwei bestätigten
Messungen wieder deckt; die Staubregel misst die Restmenge statt des
Kontobestands; die Fill-Historie bucht nur in der eigenen Abstammungslinie;
eine Sperre vor der Signalprüfung erzeugt eine Entscheidung je Kerze statt alle
8 Sekunden, und der JSONL-Spiegel des Entscheidungsjournals rotiert (20 MB).
Kaufkaskade, Risikoprüfung, Stops, Ziele und Broker-Verhalten sind unverändert.
Es wird kein Verkauf erfunden und keine Restzeile automatisch verkauft.

## Phase 1 – Paketprüfung

```bash
bash "$HOME/Downloads/NEXUS_10.8.1_Installieren.sh" --paket-pruefen
```

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.8.1_Installieren.sh"
```

Der Installer entpackt, führt den vollständigen Volltest aus (inklusive
`tests/test_v1081_restzeilen.py` und der Architektur-Suite) und startet die
Dienste nur, wenn alle Tests bestehen. Der laufende 10.8.0-Stand ist die
Datenquelle; er bleibt bei einem Abbruch unverändert weiterlaufen.

**Datenübernahme, neu in 10.8.1:** Vom Spiegel `decision_journal.jsonl`
(auf dem Pi 833 MB) werden nur die letzten ganzen Zeilen (≤ 20 MB) in die neue
Installation übernommen. Die Migration meldet das im Systemprotokoll
(`decision_journal.jsonl: nur die letzten … MB von 833 MB uebernommen`). Die
vollständige Entscheidungshistorie liegt unverändert in
`decision_history.sqlite`; die Quelle im alten Ordner bleibt unangetastet.

## Phase 3 – Was nach dem Start von selbst passiert

**Sofort (Startkontrolle):** Der Positionsabgleich läuft wie bisher. Liegt in
diesem Moment kein lesbarer Guthabenstand vor, meldet das Log
`Guthabenstand fuer diesen Durchlauf nicht lesbar` und der Takt wird ohne
Buchung abgebrochen; Neueinstiege bleiben zu, bis ein vollständiger Abgleich
durchgelaufen ist (Bereitschaft „Positionen einmal mit dem Konto abgeglichen").

**XRP (Trade 85, Beleg offen seit 19.09. 05:20 UTC):** Im ersten Takt zählt die
Wiedersicht (`Konto weist 50000.0000532 aus und deckt die gebuchte Menge …
(1. Messung …)`); nach der zweiten bestätigten Messung (≥ 120 s) kommt die
Telegram-Meldung `Krypto XRP: Bestandsbeleg der Ledgerzeile 85 aufgeloest … XRP
ist fuer Neueinstiege wieder frei`, und der Rest (0,0000532 XRP) wird als
belegter Staub abgeschlossen. Danach ist XRP im Scan wieder ein normaler
Kandidat – einmal je 5-Minuten-Kerze.

**ETH-Reste 86/91:** werden im ersten Takt als Staub belegt abgeschlossen,
sofern ihre Abstammungslinie belegt ist; sonst bleiben sie
`RESIDUAL_EXPOSURE` (kein Sperrgrund) und der Grund steht einmal im Log.
Der Traceback `Broker-Exitanker gehoert zu einer anderen expliziten trade_id`
aus jedem Takt ist weg.

## Phase 4 – Abnahme

1. **Systemprotokoll:** Migrationszeile zum Journal-Spiegel; keine Zeile
   `konnte nicht ins Ledger geschrieben werden` mehr im Takt.
2. **Übersicht → OKX:** nach ≥ 2 Minuten kein gesperrter Coin mehr
   (`blocked_symbols` leer); Bestandsbeleg von Trade 85 `RESOLVED` mit
   `BALANCE_RESTORED:…:ledgerzeile;snapshots=2`; die Zeile selbst als
   RESIDUAL abgeschlossen (sofern die Abstammungslinie belegt ist, sonst
   `RESIDUAL_EXPOSURE` ohne Sperrwirkung).
3. **Telegram:** Meldung „Bestandsbeleg der Ledgerzeile 85 aufgeloest".
4. **Diagnose** nach ~30 Minuten
   (`bash ~/Georg/TradingBot_v10.8.1_NEXUS/NEXUS_10.8.1_Diagnose_Starten.sh --minuten 30`):
   `okx_balance_gaps` ohne PENDING-Zeile; Entscheidungsjournal: XRP höchstens
   eine BLOCKED/NO_SIGNAL-Entscheidung je 5-Minuten-Kerze;
   `decision_journal.jsonl` ≤ 20 MB (bei Rotation zusätzlich
   `decision_journal.1.jsonl`).

## Was diese Version NICHT tut

Keine neue Kaufregel, keine Änderung an Stops, Zielen oder Sperrgründen.
Restzeilen werden nie automatisch verkauft; ein fehlender Bestand ist kein
Verkaufsbeleg; UNKNOWN bleibt UNKNOWN. Der BTC-Rest 84 bleibt
`EXTERNAL_OBSERVE`, solange die BTC-Position 89 im Buch liegt (bewusst).

## Optionen (unverändert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
