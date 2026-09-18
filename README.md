# NEXUS Trading-Bot

**Regelbasierter Trading-Bot für eToro-Aktien und OKX-Spot-Krypto auf dem Raspberry Pi 5.**
Aktueller Stand: **10.7.1** (`10.7.1-LOCK-RESOLUTION-AND-LEDGER-RECEIPTS`), September 2026.

NEXUS handelt standardmäßig auf **Demo-/Paper-Konten**. Er analysiert Kerzen,
Markt- und Kontodaten, kauft nach festen Regeln mit Stop-Loss und Take-Profit
und schreibt jede Entscheidung, jede Order und jedes Ergebnis in ein Geldbuch
(Ledger), das vor jedem neuen Kauf geprüft wird. Nichts wird erfunden: Fehlt
ein Beleg, gilt das Ergebnis als **UNKNOWN**, nicht als 0.

> Hinweis des Autors: Der Bot ist ein privates Lernprojekt, hat noch Fehler
> und ist mit viel Unterstützung von Claude entstanden. Keine Anlageberatung,
> keine Gewinnversprechen. Echtgeldhandel ist möglich, aber ausdrücklich
> abgesichert und nie die Voreinstellung.

---

## Inhalt

1. [Was NEXUS macht](#was-nexus-macht)
2. [Architektur](#architektur)
3. [Broker](#broker)
4. [Strategien](#strategien)
5. [Risiko und Sperren](#risiko-und-sperren)
6. [Geldbuch und Belege](#geldbuch-und-belege)
7. [PULSAR und Marktintelligenz](#pulsar-und-marktintelligenz)
8. [WebUI](#webui)
9. [Telegram](#telegram)
10. [Diagnose](#diagnose)
11. [Backtest](#backtest)
12. [Installation auf dem Raspberry Pi 5](#installation-auf-dem-raspberry-pi-5)
13. [Konfiguration](#konfiguration)
14. [Tests und Release-Prozess](#tests-und-release-prozess)
15. [Projektstruktur](#projektstruktur)
16. [Dokumentation](#dokumentation)
17. [Unveränderliche Regeln](#unveränderliche-regeln)

---

## Was NEXUS macht

- **Aktien (eToro):** Scan eines festen Kernuniversums plus dynamischer
  Kandidaten (Favoriten, PULSAR-Gäste), Signale nach der Standardstrategie
  oder einer der Zusatzstrategien, Kauf per limitierter Order mit
  mitgesendetem Stop/Take-Profit, Mindestabstand des Stops 1 % zum Kaufkurs.
- **Krypto (OKX Spot):** Universum aus den handelbaren Kontoinstrumenten
  (EUR-/USDC-Paare), Signale im Freqtrade-Modus oder nach Zusatzstrategien,
  Schutz durch OCO-Orders beim Broker, Bestandsabgleich gegen das Konto.
- **Risiko:** Tagesverlustgrenze, Equity-Bremse, Verlustserien-Pause,
  Positionslimits, Einsatz je Trade in drei Stufen je Broker – alles
  unabhängig vom Broker durchgesetzt und persistiert.
- **Buchhaltung:** Jeder Kauf und Verkauf wird mit Broker-Belegen (Fills,
  Gebühren, Barbestand) im Ledger abgeglichen. Offene Belege sperren neue
  Käufe nur so weit, wie ihr Schutzzweck reicht (je Coin, je Handelstag).
- **Beobachtung:** WebUI, Telegram-Meldungen, passives Diagnosewerkzeug
  (30-Minuten-Beobachtung als ZIP), Backtest-Werkzeug.

## Architektur

```
live_trader.py             Aktienseite: Scan, Entscheidung, Order, Positionsführung (eToro)
crypto_engine.py           Kryptoseite: Universum, Signale, Orders, Schutz, Bestandsabgleich (OKX)
risk_manager.py            Risikozustand je Broker (Tagesbasis, Ergebnisse, Belege, Sperrgründe)
risk_pots.py               Risikotöpfe: Einsatz, Positionslimits, Kaufprüfung (OKX)
risk_levels.py             Drei Einsatzstufen je Broker, ohne Neustart umstellbar
handelsfreigabe.py         Sperrliste je Broker: Grund, Reichweite, Ablauf, Auflösung
trade_ledger.py            Geldbuch (SQLite): Trades, Fills, Exit-Ereignisse, Belege
okx_accounting.py          OKX-Buchhaltung: Bestandslücken, Reichweite je Coin, Reparatur
etoro_reconciliation.py    eToro-Abgleich: Orders, Positionen, Abschlüsse, Buchungsfälle
etoro_settlement_review.py eToro-Abschlusskosten: Barbestand, Intervallrechnung, Erwartungswert
risk_result_recovery.py    Verbindet Ledger und Risikozustand (Belege nachholen, beziffern)
broker/                    Adapter: eToro (REST + Privatstream), OKX (REST, EEA-Endpunkte)
universe/                  Universumsauswahl Aktien/Krypto
pulsar/                    PULSAR: Social-/News-Aufmerksamkeit, Hype-Spur, Messung
market_intelligence/       X-Recherche mit Monatsbudget (nie Entscheider)
webui/                     FastAPI-Oberfläche (lokal, CSP script-src 'self', kein CDN)
NEXUS_10_Diagnose.py       Passives Diagnosewerkzeug (rein lesend)
volltest.py                Vollständiger Testlauf, Pflicht vor jedem Dienststart nach Update
```

Zwei Dienste laufen auf dem Pi: `tradingbot-pi5` (Bot) und `tradingbot-webui`
(Oberfläche). Der Bot schreibt Laufzeitstatus und Zustandsdateien; die WebUI
liest sie und schreibt Einstellungen – sie hat selbst keine Brokerverbindung.

## Broker

| Broker | Markt | Umgebung | Besonderheiten |
|---|---|---|---|
| eToro | Aktien (long, Hebel 1, kein CFD) | Demo/Live getrennt (API-Schlüssel je Umgebung) | Stop/Take-Profit werden mit der Order gesendet; Abschlusskosten liefert die API nicht – NEXUS ermittelt sie aus Barbestandsbelegen oder trägt einen gekennzeichneten Erwartungswert ein |
| OKX | Spot-Krypto (EUR-, USDC-Paare) | Demo (`x-simulated-trading`) / Live | OCO-Schutzorders beim Broker, Fill-Belege je `tradeId`, Bestandsabgleich gegen den Kontostand, Lot-Reste als eigene Ledgerzeilen |

Ein Kontowechsel (anderer Konto-Fingerabdruck) sperrt den Handel dieser
Domäne, bis die Buchhaltung ausdrücklich freigegeben ist.

## Strategien

**Aktien:** `NEXUS_STANDARD` (Momentum/Trend mit ATR-Stop), dazu
`RSI2_MEAN_REVERSION`, `HIGH_52W_MOMENTUM`, `GOLDEN_CROSS_TREND`.
**Krypto:** `NEXUS_STANDARD`, `FREQTRADE_SAMPLE` (Freqtrade-Beispielregeln
mit 10 %-Stop), `TSMOM_LONG_FLAT`, `KELTNER_BREAKOUT`, `MACD_TREND_CRYPTO`
sowie `CRYPTO_PAUSED`.

Der Modus wird je Broker in der WebUI umgeschaltet (Bestätigungsphrase
`STRATEGIE AKTIVIEREN`); Signalquelle für Live und Backtest ist dieselbe
Datei (`zusatz_strategien.py`). Der Backtest-Bericht erklärt jede Strategie
laienverständlich.

## Risiko und Sperren

- **Profile** `konservativ`, `ausgewogen`, `offensiv` (ATR-Faktoren, Limits).
- **Einsatzstufen** je Broker (WebUI → Einstellungen → „Einsatz je Trade"):
  OKX 0,3/0,6/1,2 % Risiko je Trade, eToro 0,5/1,0/2,0 %; die höchste Stufe
  verlangt die Phrase `EINSATZ ERHOEHEN`. Ohne Wahl gelten die Basiswerte.
- **Bremsen:** Tagesverlustgrenze, Equity-Bremse auf nativen Drawdown (FX-robust
  über mehrere finanzierte Währungen), Verlustserien-Pause, Positions- und
  Tageslimits.
- **Sperrliste** (Übersicht → je Broker „Kaufsperren"): jede aktive Sperre
  mit Grund, Reichweite (nur dieser Wert / ganzes Konto), Ablauf (Tageswechsel,
  Beleg, Zeit, Reparatur) und Auflösungsweg. Ein ungeklärter Bestand sperrt
  nur den betroffenen Coin; ein unbekanntes Verkaufsergebnis sperrt die
  Domäne nur am Verkaufstag; Kontowechsel, Persistenzfehler und unlesbares
  Ledger sperren immer alles.

## Geldbuch und Belege

Das Ledger (`decision_history.sqlite`) ist die finanzielle Wahrheit:
Entscheidungen, Orders, Fills, Trades, Exit-Ereignisse, Kosten- und
Ergebnisbelege, Bestandslücken. Grundsätze:

- **UNKNOWN bleibt UNKNOWN.** Fehlende Gebühren werden nie als 0 gebucht.
- **Belege statt Annahmen.** Ein Ergebnis gilt als bestätigt, wenn Fills,
  Kosten und Währung belegt sind (`CONFIRMED`, `CASH_DELTA_CONFIRMED`,
  `USER_CONFIRMED`). Ein **Erwartungswert** (`EXPECTED_UNVERIFIED`) beziffert
  die eToro-Abschlussgebühr aus mindestens drei bestätigten Abrechnungen
  desselben Kontos, ist gekennzeichnet und wird vom nächsten Beleg ersetzt.
- **Keine Währungsparität.** USDC ≠ USD ≠ EUR; Umrechnungen nur mit
  belegtem Kurs (EUR-Referenzbewertung aus Kerzen bzw. EZB).
- **Kein Verkauf auf Fremdbestand.** Nur bewiesene Bot-Positionen werden
  automatisch verkauft; Konto-Assets und Staub werden angezeigt, nicht angefasst.

## PULSAR und Marktintelligenz

PULSAR beobachtet Aufmerksamkeit (Reddit, StockTwits, FINRA Short Interest,
Volumen, News) und meldet Hype-Kandidaten per Telegram. Nominierungen laufen
nur im NY-Fenster, mit Existenzrisiko-Sperre (Insolvenz, Delisting,
Handelsaussetzung) und gemessenem Erfolg je Kandidat. X-Recherche ist auf
15 EUR/Monat budgetiert. **PULSAR, X und GPT haben keine Orderbefugnis** –
sie liefern Kandidaten und Belege, die Regeln entscheiden.

## WebUI

Lokal auf dem Pi (`tradingbot-webui`), mit Anmeldung. Seiten:

| Seite | Inhalt |
|---|---|
| Übersicht `/` | Verbindungen, Bereitschaft, Kaufsperren je Broker, Kapital |
| Handel `/trades` | Trades mit Kerzenansicht (ECharts lokal), Abschlussabrechnung prüfen |
| Positionen `/positions` | Offene Positionen, Schutz, Beobachten/Verkaufen |
| Logbuch `/logbook` | Entscheidungen, Scan-Übersicht je Zyklus |
| Universum `/universe`, Underdogs `/underdogs` | Auswahl und Freigaben |
| PULSAR `/pulsar`, Quellen & X `/sources` | Karten, Messung, Quellenstatus, Registry |
| Analyse `/analysis`, Backtest `/backtest` | Auswertungen, Backtest-Läufe und Berichte |
| Diagnose `/diagnosis` | Diagnose starten, ZIP laden, Bericht öffnen |
| Einstellungen `/settings` | Strategie-Modi, Einsatzstufen, Quellenschalter, Telegram |

Sicherheit: CSP `script-src 'self'`, keine externen Ressourcen, keine
Klarnamen in Diagnose-Exporten, Geheimnisse maskiert.

## Telegram

Meldungen zu Käufen, Verkäufen, Sperren („ERGEBNISABGLEICH ERFORDERLICH"),
Hype-Alarmen und Diagnose-Versand; eingeschränkte Steuerbefehle für den
freigegebenen Benutzer. Einrichtung: `TELEGRAM_EINRICHTUNG_DE.txt`.

## Diagnose

```bash
bash ~/Georg/TradingBot_v10.7.1_NEXUS/NEXUS_10.7.1_Diagnose_Starten.sh --minuten 30
```

Sammelt Startstand, beobachtet 30 Minuten, sammelt Endstand: Zustandsdateien,
Datenbank-Exporte (rein lesend, aus privater Online-Sicherung), Logs,
Dienststatus, Messpunkte; erzeugt `BERICHT.md`, `BEFUNDE.json`,
`ZUSAMMENFASSUNG.json`. Keine Brokeraktion, keine zusätzlichen Abrufe.
Der Bericht ist in der WebUI unter Diagnose → „Bericht öffnen" lesbar.

## Backtest

`NEXUS_Universum_Backtest_V6.sh` (eigenständig, rein lesend) testet alle
Strategien über das heutige Universum mit Kosten, Lookahead-Prüfung und
Robustheitsvarianten; Start und Bericht über die WebUI-Seite Backtest.

## Installation auf dem Raspberry Pi 5

Voraussetzungen: Raspberry Pi OS 64 Bit, Python 3.11+, Internet, ein Nutzer
ohne root-Rechte.

**Erstinstallation**

```bash
bash Pi_Installieren.sh          # .venv, Abhängigkeiten, Dienste
bash Nexus_Einrichten.sh         # Schlüssel verdeckt eingeben (.env), Demo-Modus
bash Pi_Service_Aktivieren.sh    # Bot-Dienst
bash Pi_WebUI_Aktivieren.sh      # WebUI-Dienst
```

**Update mit dem versionsgebundenen Installer** (empfohlener Weg)

```bash
bash ~/Downloads/NEXUS_<Version>_Installieren.sh --paket-pruefen   # nur prüfen
bash ~/Downloads/NEXUS_<Version>_Installieren.sh                   # installieren
```

Der Installer enthält das Quellpaket (SHA256-gebunden), übernimmt
Einstellungen, Ledger und Zustände aus dem laufenden Stand, führt den
vollständigen Volltest aus und startet die Dienste nur bei Erfolg. Ein
fehlgeschlagenes Staging wird als `ALT_FEHLGESCHLAGEN_…` archiviert; der
alte Stand läuft weiter. Details je Version:
`INSTALLATIONSANLEITUNG_NEXUS_<Version>_DE.md`.

Weitere Skripte: `Pi_Service_Status.sh`, `Pi_Service_Stoppen.sh`,
`Pi_Neue_Kaeufe_Pausieren.sh`, `WireGuard_Status_Pruefen.sh`
(VPN-Zugriff auf die WebUI, siehe `WIREGUARD_VPN_EINRICHTUNG_DE.md`).

## Konfiguration

Geheimnisse liegen ausschließlich in `.env` (nie im Repository):
`ETORO_DEMO_API_KEY`/`ETORO_DEMO_USER_KEY` (bzw. `_LIVE_`),
`OKX_DEMO_API_KEY`/`_SECRET`/`_PASSPHRASE` (bzw. Live), `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`, `FMP_API_KEY`, `FINNHUB_API_KEY`,
`ALPHAVANTAGE_API_KEY`, `MASSIVE_API_KEY`. `nexus_setup.py` liest sie verdeckt
ein. Betriebsparameter stehen in `config.py` (Umgebungsvariablen mit
Standardwerten), Betriebsentscheidungen (Strategie-Modus, Einsatzstufe,
Quellenschalter) in Zustandsdateien, die ein Update übernimmt.

`TRADING_MODE` ist standardmäßig Paper/Demo; Echtgeld braucht eine
ausdrückliche, mehrstufige Freigabe (`live_trading_arm`).

## Tests und Release-Prozess

- `tests/` – über 3.400 Tests (pytest) plus eine Node-Frontendsuite
  (`tests/frontend_v100.test.js`); viele Tests stellen echte Vorfälle mit den
  Originalzahlen nach (Dateinamen `test_v<Version>_*.py`).
- `volltest.py` – statische Release-Hygiene (kein `except: pass`, keine
  Laufzeitdateien, Versionspins, Klarnamen), Testabhängigkeiten, Geldpfad-
  Regressionen unter einem Netzwerkwächter (kein Test darf ins Netz),
  Selbsttest, `compileall`, Pi-Preflight, Shell-Syntax. Läuft auf dem Pi vor
  jedem Dienststart nach einem Update.
- Release-Bau (Windows, Bauskript außerhalb des Pakets): kompletter
  Testbestand isoliert je Modul, Hygiene, Node-Suite, Netzwerkwächter,
  **Je-Test-Vergleich gegen das zuletzt ausgelieferte Paket** (mit POSIX-Shims,
  damit lokal dieselben Tests laufen wie auf dem Pi), Installer-Selbsttest.
  Nachweise je Version: `validation/NEXUS_<Version>_TEST_EVIDENCE.json`,
  `NEXUS_<Version>_Pruefbericht.md`.

Lokal testen:

```bash
NEXUS_OFFLINE_TEST_ROOT="$PWD" python -m pytest tests -q -p no:cacheprovider
```

## Projektstruktur

```
broker/                  eToro- und OKX-Adapter, Transportbudget, Streams
universe/                Universumsauswahl (Aktien-Kern, Krypto-Selektor)
pulsar/                  Research, Evidence, Messung, Quellen (StockTwits, FINRA, Volumen)
market_intelligence/     X-Abfragen, Budget, Konten-Registry
webui/                   FastAPI-App, Templates, statische Skripte (ECharts lokal)
tests/                   Testbestand inkl. Fixtures mit echten (anonymisierten) Belegen
validation/              Testnachweise je Version
docs/history/            Historische Berichte früherer Versionen
offline_test_bootstrap/  Netzwerkwächter für Tests
```

## Dokumentation

- Aktuelle Version: `CHANGELOG_v10.7.1_NEXUS.txt`,
  `INSTALLATIONSANLEITUNG_NEXUS_10.7.1_DE.md`,
  `NEXUS_10.7.1_IMPLEMENTATION_REPORT.md`, `NEXUS_10.7.1_Pruefbericht.md`.
- Architektur und Hintergrund: `ARCHITEKTUR_V8_NEXUS_DE.md`,
  `FREQTRADE_MODUS_NEXUS_9.0_DE.md`, `RISIKOPRUEFUNG_NEXUS_9.0_DE.md`,
  `KRYPTO_UNIVERSUM_UND_TRADEANALYSE_NEXUS_9.0.1_DE.md`, `PULSAR_9.8.7_DE.md`,
  `WEBUI_RESEARCH.md`, `GUI_README_DE.md`, `README_RASPBERRY_PI5_DE.md`.
- Bekannte Grenzen: `KNOWN_ISSUES.md`. Frühere Versionen: `CHANGELOG_v*.txt`,
  `docs/history/`.

## Unveränderliche Regeln

1. Fehlende Daten sind UNKNOWN – nie 0, nie „sicher", nie „geschlossen".
2. Keine Gebühr wird erfunden; ein Erwartungswert ist gekennzeichnet und nie bestätigt.
3. Keine Währungsparität, kein synthetisches Volumen, kein TLS-Bypass.
4. Eine unklare Order wird nie durch eine zweite wirtschaftliche Order „repariert".
5. PULSAR, X, GPT und Reddit geben keine Order frei.
6. Kein Verkauf auf Fremdbestand; Kontowechsel sperrt die Domäne.
7. Diagnose-Exporte enthalten keine Klarnamen und keine Geheimnisse.
8. Ein Update startet den Dienst nur nach bestandenem Volltest.

---

Lizenz: noch nicht festgelegt. Bis dahin gilt: private Nutzung; vor einer
Weitergabe bitte eine Lizenz ergänzen.
