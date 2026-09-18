# NEXUS 10.2.2 – Pruefbericht (Punkt-Hotfix)

**Build:** `10.2.2-LOT-RESIDUAL-VALUATION-FIX` · Stand: 17.09.2026

- Aenderungsumfang: eine Funktion (`okx_reference_valuation._native_consistency_composite`)
  plus Versionsfuehrung/Doku; alles Uebrige identisch zu 10.2.1 (dessen
  Pruefbericht vollstaendig gueltig bleibt, inkl. OKX-API-Durchsicht).
- Gezielte Regressionen: `tests/test_v1021_composite_exit.py` = 15/15 gruen,
  darunter der neue End-zu-End-Fall mit den exakten Pi-Zahlen inkl. 0,00847
  DOGE Lot-Staub (Verbuchung → Konsistenz → Speicherung → vollstaendige
  Nachrechnung beim Laden ueber ein formatgueltiges Kursbeleg-Dokument) und
  der Negativfall (Abweichung ueber dem dokumentierten Staub bleibt Fehler).
- Automatisierte Release-Checkliste: 0 Befunde. AST 0 Fehler, keine BOMs.
- Massgeblich bleibt der Pi-Volltest der Installation. Erwartete Abnahme:
  binnen ~10 Minuten nach Dienststart die Meldung „EUR-Referenzbewertung fuer
  Trade 73 gespeichert: netto … EUR", danach Wegfall der Sperrmeldung.

Maschinenlesbarer Nachweis: `validation/NEXUS_10.2.1_TEST_EVIDENCE.json`
(der Fix ist dort als Revision-2-Highlight dokumentiert).
