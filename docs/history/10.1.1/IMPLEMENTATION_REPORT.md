# NEXUS 10.1.1 – Umsetzungsbericht

Nutzerfreigabe zur Erstellung der neuen Version liegt vor. NEXUS 10.1.1 übernimmt die geprüften Korrekturen aus der Diagnose vom 13.09.2026. Die nachfolgend beschriebenen Broker-/Pi-Abnahmen bleiben erforderlich; das Erstellen des Installers ist keine Live-Handelsfreigabe.

Botversion: **10.1.1-NEXUS**. Diagnosewerkzeug: **1.2.0**. Die vorherigen 10.1.0-Berichte bleiben unter `docs/history/10.1.0/` erhalten.

## Ergebnis

| Punkt | Änderung | Praktisch noch zu belegen |
|---|---|---|
| D01 – eToro-Risikobasis | Lesender Konto-Collector, nachvollziehbare Beleglücken und eng begrenzte Metadatenreparatur aus einem ursprünglichen, passenden Risiko-Checkpoint | Die fehlerhafte OKX/USDC-Zuordnung ist auf dem Pi noch nicht repariert. Historischer Tages-/Kontobeleg fehlt. |
| D02 – PEP-Schutz | Expliziter effektiver Schutzplan mit Originalhistorie; frischer, konto- und positionsgebundener Abgleich; offene Schutzaufträge verhindern eine vorzeitige Bestätigung | Zwei aktuelle eToro-Lesebelege, Übernahme auf dem Pi und anschließende Bestätigung durch den laufenden Kern |
| D16 – Diagnose | Zellverlust ist unbekannt statt null; nachgewiesener Verlauf als Ersatz; getrennte Start-, Mess- und Endphasen | Neuer Diagnoseexport auf dem Pi |
| D17 – OKX-DEMO | Datenlieferung, Aktualität und Handelsaktivität getrennt angezeigt | Neue Broker-Daten bleiben abzuwarten; die Volumenregel bleibt aktiv |
| D18 – Nasdaq | Verwertbare Halt-Teilbelege erreichen den Filter; bekannte aktive Halts sperren das konkrete Symbol | Vollständige Feedabdeckung und externe Erreichbarkeit sind nicht durch Offline-Tests belegt |
| D19 – PULSAR-Auswahl | ETF/Fonds vor den fünf Aktienplätzen ausgeschlossen; geeignete Aktien rücken im begrenzten Suchlauf nach | Tatsächliche neue Kandidatenliste im nächsten Lauf |
| D20 – FMP-Nutzung | Vorhandene FMP-Eingabebelege mit erfolgreichen GPT-Antworten verknüpft | Keine zusätzliche GPT-Frequenz nötig |
| D21 – Anzeigen | Anlaufsperren pro angezeigter Seite zusammengefasst, Einzelbelege aufklappbar; korrekter Brokername | Visuelle Browserabnahme auf dem Pi |
| D22 – Quellenfehler | Aktuelle Wirkung, historische Fehler und Anbieterpausen getrennt; persistenter GDELT- und Tradestie-Backoff | Verhalten unter echten Anbieterantworten |
| D23 – historische Kosten | Fehlende Kosten und Nettoergebnisse bleiben unbekannt; alte Schätzwerte nur als Audit | Originale Abschlussbelege, soweit noch verfügbar |

## PEP: Zuordnung erhalten, Schutzplan nachvollziehbar ändern

Die untersuchte Position ist bereits eindeutig zugeordnet: eToro DEMO, PEP, Instrument 1043, Position 3597440106, 109 Aktien; Herkunft BOT, Eigentum VERIFIED. Die beiden Schutzflags sind im Diagnosematerial ausdrücklich `false`.

Die beabsichtigte neue Planung lautet SL 135,90 USD und TP 138,52 USD. Sie ersetzt für den weiteren Abgleich die alten Sollwerte 135,8946 / 138,5207. Der Originalplan einschließlich ursprünglicher Risikoangaben bleibt in der Historie erhalten. Es wird keine allgemeine Zweinachkommastellenregel behauptet und keine Vergleichstoleranz aufgeweicht.

Der neue Wartungspfad ist auf die Übernahme bereits exakt passender Brokerwerte begrenzt. Er sendet keinen Schutzauftrag und keinen Kauf oder Verkauf. Er verlangt passende Konto-/Umgebungs-/Instrument-/Positionsdaten, unveränderte Menge, explizite Schutzflags, einen belegten festen Stoptyp und eine vollständige Sicht auf offene Aufträge. Ein abweichender oder unvollständiger Brokerzustand blockiert.

Vor der lokalen Übernahme werden ein neuer Lesebeleg, unveränderte Quelldatei, gestoppte Schreiber und die ausdrücklich ausgewählte Entscheidungsprüfsumme geprüft. Die echte Zustandsdatei `position_state.json` wird gesichert und atomar geändert. BOT-Herkunft und Eigentum bleiben erhalten. Die Verwaltung bleibt zunächst `PENDING_CONFIRMATION`; erst der Handelskern darf nach erneutem strengem Abgleich bestätigen. Auch PULSAR verwendet danach die wirksamen Sollwerte.

Die vorhandene allgemeine Schutzänderung behandelt Annahme und Ausführung weiterhin getrennt. Fehlende oder widersprüchliche Auftragsbestätigungen, Zeitüberschreitungen und noch offene Änderungen führen nicht zur Bestätigung des alten Plans.

Rechnerischer Unterschied des PEP-Plans: Der Stop liegt 0,0054 USD je Aktie höher, bei 109 Aktien 0,5886 USD. Das Ziel liegt 0,0007 USD je Aktie niedriger, insgesamt 0,0763 USD. Das sind Niveauunterschiede, keine zugesagten Ausführungsergebnisse.

## eToro-Risikobasis: keine erfundene Entsperrung

`NEXUS_eToro_Risikopruefung.sh` erstellt ohne Optionen einen rein lesenden DEMO-Kontobeleg. Kontobindung, Währung, Zeitpunkte, Positionen und paginierte Historie werden geprüft. Fehlende oder fehlerhafte Rohdaten bleiben als Lücken sichtbar. Bereits der Konfigurationsimport darf bei diesem Aufruf keine abgelaufene LIVE-Freigabedatei löschen; eine abgelaufene Freigabe bleibt selbstverständlich unwirksam.

Ein frischer Kontowert ist kein rückwirkender Tagesstartwert. Ein leerer Historienabruf beweist keine Nullkosten oder Nullcashflows. Deshalb gibt es keinen automatischen neuen Risikozeitraum und keine Nullsetzung alter Verluste. Ein Tageswechsel alleine beseitigt die falsche Basis nicht.

Der Reparaturpfad akzeptiert nur einen passenden ursprünglichen Archivbeleg, einen frischen Kontobeleg und einen vollständigen identischen wirtschaftlichen Zustand. Eine neu umetikettierte Kopie der fehlerhaften Datei ist kein historischer Nachweis. Ohne diesen Beleg bleibt die eToro-Kaufsperre bestehen.

## Korrigierte Auswertung des ursprünglichen Diagnoselaufs

| Beleg | Ergebnis des Offline-Replays |
|---|---:|
| Vollständige PULSAR-Karten | 5 |
| Eindeutige erfolgreiche GPT-Requests im Gesamtlauf / Messfenster | 3 / 0 |
| Neue FMP-Einträge in Startsammlung / Gesamtlauf | 12 / 12 |
| Massive-Einträge im Gesamtlauf | 0 |
| GPT-Requests mit belegter FMP-Eingabe | 3 |
| Geschlossene eToro-Zeilen ohne Nettoergebnis | 7 |

Die Karten stammen aus einem prüfsummengeprüften Verlaufsobjekt desselben Laufs und derselben gespeicherten Revision. Die drei Request-IDs werden nicht mit zusätzlich gespeicherten Tokenzeilen ohne zuordenbare ID addiert; der Bericht benennt diese Vollständigkeitsgrenze. Insgesamt enthalten 26 geschlossene eToro-Zeilen unvollständige Gebührenqualitätsangaben, darunter die sieben ohne Netto.

Die Laufzeitbeobachtung des ursprünglichen 10.1.0-Laufs – 30 Minuten, 61 Messpunkte, keine Neustarts – bleibt ein Befund dieses alten Laufs. Sie ist keine Laufzeitabnahme des geänderten Quellstands.

## Prüfung und Grenzen

Der freigegebene Reparaturstand bestand 2.714 Tests und 253 Untertests sowie den getrennten Original-ZIP-Replay. Für die konkrete Version 10.1.1 werden zusätzlich die Versionskonsistenz, das vollständige Releasepaket, beide eigenständigen SH-Dateien und die abgesicherte Installertransaktion geprüft. Der aktuelle Nachweis steht in `TEST_REPORT.md` und `validation/`.

Die visuelle Browserprüfung des Reparaturstands wurde durch `ERR_BLOCKED_BY_CLIENT` verhindert. Echte JavaScript-Renderer und Backend-Antworten sind automatisiert geprüft; Desktop-/Tablet-/Smartphone-Abnahme bleibt offen. Ebenso fehlen echte ARM64-/systemd-Installation, neue Laufzeitdiagnose und reale Handelsausführung.

Keine authentifizierten Brokerabfragen, echten GPT-/FMP-/Massive-Aufrufe oder Handelsausführungen wurden während der Erstellung vorgenommen. Die uneingeschränkte Live-Freigabe bleibt NEIN. Freqtrade-Strategieregeln, Volumenschutz, Massive-Minutenlimit und GPT-Frequenz wurden nicht gelockert.

## Zusätzlich abgesicherter Installerübergang

Die Releaseprüfung fand einen alten Stromausfallpfad: Gestoppte, aber weiterhin aktivierte systemd-Units konnten während der Umstellung nach einem Rechnerneustart wieder anlaufen. Die Version sichert beide Dienste vor der Umstellung gegen automatischen Neustart und stellt frühere Aktivierungszustände bei einem zulässigen Rückweg wieder her. Nach dem ersten kontrollierten Start wird weiterhin kein alter Handelszustand blind restauriert. Details stehen in `MIGRATION_NOTES.md` und den Installer-Verhaltenstests.

## Inhalt und nächster Schritt

Eigenständiger Installer `NEXUS_10.1.1_Installieren.sh`, vollständiges Quell-ZIP, eigenständiger Diagnosestarter `NEXUS_10.1.1_Diagnose_Starten.sh`, neue Installationsanleitung und aktuelle Prüfbelege. Alle Quell- und Testdateien sind im Release-Manifest erfasst. Das Paket enthält keine vorbefüllten Brokerzustände oder Zugangsdaten.

Nach dem Update folgt eine neue passive Diagnose auf dem Pi. Die ausdrückliche PEP-Planübernahme und der fehlende eToro-Tages-/Kontobeleg sind gesonderte Schritte; das Update hebt sie nicht automatisch auf.

Ausgangspaket 10.1.0 SHA-256: `eebe8a1a526b859c825541f8b18d295f558b4cbfe9c1c6c7f9d6076687733e12`.

Originaldiagnose SHA-256: `0c03e93a244fdbf1a07b1450762214ede15ad9b5ee209ed4d29d329697257de7`.
