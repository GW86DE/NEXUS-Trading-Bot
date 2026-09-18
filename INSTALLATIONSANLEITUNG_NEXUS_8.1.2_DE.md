# Installationsanleitung NEXUS 8.1.2 – Raspberry Pi 5

Diese Version ist ein **Update auf ein laufendes 8.1.1**. Teil A beschreibt genau
diesen Weg. Teil B ist die vollständige Neuinstallation und nur nötig, wenn kein
funktionierendes 8.1.1 vorhanden ist.

> **Wichtig vorab:** An den Verbindungseinstellungen wurde nichts geändert.
> Broker-Zugänge, WireGuard, der WebUI-Login und die Einstellungsübernahme
> funktionieren unverändert. Du musst nichts davon neu einrichten.

---

## Teil A — Update von 8.1.1 auf 8.1.2

Dauer: etwa 15 Minuten, davon 10 Minuten Warten.

### A1. Vorbereitung

```bash
cd ~/Georg
```

Den alten Ordner **nicht** löschen und **nicht** überschreiben — er ist deine
Rückfallmöglichkeit.

### A2. Trading-Core stoppen

Die Datenübernahme braucht einen gestoppten Core.

```bash
cd ~/Georg/TradingBot_v8.1.1_NEXUS
./Pi_Service_Stoppen.sh
```

Die WebUI darf weiterlaufen.

### A3. Neues Paket entpacken

```bash
cd ~/Georg
unzip TradingBot_v8.1.2_NEXUS.zip
cd TradingBot_v8.1.2_NEXUS
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

Bricht der Volltest ab, hier stoppen und die Meldung schicken. Der alte Ordner
läuft in diesem Fall unverändert weiter.

### A5. Einstellungen aus 8.1.1 übernehmen

```bash
./.venv/bin/python settings_migration.py --auto
```

Alternativ über die WebUI: *Dashboard → Datenübernahme aus alter Version*, Quelle
auswählen, mit `UEBERNEHMEN` bestätigen.

Übernommen werden eToro, OKX, Telegram, News-Schlüssel, OpenAI, Risikoprofil,
Favoriten, Positions- und Risikozustände, Universum und Entscheidungshistorie.
Der alte Ordner bleibt dabei unverändert. eToro wird auf PAPER und OKX auf DEMO
gesetzt, alle LIVE-Arming-Dateien werden entfernt — das ist Absicht.

### A6. WebUI auf den neuen Ordner umstellen

```bash
./Pi_WebUI_Aktivieren.sh
sudo systemctl restart tradingbot-webui.service
```

Der **Neustart ist Pflicht**: `Pi_WebUI_Aktivieren.sh` startet einen bereits
laufenden Dienst nicht neu, und 8.1.2 bringt eine neue Seite und neue Routen mit.

Prüfen:

```bash
sudo systemctl status tradingbot-webui.service --no-pager
```

### A7. Abnahme — die vier neuen Punkte prüfen

Alles in der WebUI unter der gewohnten Adresse, zum Beispiel
`http://192.168.178.60:8780`.

**1. Nachrichtenquellen — der eigentliche Fix**

*Einstellungen → News- und Referenz-APIs*

- Haken bei **Finnhub** setzen → **Alle Einstellungen speichern**
- Auf *Dashboard* wechseln → bei Finnhub **„Jetzt testen"**

Erwartung: eine echte Antwort, kein *„nicht aktiviert oder Zugang fehlt"* mehr.
Der Schalter wirkt jetzt **sofort, ohne Neustart**. Genau das ging in 8.1.1 nicht.

Dasselbe für **MASSIVE**. Der Test meldet jetzt getrennt, ob Referenzdaten *und*
Nachrichten funktionieren. Steht dort *„Referenzdaten erreichbar, Nachrichten aber
nicht"*, deckt dein MASSIVE-Tarif keine News ab — dann ist das eine Tarifaussage
und kein Defekt.

**FMP** zeigt bewusst *„Nachrichten nicht im Gratistarif enthalten (HTTP 402)"*.
Das ist richtig so. FMP liefert jetzt Symbolsuche, Kurse, Sektor und
Delisting-Status für das Aktien-Universum — begrenzt auf 250 Anfragen pro Tag.

**2. Neue Universums-Seite**

Oben in der Navigation → **Universum**

Zwei getrennte Tabellen für OKX und eToro, mit Zustand, Rang, Score, Aufnahmezeit,
KI-Urteil und Grund. BTC, ETH und SOL sind mit **KERN** markiert. Direkt nach dem
Update ist die Liste noch leer — der erste Universumslauf füllt sie (Krypto nach
etwa 15 Minuten, Aktien nach etwa 45 Minuten bei geöffnetem Markt).

**3. OKX-WebSocket**

Nach dem Start des Cores (Schritt A8):

```bash
sudo journalctl -u tradingbot-pi5.service -n 200 --no-pager | grep -i "60033\|4004\|Privatstream"
```

Erwartung: **keine** `60033`- oder `4004`-Zeilen mehr, stattdessen zweimal
`Kanal ... bestaetigt`. Die Reconnect-Schleife im Minutentakt ist damit weg.

**4. GUI**

```bash
./Pi_GUI_Starten.sh
```

*Universum* zeigt jetzt das echte dynamische Universum statt der alten festen
Liste. Die *Broker*-Karten zeigen den wirklichen Zustand statt immer „AKTIV".
Neu ist der Navigationspunkt *WebUI*.

### A8. Trading-Core starten

Vorher in der WebUI kontrollieren: eToro **PAPER**, OKX **DEMO**, LIVE-Arming
**AUS**, Risikoprofil wie gewünscht.

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Status.sh
```

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

Lass `TradingBot_v8.1.1_NEXUS` mindestens eine Woche liegen. Erst wenn 8.1.2
mehrere Tage stabil läuft, kannst du ihn entfernen.

---

## Teil B — Vollständige Neuinstallation

Nur nötig, wenn kein lauffähiges 8.1.1 existiert.

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
unzip TradingBot_v8.1.2_NEXUS.zip
cd TradingBot_v8.1.2_NEXUS
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

### B5. Core starten

Weiter mit den Schritten **A8** und **A9** oben.

---

## Was in 8.1.2 anders ist als in 8.1.1

| Bereich | Vorher | Jetzt |
|---|---|---|
| News-Schalter | wirkten erst nach Neustart | wirken sofort |
| Neuer API-Schlüssel | wirkte erst nach Neustart | wirkt sofort |
| FMP | galt als defekt (402) | Referenzquelle, 250 Anfragen/Tag |
| MASSIVE-Test | meldete „OK", auch ohne News | prüft beide Fähigkeiten getrennt |
| Quellenschalter | 6 von 10 vorhanden | alle 9 vorhanden |
| Speichern der Quellen | löschte Yahoo/Google/Nasdaq | behält alle |
| Universum | nur in der GUI, statische Liste | eigene WebUI-Seite, echte Daten |
| OKX-WebSocket | 60033/4004-Schleife | stabil, Keepalive, ACK-Prüfung |
| BTC/ETH/SOL | per Rang entfernbar | fester Kern |
| Guthaben ohne Buchung | eine Sammelzeile im Log | fünf Klassen, Sperre bei Ungeklärtem |

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

Bitte bis dahin in DEMO/PAPER bleiben. Im Demo-Betrieb wirken sich diese drei
Punkte nicht aus.
