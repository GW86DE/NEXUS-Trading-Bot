# NEXUS 10.5.0 – Pruefbericht

**Build:** `10.5.0-PULSAR-SOURCES-MEASUREMENT` · Stand: 18.09.2026 · Basis 10.4.0-NEXUS Rev 2

## Vor dem Bau: dieselben Schritte wie der Pi-Volltest

- **Statische Hygiene komplett** (`volltest._static_hygiene()`): 0 Befunde.
- **Node-Frontendsuite** (`tests/frontend_v100.test.js`, lokal mit Node 18.4):
  28/28 gruen. Neu: PULSAR-Karten-Chips (Ausloeser, 3 Bestaetigungen,
  Existenzrisiko, Squeeze, Bilanz), Messungspanel mit Urteilsplakette und
  Einzelmessungen, Diagnoseliste mit „Bericht oeffnen" nur fuer fertige
  ZIPs und die Berichtsseite (Kennzahlen, Bereitschaft, Messung, Befunde,
  Blocker, Quellen, Escaping). Der Community-Test entfiel mit der Funktion.
- **Neue Suite** `tests/test_v1050_pulsar_messung_quellen.py`: 23/23 gruen
  (Details in `validation/NEXUS_10.5.0_TEST_EVIDENCE.json`).
- **Kompletter Testbestand** (222 Module, je Modul isolierter Prozess, Node
  auf PATH): 193 Module gruen, 3133 Tests bestanden. Alle 29 Fehlmodule sind
  die dokumentierten Windows-Artefakte; keine neue Fehlstelle gegenueber
  10.4.0 Rev 2. Ein Befund im Sammellauf war ein Testfehler am
  NY-Tageswechsel (Messungstest mit `NOW+600 s`); der Test wurde auf einen
  festen Zeitpunkt umgestellt und ist einzeln gruen.
  `test_v1012_diagnosis_webui` laeuft unter Windows erstmals bis zur
  Dateisperre (fcntl optional) und faellt dort an flock/Symlink; auf dem Pi
  ist die Sperre unveraendert Pflicht.
- Quellen gegen die echten Server geprueft (StockTwits, FINRA, Apewisdom,
  Tradestie erreichbar; Reddit RSS 1/min, Reddit JSON 403, Nasdaq
  Bot-Schutz). Fixtures aus echten Antworten, anonymisiert (keine
  Nachrichtentexte, keine Autorennamen).
- Visuelle Pruefung im eigenen Browser gegen die Paketdateien mit
  API-Attrappe: PULSAR-Seite (Messung, Quellen-Chips, Karten) und
  Diagnosebericht ohne JavaScript-Fehler.
- BOM 0, AST 0 Fehler, `REQUIRED_RELEASE_FILES` vollstaendig, alte
  10.4.0-Diagnose-Wrapper entfernt, CSP `script-src 'self'` unveraendert.

## Laufzeit bewiesen vs. offen

- Bewiesen: Erreichbarkeit und Antwortformate der neuen Quellen (18.09.);
  Urteilsregeln in Bot und Diagnose rechnen identisch.
- Offen (nur auf dem Pi): DNS-Aufloesung von `api.stocktwits.com` aus dem
  Heimnetz; erste echte Messzeilen; Diagnosebericht mit echter ZIP;
  FINRA-Verzug (15.09. erscheint erst um den 25.09.).
- Bewusst nicht behauptet: dass die Hype-Spur Geld verdient. Das Urteil der
  Messung gibt es erst ab 30 vollstaendigen 5-Tage-Messungen, und es ist
  keine Handelsfreigabe.

## Revision 2 (nach Pi-Runde 1, 18.09.2026)

Pi-Volltest Runde 1: alle 3298 Tests gruen, aber der Netzwerk-Tripwire des
Volltests meldete sieben Worker-Tests mit echtem HTTP-Versuch -> keine
Testfreigabe, Update vertragsgemaess abgebrochen, Staging archiviert
(`ALT_FEHLGESCHLAGEN_20260918T052942_...`), 10.3.1 lief weiter.

Ursache: Der Worker-Zyklus rief StockTwits und FINRA direkt ab, die
bestehenden Worker-Tests stubben aber nur `research.discover`/`fetch_social`
und `worker.gather_market`. Lokal blieb das unsichtbar, weil meine Pruefung
pytest je Modul fuhr, ohne den Netzwerkwaechter des Volltests: Die Abrufe
scheiterten, der Worker schluckte den Fehler, die Tests blieben gruen. Das
ist derselbe Fehlertyp wie bei 10.4.0 Rev 1 -- ein Pruefschritt des Pi, den
der lokale Bau nicht mitfuehrte.

Revision 2: (1) StockTwits und FINRA sind opt-in-Schalter auf der
PULSAR-Seite (wie Tradestie/GPT-Websuche), Standard aus; ausgeschaltet kein
externer Abruf, Felder UNKNOWN. (2) Trending laeuft ueber
`research.fetch_social("stocktwits")` in `discover()`, Strom und FINRA im
Marktpaket (`gather_market`) -- die Naehte, an denen die Offline-Tests ihre
Doubles setzen. (3) Der lokale Sammellauf installiert jetzt denselben
Netzwerkwaechter: 0 http-/dns-Ereignisse in 222 Modulen. Verbleibende
`socket`-Ereignisse stammen nur aus TestClient-Tests, weil Windows
`socket.socketpair()` ueber AF_INET emuliert (nachgestellt); auf dem Pi
liefen dieselben Tests in Runde 1 ohne Ereignis. Neue Suite 24/24, Node
28/28, Hygiene 0.

Massgeblich bleibt der Pi-Volltest der Installation. Maschinenlesbarer
Nachweis: `validation/NEXUS_10.5.0_TEST_EVIDENCE.json`.
