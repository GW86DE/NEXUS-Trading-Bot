# NEXUS 10.2.2 – Installation auf dem Raspberry Pi 5 (Punkt-Hotfix)

**Build:** `10.2.2-LOT-RESIDUAL-VALUATION-FIX` · Basis: 10.2.1-NEXUS · Diagnose 1.8.1

Behebt den letzten Schritt der OKX-Freigabe: Die EUR-Bewertung des bereits verbuchten DOGE-Verkaufs scheiterte an 0,00847 DOGE Lot-Staub. Nach diesem Update beziffert NEXUS das Ergebnis automatisch (spätestens ~10 Minuten nach Dienststart) und gibt die OKX-Kaufdomäne frei — keine Aktion von dir nötig. Der normale Updateweg funktioniert jetzt wieder, weil 10.2.2 eine neue Versionsnummer trägt (dein laufender 10.2.1-Stand ist die Datenquelle; alle heutigen Buchungen bleiben erhalten).

## Phase 1 – Paketpruefung

```bash
bash "$HOME/Downloads/NEXUS_10.2.2_Installieren.sh" --paket-pruefen
```

Erwartet: `PAKETPRUEFUNG OK: NEXUS 10.2.2, ... Quellhashes geprueft.`

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.2.2_Installieren.sh"
```

## Phase 3 – Abnahme (wenige Minuten nach dem Start)

1. Log/Telegram: „**EUR-Referenzbewertung für Trade 73 gespeichert: netto … EUR**"
2. Die Meldung „1 OKX-Buchungs-/Bestandsbelege offen" verschwindet; OKX kauft wieder (Demo).

Optional danach die 30-Minuten-Diagnose: `./NEXUS_10.2.2_Diagnose_Starten.sh` im neuen Quellordner.

## Optionen (unveraendert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
