# NEXUS 10.8.0 – Umsetzungsbericht

**Build:** `10.8.0-ARCHITEKTUR-LEITPLANKEN-UND-PULSAR-BESTAETIGUNG` · 19.09.2026 · Basis 10.7.1

## Auftrag

Georg, 19.09.2026, nach dem Architektur-Audit (`NEXUS_Architektur_Audit_2026-09-19.md`):
„Fang mit 0–2 direkt an. Nimm die offenen Punkte direkt mit und baue es um.
Danach direkt das Update auf Graphify." Versionsnummer 10.8.0 bestätigt.

Die „offenen Punkte" sind die fünf am 19.09. freigegebenen Funktionen aus der
SanDisk-Auswertung (Hype-Alarm 21:21 Uhr, Karte erst 15:50 NY stabil):
Quote nachladen, StockTwits-Ausbau, X-Bestätigungssuche, eToro-15-Minuten-
Kerzen, Luna 200. Gestrichen (Georg): Katalysator-Bestätigung (widerspricht
dem Hype-Grundgedanken), FMP-Intraday (nur ab Premium, kein Upgrade).

## Teil A – Architektur

| Schritt | Umsetzung | Nachweis |
|---|---|---|
| 0 Leitplanken | `nexus/architektur/importgraph.py` (AST-Importgraph, Tarjan), `schichten.json`/`schichten.py` (336 Module → 7 Schichten + alias, Regeln), `validation/ARCHITEKTUR_BASELINE.json` | `tests/test_v1080_architektur.py`: Zyklen ⊆ Baseline, Verstöße ⊆ Baseline, domain rein, WebUI ohne Broker, Großmodule ≤ +5 %, Baseline passt zum Baum |
| 2 kurze Zyklen | 8 Komponenten / 44 Module → **2 / 21** (PULSAR/ai_router 11, OKX-Buchhaltung/trade_ledger 10). Neue Helfermodule: `installer_host`, `risk_basis_status`, `risk_grenzen`, `etoro_protection_readback`, `etoro_nachlauf`, `fmp_kontext`, `news_model`; `market_intelligence.store.digest`; `approved_universe` ohne `config`-Import | 13 parametrisierte Tests „kurzer Zyklus ist gebrochen", Re-Export-Tests, `background_tick(nachlauf=...)`-Test |
| 1 Paket + Weichen | `nexus/` (domain, application, ports, adapters, state, interfaces), `nexus/weiche.py` (`sys.modules`-Weiche), `nexus/pfade.py`; umgezogen: `ledger_result`, `okx_receipt_math` (domain), `handelsfreigabe`, `risk_levels` (application) | Weiche liefert EIN Modulobjekt; Alias ≤ 6 Zeilen; `risk_levels.path()` bleibt im Projektordner |

Kennzahlen (eigener AST-Graph, Tests ausgenommen): 336 Module, 1.089 Kanten,
2 Zyklus-Komponenten (21 Module), 93 Schichtverstöße (71 application→adapters,
13 adapters→application, 4 konfiguration→state, 3 state→application, 1
application→interfaces, 1 state→adapters), Adapter→Kern 6 Kanten, WebUI→broker 0,
PULSAR→außen 35, 9 Module > 1.500 Zeilen (Fan-out live_trader 82,
crypto_engine 48, webui.app 31). Diese Zahlen sind die Baseline; sie dürfen
nur sinken.

## Teil B – die fünf Punkte

| # | Modul(e) | Kern der Änderung |
|---|---|---|
| 1 | `pulsar/worker.refresh_quotes`, `fmp_service.request(max_age)`, `fmp_reference.quote(max_age)` | OFFENE Karten mit Auslöser bekommen in offener NY-Sitzung einen frischen Quote (≤ 5 je Zyklus, Cache nur < 2 min) und werden neu bewertet |
| 2 | `pulsar/stocktwits.py`, `pulsar/research.LIMITS` | `max`-Cursor bis zu 3 Folgeseiten bei Untergrenze; `stream(active=True)` 15-min-Takt; Trending 15 min; Budget 400/2000; HTTP 403 → 4 h Pause mit Meldung |
| 3 | `market_intelligence/candidate_research.request_confirmation`/`_confirm_plan`, `service._reserve` (Kontext `__PULSAR_CONFIRM__`, 8/Tag), `pulsar/evidence._x_confirmation`, `pulsar/worker.request_x_confirmations` | AUSLOESER + genau eine Bestätigung + Fenster offen → eine X-Suche; Ergebnis nur als zweite Social-Familie |
| 4 | `etoro_chart_store.ergaenze_fuer_karten`/`lade_neueste`, `pulsar/core.aktive_karten_instrumente`, `live_trader` (2 Zeilen), `pulsar/volume_watch.intraday_from_store`/`session_average_volume`, `pulsar/evidence._price_confirmation`, `pulsar/worker.build_card` | Kern ruft 15-min-Kerzen für ≤ 5 aktive Karten ab (≤ 20/h); PULSAR liest; Volumen skalenfrei aus eToro-Kerzen; Kursbestätigung aus Kerzen kann nur hinzukommen, der Quote bleibt Rückfall |
| 5 | `config`, `ai_budget`, `ai_status`, `nexus_setup`, `settings_migration._migrate_v1080_luna_tagesbudget`, `webui/settings_store`, `settings.html` | Luna 200 Standard, einmalige Anhebung 40/50 → 200 beim Update, Feld in der WebUI, USD-Deckel unverändert |

## Was der Betrieb noch zeigen muss

- **StockTwits 403** (seit 18.09. 20:20 UTC auf Trending und Strom): der
  Ausbau greift erst, wenn der Zugang wieder antwortet. Bis dahin: UNKNOWN,
  Quelle sichtbar in Pause. Alternativen: Georgs Dokument zum StockTwits-
  Zugang über Drittanbieter.
- **eToro-Kerzenvolumen bei Aktien**: offline nicht prüfbar (Kerzenspeicher
  nicht in der Diagnose; Stundenkerzen-Volumenscan seit 10.5.0 ohne Treffer).
  Deshalb rechnet 10.8.0 eToro-Volumen nur gegen den eigenen Tagesdurchschnitt
  und lässt den Quote-Weg unangetastet. Die nächste Diagnose in offener
  Sitzung zeigt `intraday_avg_day_volume` je Karte.
- **X-Monatsbudget**: 5 Slot-Suchen + 2 Makro-Suchen + bis zu 8 Bestätigungen
  je Tag teilen sich 15 EUR/Monat; die Reservierung bricht ab, bevor das
  Budget überschritten wird. Wer alle Bestätigungen will, hebt das Budget in
  den Einstellungen an.

## Grenzen / bewusst nicht gemacht

- Die zwei großen Ringe (PULSAR/ai_router, OKX-Buchhaltung) bleiben als
  dokumentierte Baseline; sie sind Gegenstand der Schritte 4, 5 und 9.
- ~150 Wurzelmodule sind nur in der Schichtenkarte einsortiert, nicht
  physisch verschoben. Umzug „beim Anfassen" mit derselben Weiche.
- Kein Bypass der StockTwits-Sperre (kein Browser-User-Agent, kein Proxy).
