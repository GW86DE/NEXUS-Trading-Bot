# TradingBot NEXUS 9.0 installieren oder aktualisieren

## 1. Bestehende Version sichern und Dienste stoppen

Beispiel für 8.3.1:

```bash
cd ~/Georg/TradingBot_v8.3.1_NEXUS
./Pi_Service_Stoppen.sh
./Pi_Service_Deaktivieren.sh
cp -a . ~/Georg/Backup_TradingBot_v8.3.1_$(date +%Y%m%d_%H%M)
sudo systemctl stop tradingbot-webui.service 2>/dev/null || true
```

## 2. NEXUS 9.0 in einen neuen Ordner entpacken

```bash
cd ~/Georg
unzip TradingBot_v9.0_NEXUS.zip
cd TradingBot_v9.0_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
```

Den alten Ordner nicht überschreiben. Zugangsdaten oder Datenbanken niemals
manuell in das ZIP kopieren.

## 3. Installieren und Zustand sicher übernehmen

```bash
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration erzwingt eToro-Paper und OKX-Demo und übernimmt unter anderem
Entscheidungshistorie, Trade-Ledger, Reconciliation, CORE_VOLUME_20,
Positionsbuch und den Krypto-Modusschalter.

Bei offenen 8.3.1-Kryptopositionen gilt:

- `BOT` + `AUTO` + positive `decision_id` ist ein eindeutiger Beleg für die
  damalige NEXUS-Standardstrategie und wird entsprechend markiert;
- fremde, manuelle oder unvollständig belegte Positionen werden keiner
  Strategie zugeraten und bleiben `BEOBACHTEN`;
- offene Positionen werden niemals allein anhand Symbol und Menge übernommen.

## 4. Offline prüfen

```bash
cat VERSION.txt
./.venv/bin/python volltest.py
./.venv/bin/python self_test.py
./Nexus_Einrichten.sh --test
./Pi_Service_Unit_Pruefen.sh
```

Erwartete Version: `9.0.0-NEXUS`. Der Broker-Test ist read-only und sendet
keine Testorder.

Kryptomodus anzeigen:

```bash
./.venv/bin/python -c "import json,crypto_strategy_mode as m; print(json.dumps(m.status(),indent=2,ensure_ascii=False))"
```

## 5. Dienste bewusst aktivieren

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Starten.sh
./Pi_WebUI_Aktivieren.sh
./Pi_Service_Status.sh
```

Öffne anschließend die WebUI und kontrolliere:

- Version `9.0.0-NEXUS`;
- eToro `PAPER` und OKX `DEMO`;
- offene Positionen einschließlich ihrer Einstiegsstrategie;
- Reconciliation ohne ungeklärte Order;
- Telegram-Verbindung;
- gewünschter OKX-Kryptomodus.

## 6. SampleStrategy bewusst aktivieren

In **Einstellungen → OKX-Kryptostrategie** `Freqtrade SampleStrategy` wählen
und `FREQTRADE AKTIVIEREN` bestätigen. Ein Neustart ist nicht erforderlich.
Alternativ über Telegram:

```text
/crypto freqtrade
```

Die Telegram-Schaltfläche danach ausdrücklich bestätigen. Erst im Demo-Modus
beobachten und Ergebnisse nach Strategieversion/Ausstiegsgrund auswerten.

Notbremse:

```text
/cryptopause
```

Sie stoppt neue Kryptokäufe, lässt eToro und die Schutz-/Ausstiegsverwaltung
offener OKX-Positionen aber weiterlaufen.

## 7. Rückweg

Ein älterer Bot versteht die neuen Strategie-Snapshots und
`FREQTRADE_SAMPLE`-Positionen nicht. Deshalb nicht einfach mit einer offenen
SampleStrategy-Position auf 8.3.1 zurückschalten.

Vor einem Rückweg:

1. `/cryptopause` ausführen;
2. alle Brokerpositionen, Pending Orders und Schutzorders eindeutig prüfen;
3. SampleStrategy-Positionen kontrolliert schließen oder dokumentiert manuell
   übernehmen;
4. aktuellen 9.0-Ordner vollständig sichern;
5. erst danach den alten Dienst aktivieren.

## Sicherheitsgrenzen

- Installation und Migration aktivieren niemals LIVE.
- `CRYPTO_PAUSED` stoppt keine eToro-Verarbeitung.
- Ein Moduswechsel verändert keine bereits offene Position.
- Fremde oder zweifelhafte Positionen werden nicht automatisch verkauft.
- SampleStrategy-Mitgliedschaft umgeht keinen Broker-, Risiko-, Kosten-,
  Liquiditäts-, Datenfrische- oder Doppelorder-Schutz.

