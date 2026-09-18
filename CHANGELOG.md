# NEXUS 10.1.10 – Brokerstabilitaet, Backtest-Integration und dynamische Quellen

Die 10.1.9-Laufzeitabnahme (16.09.2026) bestaetigte die Risikokapitalreparatur (85.021,29 EUR ueber die USDC-Lane, erste OKX-Kaeufe) und fand vier neue Befunde, die 10.1.10 behebt: (1) Die Risikobasis bewertete eigene Positionen zum monoton steigenden Hoechstkurs statt zum Marktkurs. (2) Die Equity-Tagesbremse mass bei rein fremdwaehrungsfinanziertem Konto vor allem den USDC/EUR-Kurs; sie bewertet jetzt zusaetzlich eine native Reihe in der finanzierten Waehrung und bremst auf Handels- statt FX-Verlust. (3) Die Diagnose-Schrittsicherung der stark beschriebenen decision_history lief ins Zeitlimit und stoerte Bot-Abfragen (OperationalError); WAL-Datenbanken werden nun in einem Schritt gesichert, und eine taegliche Retention entfernt alte Kandidatenentscheidungen ohne Order-/Eventbeleg sowie Heartbeat-Telemetrie (niemals Orders, Fills, Trades oder Belege). (4) Ein neues unbekanntes Verkaufsergebnis meldet sich sofort mit Klaerungshinweis, statt die Kaufsperre nur im Log zu zeigen.

Neu: USD/USDG lassen sich als OKX-Abrechnungswaehrungen ausschliesslich manuell mit der Bestaetigung `WAEHRUNGEN FREIGEBEN` freischalten (OKX-USD-Umstellung, Parallelphase ab 23.09.2026); Standard bleibt EUR+USDC. Der read-only Universum-Backtest V5 ist als WebUI-Seite mit Hintergrundlauf, Live-Protokoll, HTML-Bericht und SHA256-gesichertem ZIP-Download integriert. Die X-Quellen erhalten eine beobachtungsbasierte dynamische Konten-Registry (Vorschlaege nur aus bezahlten Stichproben, Aktivierung nur durch den Nutzer mit Identitaetsvermerk, maximal 5, TTL/Quarantaene). GDELT erhaelt einen eigenen 30-s-Timeout (die Quelle antwortete real nach ~13,5 s), Tradestie nutzt die Hauptdomain mit gueltigem Zertifikat (kein TLS-Bypass). Desktop-Navigation buendelt seltene Seiten in einem "Mehr"-Menue; das OKX-Guthaben-Log schreibt nur noch bei Aenderung.

Handelsstrategien, Order-State-Machine, Schutzregeln und alle Sicherheitsgrenzen bleiben unveraendert. Details: `CHANGELOG_v10.1.10_NEXUS.txt`.

# NEXUS 10.1.9 – OKX-Risikokapital und Diagnosebelege

Die Diagnose vom 15.09.2026 zeigte 1.807 OKX-Entscheidungen am RISK_GATE mit `handelbar 0.00`. 10.1.9 vereinheitlicht deshalb die bereits freigegebenen EUR/USDC-Cash-Lanes in einer belegten Risikowaehrung, ohne USD/USDC/EUR-Paritaet anzunehmen. Nicht freigegebene Guthaben bleiben sichtbar und gesperrt. Diagnose 1.8.0 erfasst den Kapitalbeleg und kann uebergrosse PULSAR-Top-5-Caches ueber eine revisionsgebundene kompakte Projektion pruefen. Begrenzte Tabellenexporte werden als Untergrenze statt als technischer Fehler gekennzeichnet. Handelsstrategien und Sicherheitsregeln bleiben unveraendert.

Details: `CHANGELOG_v10.1.9_NEXUS.txt`.

# 10.1.8 – Korrektur der OKX-Kontovorpruefung

Die Kontovorpruefung verwendet fuer Trailing-Stop-Orders nun `move_order_stop`.
Fehler nennen GET-Endpunkt, freigegebene Pruefparameter, HTTP-Status und numerischen OKX-Code.
Freitextantworten, Zugangsdaten und Kontodaten werden nicht ausgegeben.
`--nur-pruefen` im Kontowechselwerkzeug fuehrt dieselben GETs ohne Aktivierung aus.
Alle sonstigen Konto-, Archivierungs- und Bestandssperren bleiben bestehen.
Offline-Tests ersetzen keinen Abruf mit dem neuen OKX-Demoschluessel.

# NEXUS 10.1.7

Neuer OKX-Kontokontext, expliziter archivierter Demo-Kontowechsel, konservativer Bestandsabgleich und vorgelagerte Risikoprüfung. Kein Abschluss und kein Storno allein wegen fehlenden Guthabens. Historische Schutzinformationen bleiben erhalten; aktuelle Bestätigung wird bei ungeklärtem Bestand zurückgenommen.

Grundlage: vollständiger Release 10.1.6, nicht der unverfügbare Zwischenstand aus dem alten Chat. Installation und Grenzen siehe INSTALLATIONSANLEITUNG_NEXUS_10.1.7_DE.md. Die konkreten Abschlussprüfungen werden im separat ausgelieferten NEXUS_10.1.7_Pruefbericht.md dokumentiert.


---

## Historischer Stand bis 10.1.6

# NEXUS 10.1.6 – 15.09.2026

- PULSAR-Datenbank: WAL, getrennte Lesetransaktionen, fällige Wartung, nachvollziehbare Worker-Erholung.
- OKX 54092: kontogebundene Kaufpause und genau ein regulärer Versuch nach eigener Brokerbestätigung.
- Exakte Entscheidungs-IDs; Originalentscheidung und endgültige Brokerantwort getrennt darstellen.
- Originale Nullvolumen-Antworten in Diagnose und WebUI sichtbar.
- Zusätzliche dynamische, budgetgebundene X-Recherche zu PULSAR-Kandidaten mit Stichproben-Hinweisen in PULSAR/Quellen/Diagnose.
- PEP-Abrechnung, Risikoregeln, TP/SL und Mehrquellenanforderungen unverändert erhalten.

# Aktueller Stand: NEXUS 10.1.5

Siehe `INSTALLATIONSANLEITUNG_NEXUS_10.1.5_DE.md` und `docs/NEXUS_10.1.5_Aenderungen.md`. Die folgenden Abschnitte dokumentieren frühere Versionen.

# NEXUS 10.1.4

- eToro: gemeinsame persistente REST-Limits und vollständige Retry-After-Fristen; keine automatischen POST-Wiederholungen.
- eToro: dauerhafte kontogebundene private Ereignisse, begrenzte Wiederzuordnung, exakte REST-Prüfung nativer Abschlussbelege.
- PEP: Broker-History-Ergebnis getrennt speichern/anzeigen; bestätigte Einstiegskosten bleiben erhalten; widersprüchlicher Kostenumfang ausdrücklich ausgewiesen.
- X: automatische tägliche Konten-/Unternehmenssuche ohne Pflicht-Aktienliste; Kandidaten zuerst durch die FMP-Aktienprüfung, danach begrenzte PULSAR-Rechercheplätze.
- Quellen: Artikelduplikate und Anbieterlabels begründen keine unabhängige Bestätigung; vorsorgliche Nachrichten-Krisenpause benötigt mehrere frische Ursprünge.
- PULSAR: Stichproben, gemessene Tageszahlen, zensierte Toplisten-Tage und unbekannte Tage getrennt; Quellenrollen und Verarbeitung sichtbar.
- WebUI/Diagnose: Kandidatenfluss, Abdeckungsfehler, Normierungswechsel, News-Risikohinweise, Broker-History-Konflikte und private Stream-Metadaten ergänzt.
- Migration: neue REST-Budget- und Ereignisdatenbanken über konsistentes SQLite-Backup erhalten.

Frühere Berichte liegen unter `docs/history/10.1.3/`.
