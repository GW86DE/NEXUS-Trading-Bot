# NEXUS 10.7.1 – Umsetzungsbericht

**Build:** `10.7.1-LOCK-RESOLUTION-AND-LEDGER-RECEIPTS` · 18.09.2026 · Basis 10.7.0 Rev 2

## Auftrag

Georg, 18.09.2026 (nach der Diagnose 18:28 UTC auf 10.7.0): „Löse das Problem
nachhaltig. … Die Sperren müssen sauber gelöst werden und überprüfe, ob
nachfolgende Trades dann endlich sauber laufen. Bitte achte darauf, dass die
Installation sauber läuft."

## Befund auf 10.7.0 Rev 2

| Sperre | Sichtbarer Grund | Tatsächliche Ursache |
|---|---|---|
| eToro, ganzes Konto | PNL_INCOMPLETE (CSCO) | Ledgerabgleich von Trade 88 schlug alle 10 s fehl: Gebühr 1,00 mit leerer Währung gespeichert (`trade_open` las nur `feeCcy`), Abgleich las das als Widerspruch → Einstieg UNKNOWN → kein Historienbeleg → Intervall/Erwartungswert dürfen nicht rechnen; zusätzlich fehlte `ledger:88` im Risikozustand, weil der Abgleich-Verkaufspfad nie an den Risikozustand meldete |
| OKX, BTC/ETH/XRP | BALANCE_REDUCTION (Trades 81–83) | Lot-Rest-Reparatur erkannte Rest-Zeilen nur an der Notiz „Rest nach Teilverkauf"; der Positionsabgleich hatte die Zeilen 84–86 als RESIDUAL_EXPOSURE umbenannt |
| OKX, Anzeige | zusätzlich BESTAND_FEHLT_UNBESTAETIGT | Waechter übergab die Gesamtsperre als Fehlbestand |
| eToro, Anzeige | „endet durch TAGESRESET" | ein fehlgeschlagener Ledgerabgleich endet nicht mit dem Handelstag |

Was funktionierte: OKX-Konto frei (Reichweite je Coin), Erwartungswert (TXN,
AMD um 20:05 automatisch), Sperrliste.

## Umsetzung

1. `risk_result_recovery.register_closed_etoro_rows` – geschlossene Bot-Trades
   ohne Beleg werden vor jedem Abgleichlauf registriert (Alias-Übernahme,
   keine Doppelzählung, Fremdpositionen und bezifferte Zeilen unberührt).
2. `trade_ledger`: `trade_open` speichert `fee_currency`; `reconcile_entry_fees_exact`
   trägt eine leere gespeicherte Währung bei gleicher Gebühr nach.
3. `okx_accounting._ist_rest_split` – Rest an der Struktur erkannt.
4. `crypto_engine._fehlende_symbole` + `handelsfreigabe.etoro_sperren` je
   Buchungsfall (PNL_INCOMPLETE/TAGESRESET vs. LEDGERABGLEICH_OFFEN/REPARATUR).

## Nachweis „nachfolgende Trades laufen"

`tests/test_v1071_sperren_aufloesung.py` stellt die Datenlagen des Pi nach und
führt sie weiter: nach der Reparatur ist der Coin für `require_tradable` frei,
ein neuer Kauf mit Vollverkauf hinterlässt keine Lücke; ein vom Abgleich
verbuchter Verkauf wird in EINEM Lauf registriert und beziffert, die
Kaufsperre bleibt leer (`kaufsperre_grund == ''`, `offene_ergebnisse_heute == 0`);
ein zweiter Verkauf im nächsten Lauf läuft genauso, und alle Belege sind
CONFIRMED oder ALIAS.

## Grenzen

- Die Bezifferung braucht den Historienbeleg des Brokers (Kostennachlauf, bis
  zu 30 Minuten nach dem letzten Versuch) und mindestens drei bestätigte
  Abrechnungen desselben Kontos (vorhanden: fünf).
- Ältere geschlossene Trades ohne Einstiegsbeleg bleiben offen sichtbar; sie
  sperren nicht.
