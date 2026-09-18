# NEXUS 10.3.1 – Installation auf dem Raspberry Pi 5 (Punkt-Hotfix)

**Build:** `10.3.1-ETORO-CASH-DELTA-SETTLEMENT` · Basis: 10.3.0-NEXUS · Diagnose 1.8.1

Behebt die wiederkehrende eToro-Kaufsperre nach jedem Verkauf („Ergebnisabgleich
ausstehend"). Ursache: eToro liefert für geschlossene Positionen keine
Abschlusskosten, und die bisherige Zuordnung über den Barbestand funktionierte
nur bei genau einer offenen Position. Jetzt ordnet NEXUS die Barbestandsbewegung
auch bei mehreren offenen Positionen exakt zu und verbucht die Abrechnung unter
engen Plausibilitätsgrenzen automatisch — aus zwei echten Brokerbelegen, ohne
Gebührenannahme. Kein Umbuchen bestehender Daten.

## Phase 1 – Paketpruefung

```bash
bash "$HOME/Downloads/NEXUS_10.3.1_Installieren.sh" --paket-pruefen
```

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.3.1_Installieren.sh"
```

## Phase 3 – Abnahme (wenige Minuten nach dem Start)

1. Log: „**eToro-Abrechnung automatisch protokolliert: Trade 79 netto 122.08 USD
   (Abschlusskosten 1.00 USD aus Barbestandsbelegen)**".
2. Die Sperre „Ergebnisabgleich ausstehend: 1 Verkaufsergebnis(se) …" fällt;
   eToro-Käufe sind wieder möglich (Demo).
3. Im Trades-Dialog „Abschlusskosten prüfen" steht bei AAPL: bereits
   protokolliert (automatisch aus Barbestandsbelegen).

Ab jetzt läuft das nach jedem eToro-Verkauf von selbst, solange die Belege
eindeutig sind. Wenn nicht (z. B. Kosten über 5 USD, offene Orders, Bewegung
einer anderen Position im selben Intervall), zeigt der Dialog den Grund und
wartet wie bisher auf deine Bestätigung.

## Optionen (unveraendert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
