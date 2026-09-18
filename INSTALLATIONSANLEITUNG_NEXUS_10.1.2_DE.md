# NEXUS 10.1.2 auf dem Raspberry Pi

Voraussetzung: vorhandene NEXUS-DEMO/Paper-Installation mit beiden eingerichteten systemd-Diensten, Raspberry Pi OS 64 Bit/ARM64 und Python ab 3.11. Dieses Paket wurde hier offline geprüft; die praktische Abnahme auf deinem Pi folgt nach der Installation.

## Update

Speichere den eigenständigen Installer in Downloads. Er enthält das vollständige Quellpaket. Ein zusätzliches manuelles Entpacken ist nicht nötig.

```bash
bash ~/Downloads/NEXUS_10.1.2_Installieren.sh --paket-pruefen
bash ~/Downloads/NEXUS_10.1.2_Installieren.sh
```

Als normaler Pi-Benutzer starten, nicht mit `sudo bash`. Benötigte Systemrechte fordert der bestehende Updater selbst an. Das Terminal bis zur Abschlussmeldung geöffnet lassen. Den alten Botordner vorher nicht löschen oder selbst kopieren.

Der Installer erkennt die Quelle aus den beiden Diensten `tradingbot-pi5.service` und `tradingbot-webui.service`. Beide müssen denselben bestehenden Quellordner nennen. Das neue Ziel ist `~/Georg/TradingBot_v10.1.2_NEXUS`. Der Ablauf prüft und sichert Software, Handelsdaten und Dienste, übernimmt den bestehenden Zustand, installiert die Dienstpfade und startet WebUI und DEMO/Paper-Core. Eine LIVE-Konfiguration wird vom bestehenden Updater abgewiesen; sie wird nicht still umgestellt.

Erwartete Erfolgsmeldung: `UPDATE UND STARTKONTROLLE ERFOLGREICH:`. Eine weiterhin sachlich begründete Handelssperre ist getrennt vom Installationserfolg zu bewerten.

| Option | Wirkung |
|---|---|
| ohne Option | gesichertes Update mit Tests und Dienststart |
| `--paket-pruefen` | nur eingebettete Prüfsummen prüfen; keine Dienstaktion |
| `--plan` | Paket entpacken und Quelle/Ziel prüfen; noch keine Dienstumstellung |
| `--nur-entpacken` | nur Quellpaket ins neue Ziel entpacken; keine fertige Installation |

## Prüfen

```bash
systemctl status tradingbot-pi5.service tradingbot-webui.service --no-pager
systemctl show tradingbot-pi5.service tradingbot-webui.service --no-pager -p WorkingDirectory
```

In der bisherigen WebUI-Adresse die Seite aktualisieren. Die Oberfläche soll 10.1.2 anzeigen. Das neue Menü **Diagnose** ist auch im Auswahlmenü auf dem Handy enthalten.

Prüfe eToro-Kontozuordnung und Risikoperiode, PEP-Menge/Schutz und einen möglicherweise noch offenen Schließauftrag getrennt. Die vorliegenden historischen PEP-Belege bestätigen bereits die Übernahme von Stop 135,90 und Ziel 138,52 USD. Diesen Wartungsschritt deshalb nicht routinemäßig wiederholen. Maßgeblich für den Betrieb ist der jeweils neue Brokerabgleich.

Die neue Risikoperiode startet nur beim bekannten unzugeordneten Altzustand, ohne heutige lokale Geldaktivität, ohne vorhandene Tagesbasis und nach vollständigem frischem Kontoabgleich. Das Datum ist der erste belegte Zeitpunkt der neuen Periode, nicht ein behaupteter Mitternachtskontowert. Ist eine Voraussetzung nicht erfüllt, nennt NEXUS weiter die fachliche Sperre. Ein Verlustlimit, eine Abkühlzeit oder ein offener Auftrag wird dadurch nicht gelöscht.

## Diagnose in der WebUI

1. **Diagnose** im Menü auswählen.
2. Bei Bedarf **Fertige ZIP zusätzlich über Telegram senden** einschalten. Der Schalter ist zunächst aus und nutzt ausschließlich den schon eingerichteten Telegram-Chat.
3. **Sofort starten** oder **30 Minuten beobachten** wählen.
4. Nach Fertigstellung **ZIP herunterladen** wählen. Eine bisher nicht versendete ZIP kann außerdem über ihren eigenen Versandknopf an Telegram geschickt werden.

Die Start-/Endsammlung benötigt zusätzliche Zeit; beim 30-Minuten-Modus beginnt die volle Beobachtungsdauer erst nach der Startsammlung. Ein weiterer WebUI-Lauf wird solange abgewiesen. Die Seite kann geschlossen werden. Ein Dienstneustart kann die Sammlung unterbrechen; ein unterbrochener Lauf wird als solcher angezeigt.

Ablage unverändert: `~/Downloads/NEXUS_Diagnosen`. Berichte bleiben dort auch bei einem Versionswechsel. Die Jobverwaltung liegt ebenfalls außerhalb des Versionsordners. Telegram-Ausfälle verhindern die lokale Ablage nicht. Bei mehr als 50 MB wird die ZIP nur lokal bereitgestellt. Ist unklar, ob Telegram die Datei angenommen hat, gibt es keinen automatischen Neuversand.

## Diagnose im Terminal

30 Minuten:

```bash
bash ~/Georg/TradingBot_v10.1.2_NEXUS/NEXUS_Diagnose_Starten.sh --minuten 30
```

Sofort:

```bash
bash ~/Georg/TradingBot_v10.1.2_NEXUS/NEXUS_Diagnose_Starten.sh --sofort
```

Der eigenständige Diagnosestarter ist auch vor dem Update nutzbar:

```bash
bash ~/Downloads/NEXUS_10.1.2_Diagnose_Starten.sh --minuten 30
```

Die Diagnose führt selbst keine Broker-/KI-Abfragen und keinen Handel aus. Der laufende Bot wird dadurch nicht angehalten.

## PEP manuell beobachten lassen

Ein beim Broker storniertes Einzelgeschäft ist keine dauerhafte Verwaltungseinstellung in NEXUS. Soll der Bot PEP nur beobachten, die bestehende Positionsfunktion **Beobachten** in NEXUS verwenden. Eine bereits vorhandene Brokerorder muss trotzdem separat bis zum Abschluss/Storno abgeglichen werden; ein Screenshot des Stornodialogs ersetzt diesen Abschlussbeleg nicht.

## Bei Updatefehlern

Die bestehende Updatejournalführung erhält den bisherigen Dienst-/Autostartzustand und sichert die Handelsdaten. Vor dem ersten Start der neuen Dienste kann der Updater die alten Dienste zurückstellen. Nach dem ersten Start könnten bereits neue Brokerereignisse verarbeitet sein: Deshalb gibt es dann keine automatische Rückkopie alter Handelsdaten.

Bei einem Fehler nach Dienststart `nexus_update.log`, `nexus_update_state.json` und eine passive Diagnose erhalten. Den neuen Ordner nicht löschen und keine alte Sicherung über den neuesten Handelszustand kopieren. Die genaue Fehlermeldung bestimmt die Wiederherstellung. Es wurde hier kein realer Stromausfall oder Dienstwechsel auf deinem Pi ausgeführt.
