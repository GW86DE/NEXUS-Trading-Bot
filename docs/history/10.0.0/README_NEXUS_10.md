# NEXUS 10.0.0 – kontrollierter Umsetzungskandidat

Stand: 13. September 2026. Ausgangspunkt ist die vollständige, unveränderte NEXUS 9.9.0 FIX1 mit 667 geprüften Manifestdateien. Dieses Paket ist eine eigene Version; keine bestehende Installation wurde überschrieben oder gestartet.

**Releaseentscheidung für unbeaufsichtigten Handel: NEIN – NICHT RELEASE-FÄHIG.** Die implementierten Maßnahmen werden offline geprüft. Eine reale Raspberry-Pi-5-Abnahme, die kontogebundene Brokerabnahme und eine vollständige visuelle Geräteprüfung fehlen. Einzelne weitergehende Empfehlungen sind bewusst nur teilweise umgesetzt. Details und endgültige Testzahlen stehen in `TEST_REPORT.md` und `KNOWN_ISSUES.md`.

Der Schwerpunkt liegt auf dauerhaftem Order-/Risikozustand, eindeutigem Recovery und belegbarer Diagnose. NEXUS-Farben, Logo, Karten, Navigation und bestehende Handelsseiten bleiben erhalten. Neue Ansichten erteilen keine Handelsrechte; fehlende Daten bleiben unbekannt.

## Inhalt und Einstieg

| Datei | Inhalt |
|---|---|
| `CHANGELOG.md` | Änderungen und entdeckte Regressionen |
| `IMPLEMENTATION_REPORT.md` | Problem → Auswahl → Code → Risiko → Nachweis, mit Maßnahmenstatus |
| `TEST_REPORT.md` | Gruppierte Testergebnisse, Gegenproben, Prüfgrenzen |
| `MIGRATION_NOTES.md` | Zustandserhalt, Sicherung, Schema und Wiederanlauf |
| `KNOWN_ISSUES.md` | Offene Punkte und begründete Releaseentscheidung |
| `WEBUI_RESEARCH.md` | Quellenvergleich, Matrix, Prioritäten und Konzepte im NEXUS-Design |
| `docs/v10/` | Detaillierte Modul- und WebUI-Nachweise |

Historische Versionsberichte bleiben als historische Unterlagen im Quellbaum. Maßgeblich für dieses Paket sind die oben genannten neuen Dateien sowie `VERSION.txt` und `RELEASE_BUILD.txt`.

## Prüfung ohne Handelsstart

Das Paket in einen neuen Ordner entpacken. Der bestehende isolierte Prüfeinstieg ist `python3 volltest.py`; die Python-Testabhängigkeiten und Node.js für die JavaScript-Verhaltensprüfung müssen installiert sein. Node.js wird nur zur Prüfung benötigt. Ohne Node wird diese Testgruppe übersprungen und die UI-Abnahme ist unvollständig. Der Prüfeinstieg erstellt eine geprüfte Kopie mit privaten Testpfaden und blockiert Netzwerkversuche. Die Testumgebung darf keine produktive Zustandsdatei verwenden. Ein bestandener Offline-Test aktiviert keinen Broker und startet keinen Handelsdienst.

Die vorhandenen Installations-/Updatewerkzeuge liegen zur Nachvollziehbarkeit bei. Ihre Ausführung startet unter Umständen Dienste und ist kein Ersatz für die noch offene Demoabnahme. Dieses Paket wurde nicht auf Georgs Raspberry Pi installiert. Für eine spätere Umstellung gelten die Schritte und Grenzen in `MIGRATION_NOTES.md`; alte Installation und Zustand zuerst vollständig sichern.
