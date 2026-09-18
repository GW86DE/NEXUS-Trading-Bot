# Anzeigen zu den offenen Diagnosebefunden

Diese Änderungen betreffen die Darstellung vorhandener Belege. Farben, Navigation,
Strategie-, Volumen-, Risiko- und Ausführungsregeln bleiben erhalten.

## D02: PEP getrennt beurteilen

Die offenen Aktienpositionen zeigen Eigentum, aktuelle Schutzprüfung und
Kontorisikoprüfung getrennt. Eine `protection_plan_history` wird als aufklappbare
Original-/Änderungshistorie gezeigt, inklusive vorheriger und wirksamer Werte,
Entscheidungszeit, Entscheidungs-ID und betroffener Positions-IDs. Ein früherer
Übernahmebeleg ersetzt keine frische Brokerbestätigung. Die Anzeige löst weder eine
Übernahme noch einen Schutzauftrag aus. Risikobelege müssen zur Broker-/Konto-/
Umgebungsidentität der Position passen. Fremde oder veraltete Belege werden nicht
als aktuelle Freigabe ausgegeben. Der verständliche Hauptgrund für einen
Preisabgleich behauptet keine fehlende Eigentumszuordnung und keinen fehlenden Stop.

## D17: Kerzen und DEMO-Aktivität

Die Übersicht unterscheidet Datenlieferung/Rohbeleg, Datenfrische und beobachtete
Handelsaktivität je Instrument und Zeitraster. Aktuelle Kerzen mit Nullvolumen
bleiben als solche sichtbar. Nullvolumen wird weder als REST-Ausfall noch als
fehlender Positionsschutz interpretiert. DEMO-Aktivität erlaubt keine Folgerung
über LIVE-Volumen. Die vorhandene Volumenregel bleibt unverändert.

## D20: FMP in GPT-Paketen

Die Einstellungen lesen passiv `pulsar_research.sqlite`: aktuelle `top5`-Karten
und höchstens 40 jüngste gespeicherte Bewertungen. Nur positive `ok`-Antworten mit
gültiger Eingabeprüfsumme und `INPUT_OF_VALIDATED_RESPONSE` zählen; einzelne
FMP-Quellen müssen `included=true` und eine Beleg-ID haben. Wiederholte Kopien
eines gemeinsamen Pakets werden anhand Phase/Prüfsumme/Symbol/Quellen-ID dedupliziert.
Dies ist weder ein HTTP-Aufrufzähler noch ein 30-Tage-Gesamtzähler. Die Ansicht
belegt die Übergabe, keine Modellgewichtung oder Handelswirkung. Leere zusätzliche
`evidence_uses`-Zähler verdrängen diesen unabhängigen Nachweis nicht.

SQLite wird ausschließlich `mode=ro` geöffnet. Lesedauer, Datensatz- und
Gesamtgrößen sind begrenzt; unlesbare oder zu große Werte führen zu sichtbarer
Unvollständigkeit, nicht zum Schluss, dass FMP ungenutzt sei.

## D21: wiederholte Anlaufsperren

Auf jeder Logbuchseite werden ausschließlich Anlaufsperren ohne Order-/
Ausführungsbelege gruppiert. Der Schlüssel umfasst Broker, gespeicherte Umgebung,
Konto, lokales Datum, Symbol, Instrument-ID, Zeitraster, Strategiemodus/-version/
Parameterhash, Richtung, Sperrquelle und Grund. Nur ausdrücklich erkannte
Restwartezeiten werden für den Gruppierungsschlüssel entfernt; die geplante
Freigabeuhrzeit und sonstige Zahlen/Gründe bleiben Bestandteil des Schlüssels.

Alle einzelnen Entscheidungen bleiben in SQLite erhalten und in der Gruppe
aufklappbar, einschließlich ID, Zeitpunkt, verständlicher Erklärung, originalem
Grund, Messwerten, Quellen und Ausführungsstatus. Anzahl und Zeitspanne sind
ausdrücklich auf die sichtbare Seite bezogen. Die Seitengröße ist 15/50/100 wählbar
(Standard weiterhin 15). Abweichende Gründe bleiben eigene Zeilen/Gruppen.

Bei historischen Anlaufsperren können Konto oder Instrument-ID fehlen. Solche
Zeilen werden ausschließlich als visuelle Altbeleggruppe zusammengefasst; die
fehlende Bindung steht ausdrücklich im Text. Niemals werden unbekannte und
bekannte Konten/Instrumente gemischt oder Positionsidentitäten daraus abgeleitet.
Ohne gespeichertes Zeitraster/Strategiemodus bleibt eine Zeile einzeln. Neue
Kryptoentscheidungen schreiben dafür zusätzlich bereits lokal verfügbare
Konto-/DEMO-/Instrument-/Zeitrastermetadaten. Dieser kleine Telemetrieeingriff
fragt keinen Broker ab und ändert weder Gateausgang noch Ausführungsroute.

Die Universumsdiagnose benennt den tatsächlichen Broker; der eToro-Aktienkatalog
wird nicht länger als von OKX gemeldeter SPOT-Katalog beschrieben.

## D22: optionale Quellen und Historie

Die Übersicht zeigt Quellenwirkung und Fehlerhistorie gemäß `error_scope`,
`error_age_seconds`, `error_at`, `last_error_detail` und `impact`. Historische Fehler
werden als vergangene Belege bezeichnet. Frische Nasdaq-Teilbelege sind als
Teilbelege sichtbar; fehlende Instrumente beweisen keine Abwesenheit. PULSAR zeigt
die getrennten lokalen `optional_sources` sowie Auswahlbelege zu ausgeschlossenen
ETFs/Fonds und noch ungeklärten Instrumenttypen. Ältere Auswahlbelege bleiben
als ältere Stände gekennzeichnet.

## D23: historische Abschlusskosten

Ein geschlossener Trade mit unbelegten Kosten bekommt in der Anzeige
`netto_pnl=null` und `gebuehren=null`. Ein gespeicherter ungeprüfter Altwert bleibt
unter `unconfirmed_net_audit` erhalten; das Handelsbuch wird nicht verändert.
Bestätigte echte Nullgebühren bleiben null Euro und werden nicht gelöscht. Offene
Positionen werden nicht durch diese historische Ergebnisprojektion verändert.
Die Ansicht bezeichnet den Klärungsfall als historischen Abschluss und erklärt,
dass daraus kein aktueller Positions-/Schutzfehler folgt.

## Prüfung

`tests/test_repair_ui.py` prüft Kontextgrenzen der Gruppierung, bekannte/fehlende
Identitäten, Deduplizierung und Integrität der FMP-Pakete, read-only SQLite inklusive
Dateihash, Kosten unbekannt gegenüber bestätigter Null, Kontobindung, korrekten
Brokertext und lokale Telemetrie vor einer abgelehnten Order. Ein Node-Harness
führt die echten Renderer für Gruppen, Planhistorie, Nullvolumen und historische
Fehler aus und prüft HTML-Escaping. Bestehende WebUI-/Kerzen-/Crypto-/Accounting-
Regressionen werden zusätzlich im isolierten Projekt-Harness ausgeführt.
