# Update auf NEXUS 9.4

NEXUS 9.4 ersetzt die eToro-Zuordnung aus 9.3/9.3.1. Eine Position wird nur
automatisch verwaltet, wenn ihre konkrete `positionId` durch die persistierte
Kaufkette bewiesen und im richtigen Konto sowie in der richtigen Umgebung
bestätigt wurde.

> Vor der Installation vorhandene eToro-Schutzorders nicht löschen. Die erste
> Abnahme muss im DEMO-Modus erfolgen.

## 1. Alte Version stoppen

Beispiel für 9.3.1:

```bash
cd ~/Georg/TradingBot_v9.3.1_NEXUS
./Pi_Service_Stoppen.sh
```

Prüfen, dass nur eine Version läuft:

```bash
sudo systemctl status tradingbot-pi5.service --no-pager
```

## 2. Version 9.4 entpacken und installieren

```bash
cd ~/Georg
unzip TradingBot_v9.4_NEXUS.zip
cd TradingBot_v9.4_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die alte Installation bleibt als Rückfallkopie erhalten. Die Migration
übernimmt Einstellungen, Zugangsdaten, Historie und Positionsdateien, stuft
alte automatisch verwaltete eToro-Einträge aber nicht allein aufgrund einer
beobachteten Depot-ID als bewiesen ein.

## 3. Vor dem Start testen

```bash
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
```

Erwartet werden:

```text
SELF TEST OK
VOLLTEST OK
```

Bricht ein Test ab, den Dienst nicht starten und die vollständige Meldung
sichern.

## 4. Dienste starten

```bash
./Pi_WebUI_Aktivieren.sh
sudo systemctl restart tradingbot-webui.service
./Pi_Service_Aktivieren.sh
./Pi_Service_Status.sh
```

Danach die WebUI im Browser mit `Strg` + `F5` hart neu laden. Links neben der
Versionsnummer muss das blau-türkise NEXUS-Logo erscheinen.

## 5. Verbindliche DEMO-Abnahme

### 5.1 Portfolio-ID-Diagnose

Im Systemprotokoll nach folgender neuen Diagnose suchen:

```bash
sudo journalctl -u tradingbot-pi5.service --since "-20 min" --no-pager \
  | grep -E "eToro-Portfolio-ID-Diagnose|positionId|Zuordnung"
```

Die Diagnose muss mindestens ein erkanntes positionId-Feld und eine
Snapshot-ID nennen. Die Meldung darf keine Zugangsdaten enthalten.

Erscheint bei einer offenen Position `keine normalisierbare positionId`,
handelt NEXUS absichtlich fail-closed. Dann weder übernehmen noch erneut
kaufen; Log und anonymisierte Depotstruktur zur Analyse sichern.

### 5.2 Vorhandene MSFT-/SPGI-Positionen

Auf der Seite **Positionen** kontrollieren:

- Jede eToro-`positionId` steht in einer eigenen Zeile.
- Zwei Positionen desselben Symbols bleiben getrennt.
- `BOT VERWALTET` erscheint nur bei einer bewiesenen Kaufkette.
- Ein reiner Symboltreffer steht höchstens auf `WIRD BESTÄTIGT`.
- Eine bewusst auf `NUR BEOBACHTET` gesetzte Position bleibt unverändert.

### 5.3 „Bot übernehmen“

Die Übernahme zuerst an einer DEMO-Position testen:

1. Gewünschten Stop und gewünschtes Ziel eingeben.
2. Übernahme bestätigen.
3. Warten, bis eToro beide Werte an genau derselben `positionId` zurückmeldet.
4. Erst danach darf die WebUI `USER_MANAGED / BOT VERWALTET` anzeigen.

Bei Ablehnung oder Timeout müssen Quelle, Verwaltung, Stop und Ziel auf den
vorherigen Stand zurückrollen. Eine HTTP-202-Annahme allein ist noch keine
erfolgreiche Übernahme.

### 5.4 Mehrere gleichzeitige Kaufentscheidungen

Bei mehreren Signalen darf im selben Aktien-Scannerzyklus nur ein eToro-Kauf
abgesendet werden. Weitere Signale erscheinen mit dem Grund
`etoro_buy_coordination` und werden erst nach einem frischen Cash-/Risiko-
Abgleich erneut bewertet. Es darf weder ein doppelter POST noch eine zweite
Kaufabsicht für dieselbe freie Kontodomain entstehen.

### 5.5 Verkauf

Ein automatischer Verkauf darf nur eine durch `owned_position_ids` bewiesene
Position schließen. Bei fehlender, falscher oder konto-fremder positionId muss
der Verkauf abgelehnt werden. Ein Symboltreffer allein darf niemals genügen.

## 6. Erst danach LIVE aktivieren

LIVE erst aktivieren, wenn mindestens ein vollständiger DEMO-Ablauf

```text
Kaufentscheidung -> Order -> positionId -> Depotbestätigung -> BOT/AUTO
```

und eine DEMO-Übernahme mit bestätigtem Stop und Ziel erfolgreich waren.

## 7. Rückfall auf 9.3.1

```bash
cd ~/Georg/TradingBot_v9.4_NEXUS && ./Pi_Service_Stoppen.sh
cd ~/Georg/TradingBot_v9.3.1_NEXUS && ./Pi_Service_Aktivieren.sh
```

Wichtig: 9.3.1 kennt die in 9.4 getrennten Felder für beobachtete und
bewiesene positionIds nicht. Deshalb nach einem Rückfall keine automatische
eToro-Verwaltung freigeben, bevor die Zuordnung erneut geprüft wurde.

