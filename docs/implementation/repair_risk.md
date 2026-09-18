# D01 – lesender eToro-Kontoabgleich und belegte Metadatenreparatur

## Ergebnis und verbleibende Grenze

Der Wartungspfad ist ausfuehrbar. Er liest das ausgewaehlte eToro-Konto und
liefert eine JSON-Datei mit aktuellen Kontobelegen, Integritaetspruefsummen,
Zeitpunkten und verstaendlichen Belegluecken. Er erzeugt keine Orders, aendert
keinen Schutz und setzt keine Risikozaehler zurueck. OKX wird nicht angesprochen.

**Die konkrete alte eToro-Datei mit OKX/USDC-Basis und leerem Kontoscope kann
ohne zusaetzlichen historischen Beleg noch nicht entsperrt werden.** Dies ist
keine abgeschlossene Reparatur der laufenden Installation. Ein neuer Handelstag
allein beseitigt die Kontopruefsperre nicht. Die Tests enthalten genau diesen Fall.

Ein automatischer neuer Risikozeitraum wird bewusst nicht implementiert, solange
Tagesstartbewertung, vollstaendige Tagesbuchungen und Kosten nicht ausreichend
belegt sind. Die aktuelle Equity wird niemals als rueckwirkender Tagesbeginn
eingetragen. Ein leerer Tradehistorienabruf beweist keine Null bei Kosten oder
Cashflows. Unbekannte historische P&L bleiben unbekannt und gespeichert.

## Bedienung auf dem Pi

Im installierten NEXUS-Verzeichnis zuerst den mitgelieferten SH-Starter fuer
die Risikopruefung aufrufen oder direkt:

```bash
./.venv/bin/python etoro_risk_maintenance.py --collect --environment DEMO --output eToro_Risikobelege.json
```

Die Ausgabe ist ein einmaliger rein lesender Abgleich. Bestehende Ausgabedateien
werden nicht ueberschrieben. Der Starter waehlt einen eindeutigen Namen.
DEMO ist Standard; LIVE muss ausdruecklich angegeben werden. Die jeweils
passenden Zugangsdaten werden lokal aus der bestehenden Konfiguration gelesen.
Sie werden nicht im Export gespeichert. Namen/CIDs aus `/api/v1/me` werden
ebenfalls nicht ausgegeben; verwendet wird derselbe Kontofingerabdruck wie im
Adapter. Positions- und Finanzbelege bleiben finanzielle Kontodaten.

Bereits erstellte Belege lassen sich ohne Netzwerk erneut pruefen:

```bash
./.venv/bin/python etoro_risk_maintenance.py --evidence eToro_Risikobelege.json
```

Der Exitcode `0` bestaetigt nur den gelungenen aktuellen Kontoabgleich.
Er ist **keine Handelsfreigabe**. `new_period_plan.status` bleibt mit den
vorhandenen Daten `REVIEW_REQUIRED`. Ein veralteter/fehlerhafter Abgleich liefert
Exitcode `2`. Gelesen wird der Dateiinhaltszustand ohne `RiskState.load()`, damit
der Aufruf nicht nebenbei die Tagesmigration ausloest.

**Naechster Nutzerschritt:** Den erstellten JSON-Export zur Auswertung bereitstellen.
Falls kein bereits separat aufbewahrter, korrekt zugeordneter Risiko-Checkpoint
existiert, wird zusaetzlich ein Kontoauszug des betreffenden eToro-Trading-Kontos
benoetigt: Konto/DEMO-LIVE, USD, Bewertungszeitpunkt samt Zeitzone am lokalen
Tagesbeginn und saemtliche nachfolgenden Ein-/Auszahlungen, Umbuchungen,
realisierten Ergebnisse, Gebuehren und Finanzierungen bis zum frischen Abgleich.
Bei einer anderen Stichtagszeitzone braucht es eine vollstaendige Ueberleitung.
Die passende Verfuegbarkeit speziell im DEMO-Konto ist noch nicht nachgewiesen.
Ein Screenshot des aktuellen Kontowerts oder der PEP-Stopwerte reicht dafuer nicht.

## Gesammelte und fehlende Belege

Der Collector fragt ueber GET `/api/v1/me` vor und nach der Sammlung, das
umgebungsspezifische Aggregate-Portfolio, P&L/Positionen und die paginierte
geschlossene Tradehistorie ab. Die API-Klasse erlaubt technisch ausschliesslich
diese GET-Pfade der ausgewaehlten Umgebung. Kein Daemon, Websocket oder
`connect()` wird gestartet; damit entstehen keine Hintergrundauftraege.

Positionsbelege behalten alle Zeilen einschliesslich Copy- und Shortpositionen;
fehlende IDs, Mengen/Seiten oder widerspruechliche Zeilen blockieren. Der
Tagesbeginn wird aus `LOCAL_TIMEZONE` bestimmt; der Datumsabruf der Historie
schliesst deshalb gegebenenfalls den vorausgehenden UTC-Tag ein. Rohzeilen
werden erhalten und nicht still als Tages-P&L summiert.

Der aktuelle Kontobeleg ist maximal 120 Sekunden gueltig. Fehlende explizite
USD-Waehrung, Konto-/Umgebungswechsel, nicht endliche Werte, unvollstaendige
Historie, korrupte Teilpruefsummen und zeitlich widerspruechliche Beobachtungen
blockieren. Eine waehrend der Sammlung geaenderte Risikodatei wird explizit
gemeldet und ist kein Apply-Beleg. Nach einer Aenderung erneut sammeln.

Offizielle Quellen, am 13.09.2026 nachgeschlagen:

- [eToro Balance-History](https://api-portal.etoro.com/api-reference/balances/get-account-balance-history)
  bietet Kontosalden am Tagesende. Das gelesene Schema enthaelt ein Datum, aber
  keinen zur NEXUS-Zeitzone nachgewiesenen Tagesstartzeitpunkt. Ein solcher
  Saldo wird daher nicht automatisch zur lokalen Tagesbasis umgedeutet.
- [eToro Cash-Account-Transaktionen](https://api-portal.etoro.com/api-reference/cash-accounts/list-cash-account-transactions-paginated)
  beschreibt Transaktionen eines Cash-Kontos. Das belegt keine vollstaendige
  DEMO-Trading-Kontohistorie einschliesslich Handelskosten.
- [eToro Dokumentationsindex](https://api-portal.etoro.com/llms.txt) fuehrt
  diese Funktionen getrennt von den DEMO-Trading-Endpunkten auf.

Diese moeglichen zusaetzlichen Datenquellen werden deshalb dokumentiert, aber
nicht ungeprueft als Ersatzbeweis aufgerufen. Es wurden im Entwicklungsumfeld
keine authentifizierten Brokerabfragen durchgefuehrt.

## Eng begrenzter Checkpoint-Apply

Der vorhandene identische Checkpointpfad bleibt nutzbar, ist jetzt aber ueber
die CLI bedienbar. Fuer Apply sind erforderlich:

1. Ein urspruenglicher eToro-Risikozustand aus einem separat aufbewahrten
   Diagnose-ZIP, mit bereits belegtem Kontoscope und passender Bewertungsbasis.
2. Identischer heutiger wirtschaftlicher Zustand in Ziel und Checkpoint:
   Tagesbeginn, letzte Equity, alle Zaehler, Verluste, unbekannte Ergebnisse
   und saemtliche sonstigen wirtschaftlichen Felder bleiben unveraendert.
3. Frischer erfolgreicher Collector-Beleg desselben Kontos/Umgebung und die
   vom Nutzer gepruefte aktuelle Zielpruefsumme.

Die CLI verlangt `--checkpoint-archive`, `--checkpoint-member`,
`--fresh-evidence`, `--expected-sha256` und ausdruecklich `--apply`.
Zunaechst ohne `--apply` pruefen:

```bash
./.venv/bin/python risk_basis_review.py risk_state_etoro.json --checkpoint-archive urspruengliche_Diagnose.zip --checkpoint-member ende/zustand/risk_state_etoro.json --fresh-evidence eToro_Risikobelege.json
```

Ein neu kopierter und umbenannter JSON-Zustand darf nicht als unabhaengiger
Checkpoint verwendet werden. Die CLI akzeptiert eine lose JSON-Datei nur fuer
den Wertvergleich, niemals fuer Apply. Bei der originalen Diagnose mit leerem
Scope wird der Archivpfad mit `RISK_ARCHIVE_HISTORICAL_SCOPE_UNPROVEN` blockiert.

**Herkunftsgrenze:** ZIP- und Member-Pruefsummen belegen unveraenderte Bytes ab
der Pruefung, keine digitale Signatur des Brokers. Ein nachtraeglich gefaelschtes
Archiv kann lokal nicht mathematisch als solches erkannt werden. Es darf nicht
neu aus der fehlerhaften Datei erzeugt werden; dieser Ablauf verlangt einen
tatsaechlich unabhaengig aufbewahrten Originalbeleg. Eine Self-Service-Kopie der
Legacy-Datei wird dadurch nicht zu einem historischen Nachweis.

Die komplette Lese-Pruef-Sicherungs-Schreibtransaktion haelt dieselbe
prozessuebergreifende Sperre wie der RiskState-Writer. Geaenderte Zielbytes
brechen Apply ab. Vor dem atomaren Replace liegt die byteidentische dauerhafte
Sicherung vor. Reparaturbeleg, Archivpruefsumme/Member und aktueller
Kontobeleg-Hash werden gespeichert. Wiederholung ist idempotent. Nur die
Kontozuordnungs-/Pruefmetadaten werden geaendert; Tages- und Lifetimewerte,
Botpositionen, Herkunft und unbekannte P&L bleiben bestehen.

Diese Bedingungen gelten auch fuer den direkten `apply_checkpoint()`-Aufruf,
nicht nur fuer die CLI. Der Archivmember wird innerhalb der Transaktion erneut
gelesen und gegen den Checkpoint verglichen. Alle wirtschaftlichen Felder des
v2-Schemas muessen explizit vorhanden und numerisch gueltig sein. Zwei gleich
unvollstaendige Dateien koennen sich nicht gegenseitig bestaetigen; fehlende
P&L/Kosten/Zaehler werden nicht durch Modell-Standardwerte ersetzt.

Ein sichtbarer Replace nach anschliessend fehlgeschlagenem Dateisystem-Sync
wird nicht als Erfolg gemeldet. Ein idempotenter Wiederholungsaufruf bestaetigt
die Dauerhaftigkeit erneut, bevor er Erfolg zurueckgibt.

## Abnahme

`tests/test_etoro_risk_maintenance.py` und
`tests/test_v101_risk_basis_review.py` pruefen erfolgreich: GET-Beschraenkung,
DEMO/LIVE-Trennung, lokalen Tagesbeginn, fehlende Waehrung/Mengen/Seite,
Doppelzeilen, unvollstaendige Historie, Kontoaenderung, stale/future/tampered
Belege, Archivherkunft, lose umetikettierte JSON, Verlust-/UNKNOWN-Erhalt,
OKX-Unveraendertheit, idempotente Reparatur, Datumswechsel und die bereits
vorhandenen Speicherfehler-/Concurrent-Change-Faelle. Alle Abfragen sind Fakes;
der isolierte Testlauf zeichnet keine Netzwerknutzung auf.

Die abschliessende fokussierte Abnahme umfasst 48 bestandene Tests. Zusaetzlich
abgedeckt sind rohe Nicht-Objekt-Historienzeilen vor Adapter-Normalisierung,
erneut gepruefte importierte Positions-/History-/Cash-Belege, GET-Payloads,
fehlende wirtschaftliche Felder und die nebenwirkungsfreie Konfigurationslesung
bei abgelaufener LIVE-Freigabe. Der volle Projekttest wird separat protokolliert.
