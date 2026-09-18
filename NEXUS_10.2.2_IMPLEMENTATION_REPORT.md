# NEXUS 10.2.2 – Implementierungsbericht (Punkt-Hotfix)

**Build:** `10.2.2-LOT-RESIDUAL-VALUATION-FIX` · Basis: 10.2.1-NEXUS · Stand: 17.09.2026

Eine gezielte Korrektur in `okx_reference_valuation._native_consistency_composite`:
Die EUR-Konsistenzpruefung rechnet den im nativen Verkaufsbeleg dokumentierten
Lot-Staub (`exit_native_json.receipt.residual`) in den Mengenvergleich ein – nur
diesen; ein negativer Rest oder jede darueber hinausgehende Abweichung bleibt
ein harter Fehler. Hintergrund, vollstaendige Architektur und alle uebrigen
Bausteine: `NEXUS_10.2.1_IMPLEMENTATION_REPORT.md` (unveraendert gueltig).

Neue Versionsnummer statt Revision: Der Updater verlangt Quelle ≠ Ziel; ein
Re-Install derselben Nummer ueber einen AKTIV laufenden Stand ist bewusst
unmoeglich (Installer-Abbruch am 17.09. war korrektes Schutzverhalten).

Test: `tests/test_v1021_composite_exit.py`, Klasse `LotStaubKonsistenz` –
End-zu-End mit den exakten Pi-Zahlen (7334,18847 Buch, 7334,18 verkauft,
0,00847 Staub) inkl. formatgueltigem Kursbeleg-Dokument und
load_on-Nachrechnung.
