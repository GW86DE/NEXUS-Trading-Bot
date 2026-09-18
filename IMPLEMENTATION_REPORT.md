# NEXUS 10.1.10 – aktueller Umsetzungsstand

Laufzeitbefundgetriebene Korrekturen fuer Positionsbewertung, FX-robuste Equity-Bremse, Diagnose-Sicherung/Retention und eToro-Ergebnisabgleich-Meldung; dazu manuelle USD/USDG-Freigabe, Backtest-WebUI, dynamische X-Konten-Registry und Quellen-Fixes. Keine neue Strategie und keine Lockerung der Order-/Risikoregeln. Vollstaendige technische Beschreibung: `NEXUS_10.1.10_IMPLEMENTATION_REPORT.md`.

# NEXUS 10.1.9 – aktueller Umsetzungsstand

Reportgetriebene Korrekturen fuer OKX-Risikokapital, Readiness und Diagnosebelege. Keine neue Strategie und keine Lockerung der Order-/Risikoregeln. Vollstaendige technische Beschreibung: `NEXUS_10.1.9_IMPLEMENTATION_REPORT.md`.

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

# NEXUS 10.1.6 – Umsetzungsbericht

Stand: 15.09.2026. Basis: NEXUS 10.1.5 und Diagnose `NEXUS_10_Diagnose_2026-09-15_07-08-46_331054_UTC_f842c661d4.zip`. Die Diagnose wurde bis zum Abschluss ausgewertet, nicht nur anhand ihres Dateinamens.

## Vorrangige Befunde und Korrekturen

| Befund | Umsetzung | Abgrenzung |
|---|---|---|
| PEP war bereits abgerechnet; eToro hatte keine offene Ergebnis-Kaufsperre | Abrechnung, Nutzerbeleg und Risikotag werden unverändert migriert und mit Regressionen geprüft | 1043 ist die Instrumentkennung. Der alte Sonntagsauftrag wird nicht als ausgeführter Verkauf behandelt |
| PULSAR meldete `database is locked` | WAL, echte Lesezugriffe, einmalige Schemaeinrichtung, Wartung erst bei Fälligkeit; Worker erholt sich in sichtbaren Wartestatus | Gespeicherte Fehlerhistorie bleibt erhalten |
| OKX wies XRP-EUR-Kauf mit 54092 zurück | Kontogebundene Fehlerbehandlung, keine blinden Wiederholungen, WebUI-Anleitung und einmaliger nächster regulärer Versuch nach eigener OKX-Bestätigung | Kein stellvertretendes Akzeptieren beim Broker; Verkäufe bleiben lokal getrennt |
| Genehmigte Entscheidung wurde trotz gescheiterter Order als Freigabe gezählt | Originalentscheidung und endgültiger Ausführungsbeleg getrennt speichern; Statusprojektion in Diagnose/WebUI berichtigen | Frühere nicht gespeicherte Fehlerdetails werden nicht nachträglich erfunden |
| Große Entscheidungs-IDs konnten durch Float-Konvertierung gerundet werden | Ganzzahlen/dezimale Ganzzahlstrings unverändert erhalten; ungenaue große Floats zurückweisen | Kein unbelegtes Zusammenführen historischer IDs |
| Nullvolumen in OKX-Kerzen | Original-Nullvolumen zählen und als Brokerherkunft in Diagnose/UI kennzeichnen | Qualitätsfilter bleibt unverändert; keine synthetischen Handelsdaten |
| GDELT-Timeout und Tradestie-TLS-Problem | Als externe Grenzen dokumentiert; bestehender Rückoff und Zertifikatsprüfung bleiben | Keine falsche Erfolgsmeldung oder Abschaltung von TLS-Prüfung |

## Dynamische X-Recherche

PULSAR übergibt bis zu fünf ausgewählte Aktien mit frischem, geprüftem FMP-Einzelaktienprofil. Daraus entsteht eine höchstens zwölf Einträge umfassende, ablaufende Warteschlange. Alle acht Stunden kann eine zusätzliche Anfrage bis zu zwei Aktien anhand von Cashtags und Firmennamen recherchieren. Sie ist unabhängig von der statischen Kontenliste. Gleiche Kandidaten werden fair nach letzter Abfrage bearbeitet; bloßes Wiedersehen setzt die Abrufhistorie nicht zurück.

Die vorhandenen drei allgemeinen Suchanfragen und maximal 13 Zählabfragen pro Tag bleiben erhalten. Drei gezielte Suchanfragen kommen hinzu. Reservierungen werden gemeinsam und atomar vor dem API-Aufruf gegen das unveränderte 15-EUR-Monatslimit geprüft. Es werden weder zusätzliche Nutzer-Expansionsdaten noch automatische Suchseiten angefordert. Antwortverlust verbraucht konservativ die Reservierung. Anbieterfehler, Preisverfall und ausgeschöpftes Budget erzwingen keine Umgehung.

Beiträge werden nur über exakte Cashtags bzw. abgegrenzte Firmennamen zugeordnet; Duplikate und Spam werden aussortiert. Die jeweilige Anfrage-ID bindet den Beitrag an den konkreten Lauf. Eine spätere leere Anfrage zählt keine alten Treffer nochmals als frische Antwort. Einfache positive/negative Schlagwörter erzeugen ausdrücklich vorläufige Kategorien. Fehlgeschlagene, veraltete oder leere Stichproben bleiben unbekannt. Rohtexte und Anweisungen aus Posts werden nicht als privilegierte KI-Anweisungen übernommen.

Die abgeleiteten Kategorien, Mengen, Quellen-/Zeitbelege und Ungewissheit fließen in PULSARs nächste reguläre KI-Vorprüfung/Bewertung ein. Auch bei kompakten KI-Paketen bleibt der Kandidatenkontext enthalten. X erhält keinen eigenen Handelsschalter und bestätigt keine Krise allein. Bestehende unabhängige Quellen-, Broker-, Bewertungs- und Risikoprüfungen gelten weiter. Ein mehrfach geposteter Ursprung zählt nicht automatisch als mehrere unabhängige Quellen.

Bei bereits durch Reddit entdeckten X-Kandidaten verhindert die Quellenkoordination, dass ein Duplikat entweder doppelt Plätze belegt oder unbemerkt sämtliche vorgesehenen X-Rechercheplätze verliert. Höchstens zwei reservierte X-Plätze von fünf Gesamtplätzen bleiben die Obergrenze.

## Oberfläche und Diagnose

**PULSAR**, **Quellen & X** und **Diagnose** zeigen aktuell ausgewählte Aktien, generierte Suche, Herkunft, Wartestand, Mengen, vorläufige Stimmung, Zeit und öffentliche Belege. Die PULSAR-Karten halten zusätzlich den Kontext ihrer letzten Bewertung fest; das wird nicht mit später eingegangenen Daten verwechselt. Dynamische Suchstrings werden sicher als Text dargestellt.

Diagnosewerkzeug 1.7.0 exportiert begrenzte Recherchemetadaten ohne Post-Rohtexte und führt OKX-Kontobestätigung, endgültigen Ausführungsfehler sowie originale Nullvolumen-Antworten gesondert auf. Diagnose und WebUI lesen vorhandene Belege; ein Seitenaufruf erzeugt keine bezahlte Recherche.

## Kostenannahme

Unter den bestätigten Standardwerten: 13 Zählabfragen + 6 Suchen × 10 Beiträge = 73 reservierte Einheiten/Tag. Bei 0,005 USD/Einheit, 1 USD = 1 EUR und Faktor 1,30 ergibt das maximal 14,7095 EUR für 31 Tage. Die atomare Monatsgrenze greift zusätzlich. Ein Anbieterlimit bleibt erforderlich, um auch andere API-Nutzer, Preise/Steuern und Wechselkursänderungen abzudecken. Bestehende GPT-Budgets bleiben separat; es wurde kein zusätzlicher GPT-Aufrufpfad allein für X geschaffen.

Offizielle Referenzen: [X-Preise](https://docs.x.com/x-api/getting-started/pricing), [Recent Search](https://docs.x.com/x-api/posts/search-recent-posts). Preise sind Annahmen des konfigurierten Budgets und keine garantierte Rechnung.

## Prüfung und Lieferung

Die abgeschlossenen Prüfergebnisse stehen in `TEST_REPORT.md`, die Offline-Protokolle in `validation`. Der Installer erhält ein geprüftes vollständiges ZIP und den sicheren Extraktor. Die paketbezogenen Prüfungen und verbleibenden Voraussetzungen stehen zusätzlich im mitgelieferten Prüfbericht und der Installationsanleitung. Keine Broker-/Quellenzugriffe oder Pi-Installation wurden durch diesen Entwicklungsauftrag ausgeführt.
