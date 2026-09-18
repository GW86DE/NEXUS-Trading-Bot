# NEXUS 10.1.3 – 14.09.2026

- PEP: Orderausgang und Positionsabschluss strikt getrennt; fehlerhafte Altprojektionen mit Audit wiederholbar repariert; keine erneute Schließorder.
- Historische/current Ergebnisperioden beleggebunden abgegrenzt; aktuelle Kaufentscheidung auch im Dashboard sichtbar; vollständige v2-Abschlusskosten können Nettoergebnisse nachtragen.
- Frische Brokerkurse/Handelbarkeit für Ausstiege; explizite Gewinnmitnahme mit aktuellen Kosten, Sicherheitsausstiege unabhängig; native SL/TP unverändert.
- X: optionaler gemeinsamer Sammler, dauerhaft reserviertes Monatsbudget höchstens15EUR, tägliche Counts und drei kleine Poststichproben; abgeleiteter Kontext im bestehenden Luna-Aufruf.
- WebUI-Menü Quellen & X, sichtbare Diagnose, Provider-/Verarbeitungsketten samt Prüfkennungen; kein X-Rohtext-/Schlüsselexport.

---

# NEXUS 10.1.2 – Änderungen

- Kontogebundene neue eToro-Risikoperiode für den bekannten unbegonnenen Altzustand; ursprüngliche Datei, aktuelle Belege, alte Verluste und unzugeordnete Historie bleiben erhalten.
- Zusätzliche Schutzbelege können positionsbezogen fehlschlagen, ohne den gesamten Depotabgleich durch eine solche Ausnahme abzubrechen. Globale Depot-/Kontofehler bleiben blockierend.
- Offene Exit- und Schutzjournale fließen in Kaufbereitschaft und WebUI-Abgleich ein. Ein Auftrag ohne Terminalbeleg wird nicht als abgeschlossen ausgegeben.
- Für eindeutig eigene Restpositionen mit offenem Schutzabgleich bleiben frische Client-Stop-, Gewinnziel- und Zeitstop-Entscheidungen erreichbar. Ein offener älterer PATCH wird deshalb nicht als erledigt oder überholt markiert.
- Diagnosewerkzeug 1.3.0 in WebUI: Sofort-/30-Minuten-Modus, Prozesssperre, Fortschritt, erhaltene ZIP, authentifizierter Download und optionaler Telegram-Versand.
- PULSAR-FMP-Links öffnen den tatsächlich gespeicherten, prüfsummengeprüften Beleg. Keine Weitergabe des API-Schlüssels an den Browser. GPT-Erfolg wird als „Antwort empfangen“ bezeichnet; doppelte Ablehnungsgründe zusammengeführt.
- Beobachtete Quellen-Seiten und Zeitpunkte werden für die Aufmerksamkeitsbasis gespeichert. Fehlen in einer Topliste erzeugt keine erfundenen Nullwerte oder Vergleichstage.
- Nasdaq nutzt zusätzlich eine aktuelle Channel-pubDate als Feedzeit; alte unbestätigte Haltzeilen und aktuell wirkende Anbieterpausen werden ausdrücklich ausgewiesen.

Die Korrekturen der Version 10.1.1 (Zellverluste, getrennte Diagnosephasen, ETF-Filter, Teilbelege und Anlaufsperrenanzeige) bleiben enthalten. Volumenprüfung, Massive-Limits und GPT-Takt bleiben erhalten. Historische Berichte liegen unter docs/history/10.1.1/.
