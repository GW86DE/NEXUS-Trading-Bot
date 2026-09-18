# NEXUS 10.4.0 – Implementierungsbericht

**Build:** `10.4.0-CHART-SCAN-EXISTENCE` · Basis: 10.3.1-NEXUS Revision 2 · Stand: 18.09.2026

Freigabe (Georg, 17.09.2026): „Weg A und auch bei eToro. Dann fang an und nimm
alle offenen Punkte mit. Denk an die saubere Installation." – alle fünf Punkte
der 10.4-Liste in einer Version, Installer nach komplettem isoliertem Testlauf.

## 1. PULSAR Existenzrisiko-Block

| Datei | Änderung |
|---|---|
| `pulsar/evidence.py` | `EXISTENCE_RISK_PATTERNS` (Insolvenzantrag, Chapter 7/11/15, Insolvenzverfahren, Going Concern, Insolvenz, Delisting, Handelsaussetzung, Betrugsermittlung), `EXISTENCE_RISK_MAX_AGE` 90 Tage, `_existence_risk(card, now)` aus FMP-Profil (`isActivelyTrading is False`, nur bei Symbol-Match), FMP-News (normalisiert, datiert) und SEC-Originaldokumenten; `evaluate()` hängt den Block an `blocks`, liefert `hype.existence_risk {blocked, findings}`, `hype.finance_note` (Gewinn/Free Cashflow/Eigenkapital positiv/negativ, **nur Information**) und den Check `existenzrisiko` (BLOCKIERT/KEIN_BEFUND). Revision `PULSAR-2.1-HYPE-LANE-EXISTENCE`. |
| `pulsar/analysis.py` | Luna-Anweisung: REJECT ist PFLICHT bei Existenzrisiko; schwache Bilanz AUSDRÜCKLICH KEIN REJECT-Grund. Revision `pulsar-cited-reviews-v9-existence-risk`. |
| `pulsar/worker.py` | Profil-Kompakt um `isActivelyTrading`; Quote als Kartenquelle. |
| `pulsar/telegram.py` | Alarmzeilen „Bilanz (nur Information): …" und „Existenzrisiko: KEIN Befund (…)/BLOCKIERT". |

Bewusste Grenze: erkannt wird nur, was die Quellen liefern; keine Bewertung
der Bilanzqualität, keine Kursprognose. Alte Messungen mit älterer
Regelversion zählen für `stable_candidate` nicht (bestehender Mechanismus).

## 2. Kursbestätigung per FMP-Starter-Quote

`pulsar/evidence.py::_price_confirmation`: neuer Weg **QUOTE** vor Intraday
und Tageskerze – `_quote_source(card)` (Provider FMP, kind `quote`, Symbol-
Match), Frische ≤ `QUOTE_MAX_AGE` 900 s (−60 s Toleranz), Gewinn
`price/previousClose−1 ≥ 5 %`, `volume ≥ QUOTE_VOLUME_MULTIPLE (1,0) ×`
Durchschnittstagesvolumen der 20 Vergleichstage. Ein frischer, nicht
bestätigter Quote wird **nicht** durch die Tageskerze von gestern ersetzt; ein
alter/unvollständiger Quote fällt auf die bisherigen Wege zurück. Der
Free-Rückfall in `fmp_service._policy` ist unverändert.

## 3. PULSAR-DB-Lock

`pulsar/control.py`: `_SCHEMA_READY`/`_SCHEMA_LOCK`, `_ensure_schema(con, path)`
führt `executescript` + ALTER-Migrationen einmal je Prozess und Pfad aus und
committet; danach nur eine Leseabfrage auf `sqlite_master` (erkennt eine
ersetzte Datei und baut neu). `transaction()` ruft `_ensure_schema` vor
`BEGIN IMMEDIATE`. Kein Datenformat geändert; WAL setzt weiterhin
`decision_analytics`.

## 4. Logbuch-Scan-Übersicht

| Datei | Änderung |
|---|---|
| `scan_uebersicht.py` (neu) | `Zyklus(broker, zyklus)` mit `hold/blockiert/freigegeben/fehler`, `zusammenfassung()` (Zähler, Gates gruppiert per `GATE_GRUPPEN`, Beispiel-Symbole), `text()`; `speichern()` → `scan_uebersicht.json` (Schema 1, 48 Zyklen je Broker, `atomic_write_json`, Log „SCAN …"), `lesen(limit)`. Fehler werden geschluckt (Handelspfad unberührt). |
| `live_trader.py` | Zyklus-Objekt je Broker-Block; `hold()` im Nicht-Kauf-Zweig, `blockiert()` in `journal_ablehnung`, `freigegeben()` nach „DECISION BUY approved", `speichern()` nach der Instrumentenschleife; danach `etoro_chart_store.ergaenze_fuer_trades` (nur bei Verbindung ohne Scannerfehler). `merke_scan()` direkt nach `broker.historie()`. |
| `webui/app.py` | `GET /api/logs/scan-uebersicht?limit=1..48` (Sitzung erforderlich). |
| `webui/static/logbook.js`, `webui/templates/logbook.html`, `webui/static/nexus.css` | Panel `#scan-uebersicht` über dem Journal, je Broker ein aufklappbarer Block mit Zeilen je Zyklus (`observationTime`). |
| `NEXUS_10_Diagnose.py` | `scan_uebersicht.json` in `JSON_FILES`; Tool-Version 1.8.2. |

## 5. Kerzenansicht (ECharts, OKX und eToro)

| Datei | Änderung |
|---|---|
| `webui/static/echarts.min.js`, `echarts.LICENSE.txt` (neu) | Apache ECharts 5.5.1 (Apache-2.0), 1.030.855 Bytes, SHA256 `e84270bd0cd5bdf60fefc26d00c2a391cb2e81f4d26a7a9ee16185a54773a3cf`, kein BOM; CSP `script-src 'self'` unverändert. |
| `etoro_chart_store.py` (neu) | SQLite `etoro_chart_candles.sqlite` (candles je account/environment/symbol/bar, series_state). `speichere()` inkrementell (nur Zeilen ab MAX(ts) − 2 Kerzen; kappt auf 1500 je Reihe), `merke_scan()` (nur eToro-Aktien, kein Abruf), `ergaenze_fuer_trades()` (15m „17 D"/„15 mins", 1d „400 D"/„1 day"; Symbole aus Trades ≤ 7 Tage; Ruhezeit 900 s je Reihe; max. 4 Abrufe je Aufruf), `lade()`, `verfuegbare_bars()`. |
| `trade_chart_data.py` | `OKX_BARS`, `ETORO_BARS`, `MAX_CANDLES` 500, `_axis_bounds()` (1./99. Perzentil der Körper, +8 % Rand, `clipped`), `_pick_bar()`, `_etoro_chart()` (Kern-Snapshot, Schutzlinien aus `stock_positions.json`, `data_source`, `data_age_seconds`), `chart_for_trade(trade_id, force, bar)` mit Cache je (Trade, Zeitrahmen); OKX rückwärts paginiert (5 × 100), Vorlauf 40 / Nachlauf 20 Kerzen. |
| `webui/app.py` | `/api/trades/{id}/candles?bar=` (nur 5m/15m/1h/4h/1d, sonst Automatik). |
| `webui/static/trades.js` | `zeichneKerzenECharts()` (candlestick + Volumen-Grid, dataZoom inside+slider, axis-Tooltip/Fadenkreuz, markPoint KAUF/VERKAUF, markLine Stop/TP/ROI/Aktuell, unsichtbare Hilfsreihen halten Schutzlinien im Bild, Docht-Klemmung nur in der Zeichnung, Zoom-Erhalt bei stiller Aktualisierung, `preisStellen()`), `zeichneKerzenSVG()` als Rückfall ohne ECharts, `zeichneZeitrahmen()` (Buttons aus `available_bars`), `ladeChart(id, bar)`; `ladeTrades()` räumt den Chart nur noch, wenn die Auswahl entfällt. |
| `webui/templates/trades.html`, `nexus.css` | ECharts-Script vor `common.js`, Zeitrahmenleiste, Achsennotiz, Container 460 px (mobil 360 px). |

Visuelle Prüfung (Windows, eigener Browser gegen die Paketdateien mit
API-Attrappe): eToro 1h/1d und OKX 5m gerendert, Zoom-Erhalt nach stiller
Aktualisierung bestätigt (Start 80 → 80), keine JavaScript-Fehler.

## Versionierung und Tests

`VERSION.txt` 10.4.0-NEXUS, `RELEASE_BUILD.txt` 10.4.0-CHART-SCAN-EXISTENCE,
`config.VERSION_NEXUS`, `NEXUS_10_4_0_Diagnose.py` / `NEXUS_10.4.0_Diagnose_Starten.sh`
(10.3.1-Wrapper entfernt), `volltest.REQUIRED_RELEASE_FILES`, `KNOWN_ISSUES.md`.

Neu: `tests/test_v1040_scan_chart_existence.py` (33 Tests). Fortgeschrieben:
`tests/frontend_v100.test.js` (Kerzenansicht ohne ECharts), `test_v985`
(Regelversion dynamisch), `test_v1019`, `test_v976`.

Nicht geändert: Orderpfade, Gates, Risikomanager, Cash-Delta-Automatik,
OKX-Freigabe, Datenformate. PULSAR/X/GPT ohne Orderbefugnis; UNKNOWN bleibt
UNKNOWN; kein TLS-Bypass; kein CDN.
