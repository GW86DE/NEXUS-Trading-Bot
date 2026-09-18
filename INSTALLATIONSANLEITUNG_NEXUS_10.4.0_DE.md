# NEXUS 10.4.0 – Installation auf dem Raspberry Pi 5

**Build:** `10.4.0-CHART-SCAN-EXISTENCE` · Basis: 10.3.1-NEXUS (Rev 2) · Diagnose 1.8.2

Fünf Punkte in einer Version: PULSAR-Existenzrisiko-Block (Insolvenz, Chapter 11,
Going Concern, Delisting, Handelsaussetzung, Betrugsermittlung – eine schwache
Bilanz ist ausdrücklich kein Block), Kursbestätigung der Hype-Spur per
FMP-Starter-Quote am selben Tag, PULSAR-Datenbanksperre behoben (Schema nur
noch einmal je Prozess), Scan-Übersicht im Logbuch (eine Zeile je Zyklus und
Broker) und die neue Kerzenansicht mit Apache ECharts (lokal, kein CDN) für OKX
**und** eToro mit Zoom, Fadenkreuz, Volumen und wählbarem Zeitrahmen.

## Phase 1 – Paketpruefung

```bash
bash "$HOME/Downloads/NEXUS_10.4.0_Installieren.sh" --paket-pruefen
```

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.4.0_Installieren.sh"
```

Der Installer entpackt, führt den vollständigen Volltest aus und startet die
Dienste nur, wenn alle Tests bestehen. Bestehende Daten (Ledger, Belege,
Risikozustand, Entscheidungs-DB) werden nicht umgebucht. Neu entstehen
`scan_uebersicht.json` und `etoro_chart_candles.sqlite` im Laufzeitordner.

## Phase 3 – Abnahme

1. **Logbuch** (WebUI → Logbuch): über der Entscheidungstabelle steht je Broker
   eine Scan-Übersicht, z. B. „eToro · Aktien · letzter Scan …: 22 Instrumente
   geprüft — 2 Kaufwunsch blockiert: … — 20 ohne Signal". Erscheint mit dem
   ersten vollständigen Scannerzyklus je Broker nach dem Start (wenige
   Minuten). Im Systemprotokoll zusätzlich Zeilen „SCAN etoro: …".
2. **Kerzenansicht** (WebUI → Handel → Kerzengrafik): OKX-Trade auswählen →
   Chart mit Zeitrahmen-Buttons (5m … 1d), Mausrad zoomt, Ziehen verschiebt,
   Fadenkreuz zeigt O/H/L/C/Volumen. eToro-Trade auswählen → zunächst „Noch
   keine eToro-Kerzen … gesichert" bis zum ersten Scan; danach 1h, und für
   Instrumente mit Position/Trade der letzten 7 Tage auch 15m und 1d (innerhalb
   von 15 Minuten).
3. **PULSAR** (Telegram-Alarm oder WebUI → PULSAR): Hype-Karten tragen die
   Zeile „Existenzrisiko: KEIN Befund (…)" bzw. „BLOCKIERT" und ggf. „Bilanz
   (nur Information): …". Regelversion `PULSAR-2.1-HYPE-LANE-EXISTENCE`.
4. **Systemprotokoll**: keine Zeile „database is locked" aus `pulsar/control.py`
   mehr, auch während einer laufenden Diagnose.
5. Optional: `bash NEXUS_10.4.0_Diagnose_Starten.sh` (30 Minuten, passiv) –
   der Bericht enthält `scan_uebersicht.json`.

## Optionen (unveraendert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`

## Lizenzhinweis

Die Kerzenansicht nutzt Apache ECharts 5.5.1 (Apache License 2.0), lokal
ausgeliefert als `webui/static/echarts.min.js`; Lizenztext in
`webui/static/echarts.LICENSE.txt`. Es werden keine externen Skripte geladen.
