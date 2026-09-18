# NEXUS 10.1.0 – Änderungsprotokoll

Basis: unveränderte NEXUS 10.0.0; Auftrag: Korrekturen aus Diagnose 1.0.1 plus verständliches deutsches Logbuch. Keine neue Handelsstrategie, keine neue Hauptseite.

- N10-D01: Kontogebundene Risikobasisprüfung und bedingter, gesicherter Metadaten-Korrekturpfad; alle Kaufblockaden auch bei geschlossener Börse sichtbar.
- N10-D02: Nachweisgebundene SL-/TP-Preisnormalisierung und persistentes Journal für asynchrone Schutzänderungen. Unklare PATCH-Antworten dürfen keine blinden Wiederholungen auslösen. PEP ohne Preisregelbeleg bleibt unbestätigt.
- N10-D03: Gemeinsames persistentes Massive-Free-Limit, mindestens 15 Sekunden Abstand und vier Abrufe pro gleitender Minute; Cache, 429-Pausen, Neustart-/Parallelprozessschutz. FMP-News zuerst, Massive bei belegter Lücke ergänzend.
- N10-D04: Massive-Beschreibungen, Original-URLs und Veröffentlichungszeiten bleiben bis in die Analyse erhalten.
- N10-D05: Neueste vollständige OHLCV-Kerzen einschließlich Volumen in knappen GPT-Paketen bevorzugt; Kürzungen und Datenstand sichtbar.
- N10-D06: Nicht aufeinanderfolgende Geschäftsjahre ergeben keine behauptete Jahreswachstumsrate. Stable-Kennzahlen korrekt zugeordnet; Konflikte/fehlende Felder bleiben kenntlich.
- N10-D07: FMP-Markt-Kontext wird unabhängig vom Holdings-Timer bei Ablauf erneuert.
- N10-D08: Kerzenaktivität, Aktualität, synthetische Lücken und begrenzte Rohbelege je vollständigem Instrument und DEMO/LIVE-Umgebung; keine Abschwächung der Volumenregel.
- N10-D09: Signalkerzenstatus und Start-/Setup-Text nutzen die aktive Strategie (Sample 5m); die zusätzliche Sicherheits-Anlaufsperre bleibt separat erhalten.
- N10-D10: Granularer Broker-/Quellenstatus, Schutzpreisbelege, Mengen-/Gebührenvollständigkeit sowie tatsächlich übergebene GPT-Quellen in bestehenden Details.
- N10-D11: Nasdaq-Feedfehler nach Zeile und Ursache klassifiziert, gültige Teilbelege getrennt dokumentiert; keine Entwarnung aus unvollständigem Feed. Bestehende GDELT-/Tradestie-Pausen und TLS-Prüfung bleiben erhalten.
- N10-D12: Bereits bezahlte FMP-Jahresdaten zu Cashflow, Verschuldung, Kapitalrendite und Bewertung im Research-/Positions-/Universumskontext. ETF-/Fonds-Frühfilter vermeidet nutzlose Unternehmensabschlussabrufe.
- N10-D13: Diagnose 1.1.0: sichtbare Wahl 30-Minuten-Beobachtung/Sofortexport, effizientere Verarbeitung unveränderter PULSAR-Pakete, zusätzliche Belege und ehrliche unbekannte Zähler.
- N10-D14: Hervorgehobene verständliche deutsche Sätze für Entscheidungen, technische Details aufklappbar. Erklärungen entstehen aus gespeicherten Fakten, ohne zusätzliche GPT-Anfrage. Genehmigung ist keine Ausführung; grün erst bei belegtem vollständigem Fill.
- N10-D15: Beim ergänzenden Review gefundene Migrationsrisiken schließen: fremde Zugangsdaten im Ziel und bereits gefüllte Trade-DB bei leerer Entscheidungstabelle schützen. Neue Massive-/Schutzjournale inklusive SQLite-WAL übernehmen.

Die ursprünglichen eToro-Altbelege fehlen weiterhin; die konkreten zwei P0-Altfall-Sperren werden nicht durch angenommene Werte aufgehoben. Siehe KNOWN_ISSUES.md.
