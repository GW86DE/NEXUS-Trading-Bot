# NEXUS 10.1.9 – Installation auf Raspberry Pi 5

NEXUS 10.1.9 ist ein Update von 10.1.8. Der selbstenthaltende Installer prueft zuerst die ZIP-Pruefsumme und anschliessend jede Quelldatei gegen `MANIFEST_SHA256.json`. Der vorhandene NEXUS-Updater sichert und migriert persistente Einstellungen, Handels-/Order-/Fill-Belege, Positionen, Risiko-, PULSAR-, GPT-Kosten-/Budget- und Cachezustaende. Ein unbekannter Orderausgang wird nicht durch eine neue wirtschaftliche Order ersetzt.

## Vorpruefung ohne Installation

```bash
bash "$HOME/Downloads/NEXUS_10.1.9_Installieren.sh" --paket-pruefen
```

Erwartet: `PAKETPRUEFUNG OK`. Dabei werden keine Dienste veraendert und keine Brokeraktion ausgefuehrt.

## Installation

Als normaler Benutzer `georg`, nicht mit `sudo`:

```bash
bash "$HOME/Downloads/NEXUS_10.1.9_Installieren.sh"
```

Der automatische Updatepfad ist auf Linux ARM64/Raspberry Pi OS 64-Bit begrenzt. Bestehende Dienste und Zustandsdaten werden ueber den vorhandenen `Nexus_Update.sh` behandelt. LIVE wird dadurch nicht pauschal freigegeben.

## Optional: nur planen

```bash
bash "$HOME/Downloads/NEXUS_10.1.9_Installieren.sh" --plan
```

Dies fuehrt die Quelle/Ziel-Vorpruefung aus, ohne den regulaeren Dienststart.

## Diagnose nach dem Update

Im neuen Quellordner:

```bash
cd "$HOME/Georg/TradingBot_v10.1.9_NEXUS"
./NEXUS_10.1.9_Diagnose_Starten.sh
```

Standard sind 30 Minuten passive Beobachtung. Das Diagnosewerkzeug 1.8.0 sendet keine Testorder und loest keine neuen Broker-, GPT-, FMP-, Massive-, X- oder Telegram-Anfragen aus. Die erzeugte Diagnose-ZIP anschliessend zur Auswertung hochladen.

## Was nach 10.1.9 konkret kontrolliert werden soll

1. OKX-Readiness muss nicht mehr pauschal `handelbar 0.00` melden, wenn freies USDC vorhanden und EUR/USDC als Entry-Waehrungen freigegeben sind.
2. Wenn Guthaben ausschliesslich in nicht freigegebenen Waehrungen liegt, muss NEXUS weiterhin blockieren und diese Waehrungen benennen.
3. Fehlt ein belastbarer Kreuzkurs zur Risikowaehrung, muss die Risikobasis unbekannt bleiben; es darf keine Stablecoin-Paritaet angenommen werden.
4. PULSAR-Karten sollen in der Diagnose als voller Export oder als `EXPORTED_COMPACT_PROJECTION` belegbar sein.
5. Historische eToro-Netto-/Kostenluecken bleiben offen, soweit keine neuen Originalbelege vorliegen.
