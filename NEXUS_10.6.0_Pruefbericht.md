# NEXUS 10.6.0 – Pruefbericht

**Build:** `10.6.0-RISK-LEVELS-BALANCE-RESTORE` · Stand: 18.09.2026 · enthaelt 10.4.0 und 10.5.0

## Vor dem Bau: dieselben Schritte wie der Pi-Volltest

- **Statische Hygiene komplett** (`volltest._static_hygiene()`): 0 Befunde.
- **Node-Frontendsuite** (`tests/frontend_v100.test.js`, lokal mit Node 18.4):
  vollstaendig gruen, unveraendert gegenueber 10.5.0.
- **Netzwerkwaechter** ueber den PULSAR-Worker-Modulen: kein `http`- und kein
  `dns`-Ereignis. Diese Pruefung gibt es seit 10.5.0 Rev 2, weil Rev 1 genau
  daran auf dem Pi scheiterte.
- **Neue Suiten**: `tests/test_v1060_einsatzstufen.py` 23/23 gruen,
  `tests/test_v1060_bestandsrueckkehr.py` 18/18 gruen.
- **Kompletter Testbestand**, zwei Durchlaeufe: 224 Module, 185 gruen, 3162
  Tests bestanden. Die 39 roten Module sind 13 socket-only-Faelle des
  Netzwerkwaechters (Windows `socketpair`, auf dem Pi AF_UNIX) und 26
  dokumentierte Windows-Artefakte (`fcntl`, `flock`, Symlinks, systemd,
  Installerpfade) -- exakt der Stand von 10.5.0 Revision 2, keine neue
  Fehlstelle. Details in `validation/NEXUS_10.6.0_TEST_EVIDENCE.json`.

## Zwei echte Befunde im ersten Durchlauf, beide behoben

1. `test_v814_positionsbuch` verlangt schon bei der ERSTEN Fehlmessung eine
   Meldung mit der etablierten Formulierung „wird erneut geprueft". Die neue
   Fruehmeldung sagte dasselbe mit anderen Worten. Geaendert wurde der
   Meldetext, nicht der Test.
2. `test_v980_webui` zaehlt die Bedienflaechen der Einstellungsseite. Der neue
   Abschnitt macht daraus 14 statt 13; der Test prueft jetzt zusaetzlich, dass
   der Anker und die Skriptfunktionen wirklich vorhanden sind.

Beide Module laufen einzeln gruen.

## Was die neuen Tests belegen

### Einsatzstufen

- **Kein stilles Verhalten.** Ohne gewaehlte Stufe ergibt die Rechnung exakt
  den echten ETH-Kauf vom 18.09.2026: 649,86 EUR freies EUR-Guthaben, Stop 10 %,
  19,50 EUR Positionswert. Auf den Cent wie im Entscheidungsjournal.
- **Wirkung ohne Neustart.** Am selben Topfobjekt verdoppelt `mittel` den
  Einsatz, `erhoeht` vervierfacht ihn -- ohne Neuerzeugung, ohne Neustart.
- **Anzeige = Entscheidung.** `uebersicht()` und `positionsgroesse()` liefern
  dieselben Zahlen; die Stufe steht in der Begruendung.
- **Fail-safe.** Beschaedigte und nachtraeglich entfernte Stufendatei fallen
  beide auf die kleinste Stufe zurueck, nie nach oben.
- **Getrennt.** Eine OKX-Wahl laesst eToro unberuehrt.
- **Rueckwaertskompatibel.** Ausdruecklich uebergebene `TopfGrenzen` folgen der
  Stufe nicht. Deshalb war kein bestehender Test anzupassen.
- **Bedienung.** Die hoechste Stufe verlangt `EINSATZ ERHOEHEN`; ohne die Phrase
  wird nichts gespeichert. Kleiner werden geht ohne Phrase.

### Bestandsluecke

- **Der echte Ablauf vom 18.09.** ist als Test hinterlegt: zwei Messungen im
  Abstand von neun Sekunden buchen nichts und sperren nichts.
- Eine einzelne Fehlmessung bucht nichts, meldet den Verdacht aber sofort und
  ausdruecklich als unbestaetigt.
- Zwei Messungen mit Mindestabstand buchen wie bisher; die Menge bleibt erhalten.
- Ein kaputter Zeitstempel bestaetigt nicht.
- **Rueckweg**: Zwei bestaetigte Wiedersichten geben die Position frei, loesen
  die Bestandsluecke (`BALANCE_RESTORED:<menge>@<zeit>`) und machen die
  Kontodomaene wieder handelsbereit. Die Aufloesung bleibt nachlesbar.
- **Grenzen**: kein Schnappschuss, Teilbestand, fremde Kontodomaene und bereits
  ausgestiegene Zeilen geben nichts frei.
- **Kein geglaubter Schutz**: Nach der Freigabe wird der Broker-Schutz im selben
  Takt erneut erfragt, nicht aus dem Gedaechtnis uebernommen.
- Entsperren (120 s) dauert laenger als sperren (30 s).

## Nebenbefund, mitgefixt

Eine Ledgerzeile ohne vollstaendige Kontokette liess `mark_balance_gap` werfen;
die Ausnahme riss den gesamten Positionszyklus mit, also auch Schutzabgleich und
Ausstiege aller anderen Coins. Der Fall geht jetzt in die vorhandene Quarantaene
`mark_unanchored`. Gefunden wurde er nur, weil der Fix die Buchung einen Zyklus
spaeter ausloest und dadurch ein anderer Ledgerzustand entstand.

## Bewiesen vs. offen

**Bewiesen (offline):** Stufenmechanik, Fail-safe, Fristenlogik, Ruecknahme der
Bestandsluecke auf Ledgerebene, Bedienpfad der WebUI bis zum Endpunkt.

**Offen (nur auf dem Pi):**
- Ob sich die drei gesperrten Positionen (XRP, BTC, ETH) nach dem Update
  tatsaechlich selbst aufloesen. Erwartet: zwei vollstaendige Guthaben-
  Schnappschuesse mit mindestens 120 s Abstand, danach Telegram-Meldung und
  freie OKX-Kaeufe.
- Ob eine in der WebUI gewaehlte Stufe im naechsten Scan sichtbar wirkt
  (Begruendung im Entscheidungsjournal nennt sie).
- Die aus 10.4.0/10.5.0 uebernommenen offenen Punkte: DNS-Aufloesung von
  `api.stocktwits.com` im Heimnetz, erste echte Messzeilen, Diagnosebericht mit
  echter ZIP.

## Invarianten

UNKNOWN bleibt UNKNOWN. Kein TLS-Bypass. PULSAR, X und GPT haben keine
Orderbefugnis. CSP `script-src 'self'`, kein CDN. Keine Klarnamen in
Diagnose-Exporten. Eine fehlende Messung ist kein Beweis -- neu auch in der
Gegenrichtung: eine fehlende Messung ist kein Beweis fuer einen Verlust.
