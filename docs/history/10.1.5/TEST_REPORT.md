# NEXUS 10.1.5 – abgeschlossene Prüfung

Stand: 15.09.2026. Die vollständige isolierte Prüfung `volltest.py` ist erfolgreich: **3048 Tests und 277 Untertests bestanden**, 3 bedingte Archiv-/Startertests übersprungen, 7 Warnungen zu Bibliotheksabkündigung bzw. Fork in mehrfädigen Tests.

Release-Hygiene, echte WebUI-Testabhängigkeiten, sämtliche pytest-Regressionen, Selbsttest, Python-Syntax, plattformneutraler Pi-Preflight und Shell-Syntax sind erfolgreich. Quelle und Testkopie sind über SHA256 gebunden; die Testumgebung übernimmt keine Brokerzugangsdaten und sperrt externe HTTP-/Socket-Aufrufe.

Prüfumgebung: Linux x86_64, Python 3.12.14, pytest 9.0.2. Ein Testlauf wurde vor Abschluss unterbrochen und zählt nicht als Nachweis. Der abschließende vollständige Lauf ist erfolgreich. Nach der Prüfung des Programmcodes wurden nur diese Dokumentation und die Paketmanifeste ergänzt; der Paketbau vergleicht sämtliche geprüften Programmdateien erneut.

## PEP und Buchhaltung

- Wiederholung der bereitgestellten Originaldaten mit 27 eToro-Tradezeilen und dem PEP-Historienbeleg: Trade 54, Position 3597440106, 109 Einheiten, 136,35/138,59 USD, 244,16 USD reiner Kursgewinn und 1 USD belegte Einstiegskosten.
- Passende Barbestände liefern eine Vorschau von 1 USD abgeleiteten Abschlusskosten und 242,16 USD Netto. Solange der Nutzer nicht ausdrücklich bestätigt, dass im Messintervall keine anderen Geldbewegungen vorlagen, bleibt das Ergebnis unbestätigt.
- Konto, DEMO/LIVE, Position, Historie, volle Menge, Währung und Zeitbelege werden geprüft. Andere Trades im Messintervall, Teilabschlüsse, abweichende Hashes, veränderte Vorschauen oder bereits native bestätigte Kosten erlauben keine blinde Nachbuchung.
- Bestätigung und ursprünglicher Datensatz werden atomar gespeichert. Wiederholte Bestätigung ist wirkungslos. Der Risikoabgleich übernimmt das Ergebnis einmalig zum Verkaufstag; andere Sperren und Tagesperioden werden nicht zurückgesetzt.
- Die fehlende TP-Schließorder-ID wird nicht aus dem Sonntagsauftrag ergänzt. `proceeds` bleibt ohne belegten Netto-Geldumfang eine gesonderte Beobachtung.

## Aufträge, Oberfläche und Diagnose

- Annahme/Teilausführung/ausgeführte Teilmenge mit storniertem oder abgelehntem Rest werden getrennt behandelt. Der Status `PartiallyFilled` beendet keinen noch unbelegten Restauftrag.
- Storno: HTTP-Annahme ist kein Stornobeweis; Fill während Stornierung, Antwortverlust, Neustart, falsches Konto, unbekannter Status und 404 führen nicht zu erfundenen Nullfills oder doppeltem Versand.
- WebUI: echte FastAPI-Endpunkttests prüfen Anmeldung, CSRF, strenge Bestätigung, Bindung an die aktuelle Vorschau und die Vormerkung ohne Brokerzugriff. JavaScript-Verhalten prüft Tabellen/Dialog, Maskierung, Checkbox und Bestätigungsanfrage.
- Diagnose: Nutzerabrechnungen, Barbestände, Stornos und unklassifizierte Erlöse werden getrennt ausgegeben. Metadaten enthalten keine privaten Ereignisdetails aus dem Test.
- X/PULSAR-Regressionen bleiben vollständig enthalten. Ein alter zeitabhängiger Test wurde auf korrekte Tagesendbelege umgestellt und um die Mitternachtsgrenze ergänzt. Ein vor Tagesende abgeholter Wert gilt weiterhin nicht als vollständiger Tag.

## Lieferpaket und Grenzen

Die allgemeinen drei übersprungenen Tests verlangen gesonderte Starter-/Archivparameter. Die beiden Startertests werden zusätzlich am tatsächlich erzeugten Diagnose-Starter ausgeführt; das Ergebnis steht im separat mitgelieferten Umsetzungs- und Prüfbericht. PEP-Originaldaten sind unabhängig davon im allgemeinen Test enthalten.

Keine Installation auf Georgs Raspberry Pi, kein realer Brokerauftrag, kein Telegram-Versand und keine kostenpflichtige Quellenabfrage wurden ausgeführt. Eine ARM64-/Python-3.13- oder visuelle Browserabnahme wird nicht behauptet. Vollständige native Gebührenbelege werden automatisch verarbeitet; bei fehlendem Umfang ersetzt auch diese Version einen Beleg nicht durch eine angenommene Nullgebühr.

Die Bedienung und der konkrete PEP-Abschlussweg stehen in `INSTALLATIONSANLEITUNG_NEXUS_10.1.5_DE.md`. Die fachlichen Änderungen stehen in `docs/NEXUS_10.1.5_Aenderungen.md`.
