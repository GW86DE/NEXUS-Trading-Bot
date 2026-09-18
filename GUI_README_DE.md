# Lokale Fallback-GUI — TradingBot 8.1.1 NEXUS

Die responsive WebUI ist die primaere Oberflaeche. Die Tkinter-GUI bleibt als
lokaler Wartungs-Fallback erhalten. Sie verwaltet eToro, OKX, Telegram, Risiko,
News und KI; den aktuellen Gesamtstatus zeigt die WebUI vollstaendiger.


## Erster Start nach einem Update

Auf dem Raspberry Pi zuerst `./Pi_Installieren.sh` ausfuehren. NEXUS richtet
Trading-Core und WebUI-Dienst ein, startet oder aktiviert sie aber noch nicht.
Danach alte Einstellungen uebernehmen, `webui_setup.py` ausfuehren und beide
Broker in Paper/Demo pruefen. Erst anschliessend mit
`./Pi_Service_Aktivieren.sh` den Autostart bewusst freigeben.

## KI-Rollen

Die OpenAI-Einstellungen sind jetzt in drei getrennte Rollen aufgeteilt:

- **Aufmerksamkeit:** priorisiert nur die Scan-Reihenfolge bereits qualifizierter Werte.
- **Woechentliches Research:** erzeugt nur neue Aktienvorschlaege; keine automatische Aufnahme.
- **Strategy Analyst:** interpretiert optional lokal voraggregierte Decision-History-Statistiken; kein Web und keine automatische Parameteranpassung.

Keine dieser Rollen besitzt Kauf-/Verkaufsrechte.

## Telegram-Human-Gate

Bei der Telegram-Einrichtung werden Bot-Token, Ziel-Chat-ID und eine **freigegebene User-ID** gespeichert. Freigabe-Buttons fuer neue Research-Werte funktionieren nur, wenn Chat und Benutzer uebereinstimmen. Ein privater Chat mit dem Bot wird empfohlen; dort sind Chat-ID und User-ID normalerweise identisch.

Der erste Button **„Pruefen lassen“** nimmt nichts auf. Nach bestandener deterministischer eToro-Pruefung erscheint ein zweiter Button **„Ins Universum aufnehmen“**. Auch danach wird der Wert erst beim naechsten Botstart aktiv und erneut von eToro qualifiziert.

## Weitere Grundsaetze

- Aktien laufen nur ueber **eToro**, Spot-Krypto nur ueber **OKX EEA**.
- LIVE-Handel braucht je Broker eine getrennte, kurzlebige Freigabe.
- Favoriten erhoehen nur die Beobachtungsprioritaet.
- Alle Sicherheits-, Risiko-, Kosten-, Liquiditaets-, Marktzeit-, News-/Event- und Portfoliofilter bleiben aktiv.
- Trade-Telegrammeldungen werden persistent gepuffert.

Nach wesentlichen Konfigurationsaenderungen den Bot neu starten und Status/Logs kontrollieren.

## RISK3-Bestaetigung

Das offensive Profil wird per Telegram nicht mehr durch einen einzelnen `/risk3`-Befehl aktiviert. Erst ein zweiter autorisierter, kurzlebiger Einmal-Button schaltet RISK3 frei.
