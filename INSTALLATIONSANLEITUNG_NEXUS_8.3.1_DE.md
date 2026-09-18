# TradingBot 8.3.1 NEXUS installieren oder aktualisieren

## 1. Vorher sichern und Dienste stoppen

```bash
cd ~/Georg/TradingBot_v8.3.0_NEXUS
./Pi_Service_Stoppen.sh
./Pi_Service_Deaktivieren.sh
cp -a . ~/Georg/Backup_TradingBot_v8.3.0_$(date +%Y%m%d_%H%M)
```

Falls die WebUI als eigener Dienst läuft:

```bash
sudo systemctl stop tradingbot-webui.service
```

## 2. ZIP separat entpacken

```bash
cd ~/Georg
unzip TradingBot_v8.3.1_NEXUS.zip
cd TradingBot_v8.3.1_NEXUS
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh WireGuard_Status_Pruefen.sh
```

Den alten Ordner nicht überschreiben. So bleibt der Rückweg erhalten.

## 3. Installieren und Daten übernehmen

```bash
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Die Migration setzt eToro bewusst auf Paper und OKX auf Demo. Sie übernimmt
auch eine eventuell ungeklärte eToro-Order sowie CORE_VOLUME_20-Historie. Eine
ungeklärte Order niemals durch Löschen der Zustandsdatei „lösen“.

## 4. Diagnose vor bewusster Aktivierung

```bash
cat VERSION.txt
./.venv/bin/python volltest.py
./Nexus_Einrichten.sh --test
./Pi_Service_Unit_Pruefen.sh
./Pi_Service_Status.sh
```

Erwartete Version: `8.3.1-NEXUS`. `volltest.py` darf keinen Fehler melden.
Der Broker-Test ist read-only; keine Testorder wird gesendet.

Zusätzliche lokale Diagnose:

```bash
./.venv/bin/python -c "import json,etoro_reconciliation as r; print(json.dumps(r.status(),indent=2,ensure_ascii=False))"
./.venv/bin/python -c "import json,core_volume_20 as c; print(json.dumps(c.load(),indent=2,ensure_ascii=False))"
```

`RECONCILING`/`UNKNOWN_AFTER_SUBMIT` heißt: keinen manuellen Doppel-Kauf
auslösen; zuerst das eToro-Konto und die gespeicherten IDs prüfen. `STALE` beim
CORE_VOLUME_20 behält die letzte Liste zur Anzeige, sperrt daraus aber neue Käufe.

## 5. Dienste bewusst aktivieren

Erst nach erfolgreicher Diagnose:

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Starten.sh
./Pi_WebUI_Aktivieren.sh
./Pi_Service_Status.sh
```

Live-Handel wird durch Installation/Migration nicht automatisch aktiviert.
Demo/Live und eToro/OKX bleiben getrennt.

## 6. Rückweg auf 8.3.0

```bash
cd ~/Georg/TradingBot_v8.3.1_NEXUS
./Pi_Service_Stoppen.sh
./Pi_Service_Deaktivieren.sh
cd ~/Georg/TradingBot_v8.3.0_NEXUS
./Pi_Installieren.sh
./Pi_Service_Aktivieren.sh
./Pi_Service_Starten.sh
```

Vor einem Rückweg das aktuelle 8.3.1-Verzeichnis zusätzlich sichern. Wenn in
8.3.1 ein eToro-Submit ungeklärt ist, nicht mit 8.3.0 weiterkaufen: 8.3.0 kennt
den neuen Reconciliation-Zustand nicht. Zuerst brokerseitig eindeutig klären.

## Sicherheitsgrenzen

- Keine manuell vorhandene Position wird automatisch übernommen oder verkauft.
- OBSERVE bleibt read-only; nur AUTO behält Strategie-/News-/Time-Stop-Exits.
- CORE_VOLUME_20 erzwingt keinen Kauf und umgeht keinen Signal-, Spread-,
  Liquiditäts-, Datenfrische-, Kosten-, Cash-, Cooldown- oder Risikofilter.
- Eine Listenentfernung löst keinen Verkauf einer bestehenden Position aus.
