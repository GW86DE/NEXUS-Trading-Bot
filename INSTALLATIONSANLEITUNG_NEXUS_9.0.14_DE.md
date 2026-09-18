# Update auf NEXUS 9.0.14

NEXUS 9.0.14 korrigiert den Freqtrade-Sample-Exitpfad, den OKX-Schutzstatus,
die Gebühren-/Währungsrechnung und erweitert die Trades-Seite. Der bisherige
Ordner bleibt als Rückfallkopie erhalten.

## 1. Version 9.0.13 stoppen

```bash
cd ~/Georg/TradingBot_v9.0.13_NEXUS
./Pi_Service_Stoppen.sh
```

## 2. Version 9.0.14 installieren

```bash
cd ~/Georg
unzip TradingBot_v9.0.14_NEXUS.zip
cd TradingBot_v9.0.14_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration übernimmt Einstellungen und Laufzeitdaten. Bestehende
Entscheidungen, Trades und Positionsnachweise werden nicht gelöscht.

## 3. Vor dem Start vollständig testen

```bash
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
```

Erwartet werden `SELF TEST OK` und `VOLLTEST OK` für `9.0.14-NEXUS`.

## 4. Dienste starten

```bash
./Pi_Service_Starten.sh
```

## 5. Unmittelbare Kontrolle

In Telegram:

```text
/version
/health
/crypto status
/decisions
```

In der WebUI unter **Trades** kontrollieren:

- Jede offene OKX-Position besitzt ein exaktes Paar, beispielsweise
  `LINK-USDC`; es wird kein Paar aus einer Standardwährung geraten.
- Unter **Aktuell** erscheint der Full-size-Sell-VWAP mit Zeitpunkt und
  Quelle. Fehlt ein sicherer Wert, bleibt die Anzeige bewusst unbekannt.
- Nettoergebnis, Stop-Loss, Abstand zum Stop, aktives ROI-Ziel und
  brokerseitiges Take-Profit werden getrennt angezeigt.
- Der Schutzstatus wechselt nach einem vollständigen Abgleich auf
  `OKX-Schutz aktiv`. Bei einem API-Fehler muss er `Schutzabgleich läuft`
  beziehungsweise `PENDING` bleiben und darf nicht fälschlich `fehlt` melden.
- Geschlossene Trades zeigen Gewinn oder Verlust als Betrag und Prozentwert.
- EUR, USD und USDC werden in Auswertungen getrennt geführt.

## 6. Was für bestehende FREQTRADE_SAMPLE-Trades gilt

- Stop-Loss: 10 Prozent unter dem echten durchschnittlichen Fill.
- Broker-Sicherheitsziel: netto 4 Prozent, gebührenbereinigt.
- Aktives ROI-Ziel: 4 Prozent ab Einstieg, 2 Prozent nach 30 Minuten,
  1 Prozent nach 60 Minuten – jeweils netto und gebührenbereinigt.
- Zusätzlich kann ein neues Exit-Signal auf einer abgeschlossenen
  5-Minuten-Kerze verkaufen, auch bevor TP oder SL erreicht wurden und auch
  dann, wenn der Trade im Verlust liegt.
- Der Parameter-Hash bestehender Positionen bleibt gleich; das Update pausiert
  sie nicht allein wegen der technischen Korrektur.

Beispiel LINK: Bei 11,429 USDC Einstieg und jeweils 0,35 Prozent Einstiegs-
und Ausstiegsgebühr liegt das netto +1-Prozent-Ziel ungefähr bei 11,6245 USDC.
Ein Kurs von 11,48 USDC reicht dafür nicht.

## 7. Tagesverlustgrenze

Der Handelstag wird nach `LOCAL_TIMEZONE`, standardmäßig `Europe/Berlin`,
zur lokalen Mitternacht gewechselt. Bitte weder die Raspberry-Pi-Uhr
verstellen noch die Risikodatei löschen.

Ein bereits heute gesetzter Sicherheitsstopp bleibt absichtlich bis zum
lokalen Tageswechsel bestehen. Das Update rechnet neue Ergebnisse korrekt,
hebt aber eine vorhandene Schutzsperre nicht rückwirkend automatisch auf.

## 8. Kontrollierter DEMO-/PAPER-Test

eToro bleibt **PAPER**, OKX bleibt **DEMO**. Vor LIVE mindestens einen
vollständigen Zyklus beobachten:

1. Kauf wird als preisbegrenzte FOK-Order gesendet.
2. Genau eine ordId/clOrdId- und Fill-Kette wird gespeichert.
3. Stop und Take-Profit werden aus dem echten Fill berechnet.
4. Genau ein brokerseitiger Schutz wird bestätigt.
5. Die WebUI zeigt den exakten Markt, Live-VWAP, aktives ROI-Ziel und Schutz.
6. Ein Exit wird genau einmal gebucht; Einstiegs- und Ausstiegsgebühr werden
   im Nettoergebnis berücksichtigt.

Erst nach einem fehlerfreien DEMO-/PAPER-Lauf darf LIVE neu bewertet werden.
