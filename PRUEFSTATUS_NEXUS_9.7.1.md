# Prüfstatus NEXUS 9.7.1

**Korrekturstand, keine uneingeschränkte Installations-/Echtgeldfreigabe.**
Prüfdatum: 07.09.2026. Basis: die tatsächlich ausgelieferte 9.7.0-NEXUS-ZIP,
SHA-256 `f7e3d196d3aa8f0a3581cbe12148560aae95695463dbc3570c07484f21e79bdc`.

## Reproduktion und Ergebnisse vor der Paketprüfung

Die Original-9.7 ergibt hier 22 Fehlschläge: die 14 auf dem Pi sichtbaren
Regressionsfehler plus 8 Importfehler wegen fehlendem yfinance. Nach den
Korrekturen bestehen 94 gezielte Tests, darin 24 neue Grenzfalltests.
Der vollständige Volltest ergibt 1246 bestandene Tests, 8 Importfehler und
201 bestandene Subtests; der Rückgabecode bleibt 1. Alle anderen Gruppen
bestehen: statische Releasehygiene, Selbsttest, Kompilierung, hostneutraler
Preflight und Shell-Prüfung. Ein vollständiges VOLLTEST OK wird nicht behauptet.

Die 8 Importfehler wurden NICHT übersprungen oder in bestandene Tests
umgewandelt. Ein Installationsversuch für echte Abhängigkeiten scheiterte an
nicht erreichbarer Paketquelle. Es wurde kein externes Ersatzmodul für
yfinance in den Testpfad eingefügt. Bestehende Testdoubles der Testsuite
bleiben unverändert Teil ihrer isolierten Tests.

## Was geändert wurde und was nicht

Produktionskorrekturen betreffen definierte Kennzahlfelder, einheitliche
Gebühren-/Ergebnisqualität in Ledger und WebUI, unbekannte Gebühren im
Teilverkaufs-Rest, Währungstrennung und die beleggebundene eToro-Replayprüfung.
Alte Testdaten deklarieren bestätigte Nullgebühren jetzt ausdrücklich.
Die geänderten kumulativen Rückgabetoken und die beabsichtigte idempotente
Alias-Verarbeitung erhalten aktualisierte Vertragstests. Diese Änderungen
sind im Changelog offen dokumentiert; keine Tests werden deaktiviert.

Es wird keine vollständige Zentralisierung aller wirtschaftlichen Zustände
oder ein Abschluss aller bisherigen Architekturbefunde behauptet. Broker-
POSTs, Strategien, Risikogrenzen und Live-Arming wurden für diese Korrektur
nicht geändert. Die Volltest-Abbruchsperre des Installers bleibt aktiv.

## Zusätzliche Gegenproben

Auf einer Kopie der echten Nutzer-Ledgerdatenbank: KO-Trade 44 wird mit einem
künstlichen alternativen Receipt derselben belegten Lineage zweimal
verarbeitet. Beide Male Trade 44, finanzielle Felder unverändert, erster
Durchlauf ergänzt genau einen Alias-Beleg, zweiter keinen weiteren; SQLite-
Integrität der Kopie ok. Originaldatenbank per SHA-256 unverändert. Der
künstliche Receipt ist kein neu vom Broker abgerufener Ausführungsbeleg.
Historische Gebührenqualität bleibt UNKNOWN, nicht künstlich CONFIRMED.

Browser-Komponententest mit echtem lokalen trade_analysis-Payload und den
originalen HTML/CSS/JS-Dateien: Desktop 1465×1024 und Mobil 390×844, bestätigtes
Netto 10 EUR, eine vorläufige Zeile und zwei Zeilen mit offenen Gebühren.
Kein JavaScriptfehler, kein globaler horizontaler Überlauf. Nur der API-
Transport wird durch lokale Daten ersetzt. Dies ist kein vollständiger Test
von Authentifizierung, Server und sämtlichen WebUI-Seiten.

## Umgebung und Grenzen

Python 3.13.5, Linux x86_64, pytest 9.0.2; kein Raspberry Pi/ARM64.
Einige installierte Webpakete weichen vom Projekt-Lockfile ab. Es gab keine
echten Orders, Stornierungen, Broker-Abnahme oder systemd-Dauerprüfung.
Der abschließende Paketprüfbericht wird neben der ZIP mitgeliefert und
benennt das Ergebnis aus einer frischen Entpackung sowie die ZIP-Prüfsumme.

## Nächster Schritt

Zunächst den Volltest mit der bereits vorhandenen Python-Umgebung aus
`TradingBot_v9.7_NEXUS/.venv` auf dem Pi ausführen. Siehe die neue Anleitung.
Bis zum vollständig bestandenen Pi-Test keine Dienste umstellen, keine
Zustandsmigration ausführen und keine bestehenden Handelsdaten löschen.
