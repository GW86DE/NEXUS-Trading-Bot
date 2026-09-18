# Installationsanleitung NEXUS 8.1.4 – Raspberry Pi 5

Diese Version ist ein **Update auf ein laufendes 8.1.1, 8.1.2 oder 8.1.3**. Teil A
beschreibt diesen Weg. Teil B ist die vollständige Neuinstallation und nur nötig,
wenn keine funktionierende Vorgängerversion vorhanden ist.

> **Wichtig vorab:** An den Verbindungseinstellungen wurde nichts geändert.
> Broker-Zugänge, WireGuard, der WebUI-Login und die Einstellungsübernahme
> funktionieren unverändert. Du musst nichts davon neu einrichten.
>
> **Neu bei der Übernahme:** `crypto_positions.json` wandert jetzt mit. Ohne das
> Positionsbuch wüsste die Kryptoseite nach dem Wechsel nicht mehr, wo ihre Stops
> liegen — OKX Spot kennt keinen Einstandspreis.

---

## Teil A — Update auf 8.1.4

Dauer: etwa 15 Minuten, davon 10 Minuten Warten.

### A1. Vorbereitung

```bash
cd ~/Georg
ls -d TradingBot_v8.1.*
```

Merk dir, welche Version dort läuft — du brauchst den Namen gleich zweimal. Den
alten Ordner **nicht** löschen und **nicht** überschreiben: er ist deine
Rückfallmöglichkeit.

### A2. Trading-Core stoppen

Die Datenübernahme braucht einen gestoppten Core.

```bash
cd ~/Georg/TradingBot_v8.1.3_NEXUS      # oder die Version, die laeuft
./Pi_Service_Stoppen.sh
```

Die WebUI darf weiterlaufen.

### A3. Neues Paket entpacken

```bash
cd ~/Georg
unzip TradingBot_v8.1.4_NEXUS.zip
cd TradingBot_v8.1.4_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
```

### A4. Installer ausführen

```bash
./Pi_Installieren.sh
```

Niemals mit `sudo` starten. Der Installer legt `.venv` neu an, installiert die
gesperrten Pakete, führt den vollständigen Offline-Volltest aus und schreibt die
systemd-Units auf den **neuen** Ordner um. Der Trading-Core bleibt gestoppt und
deaktiviert.

Erwartetes Ergebnis am Ende:

```text
VOLLTEST OK
INSTALLATION UND TESTS ERFOLGREICH - BOT NOCH NICHT GESTARTET
```

Bricht der Volltest ab: hier stoppen und die Meldung schicken. Der alte Ordner
läuft in diesem Fall unverändert weiter.

### A5. Einstellungen aus der Vorgängerversion übernehmen

```bash
./.venv/bin/python settings_migration.py --auto
```

`--auto` sucht selbst den vollständigsten TradingBot-Ordner daneben — egal ob
8.1.1 oder 8.1.3. Alternativ über die WebUI: *Dashboard → Datenübernahme aus
alter Version*, Quelle auswählen, mit `UEBERNEHMEN` bestätigen.

Übernommen werden eToro, OKX, Telegram, News-Schlüssel, OpenAI, Risikoprofil,
Favoriten, Positions- und Risikozustände, **das Krypto-Positionsbuch**, Universum
und Entscheidungshistorie. Der alte Ordner bleibt unverändert. eToro wird auf
PAPER und OKX auf DEMO gesetzt, alle LIVE-Arming-Dateien werden entfernt — das
ist Absicht.

Kontrolle, dass das Positionsbuch angekommen ist:

```bash
ls -l crypto_positions.json
```

Fehlt die Datei und du hattest vorher offene Kryptopositionen, dann schick mir
das Ergebnis von `settings_migration.py --auto` — bitte nicht von Hand kopieren.

### A6. WebUI auf den neuen Ordner umstellen

```bash
./Pi_WebUI_Aktivieren.sh
sudo systemctl restart tradingbot-webui.service
```

Der **Neustart ist Pflicht**: `Pi_WebUI_Aktivieren.sh` startet einen bereits
laufenden Dienst nicht neu.

Prüfen:

```bash
sudo systemctl status tradingbot-webui.service --no-pager
```

### A7. Trading-Core starten

Vorher in der WebUI kontrollieren: eToro **PAPER**, OKX **DEMO**, LIVE-Arming
**AUS**, Risikoprofil wie gewünscht.

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Status.sh
```

### A8. Abnahme — die sieben Punkte aus 8.1.4

#### 1. Telegram meldet wieder — der wichtigste Test

Bis 8.1.3 hatte die Kryptoseite **gar keinen Meldekanal**: `nexus_start`
importierte eine Klasse `Notifier`, die es nie gab. Der Fehler wurde
verschluckt, und kein Kauf, kein Verkauf, keine Sperre erreichte dich.

```bash
./.venv/bin/python -c "
import nexus_start
m = nexus_start._melder('KRYPTO')
print('Melder:', m, '| aufrufbar:', callable(m))"
```

Erwartung: eine Funktion, **niemals `None`**. Beim nächsten Krypto-Kauf muss
eine Telegram-Nachricht kommen — mit Menge, Preis, Gebühr, Stop, Ziel, Grund
und, falls die Order nur teilweise ausgeführt wurde, der Restmenge.

#### 2. `/status` kennt jetzt OKX

Schick dem Bot `Status`. Unter dem Aktienteil steht jetzt ein eigener Block:

```
KRYPTO · OKX · DEMO · 🟢 ONLINE
Neue Käufe: Anlaufsperre: neue Kaeufe frei in 6 min (13:26)
Handelbares Kapital: 9 600.00
Taker-Gebühr: 0.35 %
Guthaben: 60 798.06 EUR · 14.190800 SOL · 0.184500 BTC
Offene Positionen: 1
• BTC · 0.126632 @ 67975.1 · Stop 67386.2
```

Guthaben, Positionen, Modus und Handelsbereitschaft — all das fehlte vorher
vollständig.

#### 3. Die Anlaufsperre greift

Nach dem Start dürfen **15 Minuten lang keine neuen Käufe** stattfinden.
Verkäufe, Stops und Schutzorders laufen sofort.

```bash
sudo journalctl -u tradingbot-pi5.service --since "-20 min" --no-pager | grep -i "Anlaufsperre\|handelsbereit"
```

Erwartung: kurz nach dem Start `Anlaufsperre: neue Kaeufe frei in 14 min …`,
später `OKX: handelsbereit -- neue Kaeufe sind ab jetzt moeglich.`

Am 25.08. kaufte der Bot 2,5 Sekunden nach dem Start — bevor der Kontostand
je gegen das Positionsbuch abgeglichen war. Genau das ist jetzt ausgeschlossen:
„Positions-Reconciliation abgeschlossen" ist eine Pflichtbedingung.

#### 4. Fremdbestände werden nicht mehr mitverkauft

Der schwerste Fehler: Der Bot übernahm den **gesamten Kontostand** einer
Währung als seine Position und verkaufte am 25.08. 0,94141 BTC, obwohl seine
Position 0,06305312 BTC war.

```bash
sudo journalctl -u tradingbot-pi5.service --since "-2 h" --no-pager | grep -i "mehr im Konto\|gehoert nicht zum Bot"
```

Liegt in deinem OKX-Konto noch der SOL-Rest von 14,19, muss dort stehen:
*„14.1906 mehr im Konto als im Positionsbuch. Dieser Bestand gehoert nicht zum
Bot und wird nicht verkauft."*

#### 5. Gebühren

```bash
./.venv/bin/python -c "
import config; print('Vorgabe:', config.OKX_TAKER_FEE_PCT * 100, '%')"
```

Erwartung: **0.35 %** — dein echter Tarif („Normaler Nutzer, 0–100.000 EUR").
Der Bot fragt den Satz zusätzlich bei OKX ab und nutzt den gemessenen Wert;
im `/status` steht, womit er gerade rechnet.

Folge: Die Kostenhürde steigt von 0,90 % auf rund 1,65 %. **Es wird deutlich
seltener gekauft** — das ist beabsichtigt. Mit dem echten Satz hätte der Kauf
vom 25.08. gar nicht stattgefunden.

#### 6. GPT Second Opinion

*Einstellungen → GPT Second Opinion vor Kauforders*

Standard ist **aus** — dann läuft exakt der bisherige Ablauf. Zum Ausprobieren
auf **immer (auch im Demobetrieb)** stellen. Daneben: *KI-Zugang testen* prüft
den OpenAI-Schlüssel mit einer kleinen Anfrage und zeigt Modell, Antwortzeit
und Kosten.

Bei `kritisch` wartet die Order auf deine Freigabe:

```
/wartend           zeigt wartende Kauforders
/kaufen BTC        hebt die KI-Warnung auf (kauft NICHT sofort)
/verwerfen BTC     lehnt ab
```

Die Freigabe hebt eine Warnung auf — sie kauft nichts. Der nächste Scan
entscheidet neu und prüft vorher, ob Kurs, Guthaben, Risikotopf und Stop noch
stimmen. Nach 15 Minuten verfällt sie.

#### 7. WebUI

- Die Kopfzeile zeigt jetzt **8.1.4** (vorher stand dort an neun Stellen fest „8.1.2")
- Das Dashboard-Raster passt sich an; drei neue Karten: OKX Guthaben, OKX Positionen, Handelsbereitschaft
- Die Entscheidungsliste zeigt **Ortszeit statt UTC** und 15 statt 6 Zeilen
- Universumsseite und Logbuch sind unverändert

#### 8. Universumsdiagnose

```bash
sudo journalctl -u tradingbot-pi5.service --since "-1 h" --no-pager | grep -i "Universumsdiagnose"
```

Erwartung, sobald ein Universumslauf gelaufen ist:

```
581 Instrumente → 2 im Pool → 2 bewertet
  andere Quotewaehrung              412
  Tagesumsatz zu klein              118
  Spanne zu weit                     31
```

**Bitte schick mir diese Zeilen.** Damit klären wir, ob eine Schwelle falsch
steht oder ob das dünne Demo-Orderbuch die Ursache ist. Ich habe bewusst an
keiner Schwelle gedreht.

### A9. Neustarttest

```bash
sudo reboot
```

Nach etwa zwei Minuten:

```bash
sudo systemctl is-active wg-quick@wg0
sudo systemctl is-active tradingbot-webui.service
sudo systemctl is-active tradingbot-pi5.service
```

Alle drei müssen `active` melden. Danach die WebUI einmal im WLAN und einmal über
Mobilfunk mit aktivem WireGuard öffnen.

### A10. Alten Ordner erst später löschen

Lass die Vorgängerversion mindestens eine Woche liegen. Erst wenn 8.1.4 mehrere
Tage stabil läuft, kannst du sie entfernen.

---

## Teil B — Vollständige Neuinstallation

Nur nötig, wenn keine lauffähige Vorgängerversion existiert.

### B1. Vorbedingungen

- Raspberry Pi OS 64 Bit
- Python 3.11 oder neuer
- normaler Benutzer mit `sudo`-Recht
- vorhandenes WireGuard darf weiterlaufen

Das Installationsskript niemals mit `sudo` starten. Es nutzt `sudo` nur gezielt
für Betriebssystempakete und systemd.

### B2. Entpacken und installieren

```bash
cd ~/Georg
unzip TradingBot_v8.1.4_NEXUS.zip
cd TradingBot_v8.1.4_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
```

Der Installer prüft ARM64 und Python, prüft die Release-Vollständigkeit, erzeugt
`.venv`, installiert exakt gesperrte Pakete, führt Preflight und vollständige
Offline-Tests aus, erkennt die sichere WebUI-Adresse, installiert getrennte
systemd-Units und lässt den Trading-Core gestoppt und deaktiviert.

Eine vorhandene `/etc/wireguard/wg0.conf` wird weder kopiert noch verändert.
Private Schlüssel gehören nicht in den Projektordner.

### B3. WebUI einrichten

```bash
./.venv/bin/python webui_setup.py
./Pi_WebUI_Aktivieren.sh
```

Der Aktivierungsbefehl zeigt die verwendete Adresse, üblicherweise die private
Pi-LAN-IP.

Falls die Erkennung geändert werden soll:

```bash
NEXUS_WEBUI_ACCESS_MODE=lan   ./Pi_WebUI_Aktivieren.sh
NEXUS_WEBUI_ACCESS_MODE=vpn   ./Pi_WebUI_Aktivieren.sh
NEXUS_WEBUI_ACCESS_MODE=local ./Pi_WebUI_Aktivieren.sh
```

### B4. Zugänge eintragen

In der WebUI unter *Einstellungen*:

1. eToro-Demo-Zugang eintragen, speichern, **Demo-Verbindung testen**
2. OKX-Demo-Zugang eintragen, OKX aktivieren, speichern, **Demo-Verbindung testen**
3. Telegram speichern und `/version`, `/menu`, `/health` testen
4. News-Schlüssel eintragen (Finnhub, FMP, MASSIVE, Alpha Vantage) und die
   gewünschten Quellen einschalten
5. Risikoprofil kontrollieren

Alternativ auf der Kommandozeile:

```bash
./Nexus_Einrichten.sh
./Nexus_Einrichten.sh --test
```

Alle Tests sind read-only und erzeugen keine Orders.

**FMP** meldet bewusst *„Nachrichten nicht im Gratistarif enthalten (HTTP 402)"*.
Das ist richtig so und kein Defekt: FMP liefert Symbolsuche, Kurse, Sektor und
Delisting-Status für das Aktien-Universum, begrenzt auf 250 Anfragen pro Tag.
Genau diese Daten braucht der neue Aktien-Universumslauf.

### B5. Core starten

Weiter mit den Schritten **A7** bis **A9** oben.

---

## Was in 8.1.4 anders ist

| Bereich | Vorher | Jetzt |
|---|---|---|
| Zu wenig Cash | ganze Order fiel weg | Order wird verkleinert, Reserve bleibt |
| Ablehnungsgrund | „Cash Reserven reichen nicht" | konkrete Beträge und Grenzen |
| `OKX_CASH_RESERVE_PCT` | fester Wert im Code | einstellbar |
| Aktien-Universumslauf | fand nie statt | eigener Faden, auch bei geschlossener Börse |
| Aktienkern | kam nie im Universum an | 75 feste Werte, immer beobachtet |
| Dashboard-Karte | statischer Katalog, immer „0 Krypto" | echtes Universum, Kern und Dynamik getrennt |
| Instrumentenfilter | lief nie | keine CFDs, Hebelprodukte, SPACs, Warrants |
| Favoriten | belegten Universumsplätze | reine Prioritätsmarker |
| Bewährung Krypto | „etablierte" Coins durften sie überspringen | nur noch BTC, ETH, SOL |
| Handelsergebnisse | nur Einstiegsseite gespeichert | vollständiges Trade-Ledger |
| Strategieversion | gab es nicht | Hash je Entscheidung und Trade |
| Wochenbericht | nur Forward-Performance | zusätzlich realisierte Ergebnisse |
| Krypto-Positionsbuch | wanderte bei Übernahme nicht mit | wandert mit |

---

## Fehlerdiagnose

WebUI:

```bash
sudo systemctl status tradingbot-webui.service --no-pager
sudo journalctl -u tradingbot-webui.service -n 80 --no-pager
sudo ss -ltnp | grep 8780
```

Core:

```bash
sudo systemctl status tradingbot-pi5.service --no-pager
sudo journalctl -u tradingbot-pi5.service -n 100 --no-pager
```

Nachrichtenquellen einzeln prüfen:

```bash
./.venv/bin/python news_check.py
```

WireGuard:

```bash
sudo wg show
ip -br addr show wg0
./WireGuard_Status_Pruefen.sh
```

Alle Tests erneut:

```bash
./.venv/bin/python volltest.py
```

Erwartung: sechs Abschnitte, alle `OK`, am Ende `VOLLTEST OK`.

### Wenn das Universum leer bleibt

```bash
sudo journalctl -u tradingbot-pi5.service -n 300 --no-pager | grep -i "Universum"
./.venv/bin/python -c "
import universe_overview as uo; print(uo.dashboard_text())"
```

Steht dort dauerhaft „noch kein Lauf", obwohl der Core seit über einer Stunde
läuft, dann bitte die letzten 300 Logzeilen schicken. Steht dort „unbekannt", ist
`universe_state.json` nicht lesbar — Datei umbenennen und den Core neu starten;
der feste Kern wird beim nächsten Lauf neu gesetzt.

---

## WireGuard-Regeln (unverändert)

- FRITZ!Box: UDP `51820` zum Pi
- keine Freigabe für TCP `8780`
- WebUI-Bind: private LAN-IP oder WireGuard-IP, niemals `0.0.0.0`
- Für dieselbe LAN-URL unterwegs im Client zum Beispiel:

```ini
AllowedIPs = 10.77.0.1/32, 192.168.178.60/32
```

Details in `WIREGUARD_VPN_EINRICHTUNG_DE.md`.

---

## LIVE-Betrieb

**Noch nicht freigegeben.** Drei Punkte aus dem Änderungsbericht sind bewusst
offen und betreffen ausschließlich den LIVE-Geldpfad:

- **P0-02** Order-Lifecycle als vollständige persistente State Machine
- **P0-03** eToro- und OKX-Arming vereinheitlichen und vor jedem LIVE-Einstieg
  dynamisch prüfen
- **P0-04** Watchdog je aktivierter Brokerdomäne

Dazu kommt aus 8.1.4 ein vierter, ehrlich benannter Punkt: Die Kernsperre — ein
Kernwert, der einen harten Sicherheitsfilter reißt, wird für neue Einstiege
gesperrt — wirkt **heute nur auf der Kryptoseite**. Der Aktienkern baut sein
Handelsuniversum weiterhin aus `config.STOCK_SYMBOLS` und fragt den
Universumszustand nicht. Beide enthalten inzwischen exakt dieselben 75 Werte,
eine Sperre erreicht den Aktienhandel aber nicht. Das zu ändern ist eine
Verhaltensänderung im Geldpfad und braucht ein eigenes Release.

Bitte bis dahin in DEMO/PAPER bleiben. Im Demo-Betrieb wirken sich diese Punkte
nicht aus.
