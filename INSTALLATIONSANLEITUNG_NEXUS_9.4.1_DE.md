# Update auf NEXUS 9.4.1

NEXUS 9.4.1 aktualisiert die WebUI auf Basis des geprueften 9.4-Releases.
Die eToro-/OKX-Sicherheits- und Ownership-Regeln bleiben unveraendert.

## 1. Laufende Version stoppen

```bash
cd ~/Georg/TradingBot_v9.4_NEXUS
./Pi_Service_Stoppen.sh
```

Falls dein 9.4-Ordner anders heisst, den Pfad entsprechend anpassen.

## 2. Entpacken und installieren

```bash
cd ~/Georg
unzip TradingBot_v9.4.1_NEXUS.zip
cd TradingBot_v9.4.1_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration uebernimmt die vorhandenen Einstellungen und Zustandsdateien.
Alte beobachtete eToro-IDs werden weiterhin nicht zu Eigentumsbeweisen.

## 3. Vor dem Start pruefen

```bash
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
```

Erwartet werden `SELF TEST OK` und `VOLLTEST OK`.

## 4. Dienste aktivieren

```bash
./Pi_WebUI_Aktivieren.sh
sudo systemctl restart tradingbot-webui.service
./Pi_Service_Aktivieren.sh
./Pi_Service_Status.sh
```

## 5. Browsercache einmalig leeren

Da CSS und JavaScript geaendert wurden, die WebUI einmal hart neu laden:

- Windows/Linux: `Strg` + `F5`
- macOS: `Cmd` + `Shift` + `R`
- iPhone/iPad: Seite schliessen und neu oeffnen; falls noetig Safari-
  Websitedaten fuer die lokale NEXUS-Adresse loeschen.

Danach pruefen:

1. Das Logo steht am PC kompakt mit 46 x 46 Pixeln neben der Version.
2. Auf iPad/iPhone bleibt es kompakt in der bisherigen Groesse.
3. Der neue Menuepunkt **Analyse** oeffnet Karten, Werkzeuge, Ausgabe und
   Historie.
4. Unter **Trades** zeigt „Kumuliertes Nettoergebnis“ bei EUR und USD zwei
   getrennte Kurven statt einer leeren Flaeche.

## 6. Analyse-Seite verwenden

Eine Analyse mit **Starten** ausloesen. Der Lauf arbeitet lokal und kann je
nach Datenmenge mehrere Minuten dauern. Es ist absichtlich nur ein Lauf
gleichzeitig moeglich. Die Ausgabe aktualisiert sich automatisch.

Backtests, Walk-Forward, ML und Research koennen CPU und Netzwerk beanspruchen.
Sie erhalten keine Orderrechte; fuer moeglichst ruhigen Livebetrieb trotzdem
nicht mehrere rechenintensive Aufgaben unmittelbar hintereinander starten.

## 7. DEMO-Pruefung vor LIVE

Wie bei 9.4 gilt: Nach dem Update zuerst im DEMO-Konto pruefen. LIVE erst
verwenden, wenn Depotzuordnung, Schutz und Verkauf fuer die konkrete
`positionId` sauber bestaetigt wurden.

