# NEXUS 10.1.10 – aktueller Pruefstand

Offline-Pruefstand der Paketierung (Windows x86_64, Python 3.14, ohne pandas/fastapi): 523 Python-Dateien AST-geprueft (0 Fehler); 27 neue gezielte 10.1.10-Regressionen bestanden (1 bedingt uebersprungen, pandas-abhaengig), darunter FX-robuste Equity-Bremse, Waehrungsfreigabe-Vertrag, Entscheidungs-Retention, Diagnose-Sicherung, X-Konten-Registry, Quellen-Fixes und Backtest-Oberflaeche. Der vollstaendige Volltest (alle Testdateien, Self-Test, Pi-Preflight, Shell-Syntax) laeuft wie immer waehrend der Pi-Installation und muss bestehen, bevor Dienste starten. Nachweis: `NEXUS_10.1.10_Pruefbericht.md` und `validation/NEXUS_10.1.10_TEST_EVIDENCE.json`.

# NEXUS 10.1.9 – aktueller Prüfstand

**3.124 Tests bestanden, 3 bedingte Tests übersprungen, 283 Untertests bestanden.** Zusätzlich grün: Static Release Hygiene, Testabhängigkeiten/WebUI-TestClient, Self-Test, compileall, host-neutraler Pi-Preflight und Shell-Syntax. Der vollständige Nachweis steht in `NEXUS_10.1.9_Pruefbericht.md` und `validation/NEXUS_10.1.9_TEST_EVIDENCE.json`.

# 10.1.8 – Korrektur der OKX-Kontovorpruefung

Die Kontovorpruefung verwendet fuer Trailing-Stop-Orders nun `move_order_stop`.
Fehler nennen GET-Endpunkt, freigegebene Pruefparameter, HTTP-Status und numerischen OKX-Code.
Freitextantworten, Zugangsdaten und Kontodaten werden nicht ausgegeben.
`--nur-pruefen` im Kontowechselwerkzeug fuehrt dieselben GETs ohne Aktivierung aus.
Alle sonstigen Konto-, Archivierungs- und Bestandssperren bleiben bestehen.
Offline-Tests ersetzen keinen Abruf mit dem neuen OKX-Demoschluessel.

# NEXUS 10.1.7

Neuer OKX-Kontokontext, expliziter archivierter Demo-Kontowechsel, konservativer Bestandsabgleich und vorgelagerte Risikoprüfung. Kein Abschluss und kein Storno allein wegen fehlenden Guthabens. Historische Schutzinformationen bleiben erhalten; aktuelle Bestätigung wird bei ungeklärtem Bestand zurückgenommen.

Grundlage: vollständiger Release 10.1.6, nicht der unverfügbare Zwischenstand aus dem alten Chat. Installation und Grenzen siehe INSTALLATIONSANLEITUNG_NEXUS_10.1.7_DE.md. Die konkreten Abschlussprüfungen werden im separat ausgelieferten NEXUS_10.1.7_Pruefbericht.md dokumentiert.


---

## Historischer Stand bis 10.1.6

# NEXUS 10.1.6 – abgeschlossene Prüfung

Stand: 15.09.2026. Der vollständige isolierte Lauf von `volltest.py` ist erfolgreich: **3071 Tests und 278 Untertests bestanden**, 3 bedingte Starter-/Archivtests übersprungen, 7 Hinweise zu Bibliotheksabkündigung bzw. Fork in mehrfädigen Tests. Die beiden Startertests werden zusätzlich am tatsächlich erzeugten Starter geprüft und im Lieferbericht ausgewiesen.

Release-Hygiene, echte WebUI-Testabhängigkeiten, sämtliche pytest-Regressionen, Selbsttest, Python-Syntax, plattformneutraler Pi-Preflight und Shell-Syntax sind erfolgreich. Die Testkopie ist durch SHA256 an die Quelle gebunden. Brokerzugangsdaten werden nicht übernommen; externe HTTP-/Socket-Aufrufe werden gesperrt.

## Konkret geprüft

- PEP/eToro: bestehende Abrechnung, Konto-/Umgebungsbindung, historische Stornos und Ergebnisbelege; keine neue Buchung oder pauschale Freigabe.
- PULSAR-Datenbank: ein offener Leser blockiert einen parallelen Schreiber nicht; Leseverbindungen dürfen selbst nicht schreiben; bestehende Schema-/Historientests bleiben grün.
- OKX 54092: passende Übernahme aus dem Journal; falsches Konto/falsche Umgebung bleiben ausgeschlossen; keine automatische erneute Übermittlung; genau eine Freigabe; erneute Ablehnung bleibt gesperrt; lokaler Verkaufspfad bleibt erreichbar.
- OKX-WebUI: echte Anmeldung/CSRF, aktuelle Fehlerrevision und frischer Laufzeitbeleg vor einmaliger Freigabe. Der Endpunkt sendet keine Brokerorder.
- Entscheidungsnachweis: große 63-Bit-IDs bleiben exakt, ursprüngliche Strategieentscheidung bleibt erhalten; endgültiger Fehler wird separat gespeichert und in WebUI/Diagnose korrekt gezählt. Altbestände ohne neue Spalte bleiben lesbar.
- Kerzen: originale Nullvolumen-Antworten sind getrennt von synthetischen Zeilen gekennzeichnet. Der bestehende Qualitätsfilter wird nicht abgeschwächt.
- X: gültige Einzelaktienidentität, Ablehnung von ETF-Profilen, dynamische Query, exakte Textzuordnung, Duplikate/Spam, leere und veraltete Stichproben, vorsichtige Negationsbehandlung, faire nächste Auswahl, konkurrierende Reservierungen und gemeinsames 15-EUR-Budget.
- Ein neuer leerer Suchlauf zählt keine Beiträge eines früheren Laufs nochmals als aktuelle Antwort. Neues X-Wissen geht erst in eine nachfolgende reguläre PULSAR-Bewertung ein; die kompakten KI-Pakete behalten den Kandidatenkontext ohne Rohtext-Anweisungen.
- Quellenkoordination: kein doppelter Rechercheplatz für dieselbe Reddit-/X-Aktie; niedriger gereihte gemeinsame Kandidaten verlieren nicht unbemerkt alle X-Plätze.
- Oberfläche: JavaScript-Verhalten prüft unbekannte/leere Recherche, sichere Textmaskierung und Einbindung auf PULSAR/Quellen/Diagnose. Die Syntax der vier geänderten JavaScript-Dateien wurde zusätzlich geprüft.
- Diagnoseexport: dynamische Such- und Mengenmetadaten bleiben sichtbar, rohe Posttexte und private Nachrichtendetails werden nicht exportiert.

## Grenzen der Prüfung

Prüfumgebung: Linux x86_64, Python 3.12. Alle Netzwerk-/Brokerantworten in Regressionen sind Testbelege bzw. Testtreiber. Es wurde keine neue Brokerorder, kostenpflichtige X-/GPT-Abfrage oder Telegram-Nachricht gesendet und nichts auf Georgs Raspberry Pi installiert. Eine visuelle Browserabnahme, ARM64-/Python-3.13-Abnahme und tatsächliche neue OKX-Freigabe werden nicht behauptet.

Nach dem erfolgreichen Programmlauf werden nur Dokumentation, Protokolle und Paketmanifeste ergänzt. Der Paketbau prüft sämtliche getesteten Programmdateien erneut gegen ihre Hashes. Historische Berichte wurden nach `docs/history/10.1.5` verschoben; aktuelle Ergebnisse stehen in `validation`.
