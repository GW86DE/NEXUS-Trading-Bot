# Installationsanleitung NEXUS 8.1.3 – Raspberry Pi 5

Diese Version ist ein **Update auf ein laufendes 8.1.1 oder 8.1.2**. Teil A
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

## Teil A — Update auf 8.1.3

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
cd ~/Georg/TradingBot_v8.1.1_NEXUS      # bzw. _v8.1.2_
./Pi_Service_Stoppen.sh
```

Die WebUI darf weiterlaufen.

### A3. Neues Paket entpacken

```bash
cd ~/Georg
unzip TradingBot_v8.1.3_NEXUS.zip
cd TradingBot_v8.1.3_NEXUS
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
8.1.1 oder 8.1.2. Alternativ über die WebUI: *Dashboard → Datenübernahme aus
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

### A8. Abnahme — die fünf Punkte aus 8.1.3

#### 1. Cash-Reserve — der gemeldete Fehler

Das war dein Ausgangspunkt: *„Kauf von BTC blockiert. Cash Reserven reichen
nicht."*

Erst die Einstellung ansehen:

```bash
./.venv/bin/python -c "
import config
print('Reserve:', config.OKX_CASH_RESERVE_PCT*100, '%')
print('Taker-Gebuehr:', config.OKX_TAKER_FEE_PCT*100, '%')
print('Mindestorder:', config.OKX_MIN_POSITION_VALUE, 'EUR')"
```

Erwartung: `Reserve: 5.0 %`, `Taker-Gebuehr: 0.1 %`, `Mindestorder: 15.0 EUR`.

Dann im Log, sobald das Guthaben mal knapp wird:

```bash
sudo journalctl -u tradingbot-pi5.service -n 300 --no-pager | grep -i "Cash-Reserve\|CASH_REDUKTION"
```

Erwartung — **statt** einer pauschalen Blockade:

```text
Krypto BTC: Order von 50.00 EUR auf 32.27 EUR wegen Cash-Reserve reduziert
            (frei 34.00, Reserve 1.70, Gebuehr 0.10 %)
```

Reicht es wirklich nicht, steht dort jetzt die konkrete Zahl statt „reicht nicht":

```text
nach Reserve bleiben 11.40 EUR; Mindestordergroesse 15.00 EUR
```

Wichtig: Die Meldung erscheint **nur beim tatsächlichen Kauf**, nicht bei jedem
geprüften Coin. Das war Absicht — sonst hättest du bei knappem Guthaben pro Scan
eine Telegram-Nachricht je Kandidat bekommen.

#### 2. Universum — jetzt mit echten Zahlen

Deine Frage war: *„wie viel Aktien sind jetzt im Universum? 0 oder 80?"*

```bash
./.venv/bin/python -c "
import universe_overview as uo
print(uo.dashboard_text())
for d in uo.kennzahlen().values():
    print(f\"{d['anzeige']}: Kern {d['kern_limit']}, dynamisch {d['dynamisch_limit']}, aktiv max {d['aktiv_limit']}, Fokus {d['fokus_limit']}\")"
```

**Direkt nach dem Update** (noch kein Lauf gelaufen):

```text
Aktien noch kein Lauf (Katalog 250) · Krypto noch kein Lauf (Katalog 100)
eToro Aktien: Kern 75, dynamisch 25, aktiv max 100, Fokus 15
OKX Krypto: Kern 3, dynamisch 47, aktiv max 50, Fokus 12
```

Das ist richtig so: eine Katalogzahl darf nie wie ein aktives Universum aussehen.

**Nach dem ersten Lauf** — Krypto nach etwa 15 Minuten, Aktien nach etwa 45
Minuten:

```text
Aktien 75 aktiv (75 Kern + 0) · Krypto 41 aktiv (3 Kern + 38)
```

Dieselbe Zahl steht jetzt an drei Stellen: auf der GUI-Dashboard-Karte
*UNIVERSUM*, in der WebUI und bei `/universum` in Telegram. Wenn die drei
auseinanderlaufen, ist das ein Fehler — dann bitte melden.

#### 3. Der Aktien-Universumslauf läuft überhaupt

Das ist der Nebenbefund aus dem Änderungsbericht: bis 8.1.2 hat diesen Lauf
**niemand aufgerufen**. Deshalb blieb die Aktienseite dauerhaft leer.

```bash
sudo journalctl -u tradingbot-pi5.service -n 300 --no-pager | grep -i "Aktien-Universum\|aktienuniversum"
```

Erwartung: ein Eintrag `Aktien-Universum: …` mit den 75 Kernwerten. Kommen
zusätzlich Vorschläge, sieht das so aus:

```text
📋 AKTIEN-UNIVERSUM · VORSCHLÄGE
Diese Werte sind technisch gut bewertet. Aufnahme NUR mit deiner Freigabe --
der Bot nimmt hier nichts von allein auf.
```

Der Lauf braucht **keine** geöffnete Börse und **keine** zweite
eToro-Verbindung. Wenn du sonntags draufschaust, muss das Universum trotzdem
gefüllt sein.

#### 4. Kernwerte bleiben handelbar

Das war der schwerste Fehler, den die Nachprüfung gefunden hat: Ein Kernwert, der
dreimal nicht bewertet werden konnte, wäre **dauerhaft gesperrt** geblieben — bei
15-Minuten-Takt also nach 45 Minuten, ohne dass irgendetwas passiert ist.

```bash
./.venv/bin/python -c "
from universe.manager import UniverseManager
m = UniverseManager()
print('handelbare Coins :', m.handelbare_symbole('okx'))
print('handelbare Aktien:', len(m.handelbare_symbole('etoro')))"
```

Erwartung nach dem ersten Lauf: BTC, ETH und SOL sind dabei, bei den Aktien
stehen 75. Fehlt einer der drei Coins dauerhaft, obwohl OKX läuft — melden.

#### 5. Trade-Ledger (Strategy Evolution Engine, Etappe A)

```bash
./.venv/bin/python -c "
from decision_analytics import trade_snapshot
s = trade_snapshot()
print('geschlossene Trades:', s['gesamt']['trades'])
print('offene Positionen  :', s['offene_positionen'])
print('Strategieversion   :', s['aktuelle_strategieversion'])"
```

Direkt nach dem Update steht dort `0` — das Ledger fängt jetzt erst an
mitzuschreiben. Die Strategieversion ist ein 12-stelliger Hash und ändert sich,
sobald du einen Strategie- oder Risikoparameter veränderst. Telegram- und
Anzeigeoptionen ändern ihn **nicht**.

Der wöchentliche Strategiebericht (sonntags 18:00) hat einen neuen Block
*REALISIERTE HANDELSERGEBNISSE*. Solange zu wenige Trades vorliegen, steht dort
ausdrücklich, dass belastbare Kennzahlen einige Wochen Handel brauchen. Das ist
kein Fehler, sondern Absicht: eine Trefferquote aus drei Trades ist keine
Trefferquote.

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

Lass die Vorgängerversion mindestens eine Woche liegen. Erst wenn 8.1.3 mehrere
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
unzip TradingBot_v8.1.3_NEXUS.zip
cd TradingBot_v8.1.3_NEXUS
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

## Was in 8.1.3 anders ist

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

Dazu kommt aus 8.1.3 ein vierter, ehrlich benannter Punkt: Die Kernsperre — ein
Kernwert, der einen harten Sicherheitsfilter reißt, wird für neue Einstiege
gesperrt — wirkt **heute nur auf der Kryptoseite**. Der Aktienkern baut sein
Handelsuniversum weiterhin aus `config.STOCK_SYMBOLS` und fragt den
Universumszustand nicht. Beide enthalten inzwischen exakt dieselben 75 Werte,
eine Sperre erreicht den Aktienhandel aber nicht. Das zu ändern ist eine
Verhaltensänderung im Geldpfad und braucht ein eigenes Release.

Bitte bis dahin in DEMO/PAPER bleiben. Im Demo-Betrieb wirken sich diese Punkte
nicht aus.
