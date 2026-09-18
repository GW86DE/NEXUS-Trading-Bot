# Bekannte Grenzen und Releaseentscheidung – NEXUS 10

**Releasefähig für unbeaufsichtigten Handel: NEIN. NICHT RELEASE-FÄHIG.** Ein erfolgreich getesteter lokaler Umsetzungskandidat liegt vor; die folgenden Abnahmebedingungen sind noch offen. Die finale Zusammenfassung der tatsächlich ausgeführten Tests steht in `TEST_REPORT.md`.

| Schwere | Offener Punkt | Konsequenz / nächster Nachweis |
|---|---|---|
| Hoch | Kein Test auf dem vorgesehenen Raspberry Pi 5 mit echter SD-/SSD-Persistenz | ARM64, Last, Temperatur, RAM, Schutzzykluslatenz und Stromausfallverhalten praktisch abnehmen |
| Hoch | Keine echte eToro-/OKX-Demoabnahme mit aktuellen Kontobelegen | POST-Timeout, Teilfüllung, fehlendes WS-Ereignis, Reconnect, manueller Brokerclose und Restart am ausdrücklich freigegebenen Demokonto prüfen |
| Hoch | Historische Kontozuordnung und Kapitalflüsse nicht vollständig beweisbar | Ungebundene Altbelege bleiben ungebunden; Ein-/Auszahlungen oder geänderte Eigentumsklassifikation können konservative Sperren auslösen. Keine automatische Basisberichtigung |
| Hoch | Kein vollständiger atomarer REST-/WS-Abgleichvertrag für sämtliche Endpunkte | Lokale Requestgeneration ist kein Exchange-Wasserstand. Bestehende exakte Recovery bleibt notwendig; keine pauschale MATCHED-Anzeige |
| Mittel | Visuelle Desktop-/Tablet-/Smartphone-Abnahme offen | Der verfügbare Browser blockiert den lokalen Testserver mit `ERR_BLOCKED_BY_CLIENT`. Automatisierte HTML-/JS-/Breakpointprüfungen sind kein visueller Gerätetest |
| Mittel | GPT/PULSAR-Qualität nicht mit gelabelten echten Antworten gemessen | Quellen-/Ausführungslogik ist deterministisch geprüft; keine bewiesene Verbesserung der Prognosequalität oder Handelsrendite |
| Mittel | Allgemeine Ledger-/Risiko-/Registry-/Positions-Projektion bleibt mehrstufig | Die gemeinsame Ledger/Lifecycle-Transaktion schließt nur die belegte konkrete Commitlücke; vorhandene Recovery und Sperrbelege bleiben notwendig |
| Mittel | OKX-Septembermigration: konkrete Kontobetroffenheit unbekannt | Privaten Instrumentvertrag und Abrechnungswährungen am Konto prüfen; keine automatische Symbolumbenennung oder Aktivierung |
| Mittel | Historische fehlende Gebühren/Exitbelege können offen bleiben | Unbekanntes Netto bleibt unbekannt; bestehende konkrete Reparaturpfade brauchen native Belege, keine Annahmen |
| Mittel | Fill-Tracker wächst mit echten verarbeiteten IDs | Keine unsichere lexikografische Löschung mehr. Langfristige Archivierung benötigt belegte Replayschranken und Pi-Messung |
| Niedrig | Hart beendeter Analyse-Supervisor kann lebenden Child hinterlassen | UNKNOWN und weitere Starts gesperrt; lokale Prozessprüfung statt Kill eines bloß gespeicherten PID |
| Niedrig | Analysejob-Limits können umfangreiche legitime Forschung beenden | 30 Minuten, 5 MB Ausgabe, numerische Threads auf 1; größere Jobs gezielt beurteilen |
| Niedrig | Windows-Prozessgruppen-/Persistenzpfad nicht praktisch abgenommen | Zielplattform ist Linux auf Raspberry Pi; keine gleichwertige Windows-Zusage |

Keine Tabellenzahl in dieser Datei behauptet einen gegenwärtigen Brokerzustand. Testkonten, Fillmengen, Antwortfehler und Zeitverläufe wurden lokal kontrolliert erzeugt. Echte Brokerorders, GPT-Anfragen, Telegram-Nachrichten und Dienstumstellungen wurden in dieser Arbeit nicht ausgeführt.

Bewusst nicht umgesetzt: neue Live-ML-Logik, allgemeiner Eventbus/Message Broker, umfassende Outbox-Neuarchitektur, Fremddesign, automatische Favoritenkäufe, unbelegte Performance-/Risikoprozentwerte, automatische historische Konten-/Währungsreparatur. Diese Nichtumsetzungen sind keine versteckten Freigaben, sondern im Maßnahmenkatalog einzeln eingeordnet.
