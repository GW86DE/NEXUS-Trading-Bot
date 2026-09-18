# NEXUS 10.0.0

## Handelskorrektheit und Persistenz

- Die tägliche Equity-Basis hängt nicht mehr vom gehaltenen Symbolset ab. Ein neuer Wert im Depot hebt weder Tagesverlust noch eine erreichte Sperre auf.
- Kontobindung, Umgebung und Bewertungswährung werden konservativ behandelt. Historische ungebundene Risikobelege bleiben als solche erhalten.
- Kritische Datei- und Verzeichnis-fsync-Fehler werden nicht mehr als erfolgreiche dauerhafte Speicherung quittiert. Rekonstruierbare Telemetrie besitzt weiterhin einen ausdrücklich schwächeren Schreibvertrag.
- Stärkere native Ausführungsbelege bleiben bei schwächeren Folgeantworten erhalten. Verspätete Bestätigungen setzen Teilfüllungen nicht zurück auf OPEN.
- Fill-Tracker lesen, prüfen, vereinigen und schreiben unter derselben Prozesssperre. Mengen, Werte, Gebühren und native IDs können durch alte Instanzen nicht unbemerkt zurückgesetzt werden.
- Ledgerbuchung und zugehörige Lifecyclequittung verwenden dieselbe SQLite-Transaktion. Eine allgemeine neue Outbox wurde nicht eingeführt.
- Ein Persistenzfehler nach einem bewiesenen OKX-Fill bewahrt den bestehenden Schutzversuch und den offenen Wiederherstellungsauftrag; neue Einstiege dieser Domain bleiben gesperrt.

## Broker und Recovery

- Fehlende oder ungültige OKX-Antwortlisten werden nicht als leeres Konto interpretiert.
- Ältere Instrumentantworten verdrängen keinen neueren Cachezustand. Private Instrumentregeln bleiben maßgeblich.
- eToro-POSTs werden an der Transportgrenze auch bei versehentlicher Retry-Freigabe nicht automatisch wiederholt.
- Tatsächlicher REST-Versand, Antwort, fachlicher Erfolg, Fehler und WebSocket-Verbindung werden getrennt beobachtet.
- OKX-Schutzläufe erhalten gemessene Start-/Endzeit und Fehlerhinweise. Ein abgeschlossener Prüflauf ist keine pauschale Bestätigung sämtlicher Brokerschutzorders.
- Der September-Preflight liest nur vorhandene private OKX-Instrumentregeln. Er aktiviert keine Kontofunktion und benennt keine Position um.

## Research, PULSAR und GPT

- Historische Backtests weisen Drawdown einschließlich offener Bewertungen und abschließender Verkäufe aus; realisierte und Equity-Methode bleiben getrennt.
- Künstlich aufgefüllte Kerzen werden im neuen Standard nicht als handelbare Ausführungskerzen verwendet. Die bisherige Forschungsannahme bleibt ausdrücklich als Legacy-Option erkennbar.
- Der generische GPT-Cache berücksichtigt die tatsächlich aufgelöste Route, das Modell, den Prompt, das Schema, Eingaben und Werkzeugregeln.
- Lokaler Requeststart, Nichtstart, Cache, Timeout, Erfolg, Fehler und verworfene verspätete Antwort haben getrennte Metadaten. Ein lokaler Versand belegt keine Providerannahme.
- PULSAR zeigt tatsächlich gemessene Quellen-/Communitykriterien und konkrete Precheckgründe. Keine erfundenen Autoren-, Quellen- oder Qualitätsscores.

## Bestehende WebUI

- Granulare Brokerdiagnose, lesende native Orderdetails, begrenzte Orderhistorie und separate GPT-Aufrufdiagnose.
- Eindeutige Konto-/Umgebungssperre bei widersprüchlichen Laufzeitständen; kein positiver Abgleich aus einer leeren Liste.
- Fehlende Modus-, Kosten-, Positions- oder Systemdaten werden als unbekannt dargestellt.
- Favoritenfilter, Prüfzeit, zusätzliche Logfilter und vorhandene Pi-Telemetrie in den bestehenden Seiten.
- Begrenztes Polling ohne überlappende Aufrufe desselben Abonnements und mit Pause bei verborgenem Tab.
- Analysejobs erhalten atomare Startreservierung, Prozessidentität, Laufzeit-/Ausgabelimit und begrenzte numerische Threadzahl.
- Passwortneueinrichtung widerruft alte Sitzungen. Fehlende Zugangsdaten können keine Sitzung mit leerem Signaturschlüssel legitimieren.

## Im Integrationsprozess entdeckte Fälle

Die ersten vollständigen Prüfläufe waren nicht durchgehend grün. Aufgedeckte Fälle und Korrekturen werden im Testbericht mit ihren Nachläufen dokumentiert. Dazu gehören die unverknüpfte eToro-Risikokontobindung, ein durch einen unbeteiligten Schreibvorgang aufgehobener Persistenzhalt, unzureichende Tracker-JSON-Typprüfung, gemischte UI-Kontostände, sehr lange Logzeilen und veraltete Testeinbettungen der gemeinsamen JavaScript-Abhängigkeit. Testkriterien für Handelsbeträge, UTF-8 und Charts wurden nicht abgeschwächt.

## Bewusst unverändert oder vertagt

Keine neue Live-ML-Strategie, keine erzwungene Freqtrade-Renditegleichheit, kein automatischer Konten-/Währungsumbau, kein weiterer Orderwriter, keine Fremd-UI/CSS-Kopie, kein großer Chartingstack, keine unbewiesenen Performance-/Risikokennzahlen und keine automatische Reparatur fehlender historischer Brokergebühren.
