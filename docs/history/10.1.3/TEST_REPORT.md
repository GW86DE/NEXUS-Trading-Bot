# NEXUS 10.1.3 – Prüfbericht

## Vollständige Releaseprüfung

Der finale Quellstand wurde mit `volltest.py` in einer manifestgeprüften Kopie, getrenntem Zustands-/Homeverzeichnis und aktivem HTTP-/Socket-Testschutz geprüft. Ergebnis: **2891 Tests und 271 Untertests bestanden**, 3 optionale Tests übersprungen. pytest-Laufzeit: 104.87s (0:01:44). Keine Programmdatei wurde während dieses erfolgreichen Prüflaufs geändert.

| Prüfgruppe | Ergebnis |
|---|---|
| Release-Hygiene, Pflichtdateien, Quellmanifest | OK |
| Testabhängigkeiten und echter WebUI-TestClient | OK |
| Gesamte pytest-Suite | 2891 bestanden, 271 Untertests bestanden |
| Selbsttest | OK |
| Python-Kompilierung | OK |
| Pi-Preflight im hardwareunabhängigen Modus | OK auf x86_64 |
| Shellsyntax aller Release-Starter | OK |
| Externe Netzwerkversuche | 0 |

Prüfumgebung: Python 3.12.14 und pytest 9.0.2. 7 Warnungen betreffen bestehende Starlette/AnyIO-/Multiprocessing-Verwendungen. Die drei optionalen Tests benötigen den erst danach gebauten eigenständigen Diagnosestarter beziehungsweise das ursprüngliche 10.1.0-Archiv mit seinem damaligen Exportfehler. Die beiden Starterfälle werden zusätzlich am fertigen Artefakt geprüft. Das ursprüngliche Archiv bleibt nicht verfügbar.

Der erste Gesamtlauf deckte veraltete Versions-/Navigationserwartungen auf. Die Navigation wurde auf allen Seiten vereinheitlicht. Ein Test für verspätete Buchungen nutzte das Hostdatum anstelle des konfigurierten Bot-Handelstags; er prüft jetzt kontrollierte Tageswechsel einschließlich verspäteter Gewinne und Verluste, Wiederholung und Neustart. Die Produktionsbuchungslogik musste hierfür nicht verändert werden. Die unabhängige X-Gegenprüfung ergänzte die strenge Validierung gespeicherter Einstellungen vor jeder Reservierung, einschließlich ungültiger Budgets, fremder Felder und zukünftiger Preisbelege.

## Geprüfte neue Fehlerfälle

- PEP: fehlende/falsche Schließorder-ID, falsches Konto/Umgebung/Instrument, frischer und veralteter Positionsbeleg, doppelte Schließversuche, alte falsche FILLED-Zuordnung und Wiederaufnahme zwischen zwei Datenbankprojektionen.
- Risikoperiode: Originalarchiv, Kontobezug, fehlender oder geänderter Beleg, alte unbekannte Ergebnisse, nachträgliche native Gebührenbelege, Einmaligkeit und zeitlich korrekte Buchung.
- Ausstiege: frischer Bid auch ohne Ask, Veraltung während Kosten-/Handelbarkeitsabfragen, Stop-/Zeitrisiko vor Gewinnmitnahme, unbekannte Kosten, kein doppelter Spread, unveränderte native Schutzorders und bestehendes PULSAR-Ziel.
- X: parallele Reservierungen, Neustart und Übernahme bezahlter Nutzung, 15-EUR-Grenze, Timeouts, 401/429, Fehlerantworten, Querywechsel, unvollständige Tage, Deduplizierung, Ablauf/Löschung, abgeleitete Forschungsübergabe und keine automatische Handelswirkung.
- WebUI/Diagnose: tatsächliche JavaScript-Renderer, Anmeldung/CSRF, konsistente Navigation, geheime Werte, passive Quellenabdeckung, sichere referenzierte Risikoarchive sowie vorhandene ZIP-/Telegram-Fehlerpfade.

## Replay der tatsächlichen PEP-Dateien

Das reale exportierte Exit-Journal und die Ausführungsdatenbank wurden isoliert rekonstruiert. Die frühere falsche Zuordnung `FILLED / 109` wird mit einer simuliert frisch erneut gelesenen unveränderten Positionshistorie in beiden Speichern zu `POSITION_CLOSED_ORDER_UNPROVEN / 0` fortgeschrieben. Der Positionsabschluss über 109 Stück bleibt separat erhalten; offene Auftragsoperationen: 0. Zwei Wiederholungen erzeugen genau einen Korrektur-Audit-Eintrag.

Die tatsächlich archivierte Historie von 13:38 Uhr ist für den Depotabruf von 17:55 Uhr zu alt und wird abgewiesen. Der erfolgreiche Replay simuliert ausdrücklich einen erneuten Historienabruf; er ist kein neuer Brokerbeleg. Alle sieben authentischen v2-Einstiegskostenbelege bestehen die Zuordnung. PEP erhält 1 USD genau einmal; seine Verkaufskosten und das Nettoergebnis bleiben unbekannt.

Das ursprüngliche Risikoarchiv und der zugehörige Periodenbeleg fehlen in den Uploads. Deshalb kann der echte Datenreplay die sechs historischen Lücken noch nicht aus der aktuellen Periode ausklammern und belässt konservativ sieben ungeklärte Ergebnisse. Die Trennung 6 historisch / 1 aktuell ist mit vollständigen Testbelegen geprüft. Auf dem Pi müssen dafür die Originaldateien vorhanden sein. Die neue Diagnose erfasst genau diese referenzierten Dateien.

## Auslieferungsprüfung

**13 Artefaktprüfungen bestanden.** Installer und Quell-ZIP enthalten bytegleiche Nutzdaten; der eingebettete Entpacker und der eigenständige Diagnosestarter entsprechen den geprüften Release-Dateien. Die echte Option `--paket-pruefen` prüft alle Dateihashes ohne Dienstaktion. Beschädigte Prüfsummen, ZIP-Pfadtraversal und Symlinks werden abgewiesen. Wiederholtes Entpacken erhält vorhandene Benutzerdaten; geänderte Programmdateien werden nicht überschrieben.

Die beiden in der Gesamtsuite zunächst übersprungenen Tests für den eigenständigen Diagnosestarter wurden am fertigen Artefakt zusätzlich ausgeführt: **2 bestanden**, keine externen Netzwerkversuche. Der dritte optionale Fall zum nicht verfügbaren ursprünglichen 10.1.0-Diagnosearchiv bleibt übersprungen. Nach der Berichtsergänzung wurden alle Auslieferungsprüfungen am endgültigen Paket wiederholt. Programmdateien werden vor dem Bau bytegleich gegen den erfolgreichen Volltest-Snapshot geprüft; danach änderten sich nur Berichte und Manifest.

## Grenzen der Abnahme

Kein echter Brokerauftrag, keine Schutzänderung, keine kostenpflichtige X-/GPT-Abfrage und kein Telegram-Versand wurden ausgelöst. Keine Installation auf Georgs Pi und keine echte ARM64-/30-Minuten-Betriebsabnahme wurden durchgeführt. Chromium war nicht verfügbar; eine visuelle Browserabnahme wird nicht als bestanden ausgegeben. Die HTTP-Routen und produktiven JavaScript-Funktionen wurden offline geprüft.

Der Integrationsfehler ist damit behoben und gegen bekannte Wiederholungsfälle geprüft. Fehlende aktuelle PEP-Abschlusskosten bleiben eine tatsächliche Beleglücke und können weiter die Kaufprüfung sperren.

## Diagnose starten

WebUI: **Diagnose → Sofort** oder **30 Minuten**. Optionaler getrennter Telegram-Schalter; lokale ZIP bleibt bei Versandfehler erhalten.

```bash
bash "$HOME/Georg/TradingBot_v10.1.3_NEXUS/NEXUS_Diagnose_Starten.sh" --minuten 30
```

Sofortbericht: denselben Befehl mit `--sofort` statt `--minuten 30` aufrufen.
