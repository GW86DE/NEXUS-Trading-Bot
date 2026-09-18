# NEXUS 9.3.1 — Korrektur zum SPGI-Fall

**Anlass:** 9.3.0 hat den MSFT-Fall **nicht** behoben. Am 31.08.2026 um 20:51
kaufte der Bot SPGI. Eine Minute später stand die Aktie in der Oberfläche als
„Fremdbestand beim Broker · NUR BEOBACHTET".

Ich hatte in 9.3.0 zugesagt, dass genau das nicht mehr passieren kann. Diese
Zusage war falsch. Hier steht, warum — und was jetzt anders ist.

---

## Was im Log stand

```
20:51:04 DECISION SPGI BUY approved qty=34.0 value=14887.92
20:51:31 WARNING eToro-Auftrag SPGI wird weiter abgeglichen: eToro meldet eine
         Ausfuehrung, die positionId ist im aktuellen Depot aber noch nicht
         bestaetigt; kein lokaler Trade und kein erneuter Kauf.
20:51:52 ERROR   Fehler JPM: Neue Kaeufe gesperrt: ungeklärte eToro-Ausführung
```

Zwei Dinge funktionierten dabei richtig: der Kauf lief durch, und die
Kontosperre verhinderte einen zweiten Kauf (JPM), solange SPGI ungeklärt war.
Falsch war ausschließlich die **Zuordnung**.

---

## Die zwei Löcher in meiner 9.3.0-Korrektur

### Loch 1 — der Symbolabgleich war nach Sekunden tot

`_offene_kaufabsicht()` prüfte zuerst, ob eine der positionIds aus dem Depot
zu einem eigenen Kauf passt. Fand es keine, gab es einen Rückfallweg über das
Symbol — aber nur unter dieser Bedingung:

```python
if (treffer_symbol is None and not eigene              # <-- das war der Fehler
        and str(satz.get("symbol") or "").upper() == str(symbol or "").upper()):
```

`not eigene` heißt: der Rückfallweg galt **nur, solange eToro noch gar keine
positionId gemeldet hatte.** eToro meldet die Ausführung aber binnen Sekunden
— und genau in diesem Fenster läuft der Depot-Abgleich. Der Weg, der den Fall
retten sollte, war zu dem Zeitpunkt bereits geschlossen.

Dazu kam: verglichen wurde das rohe Symbol. „SPGI" und „SPGI.US" galten als
verschiedene Werte.

### Loch 2 — die Frage wurde nur einmal gestellt

Schwerwiegender. `_offene_kaufabsicht()` wurde **ausschließlich beim Anlegen**
eines Datensatzes aufgerufen:

```python
rec = self.records.get(key)
if rec is None:
    eigen = self._offene_kaufabsicht(pids, symbol)   # nur hier
```

Lief der Depot-Abgleich einen Moment zu früh — bevor der Reconciliation-Satz
geschrieben war, oder in der Sekunde zwischen zwei Zuständen — dann war die
Aktie als Fremdbestand angelegt, und **es gab nichts mehr, was das jemals
hätte richtigstellen können.** Ein einzelner unglücklicher Moment fror die
Fehlzuordnung dauerhaft ein. Genau so blieben MSFT und SPGI liegen.

---

## Was jetzt anders ist

### 1. Bei jedem Abgleich wird nachgeprüft, nicht nur beim Anlegen

Neu `_pruefe_eigenen_kauf_nach()`: Ein Bestand, der als fremd geführt wird,
wird bei **jedem** Depot-Abgleich erneut gegen die eigenen Käufe gehalten.
Passt einer, wird der Datensatz richtiggestellt — mit Logeintrag.

Damit repariert sich der Fehler selbst, statt sich festzusetzen. SPGI sollte
beim nächsten Zyklus von allein umspringen.

Zwei Grenzen sind eingebaut: Ein Datensatz, den **du** angefasst hast (z. B.
über „Nur beobachten"), bleibt unberührt. Und ein Bestand, der schon dem Bot
gehört, wird nicht neu bewertet.

### 2. Der Symbolabgleich gilt im ganzen Propagationsfenster

`not eigene` ist weg. Ein eigener Kauf desselben Werts zählt, solange er nicht
abgeschlossen ist — unabhängig davon, ob eToro schon eine positionId gemeldet
hat. Börsensuffixe (`.US`, `.DE`, `.L` …) werden abgeschnitten.

**Die Sicherheitsgrenze bleibt:** Ein Symboltreffer allein erzeugt
`PENDING_CONFIRMATION` — sichtbar als eigener Kauf, aber **keine automatische
Verwaltung und kein Verkauf**. Eigentum entsteht weiterhin ausschließlich aus
der positionId. Der Test
`test_ein_symboltreffer_allein_gibt_niemals_automatik` hält das fest.

### 3. Ein bestätigter Kauf verschwindet nicht mehr aus der Zuordnung

`offene_kaufabsichten()` lieferte nur Sätze in einem nicht-terminalen Zustand.
Sobald `verify_broker_truth()` die positionId bestätigt hatte, fiel der Kauf
aus der Liste — und ein in diesem Moment falsch angelegter Bestand war nicht
mehr reparierbar.

Neu `eigene_kaeufe()`: liefert **jeden** Kauf der letzten 48 Stunden, aus dem
eine Position entstanden sein kann. Für die Kapitalbindung gilt weiterhin die
engere Liste — gebundenes Geld und Zuordnung sind zwei verschiedene Fragen.

### 4. „Bot übernehmen" findet die Position wieder

MSFT ließ sich nicht übernehmen: die Schutzprüfung meldete „keine offene
Position", obwohl die Aktie sichtbar im Depot lag. Ursache: `_resolve()` löste
das Instrument auf eine andere `instrumentId` auf als die, unter der das Depot
die Position führt.

Findet die Prüfung über die instrumentId nichts, vergleicht sie jetzt über den
Instrumentenkatalog (instrumentId → Symbol) — weiterhin ID-basiert, geraten
wird nichts. Der Fall wird geloggt.

---

## Tests

**955 bestanden**, 1 übersprungen, 177 Subtests. Neu sind 9 Tests, die den
SPGI-Fall in jeder Lage festhalten:

| Test | Was er festhält |
|---|---|
| `test_eigener_kauf_wird_in_jeder_lage_erkannt` | drei Varianten: positionId stimmt / weicht ab / fehlt noch |
| `test_symbol_mit_boersensuffix_zaehlt_als_derselbe_wert` | SPGI = SPGI.US |
| `test_bereits_bestaetigter_kauf_verschwindet_nicht_aus_der_zuordnung` | Loch 3 |
| `test_falsch_angelegter_bestand_wird_beim_naechsten_abgleich_repariert` | Loch 2 — die eigentliche Lehre |
| `test_bewusst_beobachtete_position_wird_nicht_uebergangen` | deine Entscheidung bleibt |
| `test_ein_symboltreffer_allein_gibt_niemals_automatik` | die Sicherheitsgrenze |
| `test_uebernahme_findet_die_position_auch_bei_abweichender_instrument_id` | der Übernahme-Knopf |

---

## Was ich daraus mitnehme

In 9.3.0 habe ich den Fehler an der Stelle repariert, an der er entstanden
ist — beim Anlegen des Datensatzes. Das war zu eng gedacht. Ein Zustand, der
sich nur in einem einzigen Moment richtig setzen kann, wird irgendwann falsch
gesetzt. Richtig ist, ihn bei jedem Durchlauf nachzuprüfen und richtigzustellen.

Diese Lehre gilt auch für andere Stellen im Bot, und ich schaue sie mir bei
Gelegenheit daraufhin an.
