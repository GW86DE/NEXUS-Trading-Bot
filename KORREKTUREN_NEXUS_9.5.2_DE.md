# NEXUS 9.5.2 — Korrekturen

Alle Befunde wurden gegen die echten Zustandsdateien deines Pi vom
**02.09.2026** geprüft, nicht gegen angenommene Daten. Die Dateien liegen als
Testfixtures im Paket (`tests/fixtures/echt_*.json`), damit jede Korrektur
dauerhaft an der Realität hängt.

---

## 1. Der Terminalfilter — die Ursache der OKX-Sperre

`order_ownership.py` definierte `ist_terminal()` und benutzte es nicht:

```python
def nicht_terminale(self) -> dict:
    return {k: dict(v) for k, v in self.pending.items()
            if not darf_per_ttl_verfallen(v)}      # ← falsche Frage
```

`darf_per_ttl_verfallen()` beantwortet, ob ein Eintrag durch bloßen
Zeitablauf verschwinden darf — wahr allein für `PLANNED`. Ein **`FILLED`**-
Eintrag galt damit als offen und sperrte den Handel dauerhaft.

An deinem Register nachgerechnet: 57 Einträge, davon 17 `FILLED` und 30
`CANCELED`. Alte Fassung meldet **57** offene, neue Fassung **10**.

## 2. Die Brokertrennung

`crypto_engine._offene_order_symbole()` las das gemeinsame Register ohne
Filter. `etoro_reconciliation` schreibt seine Aktienorders in dieselbe Datei —
eine eToro-Aktie konnte den Kryptohandel sperren.

Neu: `pending_for(broker, environment, account_fingerprint, asset_type)`.
An deinen Daten: alle 10 offenen Einträge sind eToro-Aktien (ADBE, CRM, MSFT,
ORCL, SPGI). Der Kryptohandel sieht davon jetzt **null**.

Ein unlesbares Register meldet sich außerdem als eigener Sperrgrund, statt
stillschweigend „keine offenen Orders" zu bedeuten.

## 3. Die Klärungsfrist — der FET-Fall

```
3884888641315221505   FET-EUR   790,849   ≈ 104,63 EUR
erzeugt    2026-09-01 16:45:03 UTC
CANCELED   2026-09-02 03:18:58 UTC        → 10 h 34 min
OKX-Riegel fällt 03:20:11 UTC             → 73 Sekunden später
```

Eine FOK-Order ist beim Broker nach Millisekunden terminal. Bleibt sie hier
zehn Stunden offen, fehlt die **Brokerantwort**, nicht die Ausführung — und
der Handel stand währenddessen still, ohne dass irgendwo der Grund stand.

Neu: `ueberfaellige()` plus ein sichtbarer Alarm nach
`ORDER_KLAERUNG_HOECHSTALTER_SEKUNDEN` (900 s). Die Sperre bleibt, aber sie
ist nicht mehr stumm.

## 4. Drei Domänengenerationen

In deiner `etoro_reconciliation.json` stehen drei Schlüsselformen:

```
14 Sätze   etoro:demo                              (ohne Kontobindung)
 2 Sätze   etoro:demo:ba32f97fe3482fcbc326e51a     CRM + ADBE  (9.5)
 aktuell   etoro:demo:664e7dfbc13e0cd60e493592     (9.5.1)
```

`active_for_domain()` verglich exakt — gegen `664e…` passte **kein einziger**
Satz. Der Kaufpfad meldete null ungeklärte Fälle, die WebUI zählte global
zwei. Und **ADBE konnte sich nie klären**: der Worker iterierte über
denselben exakten Vergleich, sah den Satz also nicht.
`position_absence_checks` stand seit 17 Stunden unverändert auf 1 von 3.

Neu:

* **Sperrbereich** und **Klärungsbereich** sind getrennt. Der Worker
  bearbeitet auch Altgenerationen; sperren dürfen sie erst nach Beleg.
* **Kontoaliase mit Brokerbeleg.** Findet der Order-Lookup gegen das
  *aktuelle* Konto die gespeicherte `orderId`, ist damit brokerseitig belegt,
  dass der Satz zu diesem Konto gehört. Erst dann wird er übernommen —
  der alte Fingerprint bleibt als `account_fingerprint_vorher` erhalten.
* Eine Formelrekonstruktion des alten Verfahrens findet **nicht** statt. Sie
  wäre geraten; der Brokerbeleg ist exakt.
* Ein wirklich fremdes Konto bleibt unberührt und sperrt nichts.

## 5. Buchungslücke ist keine offene Order

`CLOSED_ACCOUNTING_PENDING` stand in `NON_TERMINAL`. Beim Broker ist aber
nichts mehr offen — es kann nichts doppelt gekauft werden. Genau deshalb
erschien CRM als zweite „ungeklärte Order".

Neu: eigene Kategorie `BUCHUNGSLUECKE`, sichtbar über `buchungsluecken()` und
`pnl_unvollstaendig()`.

**Die Schutzwirkung bleibt** — unter dem richtigen Namen: die neue Bedingung
`buchung_vollstaendig` sperrt weiterhin neue Käufe, solange ein
abgeschlossener Trade unverbucht ist. Denn ohne vollständigen Tages-PnL kennt
die Verlustgrenze ihren eigenen Wert nicht.

## 6. Der Backfill hat sich selbst blockiert

Alle 14 Altsätze bekamen in **jedem Zyklus** neu:

```
"error": "Konto/Decision/Entry-Preis/Menge/Order fuer exakten Backfill unvollstaendig"
```

Sie haben positionId, orderId, decision_id, Menge und Preis. Es fehlte einzig
`account_fingerprint` — den sie nie hatten. Die Routine, die die Altlasten
aufräumen soll, wurde also von derselben Fingerprint-Änderung blockiert, die
die Altlasten erzeugt hat, und schrieb die Datei jede Runde neu.

Neu: ein Altbestand ohne Kontobindung wird **einmal** als
`LEDGER_BACKFILL_LEGACY_UNBOUND` abgeschlossen und danach in Ruhe gelassen.
Nachträglich einem Konto zugeschrieben wird er nicht — das wäre eine
Eigentumsbehauptung ohne Beweis. Und die Fehlermeldung nennt jetzt nur noch
das, was wirklich fehlt.

## 7. Der eToro-WebSocket

```python
run_forever(ping_interval=25, ping_timeout=10)     # 9.5.1
```

Das sind RFC-6455-Ping-Frames. eToro beantwortet sie nicht, also hat sich der
**Client selbst** getrennt — im Zweiminutentakt, mit wachsendem Backoff bis
30 Sekunden. OKX macht es an derselben Stelle bewusst anders
(`ping_interval=0` plus Text-`"ping"`).

Neu: kein Client-Ping mehr. Stattdessen ein Wächter mit Fristen für
Authentifizierung (20 s) und Subscription (30 s) sowie einer Stillewache
(180 s). Eine Verbindung, die **liefert**, bleibt bestehen; eine, die
schweigt, wird erneuert. Nach einer stabilen Sitzung (120 s) fällt der
Backoff auf 1 s zurück.

**Und sie ist jetzt sichtbar.** In `runtime_status.json` gab es für eToro
überhaupt keinen Streamabschnitt — deshalb stand dort durchgehend „ONLINE"
und im Log fand sich nichts. Neu: `etoro_private_stream` und
`connection_components` mit Generation, Session-ID, Zustand, Close-Code,
Close-Grund, letztem Datenempfang und nächstem Reconnect.

## 8. Die toten Diagnosefelder

`broker/okx.py` las `last_account_update` / `last_order_update` / `last_pong`.
Der Stream lieferte `last_account` / `last_order` und gar kein Pong-Feld. In
deiner Datei stehen die drei deshalb dauerhaft auf `null`, obwohl die Daten
vorliegen. Beide Schreibweisen werden jetzt geliefert und gelesen.

Dazu: `_touch()` setzte den Keepalive bei **jeder** Nachricht zurück — ein
echtes `"pong"` wurde nie nachgewiesen. Jetzt quittiert nur ein tatsächliches
Pong. Damit keine Abbruchschleife entsteht, wird nur dann neu verbunden, wenn
**weder** ein Pong **noch** sonst Daten kommen.

## 9. Der Sperrgrund nennt den richtigen Broker

`trading_ready.py` hatte für alle Broker fest „eToro-Ausführungsabgleich
abgeschlossen" hinterlegt. Auf der OKX-Seite stand damit ein Sperrgrund, der
einen fremden Broker nannte — während die Ursache eine eigene OKX-Order war.
Die Beschreibung kommt jetzt pro Domäne.

---

## Tests

**1093 bestanden**, 1 übersprungen, 184 Subtests.

Neu: `tests/test_v952_echte_zustandsdaten.py` — 22 Tests, alle gegen die
echten Dateien deines Pi. **Kein Test in dieser Datei durchsucht Quelltext.**
Der WebSocket-Test zeichnet den tatsächlichen `run_forever`-Aufruf auf, statt
nach „ping_interval" im Quelltext zu suchen — ein Texttest wäre schon an
einem Kommentar angeschlagen.

Zwei bestehende Tests wurden auf den neuen Mechanismus umgestellt, keiner
abgeschwächt:

* `test_broker_closed_but_ledger_pending_keeps_domain_blocked` prüft jetzt,
  dass die Sperre über `buchung_vollstaendig` greift statt über „ungeklärte
  Order" — die Garantie ist dieselbe, der Name ist richtig.
* Der Versionstest vergleicht gegen `config.VERSION_NEXUS` statt gegen ein
  Literal.

---

## Was noch offen ist

* **ADBE** (51 Stück) klärt sich erst, wenn der Worker die `orderId 378375675`
  gegen dein aktuelles Konto nachschlagen kann. Beim ersten erfolgreichen
  Lookup wird der Kontoalias belegt und der Satz übernommen. Gelingt der
  Lookup nicht, bleibt der Satz stehen — sichtbar, mit Grund. Er wird nicht
  geraten.
* Die **Doppeleinträge** im Orderregister (jede Order unter `orderId` *und*
  `referenceId`) sind noch drin. Sie schaden aktuell nicht, verdoppeln aber
  jede Zählung. Das gehört in eine eigene Version mit Migration.
* Der **60-Minuten-Soaktest** des WebSockets lässt sich nur auf deinem Pi
  fahren. Nach dem Update bitte prüfen:
  `journalctl -u tradingbot-pi5.service --since "-1 h" | grep -i "etoro-privatstream"`
  Erwartet: eine Verbindung, keine Zweiminutenschleife.
