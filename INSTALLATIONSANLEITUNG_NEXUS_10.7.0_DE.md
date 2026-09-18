# NEXUS 10.7.0 – Installation auf dem Raspberry Pi 5

**Build:** `10.7.0-BUY-GATE-SCOPE-AND-FEE-EVIDENCE` · Revision 2 · Basis 10.6.0 · Diagnose 1.9.0

Revision 2 unterscheidet sich von Revision 1 nur durch einen angepassten Test
(`test_v988_staging_and_guard`), an dem der Pi-Volltest von Revision 1 mit
3396 bestandenen Tests und genau diesem einen Fehler abgebrochen hat. Der
archivierte Ordner `ALT_FEHLGESCHLAGEN_20260918T171203_TradingBot_v10.7.0_NEXUS`
kann bleiben; der Installer startet sauber.

Diese Version behandelt die Kaufsperren von der Wurzel, nach deiner Entscheidung
vom 18.09.2026. Vier Dinge ändern sich im Verhalten:

1. **Ein unbekanntes Verkaufsergebnis sperrt nur noch am Verkaufstag.** Ab dem
   Folgetag bleibt es Buchhaltung, keine Sperre.
2. **Ein ungeklärter Bestand sperrt nur den betroffenen Coin**, nicht mehr das
   ganze OKX-Konto.
3. **Der Gebührenbeleg bei eToro entsteht von selbst**: aus dem Barbestand über
   alle Ereignisse im Intervall, und wenn das nicht reicht, als gekennzeichneter
   Erwartungswert aus deinen bestätigten Abrechnungen.
4. **Der Aktien-Stop hat einen Mindestabstand von 1 %** und bezieht sich auf den
   Kaufkurs, nicht auf den Signalkurs des Scans.

## Phase 1 – Paketprüfung

```bash
bash "$HOME/Downloads/NEXUS_10.7.0_Installieren.sh" --paket-pruefen
```

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.7.0_Installieren.sh"
```

Der Installer entpackt, führt den vollständigen Volltest aus und startet die
Dienste nur, wenn alle Tests bestehen. Bestehende Daten werden nicht umgebucht.

## Phase 3 – Was nach dem Start von selbst passiert

- **OKX**: Die drei Bestandslücken von XRP, BTC und ETH schließen sich im ersten
  Zyklus (Logzeile „OKX: 3 Bestandsbeleg(e) geschlossen"). OKX ist danach frei.
- **eToro, CSCO**: Innerhalb von 5 Minuten versucht der Bot die Intervallrechnung
  aus den Barbestandsbelegen. Gelingt sie, steht im Log „eToro-Abrechnung
  automatisch protokolliert … Weg INTERVAL". Gelingt sie nicht, wird 15 Minuten
  nach dem Verkauf der Erwartungswert eingetragen: „mit ERWARTUNGSWERT
  eingetragen: netto … (Abschlusskosten 1.00 USD aus 5 bestätigten
  Abrechnungen; nicht belegt)". eToro ist danach frei.
- **Ältere offene Belege** (TXN, AMD vom 08./10.09. und die 27 Trades ohne
  Kostenbeleg) sperren nicht mehr. Sie bleiben in der Diagnose als offen sichtbar.

## Phase 4 – Abnahme

1. **Übersicht**: Bei jedem Broker unter „Verbindungen, Abgleich & Bedingungen"
   der neue Block **Kaufsperren**. Er nennt je Sperre Grund, Reichweite (nur
   dieser Wert / ganzes Konto), wodurch sie endet, und die Auflösung. Ohne Sperre
   steht dort „Keine aktive Kaufsperre".
2. **Handel, CSCO**: Beim Trade steht entweder die automatische Abrechnung oder
   der Hinweis „Abschlussgebühr als Erwartungswert eingetragen · nicht belegt".
   Der Dialog „Abschlusskosten prüfen" bleibt verfügbar; eine Bestätigung dort
   ersetzt den Erwartungswert.
3. **Nächster Aktienkauf**: Im Systemprotokoll die Zeile „STOP <Symbol>: …
   Mindestabstand … erweitert" oder „… auf den frischen Kaufkurs … bezogen". Die
   Entscheidung im Logbuch trägt `entry_reference` und `stop_distance_pct`.
4. **Diagnose**: Unter „Handelsbereitschaft und beobachtete Blockaden" die
   Sperrliste je Broker; unter den Kostenlücken die Erwartungswerte als eigene
   Kategorie (INFO, nicht WARN).

## Was weiter sperrt, und zwar das ganze Konto

Kontowechsel, nicht persistierbarer Risikozustand, unlesbares Ledger und Geld
ohne Kontozuordnung. Die Tagesverlustgrenze, die Equity-Bremse und die
Verlustserien-Pause gelten unverändert.

## Hinweis zum Profil

Der Mindestabstand fängt das CSCO-Muster ab, ändert aber nicht den ATR-Faktor des
Profils. OFFENSIV setzt weiter den engsten Stop (1,5 × ATR); der Mindestabstand
greift nur, wenn der ATR-Stop enger als 1 % wäre.

## Optionen (unverändert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
