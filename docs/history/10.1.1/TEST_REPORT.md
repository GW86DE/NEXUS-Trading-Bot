# NEXUS 10.1.1 – Prüfbericht

**2.722 Tests und 253 Untertests bestanden. Alle sieben Volltest-Stufen erfolgreich.** Der eigenständige Installer und das Diagnosewerkzeug wurden zusätzlich gesondert geprüft. Die Version ist für das kontrollierte DEMO/Paper-Update erstellt. Uneingeschränkte Live-Handelsfreigabe: **NEIN**.

## Endgültiger gemeinsamer Lauf

- Lauf: `release1011_final_3318984db6`, Python 3.12.14, x86_64-Testhost.
- Pytest: 2.722 bestanden, 0 fehlgeschlagen, 253 Untertests bestanden; 66,17 Sekunden. Gesamtlauf: 72,2 Sekunden.
- Drei Tests mit externen Eingaben werden im allgemeinen Quellpaketlauf übersprungen: zwei Prüfungen der separat ausgelieferten Diagnose-SH und ein Replay der privaten Original-Diagnose-ZIP. Alle drei wurden mit den konkreten Eingaben im separaten Diagnosesatz erfolgreich durchgeführt; dort insgesamt 24 Tests, keine übersprungen.
- Keine Netzwerkereignisse unter aktivem HTTP-/Socket-Schutz. Testumgebung ohne übernommene Brokerzugänge. Echte FastAPI/Starlette-TestClient- und JavaScript-Renderer-Prüfungen; keine Ersetzung dieser Bibliotheken durch Attrappen.
- Sieben bestehende Warnungen: Starlette/AnyIO-Deprecation sowie Multiprocessing-Hinweise zum Testhost. Keine unterdrückte fehlgeschlagene Prüfung.

| Volltest-Stufe | Ergebnis |
|---|---|
| Statische Release-Hygiene und Versionskonsistenz | OK |
| Testabhängigkeiten und echter WebUI-Testclient | OK |
| Geldpfad und Regressionen | OK |
| Selbsttest | OK |
| Python-Kompilierung | OK |
| Hostneutrale Pi-Vorprüfung | OK |
| Shell-Syntax | OK |

## Geprüfte Korrekturen

PEP-Original-/Effektivplan, BOT-/Kontobindung, frische Mengen-/Preis-/Flags-/Stoptypbelege, ausstehende Schutzaufträge, Speicherfehler und Neustart; vollständige eToro-Risikobelege ohne erfundene Tagesbasis; reine GET-Collectorpfade; Diagnosephasen und verlorene Zellen; Nasdaq-Teilbelege; PULSAR-ETF-Ausschluss und begrenztes Nachrücken; FMP-Eingabequellen; gruppierte Anlaufsperren und unbekannte historische Kosten.

Die Freqtrade-Strategieregeln, Volumenregel, Massive-Minutenbegrenzung und GPT-Frequenz sind nicht gelockert. Die vorherige vollständige Prüfung des unveränderten 10.1.0-Ausgangsstands bestand 2.584 Tests/251 Untertests; der anschließend freigegebene Reparaturstand 2.714/253. Diese Zahlen sind verschiedene Stände und werden nicht addiert.

## Diagnosewerkzeug 1.2.0

24 Tests erfolgreich, darunter eigenständiger SH-Start ohne benachbarte Python-Datei, Integritätsfehler sowie der reale Original-ZIP-Replay. Eingebetteter Collector und ausgelieferte `NEXUS_10_Diagnose.py` sind bytegleich.

Realer Replaybefund: fünf vollständige PULSAR-Karten, drei eindeutige GPT-Requests im Gesamtlauf und null im Messfenster, zwölf FMP-Startabrufe, drei GPT-Requests mit FMP-Eingabebelegen. Historische Fehler und sieben eToro-Zeilen ohne Nettoergebnis werden getrennt ausgewiesen. Die drei Requests bleiben wegen zusätzlicher nicht eindeutig zuordenbarer Tokenzeilen eine ausdrücklich benannte beobachtete Untergrenze.

## Installer und Updategrenze

Der gesonderte Installer-/Wiederherstellungssatz bestand 84 Tests; ein ergänzender bestehender Fixturesatz 50. Sie verwenden echte Transaktions-/Migrationslogik mit simuliertem systemd und ohne sudo, Dienststarts oder Brokerkontakte auf einem Zielsystem.

Die Releaseprüfung fand eine alte Stromausfalllücke: gestoppte Units waren während der Umstellung noch für Autostart aktiviert. Beide Dienste werden jetzt vor Backup/Migration/Unit-Ersetzung deaktiviert. Früherer Aktivitäts- und Autostartzustand wird gesichert und bei einem zulässigen Rückweg vor der Startgrenze wiederhergestellt. Nach der Startgrenze bleiben neue Daten erhalten; alte Handelsdateien werden nicht automatisch zurückgespielt. Fehlende oder gesperrte Autostartzustände blockieren vor Dienständerungen.

Zehn zusätzliche Prüfungen betreffen den tatsächlichen eigenständigen Installer: Bash-Paketprüfung, identisches ZIP/Entpackmodul, vollständiges Manifest, Wiederholung ohne Zustandsverlust, veränderte Zielquellen, beschädigter Download, Root-/Architekturgrenzen, Optionskonflikte, Weitergabe von Argumenten und Exitcodes. Nach Ergänzung dieses Berichts und der Prüflogs wird die endgültige Auslieferungsdatei erneut durch diese zehn Prüfungen kontrolliert; die Bereitstellung setzt deren Erfolg voraus. Abschließende Artefakthashes und Protokolle stehen im separat ausgelieferten Paket `NEXUS_10.1.1_Pruefbelege.zip`.

Der erste Gesamtprüflauf fand noch einen älteren Diensttest ohne `UnitFileState`. Der Testdatensatz wurde um seinen ausdrücklich aktivierten Autostart ergänzt; die neue Ablehnung unbekannter Zustände bleibt bestehen. Der abschließende Volltest enthält diese Korrektur.

## Identität des Pakets

Alle Programm-, Test- und Assetdateien wurden vor der Paketierung gegen den erfolgreichen vollständigen Prüfsnapshot verglichen. Danach wurden ausschließlich dieser Bericht, Prüflogs und das Release-Manifest ergänzt. `validation/SOURCE_CHANGESET.json` enthält die geprüften Programmhashes und Änderungen gegenüber 10.1.0. Der eigenständige Installer kontrolliert nochmals das gesamte endgültige ZIP und jeden Manifest-Eintrag, bevor er den Updater starten kann.

Die früheren 10.1.0-Berichte und alten Validierungsdateien sind unter `docs/history/10.1.0/` als historische Nachweise erhalten. Sie sind keine Testergebnisse für 10.1.1.

## Praktische Grenzen

Keine Installation auf dem tatsächlichen Raspberry Pi/ARM64 und keine neue 30-Minuten-Laufzeitabnahme wurden hier ausgeführt. Keine echten Broker-, FMP-, Massive-, GPT- oder Telegram-Anfragen und keine Handelsaktionen. Massive-Minutenlast bleibt real ungeprüft.

Visuelle Browser-/Tablet-/Smartphone-Abnahme offen: Der Browser blockierte die lokale Vorschau mit `ERR_BLOCKED_BY_CLIENT`. Automatisierte Rendererprüfungen sind kein Nachweis einer Pixel-/Touchabnahme.

Die PEP-Schutzübernahme muss mit frischen Originalbelegen auf dem Pi erfolgen und danach vom laufenden Core bestätigt werden. Die historische eToro-Risikobasis bleibt ohne passenden unabhängigen Kontobeleg gesperrt. Installationserfolg allein hebt diese fachlichen Bedingungen nicht auf.
