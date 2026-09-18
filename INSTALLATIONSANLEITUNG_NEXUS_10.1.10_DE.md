# NEXUS 10.1.10 – Installation auf dem Raspberry Pi 5

**Build:** `10.1.10-BROKER-STABILITY-AND-DYNAMIC-INTELLIGENCE` · Basis: 10.1.9-NEXUS · Diagnose 1.8.1

Der Installer ist selbstenthaltend und prueft das eingebettete Quellpaket (SHA256) sowie jede einzelne Datei gegen das Manifest, bevor irgendetwas veraendert wird. Der Updateablauf sichert den bestehenden Stand, uebernimmt Einstellungen, Positionen, Orders/Fills, Risiko-, PULSAR- und Budgetzustaende und fuehrt den Volltest aus; erst danach starten die Dienste. LIVE wird nicht automatisch freigegeben.

## Phase 1 – Paketpruefung (keine Dienste, keine Brokeraktion)

```bash
bash "$HOME/Downloads/NEXUS_10.1.10_Installieren.sh" --paket-pruefen
```

Erwartete Ausgabe: `PAKETPRUEFUNG OK: NEXUS 10.1.10, ... Quellhashes geprueft.`

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.1.10_Installieren.sh"
```

Der Ablauf stoppt die Dienste, sichert den alten Stand, entpackt nach `~/Georg/TradingBot_v10.1.10_NEXUS`, uebernimmt die Daten, fuehrt den Volltest aus und startet Core und WebUI neu. Schlaegt ein Schritt fehl, bricht das Update ab und der alte Stand bleibt lauffaehig.

## Phase 3 – passive 30-Minuten-Diagnose (Abnahme)

```bash
cd "$HOME/Georg/TradingBot_v10.1.10_NEXUS"
./NEXUS_10.1.10_Diagnose_Starten.sh
```

Alternativ startet die Diagnose in der WebUI unter „Diagnose". Die fertige ZIP liegt unter `~/Downloads/NEXUS_Diagnosen/` und laesst sich dort auch herunterladen.

## Phase 4 – Abnahmefragen der Diagnose 1.8.1

1. Ist `decision_history` jetzt vollstaendig exportiert (kein `DATABASE_EVIDENCE_ERROR`, keine `OperationalError` waehrend der Sammlung)?
2. Zeigt der Risikoblock `native_drawdown_pct`/`fx_drawdown_pct` getrennt, und bleibt die Equity-Bremse bei reiner USDC/EUR-Kursbewegung offen?
3. Bewertet der Kapitalbeleg Positionen zum Marktkurs (Wert folgt fallendem Kurs, nicht dem Hoechststand)?
4. Meldet ein extern geschlossener Verkauf sofort „ERGEBNISABGLEICH ERFORDERLICH"?
5. Sind GDELT und Tradestie wieder gruen?

## Neue Bedienpunkte nach dem Update

- **Backtest:** Reiter „Backtest" → Umfang waehlen → „Backtest starten". Ergebnis-ZIP und HTML-Bericht erscheinen unter „Laeufe und Ergebnisse" (Ablage `~/NEXUS_Backtests`). „Nur Plan anzeigen" prueft ohne Netzabruf.
- **OKX-Waehrungen:** Einstellungen → „OKX EEA Spot" → Abrechnungswaehrungen. USD/USDG erfordern beim Speichern die exakte Eingabe `WAEHRUNGEN FREIGEBEN`. Empfehlung: erst freigeben, wenn die OKX-USD-Umstellung (ab 23.09.2026) es noetig macht.
- **X-Quellen-Registry:** „Quellen & X" → „Dynamische Quellen-Registry". Vorschlaege erscheinen automatisch; aktivieren nur nach eigener Identitaetspruefung des Kontos (Vermerk erforderlich).
- **eToro-Ergebnisabgleich:** Bei der Benachrichtigung „ERGEBNISABGLEICH ERFORDERLICH" in der WebUI unter „Handel" den Trade oeffnen → „Abschlussabrechnung" pruefen und bestaetigen; danach kauft die Domaene wieder.

## Optionen (unveraendert zu 10.1.9)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
