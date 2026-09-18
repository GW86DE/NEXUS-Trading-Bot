# TradingBot 8.1.2 NEXUS

NEXUS 8.1.2 ist ein Stabilitäts- und Bedienrelease für Raspberry Pi 5. Es behält die bewährte Trennung bei:

- eToro handelt ausschließlich Aktien/ETF.
- OKX handelt ausschließlich Krypto-Spot/Cash.
- Beide Broker besitzen getrennte Laufzeiten, Verbindungszustände und Risikotöpfe.
- WebUI, WireGuard und Trading-Core sind unabhängige Dienste.
- KI darf nur bereits qualifizierte Werte priorisieren und dokumentieren; sie darf keinen Kauf freigeben oder blockieren.

Der Hotfix 8.1.2 behebt außerdem den 8.1-Installer-Abbruch, bei dem eine bereits
erzeugte `web_ui_settings.json` fälschlich als Release-Verunreinigung gewertet
wurde. Der Installationslauf ist jetzt wiederholbar und die WebUI-Aktivierung
kann eine nach einem Teilabbruch fehlende systemd-Unit selbst nachinstallieren.

## Wichtigste Änderungen gegenüber 8.0

- 8.0-Installationsfehler behoben; der komplette Offline-Test läuft vor der Dienstinstallation.
- Der Installer erkennt eine sichere LAN-/WireGuard-Adresse und verändert vorhandene WireGuard-Keys/Peers niemals.
- WebUI zeigt Pi-Temperatur, Uptime, CPU, RAM, Speicher und Warnungen.
- Marktkarte zeigt Öffnungs-/Schlusszeiten, Zeitzone und nächste US-Sitzung.
- Kaufentscheidungen und Ablehnungen sind mit Speicherort, Gründen und letzten Einträgen sichtbar.
- eToro und OKX besitzen explizite read-only Demo- und LIVE-Verbindungstests.
- Demo/LIVE-Auswahl je Broker; eine LIVE-Auswahl entfernt jedes Arming und genügt nie zum Handeln.
- Konservativ, ausgewogen und offensiv sind in der WebUI sichtbar und umschaltbar. OFFENSIV benötigt eine zweite Bestätigung.
- Alte Projekte können aus der WebUI übernommen werden. Der Quellordner bleibt unverändert; das Ziel wird anschließend auf Demo/Paper zurückgesetzt.
- Telegram bietet `/menu`, `/version`, `/stats`, `/performance`, `/locks`, `/telegram`, sessiongebundene Bestätigungen, Update-Idempotenz, Audit und getrennte Sende-/Command-Gesundheit.
- Täglich 18:00 Uhr Europe/Berlin: ausführlicher Telegram-Bericht plus lesbare Datei aller Kaufentscheidungen/Ablehnungen mit Begründung.

## Sicherheitsmodell für LIVE

Die Auswahl `LIVE` in der WebUI wählt nur den passenden Schlüsselsatz. Neue Echtgeldorders bleiben gesperrt, bis lokal eine kurzlebige Freigabe gesetzt wurde. Migration, Moduswechsel und Installation entfernen diese Freigaben.

```bash
./.venv/bin/python broker_live_arming.py etoro status
./.venv/bin/python broker_live_arming.py okx status
```

OKX-LIVE akzeptiert nur Spot/Cash und blockiert API-Keys mit Withdraw-Rechten. eToro-LIVE bleibt zusätzlich durch die vorhandene eToro-Arming-Sicherung geschützt. Ausstiege und Schutzpfade dürfen durch eine Kaufpause nicht blockiert werden.

## Entscheidungen

Die führende Historie ist `decision_history.sqlite`; `decision_journal.jsonl` ist der lesbare Spiegel. Protokolliert werden ernsthafte Kaufkandidaten ab technischem Signal, einschließlich Freigaben, Ablehnungen, Blockierer, Quellen und Ausführungsstatus. Ein normaler Scan ohne Signal erzeugt bewusst keinen Datensatz und wird daher nicht irreführend als „Ablehnung“ gezählt.

## Start

Die vollständige Schrittfolge steht in `INSTALLATIONSANLEITUNG_NEXUS_8.1.2_DE.md`.

```bash
./Pi_Installieren.sh
./.venv/bin/python webui_setup.py
./Pi_WebUI_Aktivieren.sh
```

Den Trading-Core erst nach Datenübernahme, Broker-Verbindungstests und Kontrolle von Demo/Paper aktivieren:

```bash
./Pi_Service_Aktivieren.sh
```

## Netzwerk

Im Heimnetz und über 5G/WireGuard kann dieselbe private Pi-LAN-Adresse verwendet werden, wenn sie im Client unter `AllowedIPs` steht. In der FRITZ!Box wird nur UDP `51820` weitergeleitet. TCP `8780` darf niemals öffentlich freigegeben werden.

Details: `WIREGUARD_VPN_EINRICHTUNG_DE.md`.
