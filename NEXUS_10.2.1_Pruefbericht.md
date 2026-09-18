# NEXUS 10.2.1 – Pruefbericht (Hotfix)

**Build:** `10.2.1-COMPOSITE-EXIT-SETTLEMENT`
**Stand:** 17. September 2026
**Pruefumgebung der Paketierung:** Windows 11 x86_64, Python 3.14.7 (pandas, numpy, requests, fastapi, pytest, scikit-learn installiert).

## Ergebnis der Offline-Pruefung bei Paketierung

- **Syntax:** alle Python-Dateien per AST geparst, 0 Fehler; keine UTF-8-BOMs; automatisierte Release-Checkliste 0 Befunde.
- **Gezielte 10.2.1-Regressionen: 13 Tests, alle gruen** (`tests/test_v1021_composite_exit.py`):
  - Der reale DOGE-Mischfall in Originalzahlen (TP 1.575,48 USDC-abgerechnet + 1.280,1 teilgefuellt-storniert + 4.478,6 DOGE/EUR = exakt 7.334,18847) wird vollstaendig belegt: zwei native Waehrungs-Legs, kein Mischkurs, eigene/fremde Orders getrennt ausgewiesen.
  - Teilgefuellt-stornierte Orders sind terminal belegbar; Unter-/Ueberdeckung, unbelegte Ordermengen, widerspruechliche Doppel-Fills und fehlende Gebuehrenwaehrung brechen ab.
  - `record_composite_exit` schliesst die Ledgerzeile, loest den PENDING-Bestandsbeleg, ist idempotent und ueberschreibt niemals ein beziffertes Ergebnis.
  - `calculate_composite` summiert die EUR-Bewertung ueber die Legs korrekt (nachgerechneter Erwartungswert) und lehnt Verkaeufe vor vollstaendigem Kauf ab.
  - Verdrahtungspruefungen: Engine-Fallback, Teilfill-Pfad, Autovaluation-Takt, Log-Ratenbremse, Gap-Aufloesung in BEIDEN Verbuchungspfaden.
- **Breiter lokaler Bestand:** pytest ueber die gesamte Suite (ohne die 8 POSIX-`fcntl`-Module): siehe Zusammenfassung in `validation/NEXUS_10.2.1_TEST_EVIDENCE.json`; verbleibende Fehlschlaege sind ausnahmslos die bekannten Windows-Plattformklassen (fcntl, Symlink-Privileg, SQLite-Dateisperren, MAX_PATH). Massgeblich bleibt der Pi-Volltest der Installation.

## OKX-API-Durchsicht (Auftrag: deutsche Version / Demo)

Gegen die offizielle EEA-API-Doku (my.okx.com/docs-v5) verifiziert:
- EEA-REST-Basis fuer Live **und** Demo: `https://eea.okx.com`; Demo-Header `x-simulated-trading: 1`. NEXUS ist seit 10.1.x exakt so konfiguriert; `my.okx.com` ist nur die Weboberflaeche derselben EU-Entitaet (API-Keys sind subdomaingebunden).
- Genutzte REST-Endpunkte (21, alle v5-konform): account/balance, account/config, account/trade-fee, market/books, market/candles, market/history-candles, market/ticker, market/tickers, public/price-limit, public/time, trade/fills, trade/fills-history, trade/order (GET+POST), trade/order-algo (GET+POST), trade/orders-algo-pending, trade/orders-pending, trade/amend-algos, trade/cancel-algos, trade/cancel-order.
- Demo-Einschraenkungen laut Doku betreffen nur Ein-/Auszahlung/Earn (von NEXUS nie genutzt). **Ergebnis: keine Anpassung erforderlich.**
- Neu genutzt (oeffentlich, kontofrei, nur fuer Kursbelege): market/history-candles USDC-EUR 1m sowie die EZB-Tagesreferenz (nur bei USD-Legs) — in exakt dem Belegformat, das die bestehende `Rates`-Validierung seit 9.8.x erzwingt.

## Erwartete Wirkung auf dem Pi (Abnahmepfad)

Nach Dienststart: zusammengesetzter DOGE-Beweis → Verbuchung mit echten Fills (Meldung mit nativen Erloesen je Waehrung) → automatische EUR-Referenzbewertung (Meldung „netto … EUR") → RESULT-/Bestandsbelege geschlossen → OKX-Kaufdomaene frei. Misslingt ein Kursbeleg voruebergehend, wiederholt der Takt mit Backoff; die Sperre bleibt bis dahin konservativ bestehen.

## Grenzen dieses Pruefstands

Vollstaendiger Volltest, Self-Test, compileall, Pi-Preflight, Shell-Syntax laufen wie immer waehrend der Pi-Installation (Pflicht vor Dienststart). Keine Brokerorder, kein kostenpflichtiger Aufruf, keine Telegram-Nachricht bei der Paketierung. Die EUR-Referenzbewertung ist eine belegte Naeherung (Qualitaet REFERENCE_VALUATION), keine Broker-Bestaetigung; der 9.8.8-Nachbeleg-Leser kann sie spaeter durch Original-Belege ersetzen.

## Erste Pi-Runde (17.09.2026, Revision 1) und der Revision-2-Fix

Revision 1 wurde installiert und arbeitete den DOGE-Fall bis auf den letzten
Schritt korrekt ab: zusammengesetzter Beweis gefunden, Trade 73 mit allen 9
Fills verbucht (CLOSED), Bestandsbeleg geloest — von 2 offenen Belegen blieb
nur noch der RESULT-Beleg. Die EUR-Referenzbewertung scheiterte dann aber
alle 10 Minuten reproduzierbar mit „EUR-Beleg passt nicht zur aktuellen
abgeschlossenen Handelszeile" (Diagnose 11:08 UTC).

**Ursache (Rev-1-Logikfehler):** Beim realen Verkauf blieben 0,00847 DOGE
als Lot-Staub im Konto (Buchmenge 7334,18847, verkauft 7334,18). Der
Verbucher akzeptiert diesen Rest ausdruecklich und dokumentiert ihn im
nativen Beleg; die neue EUR-Konsistenzpruefung verglich die Buchmenge aber
OHNE den dokumentierten Rest mit der verkauften Menge. **Fix (Rev 2):** Die
Pruefung rechnet den im nativen Beleg dokumentierten Rest ein (nur diesen —
eine groessere Abweichung bleibt ein harter Fehler; Negativrest wird
abgelehnt). Neuer End-zu-End-Test mit den exakten Pi-Zahlen inkl. Staub:
Verbuchung → Konsistenz → Speicherung → vollstaendige Nachrechnung beim
Laden ueber ein formatgueltiges Kursbeleg-Dokument (15 Tests gesamt).

Nach Installation von Revision 2 greift die naechste automatische Bewertung
(Backoff max. 10 Minuten) und die Domaene wird ohne weiteres Zutun frei.

Maschinenlesbarer Nachweis: `validation/NEXUS_10.2.1_TEST_EVIDENCE.json`.
