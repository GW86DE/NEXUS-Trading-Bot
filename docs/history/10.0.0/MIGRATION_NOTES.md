# Migration und Zustandserhalt – NEXUS 10

**Keine Migration einer echten Installation wurde ausgeführt.** Die Verfahren werden mit temporären Dateien und bestehenden Migrations-/Updatefixtures geprüft. Das Paket enthält keine Kontozugangsdaten, aktiven Handelszustände oder neue Live-Freigaben.

## Vor einer späteren Umstellung

1. Den tatsächlichen Quellordner und die laufenden Core-/WebUI-Dienste eindeutig bestimmen. Eine historische Releasebezeichnung genügt nicht als Identitätsnachweis.
2. Sämtliche zustandsschreibenden Prozesse kontrolliert beenden. Offene native Brokerschutzorders nicht als Teil eines Updates entfernen.
3. Den gesamten Quellordner einschließlich SQLite, Journals, JSON, Einstellungen, Zugangsdaten, Analysehistorie und vorhandenen Sicherungen geschützt sichern. SQLite über den bestehenden Backup-Pfad konsistent übernehmen; eine einzelne laufende `.sqlite` ohne WAL-Kontext ist kein vollständiges Backup.
4. In einen frischen NEXUS-10-Zielordner übernehmen. Bereits vorhandenen fremden Handelszustand nicht zusammenführen. Die bestehenden strengen Vorprüfungen und `quick_check` müssen erfolgreich sein.
5. Offlinevalidierung durchführen. Danach ist eine getrennte Demoabnahme mit eindeutigem Broker/Konto/Modus erforderlich. Eine kopierte alte LIVE-Freigabe wird nicht übernommen.

Die bestehenden Updatewerkzeuge sichern und stagen den Zustand vor Promotion. Nach einem gestarteten neuen Dienst darf kein alter Zustand blind zurückkopiert werden: Der Broker könnte inzwischen neue Ausführungen besitzen. Erst Broker, Registry, Ledger und Exitjournal abstimmen.

## Konkrete Schema- und Datenänderungen

| Bereich | Änderung | Erhalt / Validierung |
|---|---|---|
| Risiko-JSON | Additive Felder für Basisprüfung und Scope; normalisierte Bewertungsbasis unabhängig von Symbolen | Exakte Originaldatei vor erster Konvertierung als `.pre-v10-<hash>.bak`; Quelle und Backup validieren; wiederholbarer Lauf unter Prozesssperre |
| Historische Risikobelege | Keine nachträgliche Zuordnung zu einem heute eingestellten Konto | `LEGACY_UNASSIGNED` bleibt sichtbar; Beträge und Tagesstopps werden nicht gelöscht |
| Ledger und Lifecycle | Gemeinsamer Commit für bestehende Tabellen | Keine neue Outbox, keine Umnummerierung und keine neue SQL-Schemamigration hierfür |
| Fill-Tracker | Strenge JSON-Strukturprüfung und sperrgeschütztes Merge | Echte IDs und kumulierte Belege bleiben erhalten; korrupte Daten erzeugen Fehler, keinen gesunden leeren Tracker |
| GPT-Cache | Neue semantische Schlüsselfassung | Alte Datei bleibt erhalten; unpassende Altschlüssel werden nicht für neue Modell-/Promptanfragen genutzt |
| GPT-Audit | Additive Ausführungsmetadaten | `ai_usage_audit.jsonl` wird jetzt ausdrücklich mit übernommen; Altzeilen ohne Startbeleg bleiben unbekannt |
| PULSAR | Additive Quellen-, Community- und Ausführungsdiagnose | Vorhandene Kandidaten, Kosten, Reservierungen und SQLite-Belege bleiben erhalten |
| Laufzeitdiagnose | Additive REST-/WS-/Schutzmessfelder | Rekonstruierbare Telemetrie; veraltete Daten erteilen keine neue Freigabe |
| WebUI-Zugang | Neueinrichtung rotiert Sitzungsschlüssel | Bestehende Zugangsdaten bleiben beim normalen Update erhalten; bewusste Passwortneueinrichtung beendet alte Sitzungen |
| Analysejobs | Prozessidentität und begrenzte Ausführung | Laufende Jobs nicht in eine neue Installation verschieben oder erneut starten; alte Jobdateien bleiben im gesicherten Quellordner. Unsichere lokale Altpids werden UNKNOWN, nicht automatisch beendet |

## Fehlerbehandlung

Ein fehlgeschlagener Datei-fsync lässt den alten Zustand unbestätigt; ein Verzeichnis-fsync kann erst nach sichtbarem Replace fehlschlagen. Deshalb bedeutet ein solcher Fehler weder garantierten Rollback noch garantierte Dauerhaftigkeit. Der Geldpfad muss gesperrt bleiben, bis der passende konkrete Vorgang dauerhaft wiederhergestellt ist. Ein Statusabruf oder unbeteiligter Positionszählerwrite ist kein Recoverybeleg.

Nicht lesbare oder nicht unterstützte Risikostrukturen werden nicht mit Nullzählern überschrieben. Fehlende Brokerbeträge, Gebühren, Orderbestätigungen oder Accountbindungen werden nicht ergänzt. Die ursprüngliche Sicherung bleibt für Diagnose erhalten. Risk-, Registry-, Positions- und Journalzustand benötigen trotz gemeinsamer Ledger/Lifecycle-Transaktion weiterhin ihre bestehenden Recoverypfade.

## Noch erforderliche Abnahme

Auf dem Ziel-Pi: Backup praktisch rücklesbar prüfen, Migration mit einer geschützten Kopie der echten Daten ausführen, Quellen-/Zielzählungen und kritische IDs vergleichen, offene Orders/Teilfüllungen über Neustart prüfen, lokale eToro-/OKX-Sperrwirkung getrennt verifizieren und Systemlast messen. SD-Karten-Stromausfallverhalten und reale Demo-/Live-Kontobindung sind durch Linux-Offlinetests nicht ersetzt.
