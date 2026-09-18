# NEXUS 10.1.2

Dieses Update behebt die bekannte unzugeordnete eToro-Risikobasis unter belegbaren Startbedingungen, isoliert Fehler zusätzlicher Schutzbelege je Position und integriert Diagnosewerkzeug 1.3.0 in die WebUI. Details und Grenzen stehen in IMPLEMENTATION_REPORT.md und KNOWN_ISSUES.md.

## Installieren

Den eigenständigen Installer auf dem Pi nach Downloads speichern und als normaler Benutzer starten:

```bash
bash ~/Downloads/NEXUS_10.1.2_Installieren.sh
```

Er enthält das vollständige Paket. Der bestehende DEMO/Paper-Updateablauf sichert Einstellungen, Handelsdaten und Dienste, prüft die neue Software und startet sie im Ordner `~/Georg/TradingBot_v10.1.2_NEXUS`. Die alte Installation vorher nicht löschen. Eine zusätzliche Freigabe für Echtgeldhandel wird dadurch nicht erteilt.

Nur Paket prüfen:

```bash
bash ~/Downloads/NEXUS_10.1.2_Installieren.sh --paket-pruefen
```

## Diagnose in der WebUI

Im Menü **Diagnose** wählen. **Sofort starten** sammelt den aktuellen Stand; **30 Minuten beobachten** sammelt zuerst den Startstand, beobachtet anschließend volle 30 Minuten und sammelt den Endstand. Die Sammlungen benötigen zusätzlich Zeit. Der Browser kann geschlossen werden. Ein Dienst- oder Rechnerneustart kann den Lauf unterbrechen; unvollständige Läufe werden entsprechend angezeigt.

Die ZIP liegt wie bisher in `~/Downloads/NEXUS_Diagnosen` und lässt sich auf derselben Seite herunterladen. Der getrennte, zunächst ausgeschaltete Schalter **Fertige ZIP zusätzlich über Telegram senden** nutzt die vorhandene Telegram-Einstellung. Alternativ kann eine fertige, noch nicht versendete ZIP später über ihren eigenen Knopf gesendet werden. Bei fehlender Telegram-Einrichtung bleibt die lokale Diagnose nutzbar. ZIPs über 50 MB bleiben lokal; ein unbestätigter Versand wird nicht automatisch wiederholt.

## Diagnose im Terminal

30 Minuten:

```bash
bash ~/Georg/TradingBot_v10.1.2_NEXUS/NEXUS_Diagnose_Starten.sh --minuten 30
```

Sofort:

```bash
bash ~/Georg/TradingBot_v10.1.2_NEXUS/NEXUS_Diagnose_Starten.sh --sofort
```

Der zusätzlich gelieferte eigenständige Starter funktioniert auch vor der Installation:

```bash
bash ~/Downloads/NEXUS_10.1.2_Diagnose_Starten.sh --minuten 30
```

Alle Diagnosewege sammeln passiv. Sie lösen selbst keine zusätzlichen Broker-/GPT-/Providerabrufe oder Trades aus. Ein zeitgleich laufender Bot arbeitet regulär weiter.

## Kaufsperre beurteilen

Die neue Risikoperiode ist eine Erstbewertung ab einem belegten Zeitpunkt, keine rekonstruierte Mitternachtsbilanz. Sie wird nur für die bekannte alte OKX-Zuordnung in der eToro-Datei, bei unbegonnenem Handelstag, ohne heutige lokale Geldbewegungen und mit frischen vollständigen Kontobelegen eingerichtet. Alte Verluste, unklare historische Ergebnisse, Abkühlzeiten und Verlustbremsen werden erhalten. Andere Sperren werden separat geprüft.

Ein PEP-Schließauftrag ohne Abschlussbeleg bleibt ungeklärt. Die alte Meldung `statusID=7` wird nicht als Storno erfunden. **Storno eines einzelnen Brokerauftrags ist keine dauerhafte NEXUS-Verkaufssperre.** Soll NEXUS die Position dauerhaft nur beobachten, die vorhandene Positionsfunktion **Beobachten** verwenden. Ein vorhandener Broker-Stop bleibt eine separate Schutzmaßnahme.

FMP-Quellenlinks in PULSAR zeigen jetzt den gespeicherten, prüfsummengeprüften Beleg innerhalb der angemeldeten WebUI. Die grüne GPT-Anzeige heißt **Antwort empfangen**; die fachliche Entscheidung kann trotzdem REJECT sein.
