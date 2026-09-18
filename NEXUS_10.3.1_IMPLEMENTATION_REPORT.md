# NEXUS 10.3.1 – Implementierungsbericht (Punkt-Hotfix)

**Build:** `10.3.1-ETORO-CASH-DELTA-SETTLEMENT` · Basis: 10.3.0-NEXUS · Stand: 17.09.2026

## Wurzelanalyse (Diagnose 17:02 UTC)

1. `etoro_history_accounting.assess`: History-Zeile AAPL `netProfit=124.08`,
   `fees=0`, bestaetigte Einstiegskosten 1,00 USD → `implied_cost < known_entry`
   → `BROKER_HISTORY_COST_SCOPE_CONFLICT`, `risk_release=False`. Korrekt: Die
   History enthaelt die Kommission nicht (MSFT/PEP-Nutzerbestaetigungen
   bewiesen jeweils 2,00 USD Gesamtkosten = 1,00 je Seite).
2. `risk_manager.kaufsperre_grund` → `Ergebnisabgleich ausstehend` solange
   `ledger:79` UNKNOWN ist; `risk_result_recovery` loeste nur Zeilen mit
   `USER_CONFIRMED` aus dem manuellen Dialog.
3. `etoro_settlement_review._preview` fand fuer Trade 79 keine Belege: Es
   verlangte `position_ids == [pid]` davor und `[]` danach; die echten Belege
   waren `[AAPL, META]` → `[META]`. Der manuelle Dialog haette dieselbe
   Fehlermeldung geliefert („Passende Barbestaende … fehlen").
4. API-Recherche (api-portal.etoro.com): `fees` = „The fees of the trade",
   `netProfit` ohne Gebuehrenbezug; kein Endpunkt mit Abschlusskosten
   geschlossener Positionen. `etoro_fee_snapshots.json` (10.2.0-Vorarbeit)
   ist auf dem Pi nicht Teil des Diagnoseexports; sie liefert ohnehin nur
   Felder OFFENER Positionen.

## Aenderungen

- `etoro_settlement_review.py`: `_preview` mit Positionsmengen-Differenz
  (`before − {pid} == after`), Stueckzahlvergleich bei neuen Belegen
  (`position_units` in `save_cash`/`capture_pnl`), automatische Freigabe-
  Bewertung (`automatic_release`, `automatic_block_reason`,
  `interval_seconds`, `automatic_cost_limit`; Grenzen
  `AUTO_MAX_INTERVAL_SECONDS=1800`, `AUTO_MAX_EXIT_COST_ABS=5`,
  `AUTO_MAX_EXIT_COST_PCT=0.005`), neue Funktion `auto_settle` (Audit-Eintrag
  mit `AUTO_ACTOR`, `fee_quality='CASH_DELTA_CONFIRMED'`); `completed`-Pruefung
  akzeptiert beide Cash-Delta-Qualitaeten.
- `risk_result_recovery.py`: fuer nicht bestaetigte eToro-Zeilen
  `auto_settle` (Ratenbremse 300 s je Zeile), Belegpaar je Qualitaet.
- `risk_manager.resolve_unknown_result_at`: Belegpaare
  `{(USER_VERIFIED, USER_CONFIRMED), (AUTOMATIC, CASH_DELTA_CONFIRMED)}`.
- `ledger_result.CONFIRMED_FEES`, `trade_ledger` (2 Stellen),
  `NEXUS_10_Diagnose.py` (3 Stellen): neue Qualitaet bekannt.
- `webui/static/trades.js`: Dialog erklaert Automatik/Grund.

## Invarianten

Keine Gebuehr wird angenommen: Die Abschlusskosten stammen aus zwei
authentifizierten Barbestandsbelegen desselben Kontos, deren uebriger
Positionsbestand identisch ist. Jede Unsicherheit (fehlende Belege, andere
Bewegung, offene Orders, Intervall, unplausible Hoehe) laesst die Zeile UNKNOWN
und damit die Kaufsperre bestehen. Kein Umbuchen, kein Loeschen, kein Broker-
Call.

## Tests

`tests/test_v1031_etoro_cash_delta.py` (12): Automatik bei gleichzeitig
offener Position, exakte Pi-Zahlen (44 AAPL, META offen → 1,00 USD / 122,08),
verschwundene bzw. teilgeschlossene andere Position blockiert, identische
Stueckzahlen erlaubt, unplausible Kosten bleiben manuell (manueller Weg
weiterhin gruen), offene Orders/Intervall bleiben manuell, Risiko-Aufloesung
am Verkaufstag mit Belegpaar, falsches Belegpaar abgelehnt, Sampler speichert
Stueckzahlen, Qualitaet ueberall bekannt. Regression: v1015/v1014/v1013/v987/
v990/v100/v1012/v101-Suiten gruen (255).
