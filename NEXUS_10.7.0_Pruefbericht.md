# NEXUS 10.7.0 – Pruefbericht

**Build:** `10.7.0-BUY-GATE-SCOPE-AND-FEE-EVIDENCE` · Revision 2 · Stand: 18.09.2026 · Basis 10.6.0

## Revision 2 – warum

Revision 1 lief auf dem Pi durch den Volltest mit **3396 bestandenen Tests und
genau einem Fehler**: `test_v988_staging_and_guard::test_new_read_gate_closes_all_its_connection_handles`.
Das Update brach vertragsgemaess ab, 10.6.0 lief weiter.

Der Test prueft, ob `okx_accounting.status()` jede Verbindung wieder schliesst.
Als Nebenbedingung benutzte er die alte Regel „jeder offene Ergebnisbeleg macht
die Domaene unvollstaendig“. Sein Beleg stammt vom 10.09.; nach der neuen
Tagesregel sperrt er nicht mehr. Der Test gehoert zu den Modulen, die auf
Windows nie bis zur Sammlung kommen (Importkette ueber `nexus_update`: fcntl,
pwd, grp). Der bisherige Vergleich „gleich viele rote Module wie 10.6.0“ konnte
ihn deshalb nicht sehen.

**Fix:** nur der Test (verlangt jetzt den einen RESULT-Beleg, `complete=True`,
keine gesperrten Coins – und weiterhin fuenf geschlossene Verbindungen). Kein
Produktcode geaendert.

**Prozessfix im Bauskript:** Je-Test-Vergleich gegen das zuletzt ausgelieferte
Paket mit POSIX-Shims (fcntl/pwd/grp), sodass lokal dieselben 3400 Tests laufen
wie auf dem Pi. Jeder Test, der gegenueber 10.6.0 neu rot ist, bricht den Bau
ab; Tests, die in beiden Baeumen rot sind (Windows-Pfade, echte
Sperrsemantik), werden ausgewiesen. Zahlen des Laufs in
`validation/NEXUS_10.7.0_TEST_EVIDENCE.json`.

## Vor dem Bau: dieselben Schritte wie der Pi-Volltest

- **Statische Hygiene komplett** (`volltest._static_hygiene()`): 0 Befunde.
- **Node-Frontendsuite** (`tests/frontend_v100.test.js`, lokal mit Node 18.4):
  30/30 gruen. Neu: Sperrliste je Broker auf der Uebersicht.
- **Netzwerkwaechter** ueber PULSAR-Worker-Modulen und den neuen Suiten: kein
  `http`- und kein `dns`-Ereignis.
- **Neue Suiten**: `test_v1070_lot_rest` 11/11, `test_v1070_aktien_stop` 9/9,
  `test_v1070_gebuehrenbeleg` 15/15, `test_v1070_handelsfreigabe` 22/22.
- **Kompletter Testbestand**: siehe `validation/NEXUS_10.7.0_TEST_EVIDENCE.json`
  (Zahlen der beiden isolierten Durchlaeufe dort).

## Was die neuen Tests belegen

### Lot-Rest (Teil C)
- Die echten Zahlen vom 18.09.: 18,3284 verkauft + 0,0000532 Rest deckt 18,3284532
  gebucht; ohne Restzeile bleibt der Abgang unerklaert (wie bisher, richtig).
- Ein Teilverkauf mit Lot-Rest schliesst die Luecke im Verkaufspfad; die Reparatur
  schliesst eine Altlast aus Verkauf plus Restzeile; nie verkaufte Trades, Zeilen
  ohne Fills und fremde Kontodomaenen werden nicht angefasst.

### Aktien-Stop (Teil D)
- CSCO nachgestellt: Mindestabstand vor der Menge (108,81 statt 109,12), Bezug auf
  den frischen Kaufkurs (108,06 zu 109,15 = 1,0 % statt 3 Cent).
- Weite Stops bleiben unveraendert; Krypto und PULSAR werden nicht angefasst.

### Gebuehrenbeleg (Teil E)
- CSCO-Muster: Kauf und Verkauf im selben Intervall werden aus dem Barbestand
  abgerechnet; ein bestaetigter zweiter Verkauf wird eingerechnet; ein
  unbestaetigter, eine verschwundene Position ohne Ledgerzeile, schwebende Orders
  und eine unplausible Differenz brechen ab.
- Erwartungswert: gekennzeichnet, Median aus mindestens drei bestaetigten
  Abrechnungen, 15 Minuten Vorrang fuer den Barbestand, vom Beleg ersetzt (mit
  Abweichung), nie umgekehrt. Risikozustand und Buchungsabgleich nehmen ihn an.

### Sperrregeln und Sperrliste (Teile A, B)
- Bestandsbelege sperren nur ihren Coin; ein Ergebnis von heute sperrt die Domaene
  bis zum Tagesreset; eines von gestern bleibt offener Beleg ohne Sperre; Geld
  ohne Kontozuordnung sperrt weiter alles; unbekannte Belegarten sperren
  vorsichtshalber.
- `require_tradable` laesst den anderen Coin durch und lehnt den gesperrten ab.
- Beide Broker zaehlen offene Ergebnisse nach derselben Regel.
- Die Sperrliste traegt bei jeder Sperre Grund, Reichweite, Ablauf, Aufloesung.

## Umgestellte Zusicherungen

27 bestehende Tests schrieben die alte Regel fest. Sie pruefen jetzt bewusst die
neue: `test_v988_accounting` (8 Stellen), `test_v988_staging_and_guard` (Rev 2),
`test_v1013_etoro_result_scope`
(„survives midnight" → „releases after midnight"), `test_v1017_okx_boundary`,
`test_v1020_exit_in_progress` (5), `test_v954`, `test_v814`, `test_v1060`,
`test_v1070`. Zwei Tests aus 10.3.1 haben dabei einen echten Mangel der neuen
Intervallrechnung gefunden (verschwundene Position ohne Ledgerzeile); er ist
behoben, die Tests laufen unveraendert.

## Bewiesen vs. offen

**Bewiesen (offline):** Mengenrechnung, Sperrreichweiten und -ablaeufe,
Stopabstand, Intervallrechnung und Erwartungswert, Sperrliste in Status,
Oberflaeche und Diagnose.

**Offen (nur auf dem Pi):** Selbstaufloesung der drei OKX-Luecken im ersten
Zyklus; CSCO-Abrechnung per Intervall oder Erwartungswert; erster Aktienkauf mit
Mindestabstand; Sperrliste mit echten Daten.

## Invarianten

UNKNOWN bleibt UNKNOWN (ein Erwartungswert ist gekennzeichnet, nie bestaetigt).
Kein Verkauf auf Fremdbestand. Kontowechsel, Persistenzfehler und unlesbares
Ledger sperren weiter alles. Kein TLS-Bypass. PULSAR, X und GPT haben keine
Orderbefugnis. CSP `script-src 'self'`, kein CDN. Keine Klarnamen in
Diagnose-Exporten.
