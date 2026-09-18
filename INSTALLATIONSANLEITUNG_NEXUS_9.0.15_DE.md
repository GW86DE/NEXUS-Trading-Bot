# Update auf NEXUS 9.0.15

NEXUS 9.0.15 behebt den fehlgeschlagenen LINK-Exit, schließt extern
verkaufte Positionen anhand echter OKX-Fills und ergänzt die manuelle
Tradeverwaltung in der WebUI. Der bisherige Ordner bleibt als Rückfallkopie
erhalten.

## 1. Version 9.0.14 gestoppt lassen

```bash
cd ~/Georg/TradingBot_v9.0.14_NEXUS
./Pi_Service_Stoppen.sh
```

Nur eine Version darf laufen. Nicht parallel starten.

## 2. Version 9.0.15 installieren

```bash
cd ~/Georg
unzip TradingBot_v9.0.15_NEXUS.zip
cd TradingBot_v9.0.15_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration übernimmt Einstellungen, Zugangsdaten, Trade-Historie und
Positionsnachweise. Die alte Installation wird nicht gelöscht.

## 3. Vor dem Start testen

```bash
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
```

Erwartet werden `SELF TEST OK` und `VOLLTEST OK` für `9.0.15-NEXUS`.

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
```

In der WebUI unter **Trades** prüfen:

- ONDO darf nach dem ersten vollständigen OKX-Abgleich nicht mehr als offene
  Botposition erscheinen, wenn der Verkauf vom 30.08.2026 um 17:15:01 in
  der Demo-Handelshistorie weiterhin abrufbar ist. Ein Rest von etwa
  0,002515 ONDO ist Dust und kein offener Bottrade.
- LINK muss entweder einen bestätigten Broker-Schutz besitzen oder einen
  klaren Exit-/Retry-Zustand zeigen. Es darf keine Verkaufsorder im
  Minutentakt gesendet werden.
- Bei manuell verwalteten Trades steht **MANUELL**; ein aktives
  Freqtrade-ROI-Ziel darf dort nicht mehr angezeigt werden.

## 6. TP/SL sicher ändern

1. Unter **Trades → Offene Trades** beim gewünschten OKX-Trade
   `TP/SL ändern · manuelle Verwaltung` wählen.
2. Neuen Stop-Loss und Take-Profit eingeben.
3. Die angezeigte Phrase exakt bestätigen, zum Beispiel
   `SET_PROTECTION LINK`.
4. Auf den Status `DONE` warten und anschließend die Werte auch im
   OKX-Order-Center kontrollieren.

Mit dieser Aktion wird nur diese Position auf **MANUELL** umgestellt.
Freqtrade-ROI und Strategie-Exit werden für sie deaktiviert. Der Bot darf
sie danach nur bei deinem bestätigten TP/SL, beim clientseitigen harten
Schutz oder durch einen ausdrücklichen manuellen Verkauf schließen.

## 7. Position über NEXUS verkaufen

1. Beim Trade `Jetzt preisbegrenzt verkaufen` wählen.
2. Sperrdauer wählen: 1 Stunde, 6 Stunden, bis Tageswechsel oder manuell.
3. Die Phrase `SELL SYMBOL` exakt bestätigen.
4. Auf `DONE` warten und den Fill in der OKX-Handelshistorie kontrollieren.

Der Verkauf ist eine preisbegrenzte FOK-Order. Kann die volle Menge nicht
innerhalb der erlaubten Preisgrenze verkauft werden, bleibt die Position
offen, der Schutz wird erneuert und ein begrenzter Retry-Zustand angezeigt.
Ein unklarer Auftrag wird nicht automatisch nochmals gesendet.

Die Sperre gilt für den Basiswert im selben OKX-Konto über alle
Abrechnungswährungen hinweg. Eine manuelle Sperre kann auf der Trades-Seite
bewusst wieder freigegeben werden.

## 8. Vor LIVE

OKX bleibt zunächst **DEMO**, eToro **PAPER**. Beobachte mindestens einen
vollständigen Zyklus mit Kauf, Fill-Zuordnung, Schutzänderung oder Exit,
OKX-Abgleich und Ledgerabschluss. Erst nach einem fehlerfreien Test darf LIVE
neu bewertet werden.

