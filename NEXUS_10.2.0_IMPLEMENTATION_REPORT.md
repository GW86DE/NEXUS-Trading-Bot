# NEXUS 10.2.0 – Implementierungsbericht

**Build:** `10.2.0-STRATEGY-EXPANSION-AND-RESILIENCE` · Basis: 10.1.10-NEXUS (Revision 2) · Stand: 17.09.2026

## 1. Sechs Zusatzstrategien, live schaltbar (Kernauftrag)

**Neues Modul `zusatz_strategien.py`** ist die EINZIGE Quelle der Signal-Mathematik:
Wilder-RSI, True-Range-ATR und sechs Signal-Builder — eToro: `RSI2_MEAN_REVERSION`
(Connors, RSI(2)<10 ueber SMA200, Exit ueber SMA5), `HIGH_52W_MOMENTUM`
(George/Hwang 2004, >=97 % des 52-Wochen-Hochs ueber SMA100, Exit unter SMA50),
`GOLDEN_CROSS_TREND` (SMA50/200); OKX: `TSMOM_LONG_FLAT` (90-Tage-Rendite>0 ueber
SMA100, Exit 30-Tage-Rendite<0), `KELTNER_BREAKOUT` (Schluss ueber EMA20+2xATR10,
Exit unter EMA20), `MACD_TREND_CRYPTO` (12/26/9, nur ueber Null). Je Strategie:
Label, Version (`NEXUS-ZUSATZ-*-V1`), kanonischer Parameter-Hash (SHA256 ueber die
Semantikfelder, Muster des Freqtrade-Referenzmodus), Mindestkerzenzahl,
History-Anforderung, Quellenverweis. `bewerte()` wertet ausschliesslich
abgeschlossene Tageskerzen aus (laufende Kerze wird zusaetzlich defensiv
verworfen) und wirft bei zu wenig Daten.

**Identitaet mit dem Backtest V6 ist erzwungen:** `tests/test_v1020_zusatzstrategien.py`
extrahiert die Signalfunktionen aus dem eingebetteten Python des paketierten
`NEXUS_Universum_Backtest_V6.sh` und verlangt Signalgleichheit auf gemeinsamen
Daten. Was der Backtest bewertet, handelt der Bot.

**OKX-Integration** (`crypto_strategy_mode.py`, `crypto_engine.py`): drei neue
Modi in `VALID_MODES`/`ZUSATZ_MODES`; `entry_snapshot`/`signal_timeframe`/
`strategy_is_resolved` erweitert. Im Kaufweg laedt der Zusatzzweig Tageskerzen
(`broker.historie(..., "1 day")`, OKX-Kappung 300 Kerzen reicht: max. 195
benoetigt), bewertet, protokolliert Indikatoren und ueberspringt nur die
NEXUS-eigene 15m/1h-Bestaetigung; ALLE weiteren Gates und die Ausfuehrung sind
unveraendert (Stop/Take weiterhin NEXUS-ATR-Plan, `fill_anchored_stop_take`
unveraenderter Nicht-Freqtrade-Zweig). Ein persistenter Tageskerzen-Cursor
(`_zusatz_cursor`, ScanCursor je Konto+Parameter-Hash, Intent-first wie beim
Freqtrade-Modus) verhindert einen zweiten Kaufversuch aus derselben Kerze.
Positionsausstieg: `_strategy_exit` -> `_zusatz_exit` verifiziert Version+Hash
des unveraenderlichen Positions-Snapshots (Abweichung pausiert nur den
Strategie-Ausstieg, Broker-Schutz/Eigentum unangetastet — Politik des nicht
migrierbaren Freqtrade-Snapshots) und verkauft beim Exit-Signal der
Einstiegsstrategie.

**eToro-Integration** (`etoro_strategy_mode.py` neu, `live_trader.py`):
persistenter Schalter nach dem Muster der Kryptoseite (nur neue Einstiege,
Historie, Telegram-Meldung), bewusst OHNE Pausenmodus und mit konservativem
Rueckfall auf NEXUS Standard bei defekter Modusdatei. Im Scanner ersetzt der
aktive Zusatzmodus nur das Einstiegssignal (Tageskerzen; Preis/ATR fuer
Stop/Sizing aus der frischen 1h-Reihe bzw. Tages-ATR); Session-, News-,
Earnings-, KI-, Kosten-, Cash-, Risiko-Gates und `submit_protected_buy`
unveraendert. Der Strategie-Snapshot wandert via `pending_meta` (PULSAR-Muster)
in Ownership-Registry und Trade-Ledger. Im Positionszyklus wird eine per Ledger
belegte Zusatzstrategie-Position (`entry_strategy_mode` + passender
Parameter-Hash) von genau ihrer Strategie verkauft; jede Unklarheit faellt auf
das NEXUS-Standardsignal zurueck, Broker-Stop/TP, Time-Stop und News-Exits
unveraendert.

**WebUI:** OKX-Strategieblock um drei Buttons erweitert; neuer Abschnitt
„eToro Aktien-Strategiemodus" (settings.html/settings.js, Zustandsanzeige,
Phrase `STRATEGIE AKTIVIEREN` fuer jede Zusatzstrategie); neuer Endpunkt
`POST /api/etoro-strategy`; Settings-Snapshot fuehrt `etoro_strategy`.
Erklaertexte: Backtest-Seite „Strategien einfach erklaert" (10.1.10-Arbeitsstand,
in diesem Paket enthalten).

## 2. EXIT_IN_PROGRESS (DOGE-Fall 17.09.2026)

`okx_snapshot_guard.py`: Fehlt Bestand, weil ein BOT-EIGENER Schutz-Exit
teilerfuellt ist, wird das ueber `protection_algo_id` -> belegte Order-IDs ->
bestaetigte SELL-`accFillSz` nachgewiesen (`_eigene_exit_fills`; Quellen:
Positionsbuch UND offene Ledger-Zeilen). Erklaerte Symbole wandern aus
`missing` nach `exit_in_progress`; der Guard bleibt gueltig und nennt den
Zustand im Detailtext. `crypto_engine` sperrt dann NUR die betroffenen Werte
fuer Neueinstiege (`_exit_in_progress_symbole`, eigenes Gate im Kaufweg,
einmalige Meldung). Unerklaerte Fehlmengen und jede nicht lesbare
Orderauskunft verhalten sich exakt wie bisher (Domaenensperre).

## 3. Mehrlagen-native Equity-Bremse (USDC+USD)

`risk_pots.aktualisiere_kontowerte` baut bei >=2 finanzierten Waehrungen je
Lane native Betraege + beobachtete Kurse (`native_beitraege`); fehlt irgendwo
ein Kurs, gibt es fuer den Takt keine native Reihe (keine erfundenen
Paritaeten). `risk_manager` fuehrt die Reihe als Summe der Lanes mit am
Tagesstart eingefrorenen Kursen (neues RiskState-Feld `native_fx_day_start`,
in `risk_basis_review.OPTIONAL_FIELDS_10_2_0` klassifiziert und beim
Checkpoint-Vergleich mit Default normalisiert — historische Checkpoints
bleiben gueltig). Lane-Set-Wechsel = Basis-Ereignis mit Neustart der
Tagesbasis (deckt die untertaegige Waehrungsfreigabe ab). Der
Genau-eine-Lane-Pfad aus 10.1.10 ist unveraendert (Tests laufen weiter).

## 4. Gebuehren-Snapshots offener eToro-Positionen (Vorarbeit)

`etoro_fee_snapshot.py` haengt am bestehenden P&L-Abruf (kein zusaetzlicher
API-Call) und sichert je offener Position die gelieferten Gebuehren-/
Umrechnungsfelder samt Identitaet, Erst-/Letztsichtung und Snapshot-ID nach
`etoro_fee_snapshots.json` (gedeckelt, atomar, je Konto+Umgebung). Fehlen
Felder, wird ehrlich `KEINE_GEBUEHRENFELDER_GELIEFERT` gespeichert. Der
Ergebnisabgleich selbst ist unveraendert; die Snapshots sind Belegmaterial
fuer kuenftige Automatisierung.

## 5. Installations-/Update-Robustheit

- **Installer** (10.2.0-Kopf): Banner vor jeder Payload-Pruefung (bash+python);
  leere/abgeschnittene Datei meldet sich sichtbar. Ein vorhandener,
  abweichender Zielstand wird nur dann automatisch nach
  `ALT_<zeitstempel>_<name>` archiviert, wenn systemd beweisbar KEINEN
  NEXUS-Dienst (WorkingDirectory, laufende MainPID-cwd) in diesem Verzeichnis
  kennt; aktive/unklare Staende brechen unveraendert hart ab. Nichts wird
  geloescht.
- **Updater** (`nexus_update.py`): Fehlschlag in Phase 1 (Offline-Test, Dienste
  liefen nie aus dem Staging, nichts promotet) archiviert das eigene
  Zielverzeichnis nach `ALT_FEHLGESCHLAGEN_<zeitstempel>_<name>`.
- **Release-Checkliste automatisiert** (`volltest._release_checklist`):
  BOM-Scan, Verbot alter versionsgebundener Diagnose-Einstiege,
  Pflicht-Klassifizierung neuer RiskState-Felder, Abgleich der
  settings.html-Felder mit dem Vertragstest. Laufzeitdateien-Verbotsliste um
  `etoro_strategy_mode.json`/`etoro_fee_snapshots.json` erweitert.

## 6. Versionsfuehrung

VERSION.txt/config.VERSION_NEXUS/RELEASE_BUILD.txt auf 10.2.0; Diagnose-
Einstieg `NEXUS_10_2_0_Diagnose.py` + `NEXUS_10.2.0_Diagnose_Starten.sh`
(alter 10.1.10-Einstieg entfernt — Checklistenregel); Testpins
(test_v1019_report_repairs, test_v976) fortgeschrieben; volltest-
Pflichtdateienliste um die 10.2.0-Artefakte ergaenzt.

## Geaenderte/neue Dateien (Kern)

Neu: `zusatz_strategien.py`, `etoro_strategy_mode.py`, `etoro_fee_snapshot.py`,
`NEXUS_10_2_0_Diagnose.py`, `NEXUS_10.2.0_Diagnose_Starten.sh`,
`tests/test_v1020_{zusatzstrategien,exit_in_progress,multilane_equity,fee_snapshot}.py`,
Doku 10.2.0. Geaendert: `crypto_strategy_mode.py`, `crypto_engine.py`,
`live_trader.py`, `okx_snapshot_guard.py`, `risk_manager.py`, `risk_pots.py`,
`risk_basis_review.py`, `broker/etoro.py`, `nexus_update.py`, `volltest.py`,
`config.py`, `webui/{app.py,settings_store.py}`,
`webui/templates/settings.html`, `webui/static/settings.js`; aus dem
10.1.10-Arbeitsstand enthalten: Backtest V6 + WebUI-Berichtsknopf-Fix +
„Strategien einfach erklaert".
