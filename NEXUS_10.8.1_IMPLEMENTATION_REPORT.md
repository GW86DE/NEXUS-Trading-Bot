# NEXUS 10.8.1 – Umsetzungsbericht

**Build:** `10.8.1-RESTZEILEN-SCHNAPPSCHUSS-UND-KERZENCURSOR` · 19.09.2026 · Basis 10.8.0

## Auftrag

Georg, 19.09.2026, mit dem Diagnosepaket 12:01 UTC: „OKX ist wieder gesperrt.
analysiere dies nicht nur oberflächlich sondern ausführlich." Die Analyse
(`NEXUS_Analyse_OKX_Sperre_2026-09-19.md`) ergab drei Befunde mit vier
Mechanismen; der Vorschlag „kein Umbau, sechs Korrekturen an der Wurzel" wurde
mit „10.8.1 Bestätigt" freigegeben.

## Die sechs Korrekturen

| # | Wurzel (Stand 10.8.0) | Korrektur 10.8.1 | Nachweis (`tests/test_v1081_restzeilen.py`) |
|---|---|---|---|
| 1 | Nicht lesbarer Guthabenstand → Takt lief mit leerem Schnappschuss weiter; `positionen()` lieferte `[]`; jede Ledgerzeile ohne Position bekam sofort einen Beleg | `_pruefe_positionen` bricht den Takt ab (`_abgleich_abgebrochen`): kein Ledgerabgleich, kein Beleg, keine Freigabe; Bereitschaft `reconciliation` offen bis zum nächsten vollständigen Durchlauf; gilt auch für `positionen()`-Fehler | Abbruch ohne Beleg und ohne Fehlmessung, Bereitschaft offen; vollständiger Takt gibt sie frei; leerer gültiger Schnappschuss bleibt Messung |
| 2 | `_fehlender_ledger_bestand` wurde nur geleert, nie gezählt; erste Fehlmessung → `mark_balance_gap` | `_ledger_fehlmessung`: (Anzahl, erste Fehlmessung) je Zeile; Beleg erst nach ≥ 2 Messungen und `OKX_POSITION_MISSING_CONFIRM_SECONDS` (30 s); bis dahin `BESTAND_FEHLT_UNBESTAETIGT` | 1 Messung nichts; 2 in 9 s nichts; 2 mit 31 s → Beleg + BROKER_STATE_UNKNOWN; Wiedersicht setzt zurück; Frist 0 verlangt weiterhin zwei |
| 3 | Rückweg 10.6.0 (`_bestand_zurueckgewonnen`) kannte nur Positionen im Buch; Beleg von Trade 85 blieb bei 50.000 XRP im Konto offen | `_ledger_bestandsbeleg_geloest`: bei offenem Beleg (`okx_accounting.pending_balance_gap`, nur lesen) und Konto ≥ Menge zählt die Wiedersicht; nach ≥ 2 Messungen und `OKX_POSITION_RESTORED_CONFIRM_SECONDS` (120 s) `resolve_balance_gap_restored(…, detail="ledgerzeile;snapshots=n")`, Telegram-Meldung; im Staubpfad VOR `classify` | Heilung nach zwei Messungen ≥ 120 s (Beleg RESOLVED, SUI frei, Rest als RESIDUAL belegt); 9 s nicht; halbe Botmenge nicht; verkäuflicher Rest heilt nur den Beleg, bleibt offen |
| 4 | Staubprüfung `konto * preis <= dust_limit` maß den Gesamtbestand des Kontos | `rest = min(konto, ledger_menge)`; Ablehnungsgründe von `classify` je Zeile nur bei Änderung als WARNING | Rest 0,001765 SUI bei 5.758 SUI Fremdbestand wird belegt abgeschlossen |
| 5 | `historical_exit` hielt jede registrierte EXIT-Order desselben Symbols für die eigene; Fill von Trade 90 → `trade_close(86)` → `LedgerZuordnungUnklar` mit Traceback je Takt | Linienprüfung über Registry-Metadaten (`entry_order_id`, ersatzweise `decision_id`; Altregistrierung ohne Angabe: alter Weg); `trade_ledger.gebuchte_exit_fills` schließt fremd gebundene Fills vor dem Versuch aus; Ablehnung je (Trade, Order) gemerkt, einmal ohne Traceback gewarnt | Exit anderer Linie nicht gebucht, keine Warnung; gebundener Fill ausgeschlossen; abgelehnte Buchung: ein Versuch, eine Warnung ohne `exc_info` |
| 6 | Cursor nur nach KEIN_SIGNAL gesetzt → Vor-Signal-Sperre alle ~8 s (XRP 1055×/2,5 h), Journal-Spiegel 833 MB ohne Rotation | `_abschluss` liefert `decision_id`/`vor_signal`; `scan()` markiert `cutoff − 5 min` bei Vor-Signal-Sperre; `decision_journal` rotiert ab `DECISION_JOURNAL_MAX_MB` (20) nach `.1.jsonl`; Migration übernimmt vom Spiegel nur den Schwanz (`_kopiere`) und kürzt eine zu große Zieldatei (`_migrate_v1081_journal_kompakt`); Diagnose nimmt `.1.jsonl` mit | zweiter Scan derselben Kerze entscheidet nichts; Nach-Signal-Sperre kein Cursor; ohne decision_id kein Cursor; Rotation ab Obergrenze (Minimum 1 MB); Kopierweg/Kürzung auf ganze Zeilen, Quelle unangetastet; Verdrahtung |

Geänderte Module: `crypto_engine.py` (+289 Zeilen, davon der größte Teil
Begründungskommentare und die drei Hilfsmethoden), `okx_accounting.py`
(`pending_balance_gap`), `trade_ledger.py` (`gebuchte_exit_fills`),
`decision_journal.py` (Rotation, `max_bytes`), `config.py`
(`DECISION_JOURNAL_MAX_MB`), `settings_migration.py` (Kopierweg,
Kompaktierung), `NEXUS_10_Diagnose.py` (AUDITS). Robustheit: der
Ledgerabgleich legt seine Zähler an, wenn eine nur teilweise gebaute Engine
(Migration, Diagnose, Reparaturtests) ihn aufruft.

## Was der Betrieb noch zeigen muss

- Trade 85: Beleg RESOLVED nach zwei Messungen ≥ 120 s; Staubabschluss der
  Zeile hängt daran, dass `okx_residual_inventory.prepare` die Linie von Trade
  81 (Take-Profit-Fills, Nachbeleg) vollständig findet. Sonst bleibt die Zeile
  RESIDUAL_EXPOSURE (keine Sperre) und der Grund steht einmal im Log.
- ETH-Reste 86/91: gleiche Staubklassifizierung; BTC-Rest 84 bleibt
  EXTERNAL_OBSERVE, solange Position 89 im Buch liegt (Position-im-Buch-Pfad
  prüft nicht die Linie – bewusst nicht angefasst).
- Häufigkeit der XRP-Entscheidungen im Freqtrade-Modus: eine je Kerze.
- Journal-Spiegel: Migrationszeile, Größe ≤ 20 MB.

## Grenzen / bewusst nicht gemacht

- Keine Änderung an Post-Signal-Sperren (Orderbuchtiefe SOL, Spread): sie
  laufen weiter je Scan, weil ein wiederholter Versuch dort sinnvoll sein kann.
- Der Position-im-Buch-Pfad des Ledgerabgleichs ordnet weiterhin jede offene
  Zeile desselben Symbols der Buchposition zu (EXTERNAL_OBSERVE bei fremder
  Linie). Linienscharfe Zuordnung ist Teil von Schritt 6/7 des Migrationsplans.
- `crypto_engine` bleibt Großmodul (+4,9 % gegenüber der 10.8.0-Baseline,
  innerhalb der Toleranz; Baseline nachgezogen). Der Umzug des OKX-Abgleichs in
  `nexus/application` ist Schritt 6/7.
- Nebenbefunde der Analyse (Demo-Kerzen ohne Volumen, XRP-EUR-Cache, eToro
  Wochenende) sind Betriebsfragen, keine Codeänderung.
