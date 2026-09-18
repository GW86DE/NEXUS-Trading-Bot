# Update auf NEXUS 9.3

NEXUS 9.3 behebt den MSFT-Fall vom 31.08.2026: Der Bot hatte 29 MSFT bei
eToro gekauft, die Aktie lag im Depot — und stand in der Oberfläche trotzdem
als „Beim Broker bereits vorhanden; standardmäßig nur beobachten".

> **Der Kern in einem Satz:** Eigentum an einer eToro-Aktie hängt ab 9.3
> ausschließlich an der **positionId**, die eToro vergeben hat. Symbol und
> Menge sind nur noch Anzeigedaten. Es ist dieselbe Regel, die auf der
> Kryptoseite seit 9.2 gilt.

> **Was schiefging:** eToro zeigt eine frische Position im Depot, bevor der
> eigene Fill verarbeitet ist. Der Positionssync legte sie deshalb als
> Fremdbestand an. Die spätere Hochstufung verglich Symbol und Menge — der
> Kommentar im Code behauptete „exakt per Broker-ID", der Code tat es nicht.
> Und von Hand ging es auch nicht: der Übernahme-Knopf braucht einen Kurs,
> und `closeRate` war bei der frischen Position 0.

## 1. Version 9.2 stoppen

```bash
cd ~/Georg/TradingBot_v9.2_NEXUS
./Pi_Service_Stoppen.sh
```

Nur eine Version darf laufen. Nicht parallel starten.

## 2. Version 9.3 installieren

```bash
cd ~/Georg
unzip TradingBot_v9.3_NEXUS.zip
cd TradingBot_v9.3_NEXUS
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

Erwartet werden `SELF TEST OK` und `VOLLTEST OK` für **9.3.0-NEXUS**.
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

### 5.1 MSFT ist wieder richtig zugeordnet

Das ist die eigentliche Prüfung. Öffne **Positionen**. In der neuen Spalte
**Zuordnung** sollte stehen:

```
Eigentum über positionId bewiesen
positionId 2984571234
```

und in der Spalte Verwaltung **BOT VERWALTET** statt „NUR BEOBACHTET".

**Wichtig:** MSFT wurde vor dem Update angelegt und hat deshalb noch keine
gespeicherte positionId. Der erste Depot-Abgleich nach dem Start trägt sie
nach. Bleibt die Aktie danach immer noch auf „NUR BEOBACHTET", heißt das:
zu dieser positionId gibt es keine offene eigene Kaufabsicht mehr — der
Reconciliation-Satz ist inzwischen terminal. Dann bitte einmal auf
„Bot übernehmen" (der Knopf funktioniert jetzt, weil der Kurs da ist) und
mir Bescheid geben.

Für **künftige** Käufe greift die Kette ab dem Kaufzeitpunkt. Ein eigener
Kauf steht im Propagationsfenster als

```
EIGENER KAUF · WIRD BESTÄTIGT
```

und wird automatisch zu BOT/AUTO hochgestuft, sobald die positionId im Depot
erscheint. Als „Fremdbestand" kann er nicht mehr erscheinen.

### 5.2 Der Kurs ist da

In derselben Zeile steht jetzt ein Kurs statt „–", und darunter die Quelle:

```
508,90
Bid
```

Mögliche Quellen: Depotkurs, Bid, Ask, letzte Ausführung. Fehlt wirklich
alles, steht dort **unbekannt** — und der Buchwert ist leer statt „+0,00".
Ein fehlender Kurs wird nie mehr als null gebucht.

### 5.3 Underdogs handeln jetzt wirklich

Öffne **Universum**. In der eToro-Kachel steht neu:

```
Underdogs 0 aktiv · 10/10 Kernplätze · 25 im Katalog
```

Bis 9.2 waren es **0 von 25** — der Schalter stand auf „aktiv", und kein
einziger Underdog konnte gehandelt werden, weil der feste Kern per blindem
Schnitt aus den ersten 75 Katalogwerten gebildet wurde und die Underdogs erst
ab Position 190 stehen.

Nach deiner Vorgabe sind es jetzt 10 Kernplätze (65 Standard + 10 Underdogs
+ 25 dynamisch). Die Zahl hinter „aktiv" füllt sich, sobald der Scanner das
Universum das nächste Mal aufbaut. Das strengere Underdog-Screening bleibt
zwingend — ein Wert ohne bestandenes Screening wird nicht gekauft.

### 5.4 Ein nicht ausgeführter Kauf sieht nicht mehr wie ein Kauf aus

Im **Logbuch** war eine freigegebene, aber vom Broker stornierte FOK-Order
bisher grün als „APPROVED" zu sehen. Jetzt steht dort:

```
APPROVED · nicht ausgeführt
Kauf erlaubt – bei OKX nicht ausgeführt · cancelSource=1 · sCode=51008
```

Grün gibt es nur noch für einen tatsächlich ausgeführten Kauf.

### 5.5 Kein Geld wird zweimal gezählt

```bash
sudo journalctl -u tradingbot-pi5.service --since "-2 h" --no-pager \
  | grep -i "gebunden"
```

Läuft nichts Ungeklärtes, steht dort nichts. Ist ein Kauf noch offen,
erscheint der gebundene Betrag — und ein zweiter Kandidat rechnet nicht mehr
mit demselben Geld. Bei OKX bleiben die Währungskanäle getrennt: eine
EUR-Reservierung blockiert kein USDC.

---

## 6. Was ich bewusst NICHT gebaut habe

Der Änderungsantrag schlägt für die Kapitalreservierung eine neue
SQLite-Komponente mit elf Zuständen vor (`entry_intent_store.py`). Ich habe
stattdessen die kleine Lösung gebaut: schwebende Käufe werden vom
verfügbaren Geld abgezogen, und die bei eToro schon vorhandene Kontosperre
gilt jetzt auch für OKX je Währungskanal.

Gründe: bei eToro deckt die vorhandene Sperre den Fall bereits ab, und ein
Umbau des kompletten Geldpfads beider Broker ist genau die Art Änderung, die
uns den 9.1-Zwischenfall eingebrockt hat. Die neun Pflicht-Tests aus dem
Antrag sind trotzdem alle umgesetzt — sie gelten für beide Lösungen. Fällt
einer davon in der Praxis auf, bauen wir den Store, dann aber mit Belegen.

Ebenfalls verschoben: die Vereinheitlichung aller Statustexte über WebUI,
Telegram und Logbuch. Die vier ausdrücklich verbotenen Darstellungen sind
einzeln behoben; ein Refactoring der übrigen Texte bringt keinen
Sicherheitsgewinn.

---

## 7. Unverändert geblieben

  - Verbindungseinstellungen und der komplette OKX-Verbindungsaufbau
  - „Einstellungen übernehmen"
  - Anordnung der Kacheln in der WebUI, Logbuch- und Universum-Aufbau
  - Der Freqtrade-Modus bleibt eine Zusatzfunktion. Ist er aus, arbeiten die
    anderen Modi unverändert.
  - GPT/Terra ändert weiterhin keine Dateien, Sicherheitsfilter, kein
    Universum, keine Orders und keine Risikogrenzen.
  - Alle v9.2-Korrekturen an Eigentum, Strategie-Migration und Kerzenraster

---

## 8. Zurück auf 9.2

```bash
cd ~/Georg/TradingBot_v9.3_NEXUS && ./Pi_Service_Stoppen.sh
cd ~/Georg/TradingBot_v9.2_NEXUS && ./Pi_Service_Aktivieren.sh
```

Der 9.2-Ordner bleibt vollständig erhalten. Die dort gespeicherten
Positionen sind lesbar; die neuen Eigentumsfelder werden von 9.2 ignoriert.
Die 10 Underdogs fallen dann wieder aus dem aktiven Universum heraus.
