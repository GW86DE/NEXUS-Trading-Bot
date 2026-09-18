# NEXUS 10.1.9 – Prüfbericht

**Build:** `10.1.9-OKX-RISK-CAPITAL-AND-DIAGNOSIS-EVIDENCE`  
**Stand:** 15. September 2026  
**Prüfumgebung:** Linux x86_64, Python 3.13.5, pytest 9.0.2.

## Ergebnis

Die vollständige `tests/`-Sammlung wurde wegen der Zeitgrenze der Ausführungsumgebung in reproduzierbaren Teilgruppen ausgeführt. Insgesamt wurden **3.127 Tests gesammelt: 3.124 bestanden, 3 bedingte Tests wurden übersprungen; zusätzlich bestanden 283 Untertests**. Es gab 6 Deprecation-Hinweise aus `multiprocessing/fork` in mehrfädigen Testfällen; kein Test wurde deshalb übersprungen oder als bestanden erzwungen.

Zusätzlich bestanden die übrigen Release-Prüfgruppen: **Static Release Hygiene, Testabhängigkeiten/WebUI-TestClient, Self-Test, compileall, host-neutraler Pi-Preflight und Shell-Syntax**. Der neue reportgetriebene Korrekturblock wurde vorab separat mit **12/12** gezielten Regressionen geprüft; diese 12 sind Teil der obigen Gesamtsuite und werden nicht doppelt gezählt.

Maschinenlesbarer Nachweis: `validation/NEXUS_10.1.9_TEST_EVIDENCE.json`.

## Geprüfte 10.1.9-Invarianten

- Freigegebenes USDC wird nur über einen beobachteten Kreuzkurs in die primäre OKX-Risikowährung überführt.
- Ohne belastbaren Kreuzkurs bleibt die Risikobasis unbekannt (`RISK_CAPITAL_FX_UNKNOWN`); es gibt keine angenommene Stablecoin-Parität.
- Nicht freigegebenes USD kann sichtbar sein, wird aber nicht automatisch zum handelbaren Kapital.
- Eigene Krypto-Positionen tragen eine explizite Markt-/Abrechnungswährung in die Kapitalbewertung.
- Ein technisch gültiger Kontosnapshot mit null freigegebenem Kapital wird nicht als Abruffehler beschrieben.
- Die GET-only OKX-Kontovorprüfung zeigt Account-Modus und Fundingstatus, ohne Brokerparameter umzuschalten.
- PULSAR kann bei übergroßem `top5`-Cache eine revisionsgebundene kompakte Diagnoseprojektion liefern; Rohtexte werden darin nicht dupliziert.
- FMP->GPT-Eingabebelege bleiben in dieser Projektion prüfbar.
- Bewusst begrenzte Tabellenexporte sind INFO/Untergrenze; ausgelassene Zellen und echte Lesefehler bleiben UNKNOWN.
- Order-State-Machine, Schutzregeln, Freqtrade-Strategiebedingungen und PULSAR-Handelsgrenzen bleiben unverändert.

## Testharness

Der POSIX-Prozessgruppen-Test nutzte in 10.1.8 ein synthetisches Timeout von 0,3 Sekunden und konnte auf belasteten Hosts den Testprozess beenden, bevor dessen absichtlich gestarteter Enkelprozess seine PID geloggt hatte. Nur das **Testtimeout** wurde auf 1,0 Sekunde angehoben; der Produktwert `JOB_TIMEOUT_SECONDS=1800` blieb unverändert. Der Test prüft weiterhin, dass Leader und Descendant der neu erzeugten Prozessgruppe beendet werden.

## Grenzen

Es wurde keine reale Brokerorder, keine Testorder, kein kostenpflichtiger GPT-/X-Aufruf und keine Telegram-Nachricht erzeugt. Eine tatsächliche ARM64-/Raspberry-Pi-Installation, ein Neustart der realen Dienste und neue Providerantworten sind Laufzeitabnahmen nach Installation. Historische eToro-Abschlusskosten und andere fehlende Originalbelege wurden nicht ergänzt oder geschätzt.

Ein einzelner ununterbrochener `volltest.py`-Aufruf wurde von der Werkzeug-Zeitgrenze bei 53 % beendet und wird ausdrücklich **nicht** als Testnachweis gewertet. Stattdessen wurden alle 208 Testdateien in den dokumentierten Teilgruppen vollständig ausgeführt und anschließend alle übrigen Volltest-Gruppen separat bestanden.
