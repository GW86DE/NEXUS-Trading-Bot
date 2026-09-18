# NEXUS 10.7.1 – Installation auf dem Raspberry Pi 5

**Build:** `10.7.1-LOCK-RESOLUTION-AND-LEDGER-RECEIPTS` · Basis 10.7.0 Rev 2 · Diagnose 1.9.0

10.7.1 behebt vier Fehler in 10.7.0, die die Sperren vom 18.09. stehen ließen
(Details im Changelog). Kein Umbau, keine neuen Einstellungen, keine
Datenumbuchung – die bestehenden Belege werden im laufenden Betrieb
nachgeholt.

## Phase 1 – Paketprüfung

```bash
bash "$HOME/Downloads/NEXUS_10.7.1_Installieren.sh" --paket-pruefen
```

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.7.1_Installieren.sh"
```

Der Installer entpackt, führt den vollständigen Volltest aus und startet die
Dienste nur, wenn alle Tests bestehen. Der laufende 10.7.0-Stand ist die
Datenquelle; er bleibt bei einem Abbruch unverändert weiterlaufen.

## Phase 3 – Was nach dem Start von selbst passiert

**OKX – im ersten Zyklus (Sekunden):**
- Systemprotokoll: „OKX: 3 Bestandsbeleg(e) geschlossen – die Trades 81, 82, 83
  sind vollständig verkauft und verbucht; der Lot-Rest läuft als eigene Zeile
  weiter."
- Danach: „Krypto: BTC, ETH, XRP wieder für Neueinstiege frei."
- Übersicht → OKX → Block **Kaufsperren**: „Keine aktive Kaufsperre".

**eToro – Minuten bis etwa 30 Minuten:**
1. Die Warnung „Einstiegs-Fillregister enthält widersprechenden Beleg" hört
   auf (bisher alle 10 Sekunden).
2. Kostennachlauf (spätestens 30 Minuten nach dem letzten Versuch): Einstieg
   CSCO bestätigt, Historienbeleg aufgezeichnet.
3. Abrechnungs-Worker: „eToro-Verkauf Trade 88 (CSCO, …) im Risikozustand
   nachregistriert", dann „eToro-Abrechnung Trade 88 … Weg INTERVAL" oder
   „… mit ERWARTUNGSWERT eingetragen: netto -10.16 USD (Abschlusskosten 1.00 USD
   aus 5 bestätigten Abrechnungen; nicht belegt)".
4. Übersicht → eToro → **Kaufsperren**: „Keine aktive Kaufsperre"; die nächste
   Kaufprüfung läuft ohne PNL_INCOMPLETE.

Ältere geschlossene Trades ohne Nettoergebnis werden ebenfalls registriert
(mit ihrem alten Verkaufstag, also ohne Sperre) und bekommen einen
Erwartungswert, sobald Einstieg und Historienbeleg vorliegen.

## Phase 4 – Abnahme

1. **Übersicht**: beide Broker ohne aktive Kaufsperre. Falls doch eine steht:
   Grund, Reichweite, Ablauf und Auflösung stehen daneben – bei eToro jetzt
   je Buchungsfall mit Symbol; „LEDGERABGLEICH_OFFEN / Reparatur" heißt: der
   Abgleich hängt, bitte Diagnose ziehen.
2. **Handel → CSCO (Trade 88)**: Abrechnung automatisch (Intervall) oder
   Hinweis „Abschlussgebühr als Erwartungswert eingetragen · nicht belegt".
3. **Nächster Krypto-Kauf** (BTC/ETH/XRP-Signale kommen alle paar Sekunden):
   Kauf statt „kein Neueinstieg in diesen Coin".
4. **Diagnose** nach ~30 Minuten: unter „Historische Kosten- und Nettolücken"
   CSCO mit Ergebnis; keine `okx_balance_gaps` mehr PENDING für Konto
   244895ca (die fünf alten Lücken des Vorkontos 86b720de bleiben, sie sperren
   nichts).

## Was weiter sperrt, und zwar das ganze Konto

Kontowechsel, nicht persistierbarer Risikozustand, unlesbares Ledger, Geld
ohne Kontozuordnung, ein Verkaufsergebnis von HEUTE ohne Beleg und Erwartungswert
(bis Mitternacht), sowie ein Buchungsfall, dessen Ledgerabgleich fehlschlägt
(bis zur Reparatur). Tagesverlustgrenze, Equity-Bremse und
Verlustserien-Pause gelten unverändert.

## Optionen (unverändert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
