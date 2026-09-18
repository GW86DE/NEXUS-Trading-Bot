# NEXUS 9.5.3 — Korrekturen

Zwei Befunde vom 02.09.2026, beide an deinen echten Daten geprüft:
die **Doppelbuchung von ADBE** und der **OKX-Ausfall um 12:30 Uhr**.

---

## 1. ADBE stand mit −344,76 USD zweimal im Ledger

Deine Abfrage hat es belegt:

```
ADBE  positionId 3592625451, entry_order 378375675
  trade 35   ba32…   decision_id NULL   LEGACY_UNLINKED   exit_order = Entry-Order   −344,76
  trade 36   664e…   decision_id 4153…  LINKED            exit_order leer            −344,76
                     beide ausgestiegen_am 01.09. 15:13:35.230 — auf die Millisekunde

CRM   positionId 3592539858, entry_order 378375526
  trade 34   ba32…   decision_id NULL   LEGACY_UNLINKED   +171,68
  trade 37   664e…   decision_id 1518…  LINKED            netto_pnl NULL
                     ausgestiegen_am 02.09. 08:19 — der lokale Buchungszeitpunkt
```

**Ursache:** Der Ledger-Suchschlüssel lautet
`broker + broker_account_fingerprint + broker_position_id + entry_order_id`.
Der Kontofingerprint steht darin. 9.5.1 hat das Fingerprint-Verfahren
gewechselt, ohne das Ledger mitzunehmen — dieselbe Brokerposition lag danach
unter zwei Fingerprints, und beide Zeilen konnten unabhängig geschlossen
werden. An **zehn** Stellen in `trade_ledger.py` wird auf dieses Feld
gefiltert.

Besonders ärgerlich: `reconcile_closed_trade_exact()` wurde ausdrücklich
gebaut, um den 9.5.0-Fehler zu reparieren, bei dem die Entry-Order als
`exit_order_id` gespeichert wurde. Zeile 34 und 35 haben genau diesen Fehler.
Die Reparaturfunktion hat sie nie gefunden, weil der Fingerprint nicht passte.

**Mein Anteil:** Zeile 37 wurde am 02.09. um 08:19:07 geschlossen, also unter
9.5.2. Meine Änderung hat den Worker erstmals an diesen Datensatz gelassen —
richtig und nötig, aber ich habe die Reconciliation migriert und das Ledger
nicht mitgenommen. Die ADBE-Dublette ist älter (beide Schließungen vom
01.09.), die CRM-Geisterzeile geht auf mich.

### Was geändert wurde

**Kontoaliase im Ledger.** Neue Tabelle `account_aliases` plus
`konto_identitaeten()`. Jede Suche erweitert den aktuellen Fingerprint um
seine **belegten** Vorgänger; geschrieben wird weiterhin ausschließlich unter
dem aktuellen. Ein Alias entsteht nur aus einem Brokerbeleg — dem
erfolgreichen Order-Lookup gegen das aktuelle Konto —, nie aus einer
Namensregel. Der Beleg wird von `etoro_reconciliation` jetzt auch ins Ledger
geschrieben.

Ein Cache liegt davor: ohne ihn öffnete jede Suche die Datenbank, und die
Testsuite wurde zehnmal langsamer.

**Zusammenführung ohne Löschen.** `fuehre_dubletten_zusammen()` erkennt
Gruppen ausschließlich über `broker + positionId + entry_order_id`. Symbol,
Menge oder Preisnähe sind kein Merkmal. Die Zeile mit belegter Identität
(decision_id + LINKED) bleibt bestehen; die Altzeile bekommt
`superseded_by = <trade_id>` und fällt aus jeder Auswertung, bleibt aber
vollständig als Auditspur erhalten.

Hat die Primärzeile gar kein Ergebnis, gilt der Abschluss der Altzeile
vollständig — einschließlich Ausstiegszeit und -grund. Sonst behielte CRM den
lokalen Buchungszeitpunkt statt des echten Brokerabschlusses.

Der Lauf ist idempotent und läuft einmal je Prozessstart automatisch.

**Warum eine eigene Spalte und nicht `link_status`:** `init_ledger()` schreibt
`link_status` bei **jedem** Aufruf neu (`decision_id NULL` → LEGACY_UNLINKED,
sonst LINKED). Ein dort gesetzter Status wäre beim nächsten Ledgerzugriff
wieder weg. `superseded_by` nennt außerdem die Zeile, die übernommen hat.

**Sicherheitsgrenzen.** Unterschiedliche Mengen oder Einstände bedeuten
Teilverkauf oder zwei echte Positionen — dann wird nichts angefasst, sondern
mit Grund protokolliert.

### Wirkung an deinen Zeilen

```
Tagesergebnis VOR  der Zusammenführung:  −517,84 USD
Tagesergebnis NACH der Zusammenführung:  −173,08 USD
```

−344,76 (ADBE, einmal) + 171,68 (CRM). CRM trägt danach den echten
Brokerabschluss vom 01.09. 14:26:32 statt der Buchungszeit vom 02.09.

---

## 2. Der OKX-Ausfall am 02.09. um 12:30 Uhr

Dein Test hat die Lage genau gezeigt:

```
/public/time          (öffentlich, ohne Demo-Header)   OK    0,18 s
/account/instruments  (privat + x-simulated-trading)   503
/account/config       (privat + x-simulated-trading)   503
/account/balance      (privat + x-simulated-trading)   503
```

Der öffentliche Pfad lief einwandfrei. Ausgefallen war ausschließlich der
**OKX-Demo-Handelsdienst**. Bei falschen Zugangsdaten käme 401, bei
Ratenbegrenzung 429. Das ist ein OKX-seitiger Ausfall — nichts, was NEXUS
beheben kann.

Der Wiederverbindungsversuch lief korrekt weiter (`BACKOFF = 5, 15, 30, 60,
120, 300`). Falsch war, was NEXUS **daraus gemacht** hat:

### a) Das Universum fiel auf null

Um 12:33:46, mitten im Ausfall:

```
universe_diagnose: … → 0 geeignete Basen → Kern 0/20 → 0 im Pool → 0 bewertet
```

Der Instrumentenkatalog lag noch im Cache, die Marktdaten fehlten — der Lauf
lief also durch und rechnete das Universum leer, einschließlich des festen
Kerns. Nach der Erholung hätte der Bot ohne Kern dagestanden.

Neu: Der Lauf rechnet bei nicht authentifiziertem Broker gar nicht erst. Und
ein Ergebnis mit **0 Kandidaten bei zuvor bestehendem Universum** wird
verworfen statt angewendet — mit Meldung. Ein Brokerausfall friert das
Universum ein, er leert es nicht.

### b) Die Fehlermeldung nannte nicht, was ausfällt

25 Minuten lang stand im Log nur `OKX-Serverfehler HTTP 503.` Neu:

```
OKX-Serverfehler HTTP 503 (Demo-Handelsdienst GET /api/v5/account/balance).
OKX-Serverfehler HTTP 503 (Marktdaten GET /api/v5/public/time).
```

Wichtig dabei: Ein 503 kommt als HTML-Seite vom Gateway, nicht als JSON.
In der Praxis feuert deshalb der **Nicht-JSON-Zweig** — beide sind jetzt
korrigiert, sonst wäre die Änderung wirkungslos geblieben.

### c) Ein Serverfehler bekommt einen eigenen Takt

```
BACKOFF               = (5, 15, 30, 60, 120, 300)   Auth-/Konfigfehler
BACKOFF_SERVERFEHLER  = (5, 10, 20, 30, 45, 60)     5xx und 429
```

Bei 300 Sekunden wäre eine wiederhergestellte Verbindung bis zu fünf Minuten
unbemerkt geblieben — bei 5-Minuten-Kerzen eine ganze Kerze.

---

## Tests

**1114 bestanden**, 1 übersprungen, 184 Subtests.

Neu: `tests/test_v953_ledger_dubletten.py` — 21 Tests gegen deine echten vier
Ledgerzeilen (`tests/fixtures/echt_dubletten_trades.json`).

Kein Test darin durchsucht Quelltext. Der HTTP-Test fährt den echten
Aufrufpfad mit einer ersetzten Sitzung; die Universumstests prüfen, dass ein
leeres Ergebnis **nicht angewendet** wird — der Ersatz wirft eine Ausnahme,
falls es doch geschieht.

Abgedeckt sind auch die Grenzfälle: Probelauf schreibt nicht, nichts wird
gelöscht, Idempotenz, `init_ledger()` überschreibt die Ersetzung nicht,
unterschiedliche Mengen sind keine Dublette, verschiedene positionIds werden
nie zusammengeführt, ein fremdes Konto profitiert nicht vom Alias.

---

## Nach dem Update prüfen

```bash
# 1. Die Zusammenführung im Log
journalctl -u tradingbot-pi5.service --since "-10 min" | grep -i "zusammengefuehrt"

# 2. Das Tagesergebnis
#    ADBE darf nur noch einmal auftauchen, CRM mit +171,68 und Abschluss 01.09.

# 3. Die Ledgerzeilen
./.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('file:decision_history.sqlite?mode=ro', uri=True); c.row_factory = sqlite3.Row
for r in c.execute('''SELECT trade_id, symbol, superseded_by, ausgestiegen_am,
                             exit_grund, netto_pnl FROM trades
                      WHERE broker_position_id IN (\"3592625451\",\"3592539858\")
                      ORDER BY trade_id'''): print(dict(r))
"
```

Erwartet: 34 und 35 mit `superseded_by` 37 bzw. 36; 36 und 37 mit
`superseded_by = None`.

---

## Offen

- Der OKX-Demo-Ausfall selbst ist nichts, was NEXUS beheben kann. Die
  Änderungen sorgen dafür, dass er sichtbar ist und keinen Folgeschaden
  anrichtet.
- Die Doppeleinträge im **Orderregister** (jede eToro-Order unter `orderId`
  *und* `referenceId`) sind weiterhin drin. Sie verursachen keine
  Doppelbuchung, verdoppeln aber jede Zählung. Eigene Version mit Migration.
