# NEXUS 8.1.1: WebUI im Heimnetz und über WireGuard

NEXUS 8.1.1 übernimmt eine vorhandene WireGuard-Konfiguration unverändert. Der Installer liest höchstens, ob `wg0` und private Adressen vorhanden sind. Er erzeugt, ersetzt oder löscht keine Schlüssel, Peers oder `/etc/wireguard/wg0.conf`.

## Bewährter Aufbau

- Raspberry Pi im Heimnetz: feste private Adresse, zum Beispiel `192.168.178.60`
- WireGuard-Server auf dem Pi: zum Beispiel `10.77.0.1/24`, UDP `51820`
- WebUI: an die private Pi-LAN-IP gebunden, Port `8780`
- FRITZ!Box: ausschließlich UDP `51820` an den Pi weiterleiten
- Niemals TCP `8780` ins Internet freigeben

Damit wird im WLAN und unterwegs dieselbe WebUI-Adresse verwendet:

```text
http://192.168.178.60:8780
```

Unterwegs muss WireGuard aktiv sein und der Client die Pi-LAN-IP durch den Tunnel routen:

```ini
[Peer]
PublicKey = PI_PUBLIC_KEY
Endpoint = DEIN_DNS_NAME:51820
AllowedIPs = 10.77.0.1/32, 192.168.178.60/32
PersistentKeepalive = 25
```

Jedes Gerät erhält ein eigenes Schlüsselpaar und eine eigene Tunnel-IP. iPhone- und Laptop-Schlüssel dürfen nicht wiederverwendet werden.

## WebUI-Netzmodus wählen

Automatische Erkennung (vorhandene sichere Einstellung bleibt erhalten):

```bash
./.venv/bin/python webui_network_setup.py --mode auto
```

Alternativen:

```bash
# Heimnetz plus WireGuard-Route – empfohlen
./.venv/bin/python webui_network_setup.py --mode lan

# Nur direkt über die wg0-Adresse
./.venv/bin/python webui_network_setup.py --mode vpn

# Nur auf dem Pi
./.venv/bin/python webui_network_setup.py --mode local
```

`0.0.0.0`, öffentliche IP-Adressen und DNS-Namen werden als Bind-Adresse blockiert.

## Dienste und Neustart

```bash
sudo systemctl enable --now wg-quick@wg0
./Pi_WebUI_Aktivieren.sh
sudo reboot
```

Nach dem Neustart prüfen:

```bash
sudo systemctl is-active wg-quick@wg0
sudo systemctl is-active tradingbot-webui.service
./WireGuard_Status_Pruefen.sh
```

Die beiden Ausgaben sollten `active` sein. Der Trading-Core ist ein separater Dienst und wird durch das Aktivieren der WebUI nicht gestartet.

## Wichtige Sicherheitsregeln

- Private Schlüssel und QR-Codes niemals in Screenshots, Chats oder Logs teilen.
- In der FRITZ!Box nur UDP `51820`, niemals TCP `8780`, freigeben.
- WebUI-Login und ein langes Passwort bleiben Pflicht.
- LIVE-Handel wird weder durch WireGuard noch durch die WebUI automatisch aktiviert.
