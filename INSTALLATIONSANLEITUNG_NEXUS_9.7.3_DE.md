# NEXUS 9.7.3 – Update von 9.7.1

Diese Korrektur betrifft den eToro-Buchungsabgleich und die durchgängige Broker-/Konto-/Währungskennzeichnung der WebUI. Es wird kein Freqtrade-Prozess installiert und keine bestehende Strategie ersetzt. Die Original-Handelsdaten dürfen nicht gelöscht werden.

**Vor dem Update:** Laufende Positionen und tatsächlich vorhandene Schutzorders direkt bei den Brokern prüfen. Ein gestoppter Core führt keine Client-Stops aus. Keine zweite Core-Instanz auf dasselbe Konto starten. Diese Anleitung setzt eine bestehende 9.7.1 mit ihrem vollständigen Zustandsbund voraus.

## 1. Separat entpacken und zuerst offline prüfen

ZIP in `/home/georg/Georg` speichern. Kein bestehendes Verzeichnis überschreiben. Im Terminal als `georg`, nicht als root:

```bash
cd ~/Georg
if [ -e TradingBot_v9.7.3_NEXUS ]; then
  echo 'STOPP: Zielordner existiert bereits. Nicht darüber entpacken.'
else
  unzip TradingBot_v9.7.3_NEXUS.zip
fi
```

Nur bei einem frisch entpackten Ziel weiterarbeiten:

```bash
cd ~/Georg/TradingBot_v9.7.3_NEXUS
cat VERSION.txt
PY_OLD="$HOME/Georg/TradingBot_v9.7.1_NEXUS/.venv/bin/python"
"$PY_OLD" -c 'import sys, yfinance, pandas, numpy, fastapi, pytest; print(sys.version); print("yfinance:", yfinance.__version__)'
set -o pipefail
"$PY_OLD" volltest.py 2>&1 | tee "$HOME/nexus_9.7.3_volltest.log"
```

Erwartet: `9.7.3-NEXUS` und abschließend **VOLLTEST OK**, jede Prüfgruppe OK. Die Tests verwenden isolierte Testzustände. Dabei werden keine Brokerorders gesendet. Ein Fehler oder fehlendes echtes `yfinance` darf nicht durch Weglassen des Tests kaschiert werden. Die Importänderung der 9.7.3 entkoppelt reine Ausführungs-/Lesefunktionen vom historischen Datenanbieter; für dessen tatsächliche Verwendung bleibt das echte Paket erforderlich.

Die genannte alte Python-Umgebung muss tatsächlich existieren. Ist deine funktionierende Umgebung an einem anderen Ort, deren exakten Pfad verwenden. Noch nicht den Core oder eine neue WebUI starten. `Pi_Installieren.sh` ist **kein** isolierter Test: es ändert nach erfolgreicher Prüfung die gemeinsamen systemd-Units.

## 2. Tatsächlichen bisherigen Quellordner feststellen

```bash
systemctl show tradingbot-pi5.service -p WorkingDirectory -p ExecStart
systemctl show tradingbot-webui.service -p WorkingDirectory -p ExecStart
```

Der Quellordner muss dem zuletzt wirklich verwendeten Datenstand entsprechen. Nicht eine ältere 9.5.x-Kopie oder den rekonstruierten Arbeitsstand auswählen. Nachfolgend wird `/home/georg/Georg/TradingBot_v9.7.1_NEXUS` verwendet; nur übernehmen, wenn die Ausgabe dies bestätigt.

## 3. Beide Dienste stoppen, zusätzliche GUI/Core-Instanzen schließen, sichern

```bash
sudo systemctl stop tradingbot-pi5.service tradingbot-webui.service
systemctl is-active tradingbot-pi5.service tradingbot-webui.service
```

`inactive` beziehungsweise `failed` bedeutet hier nicht laufend. Bei `active`, `activating` oder `deactivating` noch nicht kopieren. Keine unbekannten Prozesse pauschal per kill beenden. Die WebUI ist währenddessen vorübergehend nicht erreichbar.

Alle weiteren Writer (beispielsweise separat gestarteter Tkinter-Core) schließen. Danach eine lokale private Sicherung erstellen. Diese kann Zugangsdaten enthalten; nicht öffentlich teilen:

```bash
umask 077
SOURCE="$HOME/Georg/TradingBot_v9.7.1_NEXUS"
BACKUP="$HOME/nexus_vor_9.7.3_$(date +%Y%m%d_%H%M%S).tar.gz"
tar --exclude='./.venv' --exclude='./__pycache__' --exclude='./.pytest_cache' \
  -czf "$BACKUP" -C "$SOURCE" .
printf 'Lokale Sicherung: %s\n' "$BACKUP"
```

Die vorhandenen SQLite-`-wal`-/`-shm`-Dateien nicht einzeln löschen. Der Kopierhelfer verwendet bei **beiden** SQLite-Datenbanken die SQLite-Backup-Schnittstelle und übernimmt damit auch bestätigte WAL-Inhalte.

## 4. Zusammengehörigen Zustand aus exakt dieser Quelle übernehmen

**Vor dem Installer und vor dem ersten WebUI-Start**, solange das Ziel noch keinen eigenen Handelszustand besitzt:

```bash
cd ~/Georg/TradingBot_v9.7.3_NEXUS
PY_OLD="$HOME/Georg/TradingBot_v9.7.1_NEXUS/.venv/bin/python"
SOURCE="$HOME/Georg/TradingBot_v9.7.1_NEXUS"
"$PY_OLD" settings_migration.py --strict "$SOURCE"
```

`--strict` verlangt einen anderen Quellordner, die vier wesentlichen Dateien (Ledger, eToro-Abgleich, Order-Registry und Fill-Fortschritt) und ein Ziel ohne bereits vorhandenen Handelszustand. Ein fehlgeschlagener Kopiervorgang ist ein Fehler, keine erfolgreiche Migration. Bei Abbruch **nicht starten**; die Quelle bleibt erhalten. Keine automatische Suche `--auto` und keine zweite Übernahme in bereits gemischte Zustände durchführen.

Weitere vorhandene Positions-, Risiko-, Broker-, WebUI-, Strategie- und Telegram-Einstellungen werden über den vorhandenen Migrationsweg übernommen. eToro wird auf Paper und OKX auf Demo zurückgesetzt; Live-Arming wird nicht übernommen. Das gilt auch, wenn die Quelle zuvor LIVE war. Die Quelle und dortige Brokerbestände werden nicht verändert.

Lesenden Abgleich ausführen:

```bash
"$PY_OLD" Nexus_Buchungsabgleich.py --source "$PWD" \
  --output "$HOME/nexus_9.7.3_buchungsabgleich_$(date +%Y%m%d_%H%M%S).json"
```

Der Bericht schreibt ausschließlich die neue Berichtsdatei, nicht das Ledger. In der geprüften Kombination aus älterem hochgeladenem DB-Snapshot und aktueller Reconciliation-Datei ergeben sich ADBE → Trade 36, CRM → Trade 37 sowie MSFT/SPGI → `LEGACY_AUDIT`. Das ist **keine** Garantie für einen inzwischen abweichenden Pi-Datenstand. Fehlende oder widersprüchliche Belege bleiben ausdrücklich gesperrt.

## 5. Neue Umgebung und Dienste installieren

```bash
chmod +x Pi_*.sh Nexus_*.sh WebUI_Starten.sh
./Pi_Installieren.sh
```

Der Installer prüft nochmals in der neuen `.venv` mit den gepinnten Abhängigkeiten. Erst bei **INSTALLATION UND TESTS ERFOLGREICH** weiterarbeiten. Er verändert keine WireGuard-Peers. Den Hinweis zur Einstellungsübernahme nicht ein zweites Mal ausführen: das ist bereits in Schritt 4 erledigt.

## 6. Zunächst WebUI, dann kontrollierter DEMO/Paper-Start

```bash
./Pi_WebUI_Aktivieren.sh
```

Falls noch kein WebUI-Benutzer vorhanden ist, vorher `./.venv/bin/python webui_setup.py` ausführen. Vorhandene migrierte Anmeldedaten nicht unnötig ersetzen. Seite vollständig neu laden. Erwartet: **9.7.3 NEXUS**. Auf jeder Seite sind eToro und OKX mit Laufzeitmodus, konfiguriertem Modus und Kontokürzel getrennt. Solange der Core gestoppt ist, ist der Worker nicht aktuell und keine grüne Handelsfreigabe zu erwarten.

Brokerkonfiguration **beider** Seiten auf DEMO/Paper prüfen; kein LIVE-Arming setzen. Erst danach den Core bewusst aktivieren:

```bash
./Pi_Service_Aktivieren.sh
./Pi_Service_Status.sh
```

Dieser Schritt aktiviert den Dienst samt Autostart und kann im Demokonto handeln. Auch `Nexus_Starten.sh --einmal` kann Orders senden und ist kein bloßer Verbindungstest.

## 7. Abnahme nach dem Start

Dashboard: Beide Broker besitzen eigene Zustands-, Bereitschafts- und Entscheidungsanzeigen. Die eToro-Buchungstabelle zeigt Zuordnung, historische Auditfälle und konkrete offene Belege. Alte Beobachtungszeitpunkte werden nicht als Handelsdatum ausgegeben. Das Auflösen einer Zuordnung ist keine Bestätigung unbekannter Gebühren.

Trades: Ergebnisse/Kurven bleiben getrennt nach Broker, gespeicherter Umgebung, Konto und Währung. Unbekannte oder vorläufige Nettowerte werden nicht in bestätigte Summen aufgenommen. Ein aktueller OKX-Kurs wird nicht einer eToro-Position oder einem anderen OKX-Konto zugeordnet. Kerzen und Abrechnungswerte unterschiedlicher Währungen werden ohne Umrechnungskurs nicht vermischt.

Logbuch: eToro, OKX oder System separat wählen. Nicht eindeutig einem Broker zuordenbare Systemmeldungen bleiben ausdrücklich Systemmeldungen.

Ein grüner Offline-Volltest ersetzt keine Broker-DEMO-Abnahme. Während einer Störung Schutzorders im Brokerkonto überprüfen. Eine erreichbare WebUI allein beweist keinen laufenden Handelsworker.

## Rückfall ohne Vermischung

Vor jedem Rückfall beide neuen Dienste stoppen. Den alten Datenstand nicht blind zurückspielen, wenn 9.7.3 inzwischen neue Orders/Trades verarbeitet hat: sonst fehlen diese im alten Zustand und Doppelverarbeitung ist möglich. In diesem Fall den neuesten kompletten Zustandsbund sichern und kontrolliert abgleichen. Nie alte und neue Cores parallel starten.
