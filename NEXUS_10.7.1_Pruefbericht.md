# NEXUS 10.7.1 – Pruefbericht

**Build:** `10.7.1-LOCK-RESOLUTION-AND-LEDGER-RECEIPTS` · Stand: 18.09.2026 · Basis 10.7.0 Rev 2

## Vor dem Bau: dieselben Schritte wie der Pi-Volltest, plus Je-Test-Vergleich

- **Statische Hygiene komplett** (`volltest._static_hygiene()`): 0 Befunde.
- **Node-Frontendsuite**: 30/30 (unveraendert; keine JS-Aenderung).
- **Netzwerkwaechter** ueber PULSAR-Worker-Modulen, den 10.6.0/10.7.0-Suiten
  und der neuen 10.7.1-Suite: kein `http`-, kein `dns`-Ereignis.
- **Je-Test-Vergleich mit POSIX-Shims** gegen das ausgelieferte 10.7.0-Paket:
  dieselben Module wie auf dem Pi; jeder neu rote Test bricht den Bau ab.
- **Kompletter Testbestand isoliert, zweimal**: Zahlen in
  `validation/NEXUS_10.7.1_TEST_EVIDENCE.json`.

## Was die neuen Tests belegen (`tests/test_v1071_sperren_aufloesung.py`, 15)

**OKX**
- Der Zustand vom 18.09.: Rest-Zeile als RESIDUAL_EXPOSURE umbenannt, Notiz
  ersetzt → Reparatur schliesst die Luecke (`LOT_RESIDUAL_LINEAGE`,
  `residual=0.001765`), Coin frei.
- Rest ganz ohne Notiz → ebenfalls erkannt (Struktur: gleiche Einstiegskette,
  offen, keine eigenen Fills).
- Offene Zeile MIT eigenen Fills → kein Rest, Luecke bleibt (kein Beweis
  erfunden).
- Nach der Reparatur: `require_tradable` laesst den Coin durch; neuer Kauf und
  Vollverkauf hinterlassen keine Luecke.
- Sperrliste: ein Coin mit Buchungsbeleg erscheint genau einmal, nicht als
  Fehlbestand.

**eToro**
- `trade_open` speichert die Gebuehrenwaehrung aus `fee_currency` (eToro) wie
  aus `feeCcy` (OKX).
- Der Datenbestand von Trade 88 (Gebuehr 1,00, Waehrung leer): der Abgleich
  traegt USD nach, Einstieg CONFIRMED, 1,00 USD. Andere Waehrung (EUR) und
  andere Gebuehr bleiben Widerspruch, nichts wird geaendert.
- Vom Abgleich verbuchter Verkauf ohne Risikobeleg: EIN Abgleichlauf
  registriert und beziffert ihn (EXPECTED_UNVERIFIED, netto 18,00 im
  Testfall); `kaufsperre_grund` leer, `offene_ergebnisse_heute` 0.
- Verkauf von HEUTE: registriert (heute), im selben Lauf beziffert, Tages-P&L
  gebucht, Kaufsperre erscheint nicht.
- Manuelle Position und bereits bezifferte Zeile werden nicht registriert;
  zweiter Lauf tut nichts; fremdes Konto tut nichts.
- Alter Beleg unter dem Fill-Schluessel wird als Alias uebernommen, nicht
  verdoppelt.
- Zwei Laeufe, zwei Trades: der zweite laeuft wie der erste; alle Belege
  CONFIRMED oder ALIAS.

**Sperrliste eToro** (`tests/test_v1070_handelsfreigabe.py`, +2)
- Fehlgeschlagener Ledgerabgleich → `LEDGERABGLEICH_OFFEN`, Ablauf REPARATUR;
  verknuepfter Fall ohne Netto → `PNL_INCOMPLETE`, TAGESRESET; unlesbarer
  Abgleich → `BUCHUNG_NICHT_PRUEFBAR`.

## Bewiesen vs. offen

**Bewiesen (offline):** die vier Datenlagen des Pi und ihre Aufloesung, der
naechste Trade je Broker, die Sperrliste.

**Offen (nur auf dem Pi):** die Reihenfolge Abgleich → Kostennachlauf →
Historienbeleg → Abrechnung fuer CSCO in Echtzeit (Minuten bis ~30 Minuten);
erste echte BTC/ETH/XRP-Kaeufe nach der Freigabe.

## Invarianten

UNKNOWN bleibt UNKNOWN (ein Erwartungswert ist gekennzeichnet, nie bestaetigt).
Kein Verkauf auf Fremdbestand. Kontowechsel, Persistenzfehler und unlesbares
Ledger sperren weiter alles. Kein TLS-Bypass. PULSAR, X und GPT haben keine
Orderbefugnis. CSP `script-src 'self'`, kein CDN. Keine Klarnamen in
Diagnose-Exporten.
