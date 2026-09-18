# NEXUS 10.1.10 – Implementierungsbericht

**Build:** `10.1.10-BROKER-STABILITY-AND-DYNAMIC-INTELLIGENCE`
**Basis:** `10.1.9-NEXUS` · **Diagnose:** 1.8.1 · **Stand:** 16. September 2026

## Ausloeser

Die erste echte 10.1.9-Laufzeitabnahme (Diagnosen vom 16.09.2026, Sofort-Export 13:22 UTC und 30-Minuten-Beobachtung 13:46–14:16 UTC) bestaetigte die Risikokapitalreparatur: 85.021,29 EUR handelbares Kapital ueber die USDC-Lane (beobachteter Kurs 0,85), zwei saubere FOK-Kaeufe (BTC-USDC, DOGE-USDC) mit aktivem Broker-Schutz. Dieselben Diagnosen belegten vier neue Fehler bzw. Risiken, die dieser Stand behebt.

## Korrekturen (Beleg → Aenderung)

### 1. Positionsbewertung zum Hoechstkurs (Codefehler)
- **Beleg:** Kapitalbeleg-BTC-Wert 3.023,475093750789 USDC = Menge × `hoechstkurs` (76.201,26…), zeitgleich bewertete der Live-Exposure-Block dieselbe Position mit 2.597 USDC; der Beleg blieb ueber 2 h bit-identisch.
- **Aenderung:** `crypto_engine._eigene_positionen()` uebergibt den letzten beobachteten Marktkurs (`_letzter_kurs`), Fallback belegter Einstiegspreis. Der Hoechstkurs (monoton steigend, Trailing-Marke) bewertet nie mehr Kapital.

### 2. Equity-Tagesbremse mass FX statt Handel
- **Beleg:** Tagesbasis 87.000 EUR (Kurs 0,87) vs. 85.021 EUR (Kurs 0,85) = −2,27 % Drawdown bei −2,5 %-Bremse, praktisch vollstaendig USDC/EUR-Bewegung; Positions-P&L ~0.
- **Aenderung:** `risk_manager.RiskState` fuehrt bei genau einer finanzierten Waehrung (aus `broker.capital_evidence()`, via `risk_pots.aktualisiere_kontowerte`) eine native Reihe: `day_start_native_equity`, `last_native_equity`, `native_equity_ccy`, `native_drawdown_pct`, `fx_drawdown_pct`. Liegt die native Reihe vor, entscheidet SIE ueber den Halt; der EUR-Drawdown bleibt sichtbar. Waehrungswechsel startet die native Basis neu (dann gilt fuer den Resttag wieder die Basisreihe). Grenzwerte unveraendert; Tagesreset setzt die neuen Felder mit zurueck. Alte Zustandsdateien laden ueber Defaults unveraendert.

### 3. Diagnose stoerte den Bot; decision_history wuchs unkontrolliert
- **Beleg:** Beide `OperationalError`-Vorfaelle (13:22 und 14:16 UTC) fielen exakt auf Diagnose-Sammlungen; `decision_history`-Backup zweimal `SQLite-Sicherung zeitlich begrenzt`; OKX verlor kurz die Handelsbereitschaft („OKX-Buchung nicht pruefbar"). Treiber: ~51.000 Entscheidungszeilen/Tag.
- **Aenderungen:** (a) `NEXUS_10_Diagnose.database_snapshot`: WAL-Quellen werden in EINEM Backup-Schritt gesichert (unter WAL blockiert der Leser keine Schreiber, und die Sicherung kann nicht mehr durch Schreibzugriffe neu gestartet werden); `SNAPSHOT_SECONDS` 15→60; Verbindungs-Timeout 0,1 s→2 s; Platzpruefung vorab. (b) `decision_analytics.prune_history()` + taeglicher AutoMaintenance-Lauf: loescht Kandidatenentscheidungen ohne Order-/Eventbeleg aelter 90 Tage und Heartbeats aelter 30 Tage (`DECISION_RETENTION_DAYS`, `DECISION_HEARTBEAT_RETENTION_DAYS`, `DECISION_HISTORY_PRUNE_ENABLED`). Belegtabellen sind zusaetzlich per `ON DELETE RESTRICT` geschuetzt.

### 4. eToro-Kaufsperre war unsichtbar
- **Beleg:** MSFT (Trade 71) wurde 13:17 UTC brokerseitig geschlossen; die daraus folgende Domaenen-Kaufsperre („Ergebnisabgleich ausstehend") war nur im Log/WebUI-Status sichtbar.
- **Aenderung:** `RiskState.register_unknown_pnl_trade()` sendet beim ERSTEN Registrieren eines unbekannten Verkaufsergebnisses einmalig „ERGEBNISABGLEICH ERFORDERLICH" mit Klaerungsweg (WebUI → Handel → Abschlussabrechnung). Best effort; kein Einfluss auf den Risikozustand. **Bewusst NICHT umgesetzt:** ein automatischer Abschluss. Die offizielle eToro-API liefert `totalFees`/`totalExternalFees`/`totalExternalTaxes` nur fuer OFFENE Positionen; die History meldet `fees=0` bei belegten Einstiegskosten (Scope-Konflikt). Ein Auto-Release waere Gebuehren-Erfindung. Der API-Befund ist als Vorarbeit fuer eine spaetere Positions-Snapshot-Persistenz dokumentiert (KNOWN_ISSUES).

## Neue Funktionen

### 5. Manuelle OKX-Waehrungsfreigabe (USD/USDG)
`config.py` akzeptiert in `allowed_quote_ccy` zusaetzlich USD/USDG (Standard unveraendert EUR+USDC). Die WebUI-Einstellungen zeigen drei Schalter (USDC/USD/USDG; EUR fest); neue USD-/USDG-Freigaben verlangen die exakte Phrase `WAEHRUNGEN FREIGEBEN` (Frontend-Prompt UND Server-Validierung in `settings_store`; Vergleich gegen den gespeicherten Vorzustand). `okx_entry_routing` iteriert generisch ueber `broker.allowed_quotes` statt fest EUR/USDC; der Kostenvergleich bevorzugt weiterhin die Primaerwaehrung und wechselt nur bei belegtem Vorteil. Ohne beobachteten FX-Kurs bleibt jede Nicht-Basiswaehrung gesperrt (`RISK_CAPITAL_FX_UNKNOWN`) — unveraendert.

### 6. Backtest-WebUI
`NEXUS_Universum_Backtest_V6.sh` ist Teil des Pakets. `webui/backtest_jobs.py` (Muster `diagnosis_jobs`): exklusiver Lock, abgekoppelter Worker (ueberlebt Browser-Schliessen), Log-Streaming in die Job-Anzeige, Ergebniserkennung ueber die `HTML:`/`ZIP:`-Protokollzeilen, SHA256-Bindung beider Artefakte. Routen `/backtest`, `/api/backtest` (Status/Start), `/api/backtest/{id}/download|report|log`; Bericht mit strikter CSP. Neue Seite `templates/backtest.html` + `static/backtest.js` im Hausstil (Panels, Live-Protokoll, Methodik-Abschnitt); in Laptop-/Tablet-/Telefonbreite geprueft.

### 7. Dynamische X-Quellen-Registry (Zielbild Abschnitt 7, v1)
`market_intelligence/account_registry.py`: Vorschlaege entstehen ausschliesslich aus bereits bezahlten, lokal gespeicherten Posts (Schwelle ≥3 relevante Posts an ≥2 Tagen; relevant = erkanntes Marktthema oder Primaerquellen-Link). Nur numerische Autoren-IDs; keine Profil-/Usernamen-Abrufe. Zustaende PROPOSED→ACTIVE/QUARANTINED/EXPIRED/REMOVED; ACTIVE ausschliesslich per WebUI-Aktion mit Identitaetsvermerk (≥10 Zeichen, Endpoint `POST /api/market-intelligence/registry`), maximal 5; aktive IDs laufen als `from:<id>` im Finanz-Slot mit (gleiches Budget, gleiche 3 Suchen/Tag). TTL: PROPOSED 14 Tage, ACTIVE 30 Tage ohne neue Relevanz → EXPIRED; REMOVED/Tombstone nie erneut vorgeschlagen. Snapshot im Status (`discovery.account_registry`) und Anzeige in „Quellen & X". Die dokumentierte Privacy-Ausnahme (Publisher-IDs zur Identitaetspruefung, nie Post→Autor-Zuordnung in Exporten) steht in `market_intelligence/__init__.py`.

### 8. Quellen-Fixes
GDELT: eigener Timeout `NEWS_SOURCE_GDELT_TIMEOUT_SECONDS = 30` ueber `_source_timeout()` (Messung 16.09.: HTTP 200 nach 13,5 s; globaler 10-s-Timeout erklaerte die Quelle mit 22 Folgefehlern fuer tot). Tradestie: Endpoint auf `https://tradestie.com/api/v1/apps/reddit` (Zertifikat der api-Subdomain abgelaufen, Hauptdomain gueltig; TLS-Pruefung unveraendert, per Test abgesichert: kein `verify=False` im Baum).

### 9. Oberflaechen-/Loghygiene
„Mehr"-Aufklappmenue der Desktop-Navigation (common.js/nexus.css; Mobilmenue vollstaendig); OKX-Guthaben-Logzeile nur bei Aenderung; Kernwert-Sperrwarnung nur beim Zustandswechsel (Audit-Beleg je Lauf bleibt); `crypto_exposure` wird aus dem Kapitalbeleg befuellt statt konstant 0 zu zeigen.

## Geaenderte/neue Dateien (Kern)

Geaendert: `crypto_engine.py`, `risk_manager.py`, `risk_pots.py`, `config.py`, `okx_entry_routing.py`, `news_sources.py`, `pulsar/research.py`, `decision_analytics.py`, `auto_maintenance.py`, `NEXUS_10_Diagnose.py`, `universe/manager.py`, `volltest.py`, `release_unpack.py`, `market_intelligence/{__init__,service,source_registry}.py`, `webui/{app,settings_store}.py`, `webui/templates/{settings,diagnosis*}.html`, `webui/static/{common,settings,sources,nexus.css}.{js,css}`, Versions-/Rolldokumente, 4 versionspinnende Alt-Tests.
Neu: `NEXUS_Universum_Backtest_V6.sh`, `webui/backtest_jobs.py`, `webui/templates/backtest.html`, `webui/static/backtest.js`, `market_intelligence/account_registry.py`, `NEXUS_10_1_10_Diagnose.py`, `NEXUS_10.1.10_Diagnose_Starten.sh`, `tests/test_v10110_{backtest_webui,news_source_fixes,x_account_registry,risk_and_currencies}.py`, Doku-/Validierungsdateien.

## Nicht veraendert

Freqtrade-Entry/Exit, Order-State-Machine (UNKNOWN bleibt UNKNOWN, kein Resend), Schutz-/Spread-/Liquiditaetsgates, Risikogrenzwerte, PULSAR/GPT/X ohne Orderbefugnis, Paritaetsverbot, TLS-Pruefung, Gebuehren-UNKNOWN-Regeln, Persistenzgarantien.
