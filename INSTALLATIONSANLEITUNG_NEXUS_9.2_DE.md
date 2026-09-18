# Update auf NEXUS 9.2

NEXUS 9.2 behebt den Vorfall vom 31.08.2026: Nach dem Update auf 9.1 galten
deine drei bewiesenen Krypto-Positionen plötzlich als „Externer Bestand –
ohne identische ID-Kette", und der Knopf „Erneut mit OKX prüfen" hat daran
nichts geändert.

> **Der Kern in einem Satz:** Ab 9.2 kann **kein Software-Update mehr
> bestimmen, wem ein bereits gekaufter Bestand gehört.** Eigentum hängt
> ausschließlich an den Kennungen, die OKX vergeben hat — orderId, clOrdId
> und echte tradeId-Fills. Strategieversion und Parameter-Hash sind daran
> unbeteiligt, und ein Test besteht bei jedem Lauf darauf.

> **Was war passiert:** In 9.1 wurde `STARTUP_CANDLES` von 200 auf 30
> gesenkt. Das änderte den Parameter-Hash der Strategie. Weil „gehört dem
> Bot" und „darf automatisch verwaltet werden" derselbe Schalter waren,
> verloren die Positionen mit der Pause auch ihren Eigentumsnachweis.

## 1. Version 9.1 stoppen

```bash
cd ~/Georg/TradingBot_v9.1_NEXUS
./Pi_Service_Stoppen.sh
```

Nur eine Version darf laufen. Nicht parallel starten.

## 2. Version 9.2 installieren

```bash
cd ~/Georg
unzip TradingBot_v9.2_NEXUS.zip
cd TradingBot_v9.2_NEXUS
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

Erwartet werden `SELF TEST OK` und `VOLLTEST OK` für **9.2.0-NEXUS**.
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

### 5.1 Deine drei Positionen sind wieder richtig zugeordnet

Das ist die eigentliche Prüfung. Öffne **Trades**. BNB, LINK und ONDO sollten
jetzt in **„Offene Trades"** stehen, nicht mehr unter „Klärung nötig".

In der neuen Spalte **Zuordnung** stehen die drei Zustände getrennt:

```
Eigentum bewiesen
Abgleich: Bestätigte Botposition
Verwaltung: automatisch
Strategie migriert von NEXUS-FT-SAMPLE-V1 · 31.08.2026 ...
```

Die letzte Zeile erscheint nur, wenn die Position tatsächlich migriert
wurde. Sie ist der Nachweis, dass der Wechsel protokolliert ist und nicht
still im Hintergrund passiert ist.

Falls eine Position doch stehen bleibt, steht in der Spalte **Grund**
jetzt, was ihr konkret fehlt — statt derselben Statusmeldung ein zweites
Mal.

Im Log:

```bash
sudo journalctl -u tradingbot-pi5.service --since "-1 h" --no-pager \
  | grep -i "migr"
```

### 5.2 Der Prüfen-Knopf tut jetzt etwas

„Erneut mit OKX prüfen" holt jetzt wirklich den Bestand bei OKX, gleicht das
Ledger ab und meldet ein konkretes Ergebnis, zum Beispiel:

```
Bestätigte Botposition — automatische Verwaltung aktiv
```

Bis 9.1 schrieb er nur „Erneuter OKX-Abgleich im aktuellen Zyklus
angefordert" und verließ sich auf den nächsten Zyklus — der denselben
Vergleich wiederholte.

### 5.3 Eine pausierte Position wird weiter überwacht

Wichtig für dein Geld: Wenn eine bewiesene Position aus irgendeinem Grund
pausiert, wird sie ab 9.2 **trotzdem** weiter geprüft — Menge, Stop und
externe Verkäufe. Sie sperrt auch weiterhin den Wiedereinstieg, damit kein
zweiter Kauf desselben Werts durchrutscht. Nur der automatische Verkauf
ruht.

Bis 9.1 wurde sie komplett übersprungen.

### 5.4 Universum-Seite

Öffne **Universum**. Die Kacheln zeigten vorher fest eingebaute Zahlen
(„fester Kern 75 + 20", „eToro 75 max 100"), die nicht mehr zur Version
passten. Jetzt kommen alle Zahlen aus der API:

```
Krypto (OKX)    Kern 20 · dynamisch 30 · aktiv 50 · Bewährung 24 h
Aktien (eToro)  Kern 75 · dynamisch 25 · aktiv 100 · Bewährung 4 h
```

Ändert sich eine Einstellung, ändert sich die Anzeige mit. Der Aufbau der
Seite ist bewusst geblieben wie er war.

### 5.5 Kein Fehlalarm mehr im Dauertakt

Wiederholte gleiche Störmeldungen werden jetzt gebündelt (15 Minuten). Fällt
der Grund weg, kommt eine Entwarnung.

---

## 6. Wenn eine Position nicht automatisch migriert wird

Das ist Absicht und kein Fehler. Migriert wird **nur**, wenn alle drei
Punkte stimmen:

1. die Broker-ID-Kette ist lückenlos,
2. der Bestand gehört zum selben OKX-Konto,
3. die Vorgängerversion ist eine bekannte, ausdrücklich eingetragene.

Trifft das nicht zu, bleibt die Position mit dem Status
`STRATEGY_MIGRATION_REQUIRED` oder `ACCOUNT_MISMATCH` stehen. Eigentum,
Broker-Schutz und Wiedereinstiegssperre bleiben dabei erhalten — es ruht
nur der automatische Verkauf. Schick mir in dem Fall den Screenshot der
Zuordnungsspalte, dann trage ich die Identität nach.

Eine Migration schreibt ausschließlich Strategiefelder. Sie sendet keine
Order, storniert nichts, ändert keine Menge und keinen Schutz.

---

## 7. Unverändert geblieben

  - Verbindungseinstellungen und der komplette OKX-Verbindungsaufbau
  - „Einstellungen übernehmen"
  - Anordnung der Kacheln in der WebUI
  - Logbuch-Seite
  - Der Freqtrade-Modus bleibt eine Zusatzfunktion. Ist er aus, arbeiten die
    anderen Modi unverändert.
  - GPT/Terra ändert weiterhin keine Dateien, keine Sicherheitsfilter, kein
    Universum, keine Orders und keine Risikogrenzen.

---

## 8. Zurück auf 9.1

```bash
cd ~/Georg/TradingBot_v9.2_NEXUS && ./Pi_Service_Stoppen.sh
cd ~/Georg/TradingBot_v9.1_NEXUS && ./Pi_Service_Aktivieren.sh
```

Der 9.1-Ordner bleibt vollständig erhalten. Migrierte Positionen behalten
dort ihre alten Strategiefelder nicht — sie stünden wieder als
„Klärung nötig" da. Ihr Eigentum und ihr Broker-Schutz sind davon nicht
betroffen.
