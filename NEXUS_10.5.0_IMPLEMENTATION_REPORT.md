# NEXUS 10.5.0 – Implementierungsbericht

**Build:** `10.5.0-PULSAR-SOURCES-MEASUREMENT` · Basis: 10.4.0-NEXUS Revision 2 · Stand: 18.09.2026

Auftrag (Georg, 18.09.2026): PULSAR-Datenquellen ohne Reddit-Schlüssel, messen und
gleich handeln, PULSAR über die WebUI abschaltbar, PULSAR-Seite aufräumen mit
erkennbarem Messergebnis, Messungen in der Diagnose-ZIP, aufbereiteter
Diagnosebericht in der WebUI. Vorab geprüft: Erreichbarkeit der Quellen gegen die
echten Server (StockTwits 200, FINRA 200, Reddit RSS 200 mit 1/min, Reddit JSON 403).

## Quellen

| Modul | Inhalt |
|---|---|
| `pulsar/stocktwits.py` (neu) | `trending()` (Aktien der Trending-Liste, 30 min Cache), `stream(symbol)` (30er-Stichprobe → Nachrichten/h, Konten, Bullish/Bearish, Untergrenze-Flag; Zeitreihe in `attention_hours`), `activity()` (absolut ≥ 20/≥ 10 oder ≥ 3× eigene 14-Tage-Basis). Budget `stocktwits` 150/800; Backoff und Status über `research._social_status`. |
| `pulsar/short_interest.py` (neu) | `settlement_dates()` (15./Monatsletzter, Wochenende → Freitag), `fetch()` (Partition `settlementDate`, neueste zuerst, 204 = noch nicht veröffentlicht, Cache 24 h, Budget `finra` 40/200), `squeeze_profile()` (≥ 15 % der ausstehenden Aktien oder Days-to-Cover ≥ 5; STALE > 45 Tage; nur Information), `status()`. |
| `pulsar/volume_watch.py` (neu) | `from_quote()` (volume/avgVolume zeitanteilig zur NY-Sitzung, ≤ 15 min), `from_hourly()` (kumuliert bis gleiche Stunde gegen 20 Sitzungen), `classify()` (Auslöser ≥ 3×, Bestätigung ≥ 2×, nur bei positivem Kurs), `scan_universe()`. |
| `etoro_chart_store.py` | `gespeicherte_reihen(bar)` für den Volumenscan (kein Abruf). |
| `pulsar/research.py` | `URLS["stocktwits"]`, `LIMITS` (`data` 300/1500, `stocktwits`, `finra`), Statusimpact StockTwits, `fetch_social` lehnt StockTwits ab (eigener Weg), Schema `attention_outcomes`. |
| `pulsar/source_coordination.py`, `candidate_selection.py` | `research_order(..., extra_candidates)`: frische, gültige Zusatzkandidaten (Volumen, StockTwits-Trending) reihen sich ein; Reddit-Treffer bekommen den Zusatzbeleg; Pipeline-Zähler `extra_received/extra_valid/volume_selected/stocktwits_selected`. |

## Bewertung und Worker

- `pulsar/evidence.py`: REVISION `PULSAR-2.2-TRIGGER-CONFIRM-MEASURED`, `_own_baseline_spike()`, neuer `evaluate()`: Auslöser (Volumen ≥ 3× aus Quote/Stundenkerzen oder Volumenvielfaches der Kursbestätigung, Reddit, StockTwits, X), Bestätigungen (Volumen ≥ 2×, zweite Social-Familie, Kurs ≥ 5 %), `eligible` nur mit ≥ 2 Bestätigungen **und** mindestens einer Social-Familie; `hype.trigger/confirmations/confirmed_count/stocktwits/volume/squeeze`; Zustand `AUSLOESER`; Checks `ausloeser`, `volumen`, `zweite_social_familie`, `squeeze_merkmal`.
- `pulsar/worker.py`: `cycle()` holt Volumen-Seeds aus dem Kerzenspeicher und StockTwits-Trending, je Kandidat StockTwits-Strom und FINRA (Fehler → UNKNOWN auf der Karte); `build_card()` trägt `volume`, `stocktwits`, `stocktwits_baseline`, `short_interest` und Quellenbelege (StockTwits, FINRA, Discovery-Seed) ein; nach `evaluate` `measurement.record()`; stündlicher Slot ruft `update_measurements()` (höchstens alle 6 h, FMP-Tageskerzen über `_request`, in AUS nur Cache).
- `pulsar/telegram.py`: Alarm mit Auslöser, Bestätigungen (n von 3), StockTwits, Squeeze.

## Vorwärtsmessung

`pulsar/measurement.py` (neu): Tabelle `attention_outcomes` (eine Zeile je Symbol und NY-Handelstag), `record(card, mode)`, `update_outcomes(bars_provider)` (Horizonte 1/3/5/10 Handelstage, Feiertag → letzter Schluss davor, UNRESOLVABLE nach 20 Handelstagen ohne Kurs), `summarize(rows)` (Trefferquote, Median, Median nach 0,7 % Kosten; nach Auslöser, Squeeze, Kandidat/nur Auslöser; Urteil ab 30 vollständigen 5-Tage-Messungen: ERFOLGREICH ≥ 55 % und ≥ +0,5 %, NICHT ERFOLGREICH < 45 % oder ≤ 0, sonst UNKLAR), `summary()`, `rows()`, `public_row()`.

## WebUI

- `pulsar/presentation.py`: Snapshot-Felder `measurement`, `demo_trades`, `finra`, `rules_version`; `webui/app.py`: `GET /api/pulsar/measurements`.
- `webui/templates/pulsar.html`, `webui/static/pulsar.js`: Seite neu geordnet (Betrieb → Messung mit Urteilsplakette → Quellen/Budget → Kandidaten → Handel → Technik); `renderMeasurement`, `measurementRows`, `pulsarHypeView` (Chips), `pulsarStocktwitsView`, `sourceChips`; Community-/Belegscore-Reste entfernt; `pulsarXContextView`, `pulsarAttentionView`, `tradeRows`, `pulsarSourceLink` bleiben (Testverträge). `nexus.css`: Chips, Plakette, Berichtsseite.
- Diagnose: `webui/diagnosis_jobs.py::summary()` (ZUSAMMENFASSUNG/META/BEFUNDE/BERICHT aus der SHA-verifizierten ZIP, Fallback für alte ZIPs; `fcntl` optional), `webui/app.py`: `GET /api/diagnosis/{id}/summary`, `GET /diagnosis/{id}/bericht`; `webui/templates/diagnosis_report.html` + `webui/static/diagnosis_report.js` (Kennzahlen, Bereitschaft, Messung, Befunde nach Stufe, Blocker, Quellen/Exporte, Karten, BERICHT.md); `diagnosis.js`: „Bericht öffnen" in neuem Fenster.
- `NEXUS_10_Diagnose.py` 1.9.0: `pulsar_measurement_report()` (Standalone-Kopie der Auswertung), `summary_document()` → `ZUSAMMENFASSUNG.json`, Befund `PULSAR_MEASUREMENT_*`, BERICHT-Abschnitt, `attention_outcomes` in Exportpriorität, `statistics`-Import.

## Versionierung und Tests

`VERSION.txt` 10.5.0-NEXUS, `RELEASE_BUILD.txt`, `config.VERSION_NEXUS`, `NEXUS_10_5_0_Diagnose.py` / `NEXUS_10.5.0_Diagnose_Starten.sh` (10.4.0-Wrapper entfernt), `volltest.REQUIRED_RELEASE_FILES`, `KNOWN_ISSUES.md`.

Neu: `tests/test_v1050_pulsar_messung_quellen.py` (23), Fixtures aus echten, anonymisierten Antworten (StockTwits-Strom ohne Texte/Autoren, Trending, FINRA). Fortgeschrieben: `frontend_v100.test.js` (28; Community-Test durch Chip-, Messungs- und Berichtstests ersetzt), `test_v1040` (2-von-3), `test_v1019`/`test_v976`.

Nicht geändert: Orderpfade, Gates, Risikomanager, PULSAR-Handelsgrenzen, Cash-Delta-Automatik, Kerzenansicht, X-Budget. PULSAR/X/GPT ohne Orderbefugnis; UNKNOWN bleibt UNKNOWN; kein TLS-Bypass; kein CDN.
