# NEXUS 9.9.0

Den persoenlichen Installer `NEXUS_9.9.0_Installieren.sh` nach Downloads speichern.
Als georg starten:

```bash
bash "$HOME/Downloads/NEXUS_9.9.0_Installieren.sh"
```

Er prueft das Quellpaket und die eingebetteten vorhandenen OKX-Belege,
sichert den aktiven Dienstzustand und uebernimmt Einstellungen und Datenbanken
in ein gestopptes Staging. Die vorhandene Installation wird erst nach ihren
Pruefungen umgestellt. Der Installer verwendet den bisherigen DEMO-Ablauf.
Die erstmalige Belegkorrektur ist wiederholbar und sendet keine Brokerorders.

Nur das Paket pruefen, ohne Dienstaktion:

```bash
bash "$HOME/Downloads/NEXUS_9.9.0_Installieren.sh" --paket-pruefen
```

Der persoenliche Installer enthaelt private Handelsbelege. Das separate
Quell-ZIP enthaelt keine Zugangsdaten oder Handelsdatenbanken.

Nach dem Start muessen Core und WebUI den Build
`9.9.0-REFERENCE-RECONCILIATION` melden. FMP-Tarifmodus und bestehende
PULSAR-/Handelseinstellungen werden uebernommen. Der automatische Free-
Rueckfall bleibt wirksam. Bereits verarbeitete Freqtrade-Kerzen und die
Historie werden bei weiteren Updates uebernommen.

Freqtrade-Referenz ist die offizielle SampleStrategy des beigefuegten
2026.8-dev-Standes. NEXUS bildet deren Signale und die geprueften Exit-
Entscheidungen nach; Risiko-/Belegpruefungen, FOK und Broker-OCO bleiben Teil
der NEXUS-Ausfuehrung. Bestehende bekannte Strategiehashes werden nachvollziehbar
migriert, unbekannte nicht automatisch freigeschaltet.

Fuer die tatsaechliche PEP-Schutzbestaetigung, die sechs eToro-Gebuehrenfaelle
und den Nachweis eingegangener GPT-Antworten wird der aktuelle Pi-Export
`NEXUS_9.9.0_Diagnose.py` benoetigt. Fehlende Brokerwerte werden weiterhin
als unbekannt gezeigt. Offline-Tests ersetzen diese Betriebsabnahme nicht.
