# NEXUS 10.1.0 – Migration und Datenerhalt

## Updatepfad

Eigenständige NEXUS_10.1.0_Installieren.sh mit eingebettetem Quell-ZIP und SHA-256-Prüfung. Neuer Ordner ~/Georg/TradingBot_v10.1.0_NEXUS. Vorherige Releaseordner bleiben erhalten. Wiederholtes Entpacken bewahrt lokale Zustandsdateien; ein abweichender Programmstand im Ziel wird abgewiesen. Der bestehende transaktionale Demo/Paper-Updater übernimmt Dienstprüfung, Sicherung, gestoppte Schreiber, Staging, Validierung und Dienststart.

Eine gescheiterte Übernahme startet keinen halben Datenbestand. Nach der Grenze, ab der neue Dienste eigene Fills geschrieben haben könnten, wird kein alter Handelszustand blind zurückkopiert. LIVE ist weiterhin vom automatischen Update ausgeschlossen.

## Neue persistente Dateien

| Datei | Bedeutung | Behandlung |
|---|---|---|
| massive_service.sqlite | Globale Reservierungen, Minuten-/Tagesverbrauch, Cache, Berechtigungs- und 429-Pausen | Aufnahme in strikte Prüfung, SQLite-Onlinebackup inklusive WAL, Updateübernahme und Diagnose; keine Rücksetzung beim Neustart |
| etoro_protection_journal.json | Asynchrone Schutz-PATCH-Intents und Bestätigung | Strikte Zustandsprüfung und unveränderte Übernahme; ein unklarer Intent bleibt unklar |
| candle_observations.json | Begrenzte Belege ohnehin erfolgter Candle-Requests | Übernahme und Diagnose; Fehlen erzeugt keine erfundenen Belege |
| nasdaq_halt_diagnostics.json | Zeilenbezogene Feeddiagnose | Übernahme und Diagnose; keine automatische Markt-Entwarnung |

Eine erstmalige Massive-Einführung kann alte, nicht zentral protokollierte Sekunden nicht rekonstruieren. Historischer Newszustand führt daher einmalig zu einer vorsichtigen Anlaufpause. Fremde Anwendungen mit demselben Key sind im lokalen Budget nicht vollständig sichtbar.

## Vorhandene Daten

Trade-Ledger, Registry, Positionen, Fill-Fortschritt, Reconciliation, Exit-Journal, Entscheidungen, Einstellungen, API-Zugänge, Favoriten, Universum und PULSAR-Rohdaten bleiben erhalten. Schema 2 des RiskState erhält nur ein additives basis_review_receipt-Feld. Ein korrekt belegter Risiko-Checkpoint kann ausschließlich gleiche wirtschaftliche Werte mit richtiger Metadatenbindung reparieren; Originalhash, dauerhafter Backup und atomarer Commit schützen diese Wartungsoperation. Beim normalen Installieren wird keine historische Risikobasis automatisch neu erfunden.

Allein eine leere decisions-Tabelle berechtigt nicht mehr zum Überschreiben einer Datenbank mit anderen Nutzdaten. Strikte Migration lehnt ein Ziel mit vorhandener Kontokonfiguration ab. Damit können fremde Ziel-Keys nicht mit importierten Quell-Trades vermischt werden.

## Abgeleitete Caches

- FMP-Jahresableitung jetzt FMP-ANNUAL-2; valide Rohantworten werden wiederverwendet, alte Fehler nicht als neue geprüfte Jahresänderung ausgegeben.
- PULSAR-Paket pulsar-evidence-2, kompakte GPT-Ansicht evidence-view-2, Review pulsar-cited-reviews-v7. Alte erfolgreiche Antworten bleiben Historie.
- Veraltete persistierte Retryjobs bleiben gespeichert, erzeugen aber keinen neuen bezahlten Request mit unpassender alter Eingabeversion.
- Ein regulärer neuer Analysezyklus kann wegen korrigierter Eingaben einen neuen GPT-Aufruf benötigen. Bestehende Vorfilter und Tagesbudgets gelten weiter.
- Logbuch-Erklärungen werden beim Lesen abgeleitet; keine Umarbeitung historischer Entscheidungseinträge.

## Nachkontrolle

Im neuen Versionsordner kann `python3 risk_basis_review.py --runtime runtime_status.json` den Risikobasisbefund rein lesend ausgeben. Fehlende historische Belege/PEP-Preisregel werden nicht durch diesen Aufruf ersetzt. Danach Diagnose 1.1.0 regulär 30 Minuten laufen lassen und ZIP zur Prüfung senden.
