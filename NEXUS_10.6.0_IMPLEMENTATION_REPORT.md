# NEXUS 10.6.0 – Umsetzungsbericht

**Build:** `10.6.0-RISK-LEVELS-BALANCE-RESTORE` · 18.09.2026
**Enthält:** 10.4.0 und 10.5.0 vollständig (beide auf dem Pi nie installiert)

## Auftrag

1. Dort, wo man die Stufen einstellt, soll je Broker aus drei Möglichkeiten
   wählbar sein, wie viel Prozent pro Trade investiert wird – mit kurzer
   Beschreibung, umstellbar ohne Bot-Neustart.
2. Den OKX-Fehler sauber beheben.
3. Vor Auslieferung ein Volltest über die Installationsdatei.

## 1. Einsatzstufe je Broker

### Warum es nötig war

`TopfGrenzen.fuer_broker` bevorzugt Werte mit Brokerpräfix. In `config.py`
stehen feste `OKX_RISK_PER_TRADE_PCT` (0,003) und `OKX_MAX_POSITION_PCT`
(0,05). Das Risikoprofil schreibt über `profiles.apply_profile` nur die
präfixlosen und die `CRYPTO_`-Namen. Der OKX-Topf hat einen Profilwert deshalb
nie gesehen. `crypto_engine.py` enthält weder `ACTIVE_PROFILE` noch
`CRYPTO_RISK_PER_TRADE_PCT`; beide Namen kommen in der Datei nicht vor.

### Umsetzung

| Baustein | Aufgabe |
|---|---|
| `risk_levels.py` (neu) | Stufendefinition, Zustandsdatei, `effective()` als einzige Quelle der wirksamen Zahlen |
| `risk_pots.RiskPot.einsatz()` | liest die Stufe bei jedem Kaufversuch frisch; explizit übergebene Grenzen bleiben unangetastet |
| `risk_manager.size_new_position(risk_pct=…)` | Einsatz je Trade ist jetzt vorgebbar |
| `live_trader._einsatz_etoro()` | Aktienpfad holt die eToro-Stufe je Kandidat |
| WebUI | Abschnitt „Einsatz je Trade", Endpunkt `/api/risk-level`, Bestätigungsphrase für die höchste Stufe |

Der Zustand liegt in `risiko_stufen.json` mit Revision und Wechselverlauf, nach
dem bewährten Muster von `crypto_strategy_mode.py`. `settings_migration.py` und
`volltest.py` kennen die Datei, sie überlebt Updates und wird nie ausgeliefert.

### Entwurfsentscheidungen

- **Ohne Wahl ändert sich nichts.** `status()` meldet `chosen=False` und liefert
  bewusst keine Zahlen; `effective()` gibt dann die übergebenen Basiswerte
  zurück. Damit war kein einziger bestehender Test anzupassen.
- **Anzeige = Entscheidung.** `uebersicht()` und `positionsgroesse()` rufen
  dieselbe Methode. Die wirksame Stufe steht in der Begründung und im Journal.
- **Fail-safe nach unten.** Unlesbare oder entfernte Datei ⇒ kleinste Stufe.
  Ein Fehler darf den Einsatz nie vergrößern, und er sperrt auch nichts.
- **Getrennte Broker.** Eine OKX-Wahl verstellt eToro nicht und umgekehrt.

## 2. OKX: Bestätigen statt sofort buchen

`OKX_POSITION_MISSING_CONFIRM_SECONDS` stand in der Konfiguration und wurde
nirgends abgefragt. Der Kommentar im Positionsabgleich versprach seit 9.5 „zwei
Schnappschüsse in Folge plus Mindestalter"; geprüft wurde beides nie.

Neu prüft `_fehlbestand_bestaetigt()` beides: zwei Messungen in Folge **und**
mindestens 30 Sekunden seit der ersten. Ein belegter Verkauf umgeht die Frist.
Der Verdacht wird sofort gemeldet, ausdrücklich als unbestätigt und ohne
Buchung.

## 3. OKX: Rückweg aus der Bestandslücke

`okx_accounting.resolve_balance_gap_restored()` schließt eine Lücke, wenn der
Bestand wieder messbar die gebuchte Menge erreicht. Kein erfundener Verkauf:
Der Beleg ist die Messung, festgehalten als `BALANCE_RESTORED:<menge>@<zeit>`.

`crypto_engine._bestand_zurueckgewonnen()` verlangt Schnappschuss der Runde,
volle Menge, keinen Verkaufsbeleg und zwei Messungen mit mindestens 120
Sekunden Abstand. Entsperren dauert damit länger als sperren. Erst Ledger, dann
Position: Schlägt die Buchung fehl, bleibt die Sperre. Der Broker-Schutz wird
danach im selben Takt neu bestätigt statt aus dem Gedächtnis übernommen.

## 4. Nebenbefund, mitgefixt

Eine Ledgerzeile ohne vollständige Kontokette ließ `mark_balance_gap` werfen,
und die Ausnahme riss den gesamten Positionszyklus mit. Dieser Fall geht jetzt
in die vorhandene Quarantäne `mark_unanchored`. Gefunden wurde er nur, weil der
Fix die Buchung einen Zyklus später auslöste und dadurch ein anderer
Ledgerzustand entstand.

## Dateien

**Neu:** `risk_levels.py`, `tests/test_v1060_einsatzstufen.py`,
`tests/test_v1060_bestandsrueckkehr.py`, `CHANGELOG_v10.6.0_NEXUS.txt`,
`INSTALLATIONSANLEITUNG_NEXUS_10.6.0_DE.md`, `NEXUS_10_6_0_Diagnose.py`,
`NEXUS_10.6.0_Diagnose_Starten.sh`

**Geändert:** `risk_pots.py`, `risk_manager.py`, `live_trader.py`,
`crypto_engine.py`, `okx_accounting.py`, `config.py`, `settings_migration.py`,
`volltest.py`, `NEXUS_10_Diagnose.py`, `webui/app.py`,
`webui/settings_store.py`, `webui/templates/settings.html`,
`webui/static/settings.js`, `webui/static/app.css`
