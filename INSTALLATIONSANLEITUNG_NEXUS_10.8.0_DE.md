# NEXUS 10.8.0 – Installation auf dem Raspberry Pi 5

**Build:** `10.8.0-ARCHITEKTUR-LEITPLANKEN-UND-PULSAR-BESTAETIGUNG` · Basis 10.7.1 · Diagnose 1.9.0

10.8.0 bringt die Architektur-Leitplanken (Schritte 0–2 des Migrationsplans:
Importgraph-Tests, Schichtenkarte, `nexus/`-Paket mit Weichen, sechs kurze
Import-Zyklen gebrochen) und fünf PULSAR-/Luna-Punkte (Quote nachladen,
StockTwits-Ausbau, X-Bestätigungssuche, eToro-15-Minuten-Kerzen, Luna 200).
Kaufkaskade, Risikoprüfung, Buchhaltung und Broker-Verhalten sind unverändert.
Es gibt **eine** Datenanpassung beim Update: das Luna-Tagesbudget in
`ai_router_settings.json` wird von 50 auf 200 gehoben (einmalig, markiert).

## Phase 1 – Paketprüfung

```bash
bash "$HOME/Downloads/NEXUS_10.8.0_Installieren.sh" --paket-pruefen
```

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.8.0_Installieren.sh"
```

Der Installer entpackt, führt den vollständigen Volltest aus (jetzt inklusive
der Architektur-Suite `tests/test_v1080_architektur.py`, die den Importgraphen
auf dem Pi selbst misst) und startet die Dienste nur, wenn alle Tests bestehen.
Der laufende 10.7.1-Stand ist die Datenquelle; er bleibt bei einem Abbruch
unverändert weiterlaufen.

## Phase 3 – Was nach dem Start von selbst passiert

**Sofort (Startkontrolle):**
- Systemprotokoll: Migration meldet `ai_router_settings.json: Luna-Tagesbudget
  50 -> 200`.
- `risiko_stufen.json` (Einsatzstufen) bleibt im Projektordner und wird weiter
  gelesen – die gewählte Stufe je Broker ist nach dem Update unverändert
  (Übersicht → Einsatz je Trade).

**PULSAR, im nächsten Zyklus (alle 15 Minuten, Mo–Fr 07:00–19:00 NY):**
1. Karten mit Auslöser und OFFENER Kurs-/Volumenbestätigung bekommen in
   offener Sitzung einen frischen Quote (`quote_refreshed_at` auf der Karte);
   die Meldung „Quote aelter als 15 Minuten" verschwindet für aktive Karten.
2. Aktive Karten (AUSLOESER/HYPE_KANDIDAT) werden bei StockTwits alle 15
   Minuten gezählt; bei Untergrenze werden ältere Seiten nachgeladen.
   **Achtung:** Am 18.09. antwortete StockTwits mit HTTP 403 (Anbieter-Sperre).
   Solange das so bleibt, steht die Quelle auf „Abrufpause (403)" und die
   Zählwerte bleiben UNKNOWN – das ist korrekt, kein Fehler von NEXUS.
3. Der Handelskern holt für aktive Karten 15-Minuten-Kerzen von eToro
   (≤ 5 Reihen je Zyklus, 15 min Ruhe je Reihe). Die Karte zeigt
   `intraday_source: ETORO_15M`, `intraday_saved_at` und
   `intraday_avg_day_volume`, sobald Kerzen vorliegen.
4. Eine AUSLOESER-Karte mit genau einer Bestätigung im Einstiegsfenster fordert
   eine X-Bestätigungssuche an (Karte: `x_confirmation`); der X-Worker führt
   sie innerhalb von 30 s aus, das Ergebnis zählt im nächsten Zyklus als
   zweite Social-Familie.

## Phase 4 – Abnahme

1. **Einstellungen → System / Research · OpenAI · Luna / Terra**: Feld
   „Luna-Anfragen pro Tag" zeigt 200; „Tagesbudget USD" unverändert (5).
2. **Übersicht → Einsatz je Trade**: dieselbe Stufe je Broker wie vor dem Update.
3. **PULSAR-Seite** nach dem ersten Zyklus in offener NY-Sitzung: bei aktiven
   Karten `quote_refreshed_at` gesetzt; Prüfung „volumen" oder
   „kursbestaetigung" nennt die Quelle (FMP-Quote oder eToro-15-Minuten-Kerzen).
4. **Diagnose** nach ~30 Minuten in offener Sitzung
   (`bash ~/Georg/TradingBot_v10.8.0_NEXUS/NEXUS_10.8.0_Diagnose_Starten.sh --minuten 30`):
   PULSAR-Karten mit `intraday_source`/`intraday_avg_day_volume`; StockTwits-
   Quellenstatus (ok oder 403-Pause); X-Kandidatenrecherche mit
   `confirmations_per_day_max: 8`.

## Was diese Version NICHT tut

Keine neue Kaufregel, keine Änderung an Stops, Zielen, Sperren oder
Buchungen. PULSAR, X, GPT und StockTwits lösen keine Order aus; jede
Nominierung braucht weiterhin die zweistufige Telegram-Bestätigung.

## Optionen (unverändert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
