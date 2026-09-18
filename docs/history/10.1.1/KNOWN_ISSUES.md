# NEXUS 10.1.1 – bekannte offene Punkte

**Uneingeschränkte Freigabe für unbeaufsichtigten Echtgeldhandel: NEIN.**
Dieses Release ist für das kontrollierte Update der vorhandenen DEMO-/Paper-Installation vorgesehen. Der erfolgreiche Dienststart ist kein Nachweis, dass sämtliche Kaufbedingungen erfüllt sind. Die konkreten Testergebnisse stehen in [TEST_REPORT.md](TEST_REPORT.md).

| Priorität | Offener Punkt | Auswirkung und nächster Nachweis |
|---|---|---|
| Hoch – Freigabeblocker | Historische eToro-Risikobasis mit falscher OKX/USDC-Zuordnung und fehlendem Kontoscope | Neue eToro-Käufe bleiben bei diesem Zustand gesperrt. Der neue lesende Kontoabgleich macht die benötigten Belege sichtbar. Er kann den historischen Tagesbeginn nicht aus der aktuellen Equity ersetzen. Erforderlich sind ein unabhängig aufbewahrter, korrekt gebundener identischer Tagescheckpoint oder eine gesondert belegte vollständige Tagesabrechnung für einen späteren neuen Risikoperiodenpfad. Ein Tageswechsel oder das Löschen der Risikodatei ist keine Reparatur. |
| Hoch – Freigabeblocker | PEP-Schutzübernahme auf dem tatsächlichen Konto noch auszuführen und anschließend im laufenden Core zu bestätigen | Der neue Wartungsweg kann den vorhandenen Schutz als bewusst neue lokale Planentscheidung übernehmen. Dafür müssen Konto, Position, Restmenge, feste Stopart, aktive Schutzflags und beide Preise frisch und widerspruchsfrei belegt sein. Er sendet keinen PATCH und keine Order. Die Installation allein bestätigt PEP nicht. Nach einer Übernahme bleibt zunächst die frische Core-Bestätigung erforderlich. |
| Hoch – praktische Abnahme offen | Käufe, Verkäufe, Teilfills und Wiederanlauf mit den neuen Änderungen auf dem Ziel-Pi | Offline-Tests ersetzen keine neue Broker-/Pi-Abnahme. In der bisherigen 10.1.0-Beobachtung wurden keine neuen Käufe oder Verkäufe ausgeführt. Für 10.1.1 müssen der neue Verlauf, Bestandsabgleich und Schutzstatus anhand eines frischen Diagnoseexports beurteilt werden. Testorders sind nicht Teil der Diagnose. |
| Mittel | Historische eToro-Ergebnisse mit unbekannten Exitgebühren | Unbekannte Netto-P&L und Belege bleiben erhalten. Weder Installer noch Risikoprüfung tragen dafür künstlich Gebühren oder P&L von null ein. Erforderlich sind passende ursprüngliche Ausführungs-/Gebührenbelege. |
| Mittel | Tatsächlich volumenlose und flache OKX-DEMO-Kerzen | Die bisherigen Rohbelege zeigen diese Werte bereits in Brokerantworten. Kaufablehnungen bei fehlendem erforderlichem Volumen bleiben korrekt. Es gibt keine heimliche Übernahme von LIVE-Daten und keine gelockerte Volumenregel. Die Qualität neuer Brokerantworten muss weiter beobachtet werden. |
| Mittel | Verfügbarkeit und Vollständigkeit externer Nachrichtenquellen | Nasdaq-Teilbelege werden besser genutzt; fehlende Inhalte und aktuelle Quellenfehler bleiben sichtbar. Historische Fehler sind getrennt dargestellt. Verfügbarkeit, TLS- und Timeoutprobleme externer Anbieter können durch Offline-Tests nicht beseitigt oder als gesund bestätigt werden. |
| Mittel | Massive-Minutenlimit noch nicht unter neuer tatsächlicher API-Last geprüft | Der beobachtete 10.1.0-Lauf benötigte keine neuen Massive-Abrufe. Daraus folgt kein realer Lastnachweis. Bestehende Limits, Reservierungen und 429-Behandlung bleiben maßgeblich; das Release drosselt nicht pauschal weiter. Nutzung desselben Schlüssels durch andere Programme ist nicht vollständig im NEXUS-Journal sichtbar. |
| Mittel | Neue PULSAR-Auswahl und Anzeigen im tatsächlichen Betrieb | ETFs sollen keine begrenzten Plätze im Aktienpfad belegen; geeignete Aktien können nachrücken. Ob zum jeweiligen Zeitpunkt genügend geeignete Aktien vorhanden sind und verarbeitet werden, zeigt erst der frische Lauf. Mehr GPT-Aufrufe sind kein Ziel dieser Korrektur. Die Darstellung in der konkreten Browser-/Pi-Umgebung ist nach dem Update zu prüfen. |
| Niedrig | Verlustfreie Diagnose bei fehlenden oder sehr großen Daten | Das Werkzeug unterscheidet fehlende Belege von tatsächlich null Ereignissen und nutzt vollständige Verlaufsbelege. Es kann Daten, die weder im Zustand noch in der Historie vorliegen, nicht nachträglich erzeugen. Ein Sofortexport oder Teilbericht ersetzt keinen abgeschlossenen Beobachtungslauf. |

## Was ausdrücklich keine automatische Freigabe ist

- Die neue PEP-Übernahme ist keine allgemeine Zwei-Dezimal-Rundungsregel für eToro. Abweichende Preise oder fehlende Belege bleiben gesperrt.
- `APPLIED` beim Schutzplan bedeutet: Die bewusste lokale Planänderung wurde gespeichert. Der laufende Core muss den aktuellen Schutz anschließend erneut bestätigen.
- Ein erfolgreicher eToro-Kontoabgleich bestätigt aktuelle Kontodaten. Er macht die bisherige ungebundene Tageshistorie nicht rückwirkend gültig.
- Der im 10.1.0-Lauf belegte PULSAR-/GPT-/FMP-Betrieb ist ein historischer Nachweis und kein frischer 10.1.1-Laufzeitbeleg.

## Migration und Wiederherstellung

Positionszustand einschließlich Schutzplanhistorie sowie Risikozustände werden erhalten. Einzelne historische Dateien mit Endungen wie `.protection-plan-….bak` oder `.basis-review-….bak` werden nicht automatisch in den neuen Versionsordner kopiert. Sie bleiben im alten Installationsordner und in der vollständigen Update-Sicherung erhalten; dort sind sie bei einer späteren Nachprüfung einer alten Backupreferenz zu suchen. Alten Ordner und Sicherung aufbewahren.

Nach dem ersten Start eines neuen Dienstes darf keine alte Handelsdaten-Sicherung blind zurückgespielt werden. Neue Fills oder Befehle könnten bereits verarbeitet worden sein. Bei einem späteren Startfehler stoppt der Updater die neuen Dienste und erhält den neuesten Zustand zur Prüfung. Die Grenze zwischen Rücknahme vor dem ersten Start und manueller Wiederherstellung danach ist in der [Installationsanleitung](INSTALLATIONSANLEITUNG_NEXUS_10.1.1_DE.md) beschrieben.

Die ursprünglichen bekannten Punkte von 10.1.0 liegen unverändert unter [docs/history/10.1.0/KNOWN_ISSUES.md](docs/history/10.1.0/KNOWN_ISSUES.md). Sie sind keine aktuelle Freigabeaussage für 10.1.1.
