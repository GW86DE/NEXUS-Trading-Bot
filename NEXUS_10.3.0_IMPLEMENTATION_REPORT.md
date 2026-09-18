# NEXUS 10.3.0 – Implementierungsbericht

**Build:** `10.3.0-PULSAR-HYPE-LANE` · Basis: 10.2.2-NEXUS · Stand: 17.09.2026

## Teil A – OKX-Freigabe (Prioritaet)

Die Diagnose `NEXUS_10_Diagnose_2026-09-17_12-57-35` bewies unter laufender
10.2.2 zwei getrennte Rest-Blocker. Beide Fixes sind reine Lesart-Korrekturen
ohne Umbuchung:

1. **`risk_manager.resolve_unknown_result_at`**: Methoden-Whitelist um
   `EUR_REFERENCE_CASHFLOWS_COMPOSITE_V1` erweitert. Der 10.2.2-Autovaluator
   speicherte den EUR-Beleg fuer Trade 73 korrekt (netto 298,73 EUR), der
   Risiko-Abgleich kannte aber nur die einfache Methodenkennung und lehnte den
   Beleg dauerhaft ab. Alle uebrigen Pruefungen (Quelle, Waehrung, Qualitaet,
   64-Zeichen-Hash) bleiben unveraendert streng.
2. **`exposure_klassifizierung`**: Im Zweig „Offener Trade im Ledger, aber
   kein Eintrag im Positionsbuch" wird der Saldo jetzt nach dem bereits
   dokumentierten Fungibilitaetsprinzip (Lektion vom 28.08.2026) gesplittet:
   Nur `min(saldo, ledger_rest)` bindet an den Trade; ein Ueberhang ist ein
   getrenntes Konto-Asset; liegt der gebundene Rest unter der Staubgrenze
   (1 EUR Gegenwert), ist er Staub und sperrt nicht. Ohne Kursbeleg gibt es
   keine Staub-Entwarnung (UNKNOWN bleibt UNKNOWN); ein signifikanter
   Ledger-Rest ohne Deckung sperrt weiterhin. Ausloeser: Das OKX-Demo-Konto
   traegt Standard-Startguthaben (1 BTC, 10 ETH, 50.000 XRP, 5 ETC, …); nach
   den Schutzverkaeufen vom 16.09. kollidierten 1 BTC/10 ETH mit den offenen
   Staub-Restzeilen 2,79e-9 BTC / 1,02e-7 ETH und sperrten alle Kaeufe.
3. **`risk_result_recovery`**: Ratenbremse je (Konto, Zeile, Fehlertext) —
   hoechstens eine WARNING je 10 Minuten, Wiederholungen als DEBUG.

## Teil B – PULSAR 2.0: Hype-Spur

Umbau gemaess freigegebenem Konzept (NEXUS_PULSAR_Umbaukonzept.md) mit den
vier Entscheidungen: Terra-Doppelpruefung gestrichen, Zeitstop 10 Handelstage,
Earnings-Sperre behalten, X-Budget umgeschichtet.

- **`pulsar/evidence.py`** (REVISION `PULSAR-2.0-HYPE-LANE`): `evaluate`
  bewertet Social-Spike (Reddit-Anbieterfelder bzw. X-Stichprobe) plus
  Kurs-/Volumenbestaetigung (Intraday gegen letzten Tagesschluss, sonst
  letzte Tageskerze) plus Kartenbloecke plus Luna-Warnfilter. Kein Score,
  keine Gewichte, keine Community-Kriterien, keine 14/28-Tage-Baseline.
  `valid_financials` bleibt als Bibliotheksfunktion erhalten.
- **`pulsar/source_coordination.py`**: `x_count_attention`
  (COMPLETE_DAILY_X_COUNTS) entfernt; `attention_fresh` = Reddit 1 h,
  X-Stichprobe 24 h.
- **`pulsar/research.py`**: `stable_candidate` verlangt zwei eligible
  Hype-Messungen (>=10 min Abstand, juengste <=1 h); Wochensimulation
  (`record_observation_week`/`observation_weeks`) entfernt; neu
  `merke_universumsgaeste`/`universe_guests` (Tabelle `universe_guests`,
  max 10, 14 Tage Verfall).
- **`pulsar/worker.py`**: Terra-Vertiefung/Gegenpruefung und deren Caches aus
  dem Zyklus entfernt (Luna-Vorpruefung + eine Ereignisrecherche pro Zyklus
  bleiben); nach der Bewertung Universumsgast-Pflege und `_hype_alarm`
  (Telegram, Dedupe 24 h je Symbol).
- **`pulsar/core.py`**: Kauf-Gate = `eligible`/`HYPE_KANDIDAT` (statt
  Score>=80 + Terra); Plan traegt `max_hold_sessions` aus den Regeln (10)
  und `review_sessions` 5. Einstiegsfenster, Earnings-Abstand,
  30-Tage-Verlustbremse, Kosten-/Risikogates unveraendert.
- **`pulsar/control.py`** (RULES `PULSAR-2.0`): `max_open` 1,
  `daily_nominations` 1 (Audit-basiert, Europe/Berlin), `max_hold_sessions`
  10, `total_cap_pct` 3 %. Der Wochen-Slot wird nicht mehr bei der
  Nominierung verbraucht; `pulsar_weeks` ist reines Fill-Journal, der alte
  Wochenkonflikt schaltet den Modus nicht mehr um.
- **`pulsar/positions.py`**: Zeitstop/Review aus dem eingefrorenen Plan
  (Altplaene ohne Feld behalten 20/10). Exit-Policy sonst unveraendert.
- **`pulsar/telegram.py`**: `notify_hype`/`hype_text` neu; Haltedauer-Text
  aus dem Plan; Ablehnungstext auf Tagesregel.
- **`market_intelligence/`**: Zaehlungsabrufe entfernt
  (`MAX_COUNTS_PER_DAY = 0`, tick ohne counts-Schleife), Kandidaten-Suchen
  3->5/Tag (`candidate_research.INTERVAL = 86400//5`), Makro-Suchen 3->2/Tag
  (12-h-Slot). Schaetzung 14,11 EUR/Monat im unveraenderten 15-EUR-Limit;
  gespeicherte Alt-Einstellung `searches_per_day: 3` wird beim Laden auf den
  festen Plan angehoben. Zaehlungs-Verarbeitung/-Anzeige bleiben als
  Bibliothek fuer Altdaten.
- **`live_trader.py`**: PULSAR-Universumsgaeste werden beim Aufbau des
  eToro-Kandidatenfelds wie Favoriten beigemischt (Gruppe `pulsar`) und
  durchlaufen ausschliesslich die normale Qualifikation und
  NEXUS-Standard-Pruefung; ein Fehler beim Laden verhindert nie den Start.
- **WebUI** (`pulsar.html`/`pulsar.js`): Hype-Kriterien-Checkliste,
  Alarm-Hinweise, Universumsgaeste statt Score/Community/Wochensimulation.

## Invarianten

Social-Quellen (X/Reddit/GPT/PULSAR) geben weiterhin keine Order frei: Jede
Nominierung braucht die zweistufige persoenliche Telegram-Bestaetigung und
die erneute Core-Pruefung. Fehlende Daten bleiben UNKNOWN (kein erfundener
Vortageswert, keine Staub-Entwarnung ohne Kurs, keine Erwaehnungszahlen aus
X-Stichproben). Keine Freqtrade-/TLS-/Beweisketten-Lockerung.

## Tests

- Neu: `tests/test_v1030_okx_freigabe.py` (10; exakte Pi-Zahlen
  1,00000000279 BTC / 2,79e-9 Ledger-Rest, Composite-Whitelist,
  Log-Ratenbremse) und `tests/test_v1030_pulsar_hype.py` (20; Kriterien,
  Stabilitaet, Limits, Zeitstop, Gaeste, X-Slots, Alarmtext).
- Fortgeschrieben auf die neuen Regeln: test_v984/v985/v986/v987/v989/
  v100/v1013/v1014/v1016-Suiten (Score-/Community-/X-Tageszaehlungs-Pins
  ersetzt, Wochen- durch Tagesregel, 5+2-Suchplan).
- Windows-Lauf: kompletter Testbestand gruen bis auf die bekannten, auf der
  unveraenderten 10.2.2-Basis identisch reproduzierten
  Windows-Umgebungsartefakte (fcntl/Symlink-Privileg/chmod/systemctl/
  node-Subprozess). Massgeblich bleibt der Pi-Volltest der Installation.
