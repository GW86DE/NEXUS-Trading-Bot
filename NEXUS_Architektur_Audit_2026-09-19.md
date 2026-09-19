# NEXUS – Architektur-Audit aus dem Graphify-Wissensgraphen

**Stand:** 19.09.2026 · Quellstand 10.7.1 (Git-Arbeitsbaum, 318 Python-Module ohne Tests, 86.628 Zeilen, 229 Testdateien)
**Datenbasis:** Graphify-Graph über 668 Dateien (628 Code per AST, 40 aktuelle Dokumente) – 12.134 Knoten, 32.727 Kanten, 403 Communities; daraus ein Modul-Graph mit 581 Modulen und 2.072 gerichteten Abhängigkeiten (imports, calls, references, uses, inherits, implements).

**Wie belastbar ist das?** Kanten zwischen Modulen sind aus dem Quelltext extrahiert (deterministisch). Späte Importe innerhalb von Funktionen zählen mit – sie sind echte Kopplung, auch wenn Python sie beim Start nicht auflöst. 2.514 Kanten zeigen aus dem Korpus hinaus (Standardbibliothek, Fremdpakete) und sind hier ausgeblendet; `echarts.min.js` (451 Knoten) ist Fremdcode und ebenfalls ausgeblendet. Zahlen sind Kanten- und Symbolzählungen, keine Bewertung von Codequalität.

---

## Teil 1 – Die zehn Prüfpunkte

### 1. Größte God Nodes

| Kanten | Knoten | Modul | Was es bedeutet |
|---:|---|---|---|
| 249 | `BrokerFehler` | broker/base.py | Eine Ausnahme als universelles Steuerungsmittel: Ablehnungen, Sperren, Belegfehler laufen alle über denselben Typ. |
| 228 / 165 | `live_trader.py` / `run()` | live_trader.py | Der Aktien-Zyklus ist ein Modul (4.393 Zeilen) mit 85 ausgehenden Modulabhängigkeiten – höchster Fan-out im System. |
| 183 / 109 / 97 | `OKXBroker` / `OKXClient` / `OKXInstrument` | broker/okx.py | Der OKX-Adapter (4.324 Zeilen, 238 Symbole) ist der größte Einzelbaustein. |
| 169 | `EtoroBroker` | broker/etoro.py | 3.166 Zeilen, 154 Symbole. |
| 162 | `app.py` | webui/app.py | Alle Routen in einer Datei, 28 Modulabhängigkeiten. |
| 139 | `atomic_write_json()` | safe_persistence.py | 64 Module schreiben Zustand über diese eine Funktion – der Zustand ist über das ganze System verteilt (siehe Punkt 10). |
| 121 / 108 | `CryptoEngine` / `crypto_engine.py` | crypto_engine.py | 5.634 Zeilen, 193 Symbole, 41 Modulabhängigkeiten. |
| 121 | `etoro_reconciliation.py` | – | 2.891 Zeilen, 146 Symbole; zentraler Abgleich mit 16 Abhängigkeiten. |
| 108 | `RiskState` | risk_manager.py | 40 Methoden/Attribute als eigene Knoten; 32 Module hängen an risk_manager. |
| 107 | `config.py` | – | Von 72 Modulen importiert (Fan-in 87 inkl. Tests); 61 `os.getenv`, 1.589 Zeilen, liest zur Importzeit Einstellungsdateien. |
| 102 | `NEXUS_10_Diagnose.py` | – | 2.362 Zeilen; kennt die Interna fast aller Subsysteme (read-only, aber gekoppelt). |
| 95 | `critical_state_lock()` | state_lock.py | Sperren über Prozessgrenzen – Symptom des verteilten Zustands. |
| 94 / 84 | `EvidenceError` / `OrderStatusUnklar` | okx_receipt_math / broker/base | Weitere Ausnahmen als Kontrollfluss. |

**Befund:** Drei God-*Module* (live_trader, crypto_engine, die beiden Broker-Adapter) und zwei God-*Objekte* (RiskState, config). Die Community-Zerlegung zeigt aber auch: Die Communities folgen den Dateien (eToro-Adapter, OKX-Adapter, Ledger, Risikozustand …) – die Kohäsion innerhalb der Module ist hoch. Das Problem ist die Größe und die Kopplung *zwischen* ihnen, nicht ein unstrukturierter Brei.

### 2. Größte Communities / Subsysteme

| Subsystem | Dateien | Symbole | Anmerkung |
|---|---:|---:|---|
| Tests | 239 | 4.999 | 57 % aller Symbole. Stark: Vorfälle mit Originalzahlen als Regression. Schwach: Tests greifen tief in Interna (Monkeypatching privater Methoden). |
| „Core: sonstige" (flache Wurzel) | 147 | 1.443 | ~150 Module liegen ohne Paketstruktur im Projektstamm. |
| WebUI | 41 | 1.277 | app.py + state.py + 18 JS + 12 Templates. |
| Daten / Strategie / Benachrichtigung | 51 | 817 | FMP, Massive, News, KI-Router, Kostenmodell, Backtest, Telegram. |
| Buchhaltung (Ledger, Belege, Abgleich) | 31 | 714 | Zwei Familien: 6 eToro-Module, 9 OKX-Module, plus Ledger. |
| Broker-Adapter | 11 | 596 | Zwei Adapter mit je >150 Symbolen. |
| PULSAR | 23 | 357 | Eigener Paketraum, klare Hubs (control, research). |
| Core: Krypto | 5 | 253 | crypto_engine + Wächter + Strategiemodus. |
| Core: Aktien | 7 | 246 | live_trader + position_manager + execution_lifecycle. |
| Risiko | 12 | 232 | risk_manager, risk_pots, risk_levels, handelsfreigabe, etoro_risk_period. |
| Universum | 7 | 186 | manager, modelle, Selektoren. |
| Market Intelligence (X) | 7 | 110 | sauber gekapselt (service → store). |
| Config / State | 4 | 63 | config.py, safe_persistence, bot_zustand, settings_migration. |

Größte Graph-Communities: eToro-Adapter (157 Knoten), Kostenmodell/Backtest (154), Aktienhandel (148), Signal-Scoring/Broker-Basis (144), OKX-Adapter (127), Risikozustand (109), WebUI-App (108), OKX-Buchhaltung (108), PULSAR-Steuerung (102), Ledger (90).

### 3. Zyklische Abhängigkeiten

Modul-Graph ohne Tests: **8 stark zusammenhängende Komponenten**, die größte umfasst **27 Module** (beide Broker-Adapter, etoro_reconciliation, okx_accounting, trade_ledger, execution_lifecycle, risk_manager, nexus_update, okx_reference_valuation …). Nur Importe gezählt: **6 Zyklen**.

Die kürzesten Import-Zyklen (jeweils echte Zweierschleifen):

- `repair_okx_verified_history` ↔ `nexus_update` (Installer importiert Reparatur, Reparatur importiert Installer)
- `risk_basis_review` ↔ `risk_manager`
- `etoro_risk_maintenance` ↔ `risk_basis_review`
- `etoro_protection_repair` ↔ `broker/etoro` (**der Adapter importiert Kernlogik**)
- `etoro_fee_recovery` ↔ `etoro_reconciliation` ↔ `etoro_cancellations`
- `nasdaq_halt_feed` ↔ `news_sources`

Größere Ringe: broker/__init__ → broker/etoro → etoro_protection_repair → nexus_update → repair_okx_* → risk_manager → etoro_risk_period → etoro_risk_maintenance → broker/etoro (11 Module); broker/okx → decision_analytics → execution_lifecycle → okx_accounting → okx_external_settlement → okx_reference_valuation → okx_residual_inventory → trade_ledger → broker/okx (8 Module); pulsar/analysis ↔ pulsar/sources ↔ ai_router ↔ ai_budget (4); fmp_data ↔ fmp_reference ↔ fmp_service (3).

**Befund:** Die Zyklen werden heute durch späte Importe in Funktionen „umgangen" – die Kopplung bleibt. Der große 27er-Ring ist der Grund, warum ein Fix an einer Stelle (z. B. Buchhaltung) so oft Nebenwirkungen an einer anderen hatte.

### 4. Cross-Subsystem-Abhängigkeiten

Stärkste gerichtete Beziehungen (Modulkanten):

| Von → Nach | Kanten | Bewertung |
|---|---:|---|
| Core → Daten/Strategie | 134 | erwartbar |
| Core: Aktien → Core: sonstige | 125 | live_trader nutzt alles |
| Core → Config/State | 106 | Konfiguration überall |
| Core → Buchhaltung | 97 | erwartbar |
| Core: Krypto → Broker | 55 | **direkt auf den OKX-Adapter** |
| Buchhaltung → Broker **und** Broker → Buchhaltung | 52 / 50 | **beidseitig**: Adapter kennt Belege, Belege kennen Adapter |
| Broker → Core: Aktien | 31 | **Adapter ruft Kern** (etoro_protection_repair, position_manager) |
| WebUI → Core / Buchhaltung / Daten / Config | 53 / 34 / 31 / 24 | WebUI importiert 47 Kernmodule |
| Risiko → Buchhaltung | 30 | erwartbar (Belege bestimmen Sperren) |

**Befund:** Zwei Richtungsverletzungen: Adapter → Kern und Adapter ↔ Buchhaltung. Alles andere ist die erwartbare „nach innen"-Richtung.

### 5. Broker-Kopplungen

- `broker/base.py` (der Port) wird von 18 Modulen genutzt – der Port existiert und wird verwendet.
- `broker/okx.py` wird trotzdem von **15 Modulen direkt** genutzt: crypto_engine (34 Referenzen!), okx_closed_reconciliation (9), crypto_analysis (6), universe/crypto_selector (3), trade_chart_data (3), okx_external_settlement, okx_residual_inventory, okx_account_switch, okx_gesundheitscheck, decision_analytics, live_trader …
- `broker/etoro.py` von **11 Modulen direkt**: live_trader (7), etoro_reconciliation (3), position_manager, etoro_protection_repair, etoro_risk_maintenance, etoro_risk_period, **risk_manager** (die Risikologik kennt den konkreten Broker).
- `OKXClient` (die HTTP-Schicht) wird an `OKXBroker` vorbei genutzt: crypto_analysis (5), trade_chart_data (2).
- Rückrichtung: broker/etoro importiert etoro_protection_repair und etoro_risk_maintenance; broker/okx hängt im 8er-Ring mit der Buchhaltung.

**Befund:** Die Adapter sind keine reinen Transport-/Mapping-Schichten. Sie enthalten Fachlogik (zusammengesetzter Verkaufsbeweis, FX-Kursbelegdokumente, Schutzabgleich, Risikopflege), die eigentlich Domäne ist – deshalb 4.324 bzw. 3.166 Zeilen, deshalb die beidseitige Kopplung.

### 6. Position-Lifecycle

Es gibt **zwei parallele Implementierungen** desselben Ablaufs (Signal → Order → Fill → Schutz → Ledger → Risiko → Verkauf → Abrechnung):

- **Aktien:** live_trader.run() inline (Fan-out 81: notifier 23, favorites 21, risk_manager 16, broker/base 11, etoro_exit_costs 10, cost_engine 9 …) → trade_ledger → risk_manager; Nachlauf über position_manager und etoro_reconciliation (Backfill).
- **Krypto:** crypto_engine (Fan-out 40: broker/okx 34, okx_accounting 14, base 13, crypto_strategy_mode 13, freqtrade_candles 11 …) → execution_lifecycle → okx_accounting → trade_ledger.

Gemeinsam genutzt: trade_ledger (Fan-in 8), execution_lifecycle (8), decision_analytics (31). Der Ablauf hat **mehrere Einstiegspunkte ohne gemeinsame Registrierstelle** – genau so entstand der CSCO-Fall: Der Abgleich-Verkaufspfad kannte den Risikozustand nicht; 10.7.1 hat das mit einer *dritten* Stelle (Nachregistrierung im Recovery-Loop) geheilt, nicht mit einer einzigen.

### 7. Reconciliation-Abhängigkeiten

15 Module in zwei Familien, die dasselbe Muster (Beleg → Beweis → Abrechnung → Risikobeleg) getrennt implementieren:

- **eToro (6):** etoro_reconciliation (Fan-out 16; decision_analytics 30, broker/base 19), etoro_fee_recovery (8), etoro_settlement_review, etoro_history_accounting, etoro_accounting_resolution, etoro_exit_costs.
- **OKX (9):** okx_accounting (Fan-in 11), okx_closed_reconciliation, okx_external_settlement, okx_reference_valuation (Fan-in 7), okx_reference_autovaluation, okx_residual_inventory (Fan-in 8), okx_receipt_math (Fan-in 10), okx_receipt_import, exposure_klassifizierung.
- **Gemeinsam:** ledger_result (Fan-in 17, keine Abhängigkeiten – **die einzige reine Regelbibliothek**), risk_result_recovery (Konsument beider Familien, Fan-out 8), handelsfreigabe (10.7.0, liest beide).

**Befund:** ledger_result und okx_receipt_math sind die richtigen Bausteine (reine Regeln, keine Abhängigkeiten). Alles darüber ist doppelt gebaut und ringförmig verknüpft.

### 8. PULSAR-Abhängigkeiten

- Interne Hubs: pulsar/control (Fan-in 20), pulsar/research (17). Worker mit Fan-out 23 (research, fmp_data, fmp_service, market_intelligence.service, source_coordination …).
- Market Intelligence (X) ist sauber: service → store, keine Rückkopplung.
- **Grenzverletzungen nach außen:** pulsar/measurement → etoro_accounting_resolution (Messung liest Buchhaltung), pulsar/positions → etoro_exit_costs, pulsar/core → live_settings/telegram, und live_trader zieht PULSAR-Gäste direkt ins Universum.
- Zyklus pulsar/analysis ↔ pulsar/sources ↔ ai_router ↔ ai_budget.

**Befund:** PULSAR ist das am besten abgegrenzte Subsystem, mit zwei Lecks in die Buchhaltung und einem Ring über den KI-Router.

### 9. WebUI ↔ Core

- WebUI importiert **47 Kernmodule** (broker_display_context 16, ledger_result 12, safe_persistence 11, credential_store 10, notifier 8, decision_analytics, settings_migration, pulsar/measurement, etoro_cancellations, market_intelligence, etoro_strategy_mode, etoro_settlement_review, trade_ledger, okx_residual_inventory, okx_reference_valuation …).
- **Kein** Import aus broker/* – das Prinzip „WebUI hat keine Brokerverbindung" hält.
- Rückrichtung: live_trader → webui/state (der Kern schreibt seinen Laufzeitstatus über ein WebUI-Modul), gui_app → webui/state.
- Die WebUI schreibt direkt in Domänenzustand: Abschlussabrechnung bestätigen, Einsatzstufe, Strategiemodus, PULSAR-Modus, Einstellungen. Zwei Prozesse auf denselben Dateien und SQLite-Datenbanken → `critical_state_lock` (95 Kanten) und die bekannten „database is locked"-Warnungen.

### 10. Config- und State-God-Objects

- **config.py:** 1.589 Zeilen, 61 `os.getenv`, 72 importierende Module. Liest zur Importzeit `ai_router_settings.json` u. a. und **mutiert Modulvariablen** (`AI_LUNA_MAX_CALLS_PER_DAY = int(_aid7.get(...))`). Konfiguration und Laufzeiteinstellung sind vermischt.
- **Zustand:** 45 JSON-Zustandsdateien (volltest.forbidden_runtime) + 8 SQLite-Datenbanken + Attribute auf den Engine-Objekten (`_gesperrte_symbole`, `_exit_in_progress_symbole` …). 64 Module schreiben über `atomic_write_json`.
- **RiskState:** 108 Kanten, 40 Methoden; persistiert als JSON mit eigenen Wiederanlauf-Schlüsseln (`_transaction`, recovery_keys) – faktisch eine kleine Datenbank in einer Klasse.
- **settings_store (WebUI)** schreibt Einstellungen, die `config.py` beim nächsten Start liest – zwei Wahrheiten für dieselbe Einstellung (Beispiel gestern: Luna-Limit 40 in config, 50 in der JSON, nicht in der Oberfläche).

---

## Teil 2 – IST-Architektur

```
            ┌────────────── WebUI (FastAPI, 2. Prozess) ──────────────┐
            │  app.py ── state.py ── settings_store ── 18 JS/12 HTML  │
            │  importiert 47 Kernmodule, schreibt Domänenzustand      │
            └───────────────┬───────────────────────────┬─────────────┘
                            │ JSON/SQLite (45 Dateien, 8 DBs, state_lock)
┌───────────────────────────┴───────────────────────────┴─────────────┐
│  KERN (1 Prozess, flache Wurzel mit ~150 Modulen)                   │
│                                                                     │
│  live_trader.run() ──────────────┐    crypto_engine.CryptoEngine ── │
│   (Scan, Signal, Order, Fill,    │     (Universum, Signal, Order,   │
│    Schutz, Positionen, Nachlauf) │      Schutz, Bestandsabgleich)   │
│        │           │             │            │          │          │
│  risk_manager   trade_ledger ◄───┴────────────┘   okx_accounting    │
│  (RiskState)    decision_analytics                okx_receipt_math  │
│        │                                          okx_residual_…    │
│  eToro-Abgleich (6 Module) ◄──────────────────► OKX-Abgleich (9)    │
│        ▲                     ledger_result (rein)        ▲          │
│        │                     risk_result_recovery        │          │
│        │                     handelsfreigabe             │          │
│  broker/etoro (3.2k) ◄───┐                     ┌──► broker/okx (4.3k)│
│      ▲  │ importiert Kern └── broker/base ─────┘        ▲           │
│      │  └──────────────► etoro_protection_repair        │           │
│  PULSAR (23) ── worker ── research ── control    market_intelligence│
│      └─► etoro_accounting_resolution (Leck)            (sauber)     │
│  Daten: fmp_*, massive_*, news_sources, ai_router, cost_engine …    │
│  config.py (72 Importeure, liest JSON zur Importzeit)               │
└─────────────────────────────────────────────────────────────────────┘
```

Was gut ist und bleiben soll: die Invarianten (UNKNOWN bleibt UNKNOWN, keine Orderbefugnis für KI/Social), das Ledger als finanzielle Wahrheit, `ledger_result`/`okx_receipt_math` als reine Regelmodule, `broker/base` als vorhandener Port, PULSAR und Market Intelligence als eigene Pakete, der Volltest mit Vorfall-Regressionen, der Installer mit Staging und Rückfall.

## Teil 3 – Problemstellen (nach Wirkung geordnet)

| # | Problem | Beleg aus dem Graphen | Wirkung |
|---|---|---|---|
| P1 | **Zwei parallele Lifecycle-/Abrechnungsstapel** (Aktien vs. Krypto, eToro vs. OKX) | 6 + 9 Abgleichmodule, zwei Einstiegspfade ins Ledger, drei Registrierstellen für Risikobelege | Jeder Fehler wird zweimal gefunden und zweimal gefixt (Lot-Rest 3×, Gebührenbeleg, Registrierung) |
| P2 | **God-Module** | live_trader 4.4k Zeilen/Fan-out 85; crypto_engine 5.6k/41; Adapter 4.3k + 3.2k | Jede Änderung berührt alles; Tests müssen Interna patchen |
| P3 | **Zyklen Adapter ↔ Kern ↔ Buchhaltung** | 27er-Ring, 6 Import-Zyklen, Adapter → Kern (31 Kanten), Buchhaltung ↔ Adapter (52/50) | Keine Schicht lässt sich isoliert testen oder ersetzen |
| P4 | **Zustand verstreut** | 45 JSON + 8 DBs + Engine-Attribute, 64 Schreiber, state_lock 95 Kanten | Sperrkonflikte, Wiederanlauf-Sonderfälle, zwei Wahrheiten (config vs. JSON) |
| P5 | **WebUI schreibt Domäne** | 47 Kernimporte, direkte Aufrufe von Abrechnung/Einstellungen | Zweiter Schreiber auf Ledger und Zustand; DB-Locks |
| P6 | **Fachlogik in Adaptern** | 238/154 Symbole, Composite-Beweis und FX-Belege im OKX-Adapter | Brokerwechsel oder zweiter Krypto-Broker praktisch unmöglich |
| P7 | **Konfiguration mutabel und überall** | 72 Importeure, 61 getenv, Import-Zeit-Mutation | Einstellungen ohne Oberfläche, Reihenfolgeabhängigkeit beim Start |
| P8 | **Ausnahmen als Kontrollfluss** | BrokerFehler 249, OrderStatusUnklar 84, EvidenceError 94 | Sperrgrund, Ablehnung und echter Fehler sind nicht unterscheidbar (die Sperrliste 10.7.0 musste Texte parsen) |
| P9 | **Flache Wurzel ohne Pakete** | 147 Module „sonstige" | Keine sichtbaren Grenzen, keine erzwingbaren Regeln |
| P10 | **PULSAR-Lecks** | measurement → Buchhaltung, positions → exit_costs, Ring über ai_router | Gering, aber Grenze nicht sauber |

## Teil 4 – Soll-Architektur

Ziel: dieselben Invarianten, dieselben Regeln, **eine** Implementierung je Konzept, Abhängigkeiten nur nach innen.

```
nexus/
  domain/        reine Regeln, keine I/O, keine Broker
    ledger/      Trades, Fills, Exit-Ereignisse, Belege (heute trade_ledger + ledger_result)
    belege/      EvidenceProvider-Port, Resolver (Cash-Delta, Historie, Fills, Referenzbewertung,
                 Erwartungswert), Mengenregeln (okx_receipt_math → brokerneutral)
    risiko/      RiskState als Aggregat mit expliziten Ereignissen, Sperrregeln (handelsfreigabe)
    lifecycle/   EIN Zustandsautomat je Position: geplant → gesendet → gefüllt → geschützt →
                 geschlossen → abgerechnet → im Risiko gebucht; brokerneutral
  application/   Anwendungsfälle, orchestrieren Domäne + Ports
    scan/  decide/  execute/  positions/  reconcile/  settle/  recover/
  ports/         Protokolle: BrokerPort (aus broker/base), MarketDataPort, NewsPort,
                 NotifyPort, StatePort, CandidateSinkPort (PULSAR → Kern)
  adapters/      nur Transport + Mapping
    broker/etoro  broker/okx (OKXClient privat)  fmp  massive  stocktwits  news  telegram
  state/         EIN Speicher (SQLite) mit typisierten Repositories; Einstellungen als
                 unveränderliches Settings-Objekt + Laufzeit-Overrides über einen Service
  interfaces/    webui (Lesemodell + Kommandobus), telegram-Befehle, diagnose, cli
  pulsar/        wie heute, spricht nur über Ports (CandidateSink, MarketData, Notify)
```

Verbindliche Regeln (als Tests erzwungen):
1. Abhängigkeitsrichtung: interfaces → application → domain; adapters → ports; nie umgekehrt. **Null Import-Zyklen.**
2. Kein Modul über 1.500 Zeilen; keine Klasse über 40 öffentliche Methoden.
3. Ein Broker-Adapter kennt weder Ledger noch Risiko noch Kern.
4. Ergebnisse statt Ausnahmen für Fachentscheidungen: `Entscheidung(erlaubt, grund, reichweite, ablauf)` – die Sperrliste 10.7.0 ist der Anfang.
5. Genau ein Schreiber je Zustandsart; die WebUI sendet Kommandos, der Kern führt aus.
6. Alle Vorfall-Tests (test_vXXX) bleiben unverändert grün – sie sind der Vertrag der Migration.

## Teil 5 – Schrittweiser Migrationsplan

Jeder Schritt ist eine eigene Version, ändert **kein Verhalten**, wird mit dem kompletten Testbestand, dem Je-Test-Vergleich und den neuen Architektur-Tests geprüft und ist über den Installer (Staging, Rückfall auf den laufenden Stand) rücknehmbar. Reihenfolge nach Risiko: erst Regeln und Struktur, dann Kopplung, dann Zustand, zuletzt die God-Module.

| Schritt | Inhalt | Größe | Risiko | Nachweis |
|---|---|---|---|---|
| 0 | **Leitplanken:** Architektur-Tests aus dem Modul-Graphen (Zyklenzahl darf nur sinken, Richtungsregeln, Größenlimits), Graphify-Kennzahlen als Baseline im Release-Nachweis | klein | keins | Tests rot bei Verschlechterung |
| 1 | **Pakete ohne Verschiebung von Logik:** die ~150 Wurzelmodule in `domain/ application/ adapters/ state/ interfaces/` einsortieren; alte Importpfade über Alias-Module ein Release lang erhalten | mittel (mechanisch) | gering | Testbestand identisch |
| 2 | **Kurze Zyklen brechen:** repair_* ↔ nexus_update, risk_basis_review ↔ risk_manager, etoro_protection_repair ↔ broker/etoro, fee_recovery ↔ reconciliation, nasdaq ↔ news – jeweils gemeinsame Helfer nach unten ziehen | klein je Zyklus | gering | Zyklenzahl 6 → 1 (KI-Ring), dann 0 |
| 3 | **Broker-Port härten:** Kern nur über `BrokerPort`; OKXClient privat; Fachlogik aus den Adaptern in `domain/belege` (Composite-Beweis, FX-Belegdokument, Schutzabgleich) | groß | mittel | Adapter-Fan-in aus dem Kern = 0; Adapter ≤ 1.500 Zeilen |
| 4 | **Ein Position-Lifecycle:** Zustandsautomat in `domain/lifecycle`, beide Engines rufen ihn; **eine** Stelle, die Risikobelege registriert (hätte CSCO verhindert) | groß | mittel | test_v1071 & Co. unverändert; Registrierpfade 3 → 1 |
| 5 | **Eine Abrechnung:** `belege/` mit EvidenceProvidern je Broker, ein Resolver, ein Recovery-Loop; ledger_result/okx_receipt_math werden brokerneutral | groß | mittel | Abgleichmodule 15 → ~7; alle Beleg-Tests grün |
| 6 | **Zustand konsolidieren:** JSON-Dateien schrittweise in SQLite-Tabellen hinter Repositories (Reihenfolge: unkritische zuerst – Scan-Übersicht, Quellenstatus; RiskState zuletzt); config als typisiertes Settings-Objekt, Laufzeit-Overrides über einen Service mit Oberfläche | groß, in Etappen | mittel–hoch beim Risikozustand | Wiederanlauf-Tests (v100_risk_persistence) grün; state_lock-Nutzung sinkt |
| 7 | **WebUI-Kommandobus:** Oberfläche schreibt nur noch Kommandos in eine Tabelle, der Kern führt aus (Muster manual_trade_commands existiert); Lesemodell aus dem Speicher | mittel | gering | WebUI-Kernimporte 47 → ~10; keine DB-Locks mehr |
| 8 | **God-Module teilen** entlang der vorhandenen Nähte: live_trader → scan/decide/execute/positions; crypto_engine → universe/signals/orders/protection/reconcile; app.py → Router je Seite | groß | gering (nach 3–5) | kein Modul > 1.500 Zeilen |
| 9 | **PULSAR-Grenze:** CandidateSink-Port statt live_trader-Kopplung; measurement liest Ergebnisse über die Anwendungsschicht; KI-Ring auflösen | klein | gering | PULSAR-Kanten nach außen nur über Ports |
| 10 | **Entscheidungen statt Ausnahmen:** BrokerFehler/OrderStatusUnklar/EvidenceError nur noch für echte Fehler; Fachablehnungen als Ergebnisobjekt (Sperrliste wird Quelle, nicht Anzeige) | mittel | gering | BrokerFehler-Kanten 249 → < 60 |

Was der Plan bewusst **nicht** tut: keinen Neuaufbau, kein Umschreiben der Fachregeln, keine Änderung an Broker-Verhalten. Schritte 0–2 sind die Voraussetzung und sofort machbar; 3–5 sind der Kern und sollten in dieser Reihenfolge laufen; 6–10 profitieren davon. Nach jedem Schritt wird der Graph neu gebaut (`/graphify . --update`) und mit dieser Baseline verglichen: Zyklen, Fan-out der God-Module, Adapter-Kopplung, WebUI-Kernimporte.

---

### Kennzahlen-Baseline (19.09.2026, 10.7.1)

| Kennzahl | Wert |
|---|---:|
| Import-Zyklen (ohne Tests) | 6 |
| Größte stark zusammenhängende Komponente | 27 Module |
| Fan-out live_trader / crypto_engine / app.py | 85 / 41 / 28 |
| Module > 1.500 Zeilen | 8 (crypto_engine, live_trader, broker/okx, broker/etoro, etoro_reconciliation, trade_ledger, NEXUS_10_Diagnose, config) |
| Direkte Nutzer von broker/okx / broker/etoro (ohne Tests) | 15 / 11 |
| WebUI → Kernmodule | 47 |
| Zustandsdateien (JSON) / Datenbanken | 45 / 8 |
| BrokerFehler-Kanten | 249 |
| Abgleichmodule eToro / OKX | 6 / 9 |
