# NEXUS 10.3.1 – Pruefbericht (Punkt-Hotfix)

**Build:** `10.3.1-ETORO-CASH-DELTA-SETTLEMENT` · Stand: 17.09.2026

## Code getestet (Windows-Arbeitsumgebung)

- `tests/test_v1031_etoro_cash_delta.py`: 12/12 gruen. Der Pi-Fall ist mit
  den Originalzahlen nachgestellt (Belege 15:11:07 [AAPL, META] 70.213,54 USD
  → 16:01:36 [META] 84.982,02 USD; Erloes 14.769,48; Abschlusskosten 1,00;
  netto 122,08; Intervall 58 s; automatische Freigabe).
- Bestehende Abrechnungs-/History-/Risiko-Suiten: 255 gruen, 1 Skip
  (v1015-Manuellpfad, v1014-History, v1013-Ergebnisscope/Exitkosten,
  v987-Abschluesse, v990-Recovery/Guards, v100-Risikopersistenz,
  v1012-Risiko/Schutz, v101-Basisreview, v1016/v1019).
- Node-Frontendtest des Abrechnungsdialogs (nur Pi): Fixture ohne die neuen
  Felder rendert den Zweig „Keine automatische Verbuchung"; die geprueften
  Textstellen bleiben unveraendert.

## Laufzeit bewiesen vs. offen

- Bewiesen (Diagnose): Barbestandsbelege werden minuetlich und bei jeder
  Aenderung gespeichert; credit enthaelt keine schwebenden Gewinne
  (META-Kauf: 85.026,04 → 70.213,54 = 22 × 673,25 + 1,00 exakt).
- Erwartete Pi-Abnahme: Logzeile „eToro-Abrechnung automatisch
  protokolliert: Trade 79 netto 122.08 USD (Abschlusskosten 1.00 USD …)"
  innerhalb des ersten Zyklus; Kaufsperre entfaellt; Trades 50/53 bleiben
  historisch offen (keine Belege aus dem September-Anfang), sperren nicht.
- Nicht automatisch loesbar (bewusst): Kosten ueber 5 USD bzw. 0,5 %,
  offene Orders im Beleg, andere Bewegung im Intervall → manueller Dialog.

## Revision 2 (nach Pi-Runde 1)

Pi-Volltest Runde 1: 3241/3242 gruen, ein Befund, Update vertragsgemaess
abgebrochen (Staging archiviert, laufender 10.3.0-Stand unberuehrt). Ursache:
Der Diagnose-Abrechnungsreport exportierte neu das Feld `actor` (Klarname des
Bestaetigers) und verletzte damit den Privatsphaere-Vertrag von
`tests/test_v1015_webui_diagnosis.py`. Revision 2 exportiert nur das
abgeleitete Flag `automatic`. Vor dem Neubau wurde diesmal der komplette
Testbestand (212 Module, je Modul isoliert) gefahren: keine neuen
Fehlstellen; alle verbleibenden Windows-Fehlklassen sind identisch zum
dokumentierten Artefaktvergleich der 10.3.0 (fcntl, Symlink/chmod,
SQLite-Sperren, systemctl/node, Testpin-Checks).

Massgeblich bleibt der Pi-Volltest der Installation. Maschinenlesbarer
Nachweis: `validation/NEXUS_10.3.1_TEST_EVIDENCE.json`.
