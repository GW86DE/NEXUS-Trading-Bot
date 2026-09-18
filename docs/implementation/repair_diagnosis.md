# Diagnosekorrekturen D16, D20 und D23

Umgesetzt auf Basis NEXUS 10.1.0; nach ausdrücklicher Freigabe Bestandteil von
NEXUS 10.1.1. Das eigenständig versionierte Diagnosewerkzeug trägt 1.2.0.

## Korrigiertes Verhalten

- Der generische SQLite-Export erfasst ausgelassene Zellen einzeln mit Spalte,
  Zeilenidentität und Grund. Tabellen mit Zellverlust werden als begrenzt
  ausgewiesen. Auch alte Exporte mit dem Platzhalter werden nachträglich in
  der Auswertung entsprechend markiert.
- Fehlende, unlesbare, übergroße oder ungültige `top5`-Inhalte liefern
  `card_count=null`. Nur eine belegte leere Liste liefert `0`.
- Ein PULSAR-Verlaufsobjekt wird nur über eine Referenz aus einem Messpunkt
  desselben Diagnoselaufs gelesen. Die bereinigte Objektprüfsumme muss stimmen.
  Der ursprüngliche `saved`-/`expires`-Stand muss dem Endexport entsprechen.
  Fremde Läufe, andere Revisionen, zukünftige oder beschädigte Belege ersetzen
  den Endzustand nicht. Andere belegte Revisionen stehen separat zur Verfügung.
  `observed_at` des Messpunkts und `cache_saved` bleiben verschiedene Zeitpunkte.
- Die direkte PULSAR-Abfrage ist auf 16 MiB je Zelle begrenzt. Ein darüber
  hinausgehender Verlust bleibt sichtbar und führt nicht zu null Karten.
- GPT-, FMP- und Massive-Zähler unterscheiden Startsammlung, Beobachtung,
  Endsammlung und Gesamtlauf. Phasen sind links geschlossen/rechts offen,
  nur die Endsammlung und Gesamtsumme schließen den Endpunkt ein. Keine
  überlappende Fünfsekundentoleranz. GPT-Starts und Abschlüsse haben getrennte
  Zeitzuordnungen; ausstehende Starts werden zum jeweiligen Phasenende gezählt.
- GPT wird nach lokaler Request-ID zusammengeführt. FMP-/Massive-Journaleinträge
  aus Anfang und Ende werden nach ID und Startzeit zusammengeführt; wiederverwendete
  IDs werden als Beleggrenze markiert. Massive-Reservierungen werden ausdrücklich
  nicht mit gesendeten HTTP-Anfragen gleichgesetzt.
- Fehlende Exporte liefern unbekannte Zähler mit separater beobachteter
  Untergrenze. Begrenzungen, Zellverluste, fehlerhafte Auditzeilen und fehlende
  IDs sind sichtbar. Vollständigkeit bezieht sich stets auf den exportierten
  Quellen-Snapshot, nicht auf danach entstandene Ereignisse.
- Quellenstatus wird nach Beobachtungszeit, kanonischem Anbieter, historischem
  Alias und bekannter Aktivierung in aktuell/historisch/unbekannt eingeordnet.
  Historische FMP-402- und deaktivierte finanzen.net-Meldungen erzeugen keine
  neuen Quellenwarnungen. Unbekannte Aktivierung wird nicht als deaktiviert
  erfunden. Tradestie hat einen separaten PULSAR-Schalter; dessen neue
  Status-/Backoff-Cachebelege werden mit ausgegeben.
- D20 verknüpft `input_sources` erfolgreicher validierter PULSAR-Stufen mit
  dem lokalen GPT-Ausführungsaudit. Eingeschlossene FMP-Fakten, ihre Datierung,
  Quell-ID, Kürzung und Eingabehash bleiben sichtbar. Eine leere
  `evidence_uses`-Tabelle widerlegt diese Verwendung nicht. Der Diagnosecode
  rekonstruiert die Eingabehashes nicht neu und behauptet keine kausale
  Handelswirkung.
- D23 weist fehlende Nettoergebnisse und unvollständige Gebührenbelege für
  geschlossene Trades getrennt nach Broker aus. Fehlende Werte bleiben `null`.
  Diese historischen Lücken werden nicht als zusätzliche offene Positionen
  und nicht als Performance von null dargestellt.

## Reproduktion aus einer vorhandenen Diagnose-ZIP

Die Funktion `NEXUS_10_Diagnose.replay_archive(path)` überprüft die gelesenen
Archivdateien gegen deren Manifest und erstellt die Auswertung rein im Speicher.
Sie entpackt und startet keinen archivierten Code, öffnet keine Brokerverbindung
und verändert keine Botdatei. Das Originalarchiv gehört nicht ins Quellpaket.

```python
from NEXUS_10_Diagnose import replay_archive, render_report
analysis, meta = replay_archive("/pfad/zur/Diagnose.zip")
print(render_report(analysis, meta))
```

## Eigenständiger Starter

Die separate Datei `NEXUS_10.1.1_Diagnose_Starten.sh` enthält den aktuellen
Collector komprimiert und prüft vor jeder Ausführung dessen SHA-256. Sie benötigt
Python 3 mit Standardbibliothek, aber keine benachbarte `NEXUS_10_Diagnose.py`.
`--werkzeug-pruefen` kontrolliert ausschließlich das eingebettete Werkzeug;
`--help` zeigt die Optionen, der normale Aufruf sammelt standardmäßig 30 Minuten.
Der optionale `--quelle`-Pfad bezeichnet die zu untersuchende Installation.

Im Quellpaket bleibt `NEXUS_Diagnose_Starten.sh` der kleine lokale Wrapper.
Der separate Starter wird außerhalb des Quellpaket-Manifests ausgeliefert, damit
kodierte Payloadbytes nicht irrtümlich von historischen Quelltext-Hygieneprüfungen
als Programmreferenzen behandelt werden. Die Prüfschicht wird nicht verändert.

## Prüfbelege

Original: `NEXUS_10_Diagnose_2026-09-13_15-26-49_510824_UTC_3c47dfecbf.zip`

SHA-256: `0c03e93a244fdbf1a07b1450762214ede15ad9b5ee209ed4d29d329697257de7`

| Prüfung | Ergebnis |
|---|---|
| Vollständige PULSAR-Karten | 5 aus SHA-geprüftem Objekt desselben Laufs |
| GPT-Request-IDs Gesamtlauf | 3 |
| GPT-Request-IDs Messfenster | 0 |
| FMP-Einträge Startsammlung / Gesamtlauf | 12 / 12 |
| Massive-Reservierungen Gesamtlauf | 0 |
| GPT-Requests mit belegter FMP-Eingabeverwendung | 3 |
| eToro geschlossene Zeilen ohne Nettoergebnis | 7 |
| Aktuelle Quellenwarnungen | GDELT, Nasdaq Halts |
| Historische FMP-402-Warnung als neuer Fehler | Nein |

Die drei GPT-IDs werden nicht mit den zusätzlich gespeicherten Tokenzeilen
ohne lokale ID addiert. Wegen dieser nicht eindeutig verknüpften Zeilen wird
die Zahl im Gesamtlauf als beobachtete Untergrenze bezeichnet. Das ist eine
Beleggrenze, kein Nachweis weiterer Aufrufe.

Bei eToro sind zusätzlich 26 geschlossene Zeilen mit mindestens einer
unvollständigen Gebührenqualitätsangabe vorhanden, darunter die sieben ohne
Nettoergebnis. Die übrigen historischen numerischen Ergebnisse erhalten durch
diese Diagnose keine nachträgliche Gebührenbestätigung.

`tests/test_repair_diagnosis.py`: 21 synthetische Fälle erfolgreich unter der
unveränderten Offline-Netzsperre (einschließlich echter temporärer SQLite-Datei,
Phasengrenzen, verspätetem Abschluss, falschen Fallbacks, unbekannten Exporten,
Inputbelegen und historischen Kosten). Der zusätzlich optionale Original-ZIP-Test
wurde separat mit explizitem Archivpfad unter derselben Offline-Netzsperre
erfolgreich ausgeführt. Keine Netzwerkereignisse.

Für die Auslieferung wurden zusätzlich der eigenständige Aufruf ohne
benachbarte Python-Datei, `--help`, `--werkzeug-pruefen`, Quellbytegleichheit
und der sichere Abbruch bei falscher Payload-Prüfsumme geprüft. Mit explizitem
Original-ZIP- und Starterpfad bestehen alle **24 Tests** unter unveränderter
Offline-Netzsperre, ohne Netzwerkereignisse.

Dies ist keine Handelsabnahme. Keine Orders, Brokeränderungen, neuen API-/KI-
Abrufe, Risiko- oder Schutzfreigaben wurden durch diese Arbeit ausgelöst.
