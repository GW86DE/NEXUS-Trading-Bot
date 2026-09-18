# NEXUS 10.4.0 – Pruefbericht

**Build:** `10.4.0-CHART-SCAN-EXISTENCE` · Stand: 18.09.2026 · Basis 10.3.1-NEXUS Rev 2

## Vor dem Bau: kompletter Testbestand (Regel „IMMER saubere Install-Datei")

- **Alle 221 Testmodule** je Modul in einem eigenen Prozess (Windows, 6 parallel):
  191 Module gruen, 3084 Tests bestanden, 18 uebersprungen; 53 Fehlschlaege und
  8 Sammelfehler liegen ausschliesslich in den 30 dokumentierten
  Windows-Artefakt-Modulen (fcntl-Import, Symlink WinError 1314, chmod-Bits,
  SQLite-Dateisperre WinError 32, fehlendes node/systemctl, POSIX-
  Credential-Pfade, pytest-Pin, v902-Telegram-Claim, v990-DataFrame-Index).
  **Keine neue Fehlstelle** gegenueber der Artefaktliste der 10.3.0/10.3.1.
- **Neue Suite** `tests/test_v1040_scan_chart_existence.py`: 33/33 gruen
  (Existenzrisiko-Arten, 90-Tage-Grenze, Profil `isActivelyTrading`, schwache
  Bilanz = kein Block, SEC-Dokument, Luna-Anweisung, Telegram-Text; Quote
  frisch/duenn/ohne Plus/alt/fremd; Schema einmal + Wiederaufbau, WAL-Leser
  blockiert nicht; Scan-Zyklus, 48-Grenze, defekte Datei, API mit Sitzung,
  Logbuch-Seite; eToro-Kerzenspeicher inkrementell, Ruhezeit/Budget; Chart
  eToro/OKX, Achsenklemmung, Paginierung, API-Zeitrahmen, ECharts lokal/CSP).
- Fortgeschriebene Suiten lokal gruen: v1030 (20), v985 (15), v986 (27),
  v987 (28), v984 (8), repair_pulsar_selection (18), v901 (9), v941 (6),
  v910 (27), v920 (16), v980 (14), v1015_webui_diagnosis (3), v1019 (5).
- Hygiene: BOM-Scan baumweit 0, CRLF in geaenderten Dateien 0, AST 0 Fehler,
  volltest-Release-Checkliste leer, `REQUIRED_RELEASE_FILES` vollstaendig,
  ECharts-SHA256 und Lizenzdatei geprueft, keine CDN-/https-Referenz in
  trades.html, CSP `script-src 'self'` unveraendert, alte 10.3.1-Diagnose-
  Wrapper entfernt.
- Visuelle Pruefung (eigener Browser gegen die Paketdateien mit API-Attrappe):
  eToro 1h/1d und OKX 5m gerendert, Marker/Linien/Volumen/Schieberegler
  sichtbar, Zoom-Erhalt bei stiller Aktualisierung, keine JavaScript-Fehler.

## Laufzeit bewiesen vs. offen

- Bewiesen (Diagnosen 17.09.): Wurzeln der Punkte 2–4 (Journal ohne
  HOLD-Eintraege; `database is locked` in control.py:59 durch executescript;
  nie befuellte Intraday-Reihe; AMC-Existenzrisiko ohne Pruefung).
- Erwartete Pi-Abnahme: Scan-Uebersicht im Logbuch nach dem ersten Zyklus;
  Kerzenansicht OKX sofort, eToro nach dem ersten Scan; keine Lock-Zeile
  waehrend einer Diagnose; Hype-Karten mit Existenzrisiko-Zeile.
- Nur auf dem Pi pruefbar: Node-Frontendsuite (um den Kerzenansichts-Test
  erweitert), POSIX-gebundene Module, echte eToro-Kerzenabrufe (15m/1d) im
  Lesebudget.

## Revision 2 (nach Pi-Runde 1, 18.09.2026)

Pi-Volltest Runde 1: 3274 gruen, 3 Skips, **zwei Befunde**, Update
vertragsgemaess abgebrochen (Staging archiviert als
`ALT_FEHLGESCHLAGEN_20260917T230559_TradingBot_v10.4.0_NEXUS`, 10.3.1 lief
unberuehrt weiter). Beide Befunde lagen in Pruefungen, die mein Bauskript
nicht ausgefuehrt hatte -- der Anspruch „saubere Install-Datei" war damit
nicht erfuellt:

1. **STATIC RELEASE HYGIENE:** `live_trader.py:2127: stummer Exception-Pfad`
   -- ein von mir neu eingefuegtes `except Exception: pass` um die
   Scan-Uebersichts-Zaehlung. Der Volltest verbietet genau dieses Muster; das
   Bauskript hatte nur `volltest._release_checklist()` ausgefuehrt, nicht die
   komplette `_static_hygiene()`. Fix: Ausnahme wird per `logger.debug`
   protokolliert. Das Bauskript fuehrt jetzt die **komplette** statische
   Hygiene aus und bricht bei jedem Befund ab.
2. **Node-Frontendsuite, Subtest 26:** `assert.equal(context.ausgewaehlt, 7)`
   -- `ausgewaehlt` ist im WebUI-Skript eine `let`-Bindung und im vm-Kontext
   keine Eigenschaft des Kontextobjekts (`undefined !== 7`). Der Test war
   falsch, nicht das Produkt. Fix: Lesen per `run('ausgewaehlt')`, ebenso das
   Setzen von `tradeDaten`. Ursache des Uebersehens: kein Node auf dem
   Windows-Pruefstand. Jetzt ist Node (18.4, PyPI-Paket `nodejs-bin`) lokal
   verfuegbar; die Suite lief hier **26/26** gruen, und das Bauskript fuehrt
   sie als harten Bau-Schritt aus. Zusaetzlich laufen damit auch die JS-
   Renderer-Tests (v988/v1013/v1014/v1016) lokal statt nur auf dem Pi.

Vor Revision 2 erneut der komplette Testbestand (221 Module, isoliert) plus
`volltest._static_hygiene()` = 0 Befunde: siehe TEST_EVIDENCE.

Massgeblich bleibt der Pi-Volltest der Installation. Maschinenlesbarer
Nachweis: `validation/NEXUS_10.4.0_TEST_EVIDENCE.json`.
