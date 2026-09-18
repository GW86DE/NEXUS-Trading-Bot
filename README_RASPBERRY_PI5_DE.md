# Raspberry Pi 5 / 8 GB — TradingBot NEXUS 9.0

Diese Ausgabe ist fuer 64-Bit Raspberry Pi OS auf einem Raspberry Pi 5 ausgelegt.
Sie betreibt eToro-Aktien und OKX-EEA-Spotkrypto in getrennten Prozessen und
Risikodomaenen. Die aktuelle Schnellstart- und Sicherheitsanleitung steht in
[`README_V8_1_1_NEXUS_DE.md`](README_V8_1_1_NEXUS_DE.md). Die folgenden Abschnitte
beschreiben ergaenzende Pi-Betriebshinweise.

## Installation und sauberer Versionswechsel

```bash
cd /pfad/zum/TradingBot_v9.0_NEXUS
chmod +x Pi_*.sh
./Pi_Installieren.sh
```

`Pi_Installieren.sh` prueft zuerst Python >= 3.11 und die Vollstaendigkeit des Pakets, richtet dann Python-Umgebung, Abhaengigkeiten, Tests, Desktop-Starter und die systemd-Unit ein. Auf dem Desktop wird dabei auch **TradingBot GUI** angelegt; der Desktop-Pfad wird ueber XDG ermittelt (mit Fallback fuer `Schreibtisch`/`Desktop`). **Der neue Bot wird danach absichtlich weder gestartet noch fuer Autostart aktiviert.** Ein eventuell vorhandener alter `tradingbot-pi5.service` wird beim erfolgreichen Umschalten gestoppt und deaktiviert.

Danach ist die Reihenfolge fuer ein Update verbindlich:

1. GUI oeffnen: `./Pi_GUI_Starten.sh`
2. In der GUI **Einstellungen uebernehmen** und den Ordner der alten Version waehlen.
3. eToro, Telegram, Risikoprofil, Handelsmodus und KI-Einstellungen kontrollieren.
4. Zunaechst Demo/Paper verwenden.
5. Erst danach Dienst und Autostart bewusst einschalten: `./Pi_Service_Aktivieren.sh`

Bis Schritt 5 laeuft der neue Traderprozess nicht und startet auch nach einem Raspberry-Pi-Neustart nicht automatisch. `./Pi_Service_Starten.sh` startet nur fuer die aktuelle Sitzung; `./Pi_Service_Aktivieren.sh` aktiviert zusaetzlich den Autostart.

## Pflicht-Testabhaengigkeit

`./Pi_Installieren.sh` installiert neben den Runtime-Abhaengigkeiten auch `requirements-test.txt` mit gepinntem `pytest`, bevor der verpflichtende Volltest gestartet wird. Ein frisches Pi-Setup benoetigt daher keine manuelle pytest-Installation.

## News/FMP in 8.1.1 NEXUS

FMP verwendet die aktuelle Stable API. Bei gesetztem FMP-Key und aktiviertem FMP-Schalter wird FMP nicht mehr vom Legacy-Quellen-Master deaktiviert. Der gemeinsame Marktfeed wird gecacht; gezielte FMP-Aktiennews werden nur fuer bereits relevante Kandidaten bzw. gehaltene Positionen geladen. HTTP-429-/Quota-Fehler gehen in einen Backoff, statt bei jedem Scannerzyklus erneut angefragt zu werden. Alpha-Vantage-News sind gezielt standardmaessig deaktiviert, damit das knappe Tageskontingent fuer Earnings/Fundamentaldaten erhalten bleibt.

Die Werkzeuge `news_check.py`, `research_snapshot.py` und `crypto_diagnose.py` sind Bestandteil des Release-Pakets. Interaktive Eingabeaufforderungen werden in der GUI zeichenweise weitergereicht, sodass auch Prompts ohne Zeilenumbruch sichtbar sind.

## Telegram-Freigabe

Fuer Universumsfreigaben wird ein privater Chat mit dem TradingBot empfohlen. Das Setup speichert neben der Chat-ID eine erlaubte Telegram-User-ID. Nur wenn **beide** passen, werden Inline-Callbacks angenommen.

Fuer `/risk3` gilt zusaetzlich eine zweite Sicherheitsstufe: Der Befehl selbst aendert nichts. Erst ein autorisierter Einmal-Button (Chat-ID + User-ID), standardmaessig 60 Sekunden gueltig, aktiviert das offensive Profil.

Woechentliches Research kann Vorschlaege senden. Die Aufnahme braucht zwei getrennte menschliche Schritte: erst technische Pruefung anfordern, danach bei PASS final aufnehmen. Die final aufgenommene Aktie wird erst nach einem Bot-Neustart aktiv und muss dann die normale eToro-Startqualifizierung bestehen.

## systemd / Zustand

Die Pi-Skripte richten einen einzelnen systemd-Dienst ein. Der Single-Instance-Lock verhindert parallele Traderprozesse. PAUSE blockiert neue Kaeufe; bestehende Schutz-/Verkaufslogik bleibt aktiv. STOPP beendet den Prozess vollstaendig.

## LIVE-Freigabe

LIVE bleibt zweistufig verriegelt: Handelsmodus plus kurzlebiges separates Arming. Ohne gueltiges Arming kein Echtgeldhandel.

```bash
python handelsmodus.py
```

## Update-Abnahme

1. Alten Bot auf PAUSE/STOPP setzen und bisherigen Ordner sichern.
2. Neue Version in einen separaten Ordner entpacken.
3. `./Pi_Installieren.sh` ausfuehren und bis zur Meldung **BOT NOCH NICHT GESTARTET** warten.
4. `./Pi_GUI_Starten.sh` oeffnen.
5. In der GUI **Einstellungen uebernehmen** und den alten Bot-Ordner waehlen.
6. eToro, Telegram, Risikoprofil, Handelsmodus und KI-Einstellungen kontrollieren.
7. Erst danach `./Pi_Service_Aktivieren.sh` ausfuehren. Fuer die erste Abnahme Demo/Paper verwenden.
8. FLR.US-Instrumentidentitaet, Fill-/Orderjournal, eToro Positions-ID, Schutzwerte und Telegram-Trade-Meldungen kontrollieren.
9. Optional einen Research-Vorschlag komplett durch beide Human-Gate-Stufen testen; sicherstellen, dass er vor Neustart nicht im aktiven Universum erscheint.
10. Strategy-Bericht pruefen. LIVE erst danach bewusst neu armen.

## Grenze der Offline-Pruefung

Der Release-Test simuliert kritische Pfade mit Testdoubles. Er beweist keine echte eToro-Netzwerkorder, keine echte Telegram-Zustellung/Callback-Interaktion, keinen echten OpenAI-Web-Research-Lauf und keinen ARM64-Hardwarelauf auf deinem konkreten Pi.
