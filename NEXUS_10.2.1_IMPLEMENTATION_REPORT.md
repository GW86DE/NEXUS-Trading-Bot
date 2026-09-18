# NEXUS 10.2.1 – Implementierungsbericht (Hotfix)

**Build:** `10.2.1-COMPOSITE-EXIT-SETTLEMENT` · Basis: 10.2.0-NEXUS (Rev. 2) · Stand: 17.09.2026

## 1. Diagnosebefund (Auslöser)

Diagnosen 02:31/03:51 UTC: Der 10.2.0-EXIT_IN_PROGRESS-Fix wirkte belegt
(Log 03:54), aber die DOGE-Position (trade 73) hing in
`exit_state=ACCOUNTING_PENDING` mit leeren `exit_order_ids` — der Verkauf
war der Broker-OCO-Take-Profit, dessen Kind-Fills nie als Exit registriert
wurden. `_externer_verkaufsbeweis` verlangte über
`external_exit_evidence(expected_qty=volle Menge)` EINE komplette
"filled"-Order; der reale Mischverkauf (eigener TP-Teilfill 1.575,48 via
DOGE-USD/USDC + Nutzerorders 1.280,1 teilgefüllt-storniert und 4.478,6 via
DOGE-EUR = exakt 7.334,18847) war damit strukturell unbeweisbar. Zusätzlich
hätte selbst ein gelungener nativer Abschluss die Domäne nicht freigegeben:
`record_native_exit` ließ den `okx_balance_gaps`-PENDING stehen (Nebenbug),
und ohne gespeicherte EUR-Referenzbewertung erzeugt jeder unbezifferte
Cross-Currency-Abschluss einen RESULT-Beleg — `store_on` wurde bislang nur
vom manuellen Kursexport-Reparaturweg aufgerufen.

## 2. Zusammengesetzter Verkaufsbeweis

`okx_external_settlement.composite_receipt`: mehrere terminale
Verkaufsorders desselben Basiswerts (eigene via `protection_exit_order_ids`
markiert, fremde erlaubt; Zustände filled/canceled/mmp_canceled; accFillSz
je Order exakt gleich der Summe ihrer tradeId-Fills; Duplikate mit
abweichenden Kernfeldern, fehlende Gebührenwährung, Unter-/Überdeckung der
Ledger-Sollmenge über Lot-Staub hinaus: Abbruch). Erlöse/Gebühren je
Abrechnungswährung nativ als `legs`. `broker.composite_exit_evidence`
sammelt dazu Fill-Historie basiswertweise über alle Quote-Paare (plus
frische Fills je Instrument), verweigert bei fremden Kauf-Fills nach dem
Einstieg und validiert jede Ordergruppe gegen `order_status` (Fallback
`order_history_match`). `record_composite_exit` schließt die Ledgerzeile
atomar (Fill-IDs exklusiv in `trade_native_exit_fills`, bezifferte
Ergebnisse unantastbar, idempotent bei identischem Beleg) und löst den
Bestandsbeleg; derselbe `_resolve_balance_gap` heilt auch den bestehenden
`record_native_exit`-Pfad. Engine: `_externer_verkaufsbeweis` nimmt die
LEDGER-Menge als Soll (9.0.14-Lehre) und fällt nach dem bestehenden Pfad
auf den zusammengesetzten Beweis zurück; `_position_extern_zusammengesetzt`
verbucht, meldet die nativen Erlöse je Währung („NICHT der Gewinn") und
stößt die Bezifferung an.

## 3. Automatische EUR-Referenzbewertung

`okx_reference_valuation` erhält `METHOD_COMPOSITE`
(`EUR_REFERENCE_CASHFLOWS_COMPOSITE_V1`), `calculate_composite` (ein
belegter Kauf, mehrere prove_order-belegte Verkaufsorders; identische
Fill-für-Fill-Bewertung wie `calculate`; Summenmenge exakt zur
Einstiegsmenge; Verkäufe nie vor vollständigem Kauf),
`_native_consistency_composite` (Legs gegen den nativen Beleg, exklusive
Fill-Ansprüche) und `store_on_composite`; `load_on` verifiziert beide
Methoden vollständig nach (Nachrechnung aus Originaldaten). Neu:
`broker.fx_followup_document` erzeugt das bestehende, streng validierte
Belegformat `nexus-public-fx-followup-v1` direkt — öffentliche
USDC/EUR-Minutenkerzen von `eea.okx.com` je benötigter Fill-Minute (rohe
Antwort + SHA-256), EZB-Tagesreferenz nur bei USD-Legs.
`okx_reference_autovaluation.run_one` (im Engine-Takt alle 2 min, je Trade
10-min-Backoff, höchstens ein Trade) baut Entry-/Sale-Beweise aus
Broker-Originaldaten nach, bewertet und speichert. Danach beziffert der
bestehende Risiko-Ergebnisabgleich das UNKNOWN-Ergebnis über `load_on`,
der RESULT-Beleg entfällt, die Kaufdomäne wird frei. Misslingt ein
Kursbeleg, bleibt alles konservativ gesperrt (UNKNOWN bleibt UNKNOWN).

## 4. Teil-Fill eigener Schutzorders

`crypto_engine._verbuche_eigenen_schutz_teilfill`: Ist ein Teilabgang
EXAKT (±2 Lots) durch tradeId-belegte SELL-Fills der eigenen Schutzorder
erklärt, wird er als Teilverkauf über `trade_ledger.trade_close`
geschrieben (das Ledger splittet die Zeile selbst, behält die anteilige
Einstandsgebühr für den Rest und löst den Bestandsbeleg); die Position
läuft mit Restmenge und aktivem Restschutz weiter, `exit_state` wird
freigegeben. Bezifferung wie bei jedem externen Abschluss durch den
9.8.8-Nachbeleg-Leser mit Original-Belegen — hier wird kein Ergebnis
erfunden. Einhängung ausschließlich im Teilabgangs-Zweig vor der
bisherigen Einfrier-Logik; jeder Zweifel behält den Altpfad.

## 5. Log-Hygiene

Warteschleifen-WARNING („wartet auf eindeutigen Orderbeleg") nur noch bei
Check 1–3 und danach jedem 100. (sonst DEBUG); am 17.09. standen >2.500
identische Zeilen im Log.

## 6. OKX-API-Durchsicht (Auftrag: „deutsche Version/Demo")

Recherche gegen die offizielle EEA-Doku (my.okx.com/docs-v5) und
Community-Quellen: Die EU-/deutsche Instanz nutzt API-seitig
`https://eea.okx.com` für Live UND Demo (`x-simulated-trading: 1`);
`my.okx.com` ist die Weboberfläche derselben Entität, API-Keys sind
subdomaingebunden. NEXUS ist exakt so konfiguriert (config.OKX_BASE_URL
seit 10.1.x). Alle 21 genutzten REST-Endpunkte (Auflistung im Prüfbericht)
sind v5-konform; die EEA-Doku nennt für Demo keine Einschränkung der von
NEXUS genutzten Endpunkte (nicht unterstützt: Ein-/Auszahlung, Earn — von
NEXUS nie genutzt). **Ergebnis: keine Änderung erforderlich**; der
beobachtete ReadTimeout 05:52 war transient (Retry vorhanden).

## Geänderte/neue Dateien

Neu: `okx_reference_autovaluation.py`, `tests/test_v1021_composite_exit.py`,
Doku 10.2.1. Geändert: `okx_external_settlement.py` (composite_receipt,
record_composite_exit, `_resolve_balance_gap` auch im nativen Pfad),
`broker/okx.py` (composite_exit_evidence, fx_followup_document),
`okx_reference_valuation.py` (Composite-Methode, load_on-Weiche),
`crypto_engine.py` (Beweis-Fallback mit Ledger-Sollmenge,
Composite-Verbuchung, Schutz-Teilfill-Verbuchung, Autovaluation-Takt,
Log-Ratenbremse), Versionsführung/volltest/Testpins.
