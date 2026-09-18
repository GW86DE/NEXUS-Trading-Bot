# Installationsanleitung NEXUS 8.1.5 – Raspberry Pi 5

Diese Version ist ein **Update auf ein laufendes 8.1.2, 8.1.3 oder 8.1.4**. Teil A
beschreibt diesen Weg. Teil B ist die vollständige Neuinstallation und nur nötig,
wenn keine funktionierende Vorgängerversion vorhanden ist.

> **Wichtig vorab:** An den Verbindungseinstellungen wurde nichts geändert.
> Broker-Zugänge, WireGuard, der WebUI-Login und die Einstellungsübernahme
> funktionieren unverändert. Du musst nichts davon neu einrichten.
>
> **Das Wichtigste an 8.1.5:** Der Aktienkern und der eToro-Risikotopf lasen bis
> 8.1.4 **zwei verschiedene Dateien** — `risk_state.json` und
> `risk_state_etoro.json`. Die zweite wurde nie beschrieben. Beim ersten Start
> von 8.1.5 werden sie zusammengeführt. **Es wird nichts weggeworfen**, und du
> bekommst eine Telegram-Meldung darüber. Schritt A8.3 zeigt, wie du das prüfst.

---

## Teil A — Update auf 8.1.5

Dauer: etwa 15 Minuten, davon 10 Minuten Warten.

### A1. Vorbereitung

```bash
cd ~/Georg
ls -d TradingBot_v8.1.*
```

Merk dir, welche Version dort läuft. Den alten Ordner **nicht** löschen und
**nicht** überschreiben: er ist deine Rückfallmöglichkeit.

### A2. Trading-Core stoppen

```bash
cd ~/Georg/TradingBot_v8.1.4_NEXUS      # oder die Version, die laeuft
./Pi_Service_Stoppen.sh
```

Die WebUI darf weiterlaufen.

### A3. Neues Paket entpacken

```bash
cd ~/Georg
unzip TradingBot_v8.1.5_NEXUS.zip
cd TradingBot_v8.1.5_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
```

### A4. Installer ausführen

```bash
./Pi_Installieren.sh
```

Niemals mit `sudo` starten. Erwartetes Ergebnis:

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

Alternativ über die WebUI: *Dashboard → Datenübernahme aus alter Version*.

Übernommen werden eToro, OKX, Telegram, News-Schlüssel, OpenAI, Risikoprofil,
Favoriten, Positions- und Risikozustände, das Krypto-Positionsbuch, Universum
und Entscheidungshistorie. eToro wird auf PAPER und OKX auf DEMO gesetzt, alle
LIVE-Arming-Dateien werden entfernt — das ist Absicht.

### A6. WebUI auf den neuen Ordner umstellen

```bash
./Pi_WebUI_Aktivieren.sh
sudo systemctl restart tradingbot-webui.service
```

Der **Neustart ist Pflicht**: `Pi_WebUI_Aktivieren.sh` startet einen bereits
laufenden Dienst nicht neu.

### A7. Trading-Core starten

Vorher in der WebUI kontrollieren: eToro **PAPER**, OKX **DEMO**, LIVE-Arming
**AUS**, Risikoprofil wie gewünscht.

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Status.sh
```

---

## A8. Abnahme — die sieben Punkte aus deiner Meldung vom 25.08.

### 1. „Positionslimit erreicht (offen=0)" ist weg

Das war mit **243 Meldungen der häufigste Ablehnungsgrund des Tages** — und er
war falsch. Bei null offenen Positionen kann kein Positionslimit greifen. In
Wahrheit lief die Equity-Tagesbremse; sie hatte in der Begründungskette keinen
eigenen Zweig und fiel in den Sammelfall.

```bash
sudo journalctl -u tradingbot-pi5.service --since "-2 h" --no-pager | grep -i "blocked=risk_manager"
```

Erwartung: Wenn eine Sperre greift, steht dort jetzt, **welche**:

```text
Equity-Tagesbremse aktiv: -3,40 % seit Tagesbeginn (Grenze -3,00 %).
Verkaeufe und Stops laufen weiter.
```

Und wenn wirklich das Positionslimit greift, steht die Grenze dabei:
`Positionslimit erreicht (offen=8, Limit=8)`.

**Ein „Positionslimit erreicht (offen=0)" kann es nicht mehr geben** — ein Test
prüft genau das.

### 2. „Markt geschlossen" bei offener Börse

Die Sperre war richtig, die Begründung falsch. GOOGL hatte einen **18 741
Sekunden — 5 Stunden 12 Minuten — alten Kurs**. Diese Zahl stand im
Rohdatensatz, kam aber nie bis zur Meldung.

```bash
sudo journalctl -u tradingbot-pi5.service --since "-4 h" --no-pager | grep -i "blocked=market_session"
```

Erwartung bei einem veralteten Kurs:

```text
GOOGL: Kein Einstieg, weil der Kurs 5 h 12 min alt ist (erlaubt: 3 min).
Die Boerse ist laut Kalender offen -- das ist ein Datenproblem, keine
Handelszeit. Ohne aktuellen Kurs waeren Stop und Positionsgroesse geraten.
```

Bei echtem Feierabend steht jetzt dabei, wann wieder aufgemacht wird.

**Neu:** Sobald der Kalender offen sagt und der Bot trotzdem nicht kauft,
bekommst du **einmal je Wert und Tag** eine Telegram-Nachricht
(*„MARKT OFFEN, ABER KEIN HANDEL"*) — nicht 243.

**Einstellbar:** *Einstellungen → Datenfrische*. Dort steht das erlaubte
Kursalter für Aktien und Krypto getrennt. Wirkt ohne Neustart. Höher setzen
heißt: auf älteren Kursen handeln.

### 3. Die Verlustbeträge — und der schwerere Befund dahinter

Telegram meldete *„Heute realisiert: -226,60 USD"*. Tatsächlich war an dem Tag
genau ein Trade geschlossen: **DVLT mit -44,40 USD**. FLR.US stand mit -121,26
noch offen, dazu 3,00 Gebühren. Keine Kombination ergibt -226,60.

Der Wert kam aus einem fortgeschriebenen Summenzähler ohne Belege. Ab jetzt
kommt er aus den **einzelnen Buchungen des Handelsbuchs**:

```
Realisiert heute: -44,40 USD (1 Trade)
  davon Gebuehren: -3,00 USD (bereits abgezogen)
Offen unrealisiert: -121,26 USD (1 Position)
Tagesergebnis gesamt: -165,66 USD
```

Weicht der alte Zähler ab, wird die **Abweichung gemeldet** statt eine der
beiden Zahlen auszuwählen. Ein Verkauf ohne bekannten Einstand geht nie als
0,00 in die Summe.

Prüfen mit `/pnl` in Telegram.

#### 3b. Die zwei Risikostände — bitte diesen Punkt wirklich prüfen

`live_trader` schrieb `risk_state.json`, der eToro-Risikotopf las
`risk_state_etoro.json`. Deshalb war deine hochgeladene Datei komplett auf
null. Das war **kein Anzeigefehler**:

- Die Tagesverlustbremse des eToro-Topfes verglich immer 0,00 gegen die Grenze
  und **konnte nie auslösen**.
- Die brokerübergreifende Klammer summiert beide Töpfe — die Aktienseite
  steuerte dauerhaft 0,00 bei.

Beim ersten Start führt 8.1.5 die Dateien zusammen:

```bash
ls -l risk_state*.json
sudo journalctl -u tradingbot-pi5.service --since "-30 min" --no-pager | grep -i "Risikozustand"
```

Erwartung: **nur noch `risk_state_etoro.json`** (plus `risk_state_okx.json`),
dazu eine `.abgeloest-<Zeitstempel>`-Datei als Sicherung und die Zeile

```text
Risikozustand zusammengefuehrt: die gefuehrten Werte aus risk_state.json
gelten jetzt als risk_state_etoro.json; die leere Vorgaengerdatei wurde als
.abgeloest aufbewahrt.
```

Du bekommst dieselbe Meldung auch per Telegram. Steht dort stattdessen
*„Bitte den heutigen Tagesverlust im Broker gegenpruefen"*, waren **beide**
Dateien gefüllt — dann bitte einmal bei eToro nachsehen und mir Bescheid geben.

### 4. Universumsaufnahme braucht keine Bestätigung mehr

4 Stunden Bewährung, wie besprochen — nicht 24. Die Grenzen bleiben und stehen
jetzt in der Meldung:

```text
🔍 AKTIEN-UNIVERSUM · NEU IN BEWÄHRUNG
ZZTOP
Bewährung: 4 h mit stabiler Bewertung. Beobachtet heißt noch nicht handelbar.
Grenzen: 75 feste Kernwerte + max. 25 dynamische, max. 5 Wechsel je Lauf.
Schlechte Wahl fliegt über die Rangfolge wieder raus.
```

Nach bestandener Bewährung kommt *„✅ BEWÄHRUNG BESTANDEN"*.

Wichtig und unverändert: **„im Universum" heißt nur „wird beobachtet"**. Ob
gekauft wird, entscheidet danach allein die feste Kaufkaskade. GPT kann die
Aufnahme weder freigeben noch blockieren — das ist getestet, auch für den Fall,
dass GPT ausdrücklich „blockieren" zurückgibt.

Ansehen unter *Universum* — die Seite selbst ist unverändert geblieben.

### 5. „NONE" und „UPDATED_AT" im Universum

Ursache war eine Oder-Kette: Bei `{"updated_at": …, "positions": {}}` ist das
leere Dict falsy, die Kette fiel auf die **ganze Datei** zurück, und jeder
Feldname wurde zu einem Symbol.

Ein Symbol muss jetzt wie ein Ticker aussehen. AAPL, FLR.US und BRK.B gehen
durch; „None", „updated_at" und Zeitstempel nicht. In der Universumstabelle
darf so etwas nicht mehr auftauchen.

### 6. Dashboard: eToro-Positionen statt OKX doppelt

Vorher zeigten *„Offene Krypto-Positionen"* und *„OKX Positionen"* beide OKX,
und für die Aktienseite gab es überhaupt keine Zahl. Jetzt:

- Die doppelte Kachel ist weg
- Neu: **eToro Positionen** mit Anzahl, Buchwert und je Position Menge,
  Einstand und wer sie verwaltet (🤖 Bot / 👁 nur beobachtet / ⏳ Übernahme läuft)
- Die Kacheln sind nach **Zustand · Aktien · Krypto · System** gruppiert
- Das Raster bleibt `auto-fit` — die Anordnung, die dir gefällt, ändert sich nicht

Fehlen aktuelle Kurse, steht dort *„Buchwert unbekannt"* statt einer 0. Eine 0
sähe aus wie ausgeglichen und wäre eine Falschaussage.

### 7. Eigene Position an den Bot übergeben — neue Seite *Positionen*

Die alte Prüfung war `Stop < Einstand < Ziel`, gemessen am **Einstand**. Bei
einer Position, die 30 % im Minus steht, liegt der Einstand weit über dem Kurs:
ein Stop darunter hätte über dem Marktpreis gelegen und **sofort ausgelöst**.
Deshalb wurde deine Aktie blockiert.

Maßgeblich ist jetzt der **aktuelle Kurs**: `Stop < Kurs < Ziel`. Zusätzlich
muss der Stop weiter weg sein als die Gebühren der Runde — sonst verliert er im
Auslösefall garantiert Geld. Die Zugehörigkeit zum konfigurierten Universum
wird **nicht mehr verlangt**; eine selbst gekaufte Position gehört da naturgemäß
nicht hinein.

So geht es:

1. WebUI → **Positionen**
2. Bei der Position auf **Bot übernehmen**
3. Stop und Ziel **in Prozent zum aktuellen Kurs** eintragen (vorbelegt mit
   deinem Profil)
4. **Übergeben**

Warum Prozent statt fester Beträge: Der Kurs auf der Seite ist ein paar Sekunden
alt. Der Handelskern rechnet beide Werte gegen den Kurs, den er beim Ausführen
sieht — ein fester Stop könnte bis dahin schon über dem Marktpreis liegen.

Was danach passiert: Die WebUI legt nur einen **Auftrag** ab. Der Handelskern
führt ihn beim nächsten Durchlauf aus und prüft dabei den Broker-Schutz. Erst
danach wird die Position handelbar. Das Ergebnis steht auf derselben Seite unter
*Zuletzt beauftragt* und kommt zusätzlich per Telegram.

Steht die Position im Minus, sagt die Antwort ehrlich, was ein ausgelöster Stop
kosten würde:

```text
Hinweis: Die Position steht mit -1.500,00 USD im Minus. Greift der Stop,
wird ein Verlust von rund -1.700,00 USD realisiert.
```

Umgekehrt geht auch: **Nur beobachten** nimmt dem Bot die Verwaltung wieder ab.

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

Alle drei müssen `active` melden. Danach die WebUI einmal im WLAN und einmal
über Mobilfunk mit aktivem WireGuard öffnen.

### A10. Alten Ordner erst später löschen

Lass die Vorgängerversion mindestens eine Woche liegen.

---

## Teil B — Vollständige Neuinstallation

Nur nötig, wenn keine lauffähige Vorgängerversion existiert.

### B1. Vorbedingungen

- Raspberry Pi OS 64 Bit
- Python 3.11 oder neuer
- normaler Benutzer mit `sudo`-Recht
- vorhandenes WireGuard darf weiterlaufen

Das Installationsskript niemals mit `sudo` starten.

### B2. Entpacken und installieren

```bash
cd ~/Georg
unzip TradingBot_v8.1.5_NEXUS.zip
cd TradingBot_v8.1.5_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
```

Eine vorhandene `/etc/wireguard/wg0.conf` wird weder kopiert noch verändert.

### B3. WebUI einrichten

```bash
./.venv/bin/python webui_setup.py
./Pi_WebUI_Aktivieren.sh
```

### B4. Zugänge eintragen

In der WebUI unter *Einstellungen*:

1. eToro-Demo-Zugang eintragen, speichern, **Demo-Verbindung testen**
2. OKX-Demo-Zugang eintragen, OKX aktivieren, speichern, **Demo-Verbindung testen**
3. Telegram speichern und `/version`, `/menu`, `/health` testen
4. News-Schlüssel eintragen und die gewünschten Quellen einschalten
5. Risikoprofil kontrollieren

**FMP** meldet bewusst *„Nachrichten nicht im Gratistarif enthalten (HTTP 402)"*.
Das ist richtig so und kein Defekt.

### B5. Core starten

Weiter mit den Schritten **A7** bis **A9** oben.

---

## Was in 8.1.5 anders ist

| Bereich | Vorher | Jetzt |
|---|---|---|
| Ablehnungsgrund | „Positionslimit erreicht (offen=0)" bei jeder Sperre | jeder Grund mit eigener Meldung und Zahlen |
| Entscheidung und Begründung | getrennt gepflegt, liefen auseinander | eine Quelle, per Test abgesichert |
| Marktsperre | „Markt geschlossen", auch bei offener Börse | Kalender, Broker-Sperre oder Kursalter — benannt |
| Kursalter | fest 180 s, nur per Umgebungsvariable | in der WebUI einstellbar, wirkt sofort |
| Störung bei offener Börse | fiel nur im Log auf | einmal je Wert und Tag per Telegram |
| „Heute realisiert" | Summenzähler ohne Belege | Summe der einzelnen Buchungen des Handelsbuchs |
| Abweichung der Beträge | unbemerkt | wird gemeldet, nicht weggerechnet |
| Gebühren | im Nettowert versteckt | getrennt ausgewiesen, nie doppelt abgezogen |
| eToro-Risikostand | zwei Dateien, die nichts voneinander wussten | eine Datei; die alte wird zusammengeführt |
| Tagesverlustbremse eToro | verglich immer 0,00 — konnte nie auslösen | greift |
| Globale Klammer | sah die Aktienseite mit 0,00 | sieht beide Seiten |
| Zustandspfad | relativ, löste gegen das Arbeitsverzeichnis auf | immer absolut zum Botordner |
| Universumsaufnahme Aktien | nur Vorschlag, brauchte Telegram-Freigabe | autonom, 4 h Bewährung, Deckel unverändert |
| Universumstabelle | „NONE", „UPDATED_AT" | nur echte Ticker |
| Dashboard | OKX doppelt, eToro nirgends | eToro-Kachel, Kacheln gruppiert |
| Positionsübergabe | am Einstand gemessen, im Minus unmöglich | am aktuellen Kurs gemessen |
| Übergabe in der WebUI | gab es nicht | eigene Seite *Positionen* |
| Universumszwang bei Übergabe | blockierte selbst gekaufte Werte | entfällt (`USER_MANAGED`) |

Unverändert geblieben — auf deinen Wunsch: Verbindungseinstellungen,
Einstellungsübernahme, Universumsseite, Logbuch, das `auto-fit`-Kachelraster.

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

Übergabe-Aufträge hängen:

```bash
cat position_auftraege.json
```

Steht dort ein Auftrag als *„Verfallen"*, hat der Handelskern ihn nicht
innerhalb von 30 Minuten abgeholt — dann lief er nicht.

Nachrichtenquellen einzeln prüfen:

```bash
./.venv/bin/python news_check.py
```
