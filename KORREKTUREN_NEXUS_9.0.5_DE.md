# NEXUS 9.0.5 – OKX-Verbindung und PDF-Abgleich

## Ursache des gemeldeten Ausfalls

Im Diagnoseprotokoll war die lokale Systemzeit beim Start um rund 195 Sekunden
verschoben. OKX akzeptiert private, signierte Anfragen nur in einem engen
Zeitfenster. Die anschließende HTTP-401-Antwort mit Zeitfehler wurde in 9.0.4
fälschlich wie ein dauerhaft falscher API-Key behandelt. Der BrokerHub sperrte
daraufhin automatische Versuche für eine Stunde und `starte_krypto()` beendete
den Krypto-Worker vollständig. eToro, WebUI und der systemd-Dienst liefen weiter;
deshalb sah das Gesamtsystem aktiv aus, obwohl OKX nicht mehr arbeitete.

## Abgleich mit „OKX Websocket API.pdf“

Die PDF ist ein 977-seitiger Bilddruck des OKX-API-Handbuchs. Für diese
Korrektur wurden die verbindungsrelevanten Kapitel zu EEA-Endpunkten, REST-
Authentifizierung, Serverzeit, WebSocket-Login, Verbindungsverwaltung,
Ping/Pong, Abonnements, Wartungsereignissen und Account-Instrumenten geprüft.

| Vorgabe aus der PDF | Stand 9.0.4 | Umsetzung 9.0.5 |
|---|---|---|
| Für EEA-Konten `eea.okx.com` und EEA-WebSockets verwenden | bereits korrekt | unverändert |
| Private REST-Zeit maximal etwa 30 Sekunden abweichend; vorher `/public/time` nutzen | Serverzeit gelesen, aber Demo lief trotz großer Abweichung weiter | Zeitoffset gemessen; Demo und Live blockieren transient bei zu großer Abweichung |
| REST-Signatur: `timestamp + METHOD + requestPath + body` | korrekt | korrekt beibehalten, Zeit aus gemessenem Offset |
| Demo-REST mit `x-simulated-trading: 1` | korrekt bei privaten Calls | unverändert |
| WebSocket-Login mit Unix-Sekunden und `/users/self/verify` | Signatur korrekt, Zeit nur lokale Uhr | Login nutzt den REST-gemessenen Offset |
| Nach weniger als 30 Sekunden Inaktivität Text `ping`, Antwort `pong` | Text-Ping plus WebSocket-Frame, aber kein harter pong-Nachweis | nur dokumentierter Text-Ping; fehlendes pong erzwingt Reconnect |
| `notice` kündigt Wartung etwa 60 Sekunden vorher an | ignoriert | Status DEGRADED und sofortiger geordneter Reconnect |
| Verbindungslimit-Ereignisse auswerten | ignoriert | Count wird protokolliert, Count-Error reconnectet |
| Login/Subscribe begrenzen und alphanumerische IDs verwenden | Backoff und gültige ID bereits korrekt | beibehalten |
| Account-Instrumente und `tradeQuoteCcyList` berücksichtigen | seit 9.0.4 korrekt | unverändert; Unified-USD bleibt aktiv |

## Sicherheitsverhalten

Ein Zeitfehler ist jetzt wiederholbar (`Zeitabweichung` erbt von einem
temporären Verbindungsfehler), ein echter Schlüssel-/Passphrasefehler bleibt
hingegen dauerhaft gesperrt. Bei fehlender oder veralteter Verbindung gilt
fail-closed: keine neuen Käufe. Es werden keine Orders automatisch wiederholt;
die bestehende `OrderStatusUnklar`-/`clOrdId`-Absicherung bleibt unverändert.

Die WebUI unterscheidet nun drei Aussagen:

1. **Worker aktiv** – der Krypto-Thread schreibt aktuelle Heartbeats.
2. **Broker authentifiziert** – ein aktueller privater OKX-Kontakt ist vorhanden.
3. **Einmaliger API-Test** – eine manuelle Read-only-Prüfung, die den Worker
   nicht ersetzt.

Nur 1 und 2 gemeinsam erlauben ONLINE beziehungsweise KÄUFE FREI.

