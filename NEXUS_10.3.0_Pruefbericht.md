# NEXUS 10.3.0 – Pruefbericht

**Build:** `10.3.0-PULSAR-HYPE-LANE` · Stand: 17.09.2026

## Code getestet (Windows-Arbeitsumgebung)

- Neue Zieltests: `tests/test_v1030_okx_freigabe.py` = 10/10 gruen (exakte
  Pi-Zahlen der Diagnose vom 17.09.: Saldo 1,00000000279 BTC bei Ledger-Rest
  2,79e-9; 10,000000102 ETH bei 1,02e-7; Composite-Methoden-Whitelist inkl.
  Negativtest; WARNING-Ratenbremse) und `tests/test_v1030_pulsar_hype.py`
  = 20/20 gruen (Hype-Kriterien inkl. Cold-Start/X-Stichprobe/Intraday-
  Rueckfall, Luna-REJECT, stable_candidate-Doppelmessung, Regelversion,
  Tageslimit/1-Trade-Regel, Zeitstop aus dem Plan, Universumsgaeste-Verfall
  und -Limit, 5+2-X-Suchplan im 15-EUR-Budget, Alarmtexte).
- Kompletter Testbestand (211 Module) auf Windows in isolierten Prozessen
  gefahren: alle Fehlschlaege wurden einzeln gegen die UNVERAENDERTE
  10.2.2-Basis gegengeprueft und sind dort identisch (fcntl-Import,
  Symlink-/chmod-Privilegien, systemctl/node-Subprozesse, sqlite-Filelocks,
  Testabhaengigkeits-Pins) -- keine durch 10.3.0 verursachte Regression.
- Fortgeschriebene Suiten laufen gruen: v984 (Tagesregel statt Wochenregel),
  v985/v986/v987/v100 (Hype-Kriterien statt Score/Community/Terra),
  v989 (Finanzfakten ohne Score-Komponente), v1013/v1014/v1016
  (X: 5 Kandidaten- + 2 Makro-Suchen, keine Zaehlungsabrufe).
- Automatisierte Release-Checkliste (volltest): BOM-Scan sauber, nur der
  10.3.0-Diagnose-Einstieg im Baum, RiskState-Klassifizierung vollstaendig,
  settings-Vertrag unveraendert.

## Laufzeit bewiesen vs. offen

- 10.2.2 bewies auf dem Pi bereits: Composite-Verbuchung, Autovaluation
  und Speicherung der EUR-Referenzbewertung fuer Trade 73 (Logzeile
  14:25:21). 10.3.0 aendert daran nichts; neu ist nur, dass der
  Risiko-Abgleich diesen Beleg akzeptiert.
- Erwartete Pi-Abnahme nach Installation: (a) keine wiederholte Meldung
  "Risiko-Ergebnisabgleich ledger:73 bleibt offen"; (b) BTC/ETH erscheinen
  als freie Konto-Assets, die Sperrmeldung "NEUE EINSTIEGE GESPERRT:
  BTC/ETH" entfaellt, OKX kauft wieder (Demo); (c) PULSAR-Seite zeigt die
  Hype-Spur; Alarme fruehestens beim naechsten Hype-Treffer.
- Nicht auf Laufzeit bewiesen (prinzipbedingt): ein realer Hype-Alarm samt
  Nominierung braucht einen echten Social-Spike mit Kursbestaetigung;
  der Pfad ist bis zur Nominierung testgedeckt, die Order bleibt an die
  persoenliche zweistufige Bestaetigung gebunden.

## Revision 2 (nach Pi-Runde 1)

Der erste Pi-Volltest lief 3229/3230 gruen und brach vertragsgemaess an EINEM
Befund ab (Staging selbst archiviert, 10.2.2 blieb unberuehrt): Die nur auf
dem Pi laufende Node-Frontend-Suite (`tests/frontend_v100.test.js`,
Subtest 19) pinnte noch die in 10.3.0 entfernte "Community-Breite"-Zeile der
PULSAR-Karte. Revision 2 schreibt den Subtest auf die Hype-Spur fort; jede
neue Assertion wurde gegen die im Pi-Protokoll ausgegebene aktuelle
Karten-HTML verifiziert. Kein Produktcode geaendert.

Massgeblich bleibt der Pi-Volltest, den der Installer vor der Uebernahme
ausfuehrt. Maschinenlesbarer Nachweis:
`validation/NEXUS_10.3.0_TEST_EVIDENCE.json`.
