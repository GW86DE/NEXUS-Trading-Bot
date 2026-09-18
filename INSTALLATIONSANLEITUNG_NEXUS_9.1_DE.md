# Update auf NEXUS 9.1

NEXUS 9.1 ist die Korrekturversion zu 9.0.15. Sie behebt 21 belegte Befunde
aus der vollständigen Prüfung sowie zehn Punkte aus der Detailprüfung der
OKX-API-Kommunikation gegen die offizielle V5-Dokumentation.

> **Der Hotfix ist enthalten.** `manual_trade_control.py` und
> `webui/static/trades.js` aus `TradingBot_v9.0.15_manual_webui_hotfix.zip`
> sind eingearbeitet — die manuellen Knöpfe erscheinen jetzt auch bei neuen
> OKX-Trades mit `VERIFIED_BROKER_FILL_CHAIN`.

> **Der wichtigste Fix in einem Satz:** Stirbt der Prozess während eines
> Verkaufs, war die Position bis 9.0.15 **dauerhaft ungeschützt** — die
> OKX-Schutzorder war storniert, das Buch stand aber weiter auf „geschützt",
> und der einzige periodische Abgleich lief hinter genau dieser Bedingung.

## 1. Version 9.0.15 stoppen

```bash
cd ~/Georg/TradingBot_v9.0.15_NEXUS
./Pi_Service_Stoppen.sh
```

Nur eine Version darf laufen. Nicht parallel starten.

## 2. Version 9.1 installieren

```bash
cd ~/Georg
unzip TradingBot_v9.1_NEXUS.zip
cd TradingBot_v9.1_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration übernimmt Einstellungen, Zugangsdaten, Trade-Historie und
Positionsnachweise. Die alte Installation bleibt als Rückfallkopie erhalten.

## 3. Vor dem Start testen

```bash
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
```

Erwartet werden `SELF TEST OK` und `VOLLTEST OK` für **9.1.0-NEXUS**.
Bricht etwas ab: hier stoppen und mir die Meldung schicken. Der alte Ordner
läuft dann unverändert weiter.

## 4. WebUI und Dienste starten

```bash
./Pi_WebUI_Aktivieren.sh
sudo systemctl restart tradingbot-webui.service
./Pi_Service_Aktivieren.sh
./Pi_Service_Status.sh
```

Der Neustart der WebUI ist Pflicht — `Pi_WebUI_Aktivieren.sh` startet einen
bereits laufenden Dienst nicht neu.

---

## 5. Abnahme

### 5.1 Der Schutz überlebt jetzt einen Abbruch

```bash
sudo journalctl -u tradingbot-pi5.service --since "-1 h" --no-pager \
  | grep -i "unterbrochener Verkauf"
```

Läuft alles normal, steht dort nichts. Wurde ein Verkauf je abgebrochen,
erscheint jetzt:

```
Krypto LINK: unterbrochener Verkauf erkannt, Broker-Schutz wurde neu gesetzt.
```

Auf der Trades-Seite siehst du solche Zustände neu direkt an der Position:
„Schutz während eines Verkaufs storniert — wird neu gesetzt".

### 5.2 Freqtrade läuft jetzt am Kerzenraster

```bash
sudo journalctl -u tradingbot-pi5.service --since "-2 h" --no-pager \
  | grep -i "Krypto:" | tail -20
```

Die Scans sollten dicht nach den 5-Minuten-Grenzen liegen (…:00, …:05, …:10),
nicht mehr frei wandern. Vorher lag die Entscheidung im Mittel 2,5 Minuten
hinter dem Kerzenschluss, und bei längeren Scans wurden ganze Kerzen
übersprungen.

**Wichtig:** Das gilt **nur** im Freqtrade-Modus. NEXUS_STANDARD behält seinen
freien Takt — deine Vorgabe, dass die anderen Modi unverändert arbeiten, ist
eingehalten und durch einen Test abgesichert.

### 5.3 ROI greift auch ohne volle Kerzenhistorie

Bisher konnte eine Position mit Gewinn liegen bleiben, wenn OKX gerade zu
wenige Kerzen lieferte. ROI und Stoploss rechnen jetzt wie bei Freqtrade aus
reiner Trade-Mathematik — ohne Kerzen. Zu sehen im Ausstiegsgrund:

```
freqtrade_roi_2pct     (statt vorher freqtrade_exit_signal)
freqtrade_stop_loss
```

Die Ausstiegsgründe sind damit auch in der Auswertung wieder brauchbar.

### 5.4 `/pnl` zeigt beide Broker

Schick dem Bot `/pnl`. Neu:

```
Realisiert heute: -154,00 USD (2 Trades)
Tagesergebnis gesamt: -154,00 USD
  davon eToro Aktien: +98,00 USD (1 Trade)
  davon OKX Krypto: -252,00 USD (1 Trade)
```

Vorher stand dort „+98,00 gesamt" — die Kryptoseite fehlte vollständig, ohne
jeden Hinweis. `/bericht` nutzt jetzt dieselbe Quelle.

### 5.5 Manuell verwaltete Positionen sind wieder sichtbar

Schick `Status`. Eine Position, deren TP/SL du über die WebUI geändert hast,
erscheint jetzt wieder — inklusive ihrer Schutzwarnung:

```
• BTC · 0.1 @ 50000 · Stop 45000 · ⚠️ ohne Broker-Schutz · MANUELL verwaltet (kein Freqtrade-ROI)
```

Vorher war sie im Telegram-Status unsichtbar. Da jede TP/SL-Änderung genau
diesen Zustand erzeugt, war das der Normalfall nach jedem Eingriff.

### 5.6 Zahlen sind wieder deutsch

In der Kaufmeldung stand bisher „= 1,234.57 EUR" neben „50 000.00 EUR" —
englisches und deutsches Format nebeneinander. Jetzt durchgehend „1.234,57".

### 5.7 Das Entscheidungslogbuch ist wieder lesbar

Eine unveränderte globale Kaufsperre wird nur noch **einmal** protokolliert,
nicht 288-mal am Tag. Die echten Ablehnungsgründe stehen damit wieder in der
Top-5-Liste des Dashboards.

Und: Zwischen 00:00 und 02:00 Uhr zeigte das Dashboard bisher den **Vortag**
(also 0 Entscheidungen), weil geschrieben und gelesen mit verschiedenen
Zeitzonen wurde. Das ist behoben.

### 5.8 Neustarttest

```bash
sudo reboot
```

Nach etwa zwei Minuten:

```bash
sudo systemctl is-active wg-quick@wg0
sudo systemctl is-active tradingbot-webui.service
sudo systemctl is-active tradingbot-pi5.service
```

Alle drei müssen `active` melden.

### 5.9 Alten Ordner erst später löschen

Lass 9.0.15 mindestens eine Woche liegen.

---

## Was in 9.1 anders ist

| Bereich | Vorher | Jetzt |
|---|---|---|
| Schutz beim Verkauf | Prozessabbruch ließ die Position dauerhaft ungeschützt | Verlust wird vor dem Storno vermerkt, Abgleich erzwungen |
| Fremdbestand | zweiter Verkauf aus deinem Bestand möglich | Fill-Nachweis in beiden Abweichungsrichtungen |
| Unklare Verkaufsorder | 5-Minuten-Retry, erneut gesendet | 24-Stunden-Sperre, nie blind wiederholt |
| OKX-Code 50004 / 51016 | galt als endgültige Ablehnung, Order vergessen | unklar bzw. Nachweis — Order wird gesucht |
| Storno „Order existiert nicht" | blockierte den Ausstieg | gilt als Erfolg |
| Fill-Historie | verlor ab Seite 2 Daten | korrekte Paginierung über `billId` |
| Stop-Limitpreis bei Cent-Coins | konnte 0 werden, OCO komplett abgelehnt | nie unter einem Tick, sonst ehrliche Meldung |
| Kaufgebühr in Basiswährung | ungerechnet, viel zu niedrig | mit dem Fillpreis umgerechnet |
| Freqtrade-ROI | fiel ohne Kerzen aus | rechnet ohne Dataframe |
| Freqtrade-Takt | frei laufend, bis 5 min Versatz | rastet am Kerzenraster ein |
| Freqtrade-Backtest | maß 0,6 % zu gut, verschwieg Stop-Verluste | Freqtrade-Reihenfolge |
| `startup_candle_count` | 200 | 30, wie im Original |
| Gescheiterter manueller Verkauf | Position dauerhaft MANUELL | Verwaltung wird zurückgerollt |
| Teilverkauf | „nicht ausgeführt", keine Sperre | eigener Ausgang mit Sperre |
| Wiedereinstiegssperre bei Lesefehler | fiel weg (fail-open) | sperrt (fail-closed) |
| Dust-Grenze | Eröffnungsschwelle 15,00 | echte Verkaufbarkeit (minSz) |
| `/pnl`, `/bericht` | nur eToro, „gesamt" genannt | beide Broker, aufgeschlüsselt |
| Zustandsdateien | relativ zum Arbeitsverzeichnis | immer absolut zum Botordner |

Unverändert geblieben — auf deinen Wunsch: Verbindungseinstellungen,
Einstellungsübernahme, Universumsseite, Logbuch, das `auto-fit`-Kachelraster
und die Trennung der Modi.

---

## Zur OKX-Anbindung

Die Grundlagen wurden gegen die offizielle V5-Dokumentation geprüft und sind
korrekt: `eea.okx.com` als EU-Domain, Signatur über
`timestamp + METHOD + requestPath + body` mit HMAC-SHA256 und Base64,
ISO-8601-Zeitstempel mit Millisekunden, `x-simulated-trading` nur bei privaten
Demo-Aufrufen, `expTime` als HTTP-Header, Zeitversatz über Code 50102,
Prüfung von `sCode` **zusätzlich** zum äußeren `code`, Mengen immer abgerundet,
WebSocket-Login nach der abweichenden WS-Regel.

Die Fehler lagen ausschließlich darin, **wie Antworten eingeordnet wurden** —
siehe Abschnitt A im Changelog.

Zwei Punkte konnte ich ohne Live-Konto nicht abschließend klären und habe sie
konservativ gelöst:

- `tradeQuoteCcy` wird nicht mehr an `/trade/order-algo` gesendet (dort nicht
  dokumentiert). Sollte OKX ihn dort doch erwarten, melde dich — der Verkauf
  erfolgt ohnehin in der Quote des `instId`.
- Gebühren in einer dritten erlaubten Währung fließen nicht mehr ungerechnet
  in die Quotesumme. Sie bleiben einzeln sichtbar.

---

## Fehlerdiagnose

```bash
sudo systemctl status tradingbot-pi5.service --no-pager
sudo journalctl -u tradingbot-pi5.service -n 100 --no-pager
sudo systemctl status tradingbot-webui.service --no-pager
```

Hängt ein manueller Auftrag:

```bash
cat manual_trade_commands.json
```

Steht dort `UNCLEAR`, wurde bewusst nicht wiederholt — bitte zuerst das
OKX-Konto abgleichen.
