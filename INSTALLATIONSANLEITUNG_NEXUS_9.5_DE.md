# Update von NEXUS 9.4.1 auf NEXUS 9.5.0

NEXUS 9.5.0 aendert kritische Broker- und Persistenzpfade. Bitte den alten
Core vor der Migration vollstaendig stoppen. Die alte Installation bleibt als
Rueckfallkopie unveraendert erhalten.

## 1. NEXUS 9.4.1 stoppen

```bash
cd ~/Georg/TradingBot_v9.4.1_NEXUS
./Pi_Service_Stoppen.sh
sudo systemctl stop tradingbot-webui.service
```

Mit `./Pi_Service_Status.sh` pruefen, dass kein alter Trading-Core mehr laeuft.

## 2. ZIP entpacken und installieren

```bash
cd ~/Georg
unzip TradingBot_v9.5_NEXUS.zip
cd TradingBot_v9.5_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
./Pi_Installieren.sh
```

## 3. Daten aus genau 9.4.1 uebernehmen

```bash
./.venv/bin/python settings_migration.py ../TradingBot_v9.4.1_NEXUS
```

Wichtig:

- `bot_order_registry.json`, `fill_progress.json`, `crypto_positions.json`,
  `position_state.json`, Ledger und Reconciliation muessen gemeinsam
  uebernommen werden.
- Neu uebernommen werden auch `api_daily_budgets.json` und
  `broker_exit_journal.sqlite`.
- Die Migration setzt eToro auf Paper und OKX auf Demo. LIVE wird nie aus der
  alten Version uebernommen.
- Die alte 9.4.1-Installation danach nicht parallel starten. Zwei Cores auf
  demselben Brokerkonto sind unzulaessig.

## 4. Offline pruefen

```bash
cat VERSION.txt
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
```

Erwartet werden `9.5.0-NEXUS`, `SELF TEST OK` und `VOLLTEST OK`.

## 5. Dienste starten

```bash
./Pi_WebUI_Aktivieren.sh
sudo systemctl restart tradingbot-webui.service
./Pi_Service_Aktivieren.sh
./Pi_Service_Status.sh
```

Die WebUI danach einmal mit `Strg` + `F5` hart neu laden.

## 6. ONDO nicht erneut kaufen

Der in 9.4.1 als nicht ausgefuehrt angezeigte ONDO-Auftrag wird beim Start
anhand seiner exakten registrierten OKX-orderId und echten tradeId-Fills
nachgeprueft. Wenn die vollstaendige Brokerkette vorliegt, rekonstruiert 9.5
die Position automatisch. Bitte keinen manuellen Ersatzkauf ausloesen.

In WebUI und Logbuch pruefen:

1. Es erscheint `FILLED_RECOVERED` beziehungsweise eine wiederhergestellte
   ONDO-Botposition.
2. Order-ID und Fill-IDs sind sichtbar beziehungsweise im Ledger vorhanden.
3. Broker-Schutz wird separat exakt bestaetigt.

Bleibt ONDO unbeweisbar, bleibt der Bestand nur beobachtet. Symbol und
Kontosaldo werden absichtlich nicht als Eigentumsbeweis verwendet.

## 7. LINK und Freqtrade-ROI pruefen

Die 60-Minuten-Stufe bedeutet ein Mindest-ROI von 1 %, nicht automatisch einen
Verkauf exakt nach 60 Minuten. Der aktuelle volle Verkaufs-VWAP muss den in
der Trades-Grafik eingeblendeten, gebuehrenbereinigten ROI-Schwellenkurs
erreichen.

Beim geprueften Screenshot war das nicht der Fall:

```text
Aktueller Verkaufs-VWAP:  11,33100000 USDC
Aktives ROI-Ziel:         11,62451923 USDC
```

9.5 behebt dennoch den vorhandenen Fehler, dass ein ausgefallener
Kerzen-Historienabruf einen bereits erreichten ROI-Exit blockieren konnte.

## 8. FMP-Budget kontrollieren

Am ersten Tag nach dem Update kann die Automatik bis zum Tageswechsel
vorsorglich pausieren, weil der bereits vor dem Update verbrauchte FMP-Stand
nicht sicher bekannt ist. Ab dem naechsten Tag gelten:

- maximal 250 FMP-Aufrufe insgesamt;
- maximal 80 automatische Aufrufe;
- maximal acht Aufrufe pro Aktien-Universumslauf;
- Referenzdaten bis zu sieben Tage aus persistentem Cache;
- keine automatischen FMP-Aufrufe bei deaktiviertem eToro;
- keine FMP-News im Free-Plan.

Ein reiner OKX-Freqtrade-Betrieb darf im FMP-Dashboard keine automatischen
Calls erzeugen.

## 9. Kryptoanalyse verwenden

Unter **Analyse** stehen jetzt bereit:

- **Krypto - Freqtrade Backtest**
- **Krypto - Walk-Forward**

Die Aufgaben laden nur oeffentliche, abgeschlossene OKX-Kerzen und besitzen
keine Orderrechte. Der erste Lauf kann wegen des historischen Backfills
laenger dauern; Folgelaeufe verwenden den lokalen Cache.

## 10. DEMO-Abnahme vor LIVE

Mindestens folgende Ablaeufe in DEMO/Paper pruefen:

1. eToro-Kauf: Order -> Fill -> konkrete positionId -> exakter SL/TP -> AUTO.
2. eToro-Teilverkauf und Neustart waehrend einer unklaren Close-Antwort.
3. OKX-Kauf mit mehreren Fills und bestaetigter Schutz-algoId.
4. OKX-ROI-/Stop-Verkauf, danach Ledger und Risikotopf pruefen.
5. Zwei Signale gleichzeitig: kein Doppel-POST und keine doppelte
   Kapitalreservierung.

LIVE erst wieder aktivieren, wenn diese Abnahme mit den konkreten Konten
erfolgreich war.
