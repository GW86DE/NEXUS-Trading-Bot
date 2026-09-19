# NEXUS 10.8.0 – Prüfbericht

**Build:** `10.8.0-ARCHITEKTUR-LEITPLANKEN-UND-PULSAR-BESTAETIGUNG` · Stand: 19.09.2026 · Basis 10.7.1

## Vor dem Bau: dieselben Schritte wie der Pi-Volltest, plus Je-Test-Vergleich

- **Statische Hygiene komplett** (`volltest._static_hygiene()`): 0 Befunde;
  Release-Checkliste leer, alle `REQUIRED_RELEASE_FILES` vorhanden.
- **Node-Frontendsuite**: 30/30 (keine JS-Änderung; `settings.html` bekam ein Feld).
- **Netzwerkwächter** über PULSAR-Worker-Modulen, den 10.6.0/10.7.0/10.7.1-Suiten
  und den beiden 10.8.0-Suiten: kein `http`-, kein `dns`-Ereignis.
- **Je-Test-Vergleich mit POSIX-Shims** gegen das ausgelieferte 10.7.1-Paket:
  dieselben Module wie auf dem Pi; jeder neu rote Test bricht den Bau ab
  (Ergebniszeile im Bauprotokoll).
- **Kompletter Testbestand isoliert, zweimal** (231 Module je Prozess, 6 parallel):
  - Lauf 1: 3.288 bestanden, 63 fehlgeschlagen, 7 Fehler, 9 übersprungen; 0 Netzversuche.
    Neu rot gegenüber 10.7.1: nur `test_v1080_architektur` (3 Tests) – die Leitplanke
    fand eine Scratch-Datei im Spiegel und den umbenannten Diagnose-Wrapper, der in der
    Schichtenkarte noch fehlte. Karte und Baseline wurden auf dem versionierten Stand
    neu erzeugt.
  - Lauf 2 (finaler Baum): **3.291 bestanden**, 60 fehlgeschlagen, 7 Fehler, 9 übersprungen;
    0 Netzversuche; **neu rot gegenüber 10.7.1: keine**; neu grün:
    `test_v100_execution_recovery` (in 10.7.1 unter Last einmal rot).
    Die 26 roten Module sind die dokumentierten Windows-Artefakte (fcntl, flock,
    Symlinks, systemd, Installerpfade), identisch mit 10.7.1.
  - Zahlen: `validation/NEXUS_10.8.0_TEST_EVIDENCE.json`.

## Architektur: gemessen, nicht behauptet

| Kennzahl (eigener AST-Graph, ohne Tests) | vor Schritt 2 | 10.8.0 |
|---|---:|---:|
| Module / Importkanten | 316 / 1.079 | 336 / 1.090 |
| Import-Zyklus-Komponenten / Module darin | 8 / 44 | **2 / 21** |
| größte Komponente | 11 | 11 |
| Schichtverstöße (Baseline) | – | 93 |
| Adapter → Kern (application/interfaces) | – | 6 |
| WebUI → Broker-Adapter | 0 | 0 |
| PULSAR → außen | – | 35 |
| Module > 1.500 Zeilen | 9 | 9 (Ausnahmeliste, +5 % Toleranz) |

Die zwei verbleibenden Ringe: PULSAR/ai_router (11 Module) und
OKX-Buchhaltung/trade_ledger/decision_analytics (10 Module). Beide stehen als
Komponenten in `validation/ARCHITEKTUR_BASELINE.json`; `test_zyklen_nur_sinkend`
lässt sie nur schrumpfen. Die 93 Schichtverstöße (71 application→adapters, 13
adapters→application, 4 konfiguration→state, 3 state→application, 1
application→interfaces, 1 state→adapters) sind die Kopplungen, die die
Schritte 3, 7 und 9 abbauen; `test_schichtverstoesse_nur_sinkend` verhindert neue.
Ein neu eingeführter Verstoß wurde beim Bau dieser Version selbst gefangen
(`pulsar.worker → market_intelligence.candidate_research`) und über die
Paketfassade `market_intelligence.request_confirmation` behoben.

## Was die neuen Tests belegen

**`tests/test_v1080_architektur.py` (33)** – Schicht je Modul, keine verwaisten
Einträge, Zyklen und Verstöße nur sinkend, domain rein, WebUI ohne Broker,
Großmodule begrenzt, Baseline passt zum Baum; Weiche liefert EIN Modulobjekt
(`ledger_result is nexus.domain.ledger_result`), Alias ≤ 6 Zeilen,
`risk_levels.path()` bleibt im Projektordner; 13 kurze Zyklen ohne gegenseitigen
Import; Helfer greifen nicht nach oben; Re-Exporte; `background_tick(nachlauf=…)`.

**`tests/test_v1080_pulsar_bestaetigung.py` (22)**
- Punkt 1: nur OFFENE Karten mit Auslöser, nur in offener Sitzung, höchstens
  fünf, `max_age` 120 s; nach dem Nachladen BELEGT → HYPE_KANDIDAT; Fehler
  lassen die Karte unverändert; `fmp_service.request(max_age)` umgeht einen
  älteren Cache, ohne den Cache sonst zu ändern.
- Punkt 2: Folgeseiten bis die Stunde vollständig ist (36 statt 30
  Nachrichten, 36 Konten), jede Seite mit Budget; höchstens drei Folgeseiten;
  15-min-Takt nur für aktive Karten; 403 → vier Stunden Pause, Quelle meldet
  UNKNOWN-Wirkung; Budget 400/2000; kein Text, keine Autorenkennung.
- Punkt 3: Anforderung nur mit belegter Einzelaktien-Identität, einmal je
  Symbol und Tag, acht je Tag; Plan zieht die Bestätigung vor die Slot-Suchen;
  Ergebnis mit 9 Beiträgen von 6 Konten zählt als zweite Social-Familie
  (1 → 2 Bestätigungen), nie als Auslöser, kein vierter Platz; zu dünn,
  veraltet oder offen bleibt eine benannte Lücke; ohne X nicht angenommen.
- Punkt 4: 15-Minuten-Rechnung (rvol 4,0 über 20 Sitzungen); nur
  abgeschlossene Kerzen; Frische 45 min; Tagesdurchschnitt derselben Quelle;
  eToro-Kerzen bestätigen skalenfrei und können den Quote nie verdrängen; Kern
  ruft ≤ 5 Reihen je Zyklus mit 15 min Ruhe ab (≤ 20/h), nur eToro-Aktien; nur
  AUSLOESER/HYPE_KANDIDAT in offener Sitzung; PULSAR ohne Broker.
- Punkt 5: Standard 200; Migration hebt 50 (und fehlenden Eintrag) einmalig,
  lässt 30 und 120 stehen; WebUI-Feld 0..5000; USD-Deckel unverändert.

## Bewiesen vs. offen

**Bewiesen (offline):** die Leitplanken greifen (Zyklen 8 → 2, ein neuer
Verstoß wurde beim Bau gefangen); die Weichen sind für alle 229 alten
Testdateien unsichtbar (kein Test angepasst außer drei Pins); die fünf Punkte
verhalten sich wie beschrieben, mit Budgetgrenzen und ohne Orderbefugnis.

**Offen (nur auf dem Pi):**
- StockTwits antwortete am 18.09. auf beide Endpunkte mit HTTP 403. Bleibt das,
  liefert Punkt 2 nichts – korrekt UNKNOWN, keine Sperre, kein Fehler.
- Die Volumenqualität der eToro-15-Minuten-Kerzen bei Aktien ist offline nicht
  prüfbar (Kerzenspeicher nicht in der Diagnose; Stundenkerzen-Volumenscan seit
  10.5.0 ohne Treffer). Deshalb rechnet 10.8.0 nur gegen den eigenen
  Tagesdurchschnitt und lässt den Quote-Weg unangetastet; die nächste Diagnose in
  offener Sitzung zeigt `intraday_avg_day_volume` je Karte.
- Erste X-Bestätigungssuche im Betrieb innerhalb des 15-EUR-Monatsbudgets.

## Invarianten

UNKNOWN bleibt UNKNOWN (kein Kurs, kein Volumen, keine Zählung wird erfunden).
PULSAR, X, GPT und StockTwits haben keine Orderbefugnis; jede Nominierung
braucht Georgs zweistufige Telegram-Bestätigung. Kein TLS-Bypass, kein
Umgehen der StockTwits-Sperre. CSP `script-src 'self'`, kein CDN. Keine
Klarnamen in Diagnose-Exporten. Kaufkaskade, Risikoprüfung, Buchhaltung und
Broker-Verhalten unverändert.
