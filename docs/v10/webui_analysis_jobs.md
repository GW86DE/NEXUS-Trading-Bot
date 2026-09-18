# NEXUS 10 – Analyseaufträge auf dem Raspberry Pi begrenzen

## NEXUS-IMP-ANALYSIS / UI-Analysewerkzeuge

**Problem im tatsächlichen Code:** Der Startpfad prüfte zuletzt gespeicherte RUNNING-Aufträge und startete Popen ohne gemeinsame Sperre. Zwei Browseranfragen konnten beide passieren; STARTING wurde nicht als aktiv berücksichtigt. `_running` verwendete `os.kill(int(pid or 0), 0)`, sodass eine ungültige Null-PID als lebende Prozessgruppe gelten konnte. PID-Wiederverwendung war nicht abgesichert. Der Analysechild lief ohne Timeout und ohne BLAS-/OMP-Begrenzung. Der Ausgabeleser las zunächst die vollständige Logdatei und kürzte erst danach.

**Bewertung:** Hoher Nutzwert für Pi-Stabilität und glaubwürdige Statusdarstellung, überschaubare lokale Änderung. Vorhandene Analyse-Seite und feste TASKS-Allowlist erhalten; keine zusätzlichen Dienste, Container, Scheduler, Brokeroperationen oder Arbitrary-Shell-Schnittstelle.

## Umsetzung

- `webui/analysis_jobs.py`: Ein `critical_state_lock` umfasst Admission, STARTING-Reservierung, Popen und Workerregistrierung. Thread- und Prozesszugriffe verwenden dieselbe Sperre. STARTING reserviert einen 30-Sekunden-Startzeitraum; ein abgebrochener Start bleibt nicht unbegrenzt aktiv. Auch ältere aktive Aufträge außerhalb der sichtbaren letzten 20 werden bei Admission geprüft.
- Aktive PIDs müssen strikt positiv/gültig sein. Linux-Identität besteht aus Boot-ID plus `/proc/<pid>/stat`-Starttick; Zombie/Exit zählt nicht als aktiv. Wiederverwendete PIDs werden als INTERRUPTED erkannt. Windows-Erstellzeit und GetExitCodeProcess sind ergänzend implementiert, aber nicht auf Windows ausgeführt.
- Eine noch lebende PID aus alten Statusdateien ohne Erstellidentität bedeutet UNKNOWN. Unlesbare Auftragsdateien bleiben erhalten und sperren weitere Starts, statt eine unbekannte laufende Analyse zu vergessen. Diagnoseaufrufe schicken keine Stopps an gespeicherte/fremde PIDs.
- Worker startet einen einzigen Child in einer eigenen Prozessgruppe, mit festem 30-Minuten-Limit. Unter POSIX beendet Timeout die frisch gestartete Gruppe einschließlich Nachkommen (TERM, kurze Schonzeit, KILL). Alle BLAS/OMP/NumExpr-Threadgrenzen werden für Worker und Child auf 1 gesetzt.
- Ein einzelner begrenzter Readerthread drainiert stdout/stderr. Ab 5.000.000 Bytes Ausgabe wird die Analyse mit OUTPUT_LIMIT beendet. Die Logdatei wächst nicht unbegrenzt. Das Zeitlimit und Ausgabelimit sind im API-Status sichtbar.
- `output()` liest per seek höchstens die letzten 250.000 Bytes; keine Komplettdatei im RAM. Ein einziger Redaktionsdurchlauf maskiert Zugangsdaten vor der Ausgabe. Das betrifft nur die Analyseausgabe.
- `provider_safety.redact` hat einen optionalen, nach oben auf 250.000 begrenzten `max_chars`-Parameter. Der bestehende Default von 800 bleibt für sämtliche anderen Aufrufer bestehen; die Maskierung geschieht vor der Kürzung.
- Neue Stati TIMED_OUT, OUTPUT_LIMIT und UNKNOWN, zusätzlich `overview.blocked`. Die bestehenden Frontendkontrollen wurden zur passenden Anzeige/Startsperre koordiniert, ohne CSS/Branding zu kopieren.

## Tests

`tests/test_v100_analysis_jobs.py` prüft mit synthetischen Daten und tatsächlich gestarteten kleinen lokalen Pythonprozessen:

- ungültige/Null/negative/boolesche PID ohne Signalisierung;
- PID-Wiederverwendung;
- STARTING-Sperre und abgelaufene Startreservierung;
- lebende Legacy-PID ohne Identitätsbeleg;
- beschädigte Auftragsdatei bleibt erhalten;
- zwei gleichzeitig startende Threads starten nur einen Worker;
- zwei unabhängige Linux-Forkprozesse starten nur einen Worker;
- Popen-Fehler hinterlässt FAILED statt ewiges STARTING;
- feste Allowlist weist fremden Befehl ab;
- Worker lehnt falschen Job/Prozessidentität ab;
- echter kurzer Child erhält Threadlimits und wird erfolgreich beendet;
- echter hängender Child und sein Nachkomme werden nach synthetisch kurzem Timeout beendet;
- extremer Output wird beendet, Logdateigröße bleibt innerhalb der Grenze;
- große sparse Logdatei wird ausschließlich als begrenzter Tail gelesen;
- Maskierung vor Kürzung, bestehender 800-Zeichen-Default und maximale Obergrenze;
- aktiver Child bei verschwundenem Supervisor wird UNKNOWN und sperrt neue Starts.

Erster Testlauf `analysis_jobs_e5640faa`: 53 bestanden, 1 fehlgeschlagen, kein Netzereignis. Der Fehler war real: Der alte Redaktionshelper kürzte jede Zeile auf 800 Zeichen und entfernte dadurch das Ende einer sehr langen Tailzeile. Der neue optionale Parameter korrigiert genau diesen Fall; keine pauschale Verlängerung aller Providerfehlertexte.

Nach Korrektur `analysis_jobs_final_cf2b8bcb`: 57/57 bestanden, kein Netzereignis. Enthält neue Analysejobtests, vorhandene WebUI-Analysetests sowie die Risiko-/Tagesbasis-Gruppen. Anschließend wurde die semantisch gleiche Logmaskierung auf einen einzigen begrenzten Durchlauf reduziert; endgültige Gesamtprüfung erfolgt im Release-Lauf.

## Grenzen / Restprobleme

- Kein Pi-5-Hardware-/Dauerlasttest und kein Windows-Prozessgruppentest. POSIX-Timeout/Descendant-Abbruch ist praktisch ausgeführt; der Windows-Pfad beendet derzeit den direkten Child, nicht mit demselben nachgewiesenen POSIX-Gruppenvertrag.
- Ein von außen hart beendeter Supervisor kann einen noch laufenden Child hinterlassen. Dessen gespeicherte Startidentität wird erkannt: UNKNOWN, weitere Starts zu. Die reine Diagnose führt keine automatischen Killoperationen auf historisch gespeicherte PIDs aus. Ein solcher Fall erfordert lokale Prozessprüfung; es wird keine vollständige Wiederaufnahme eines verlorenen Supervisors behauptet.
- Ein Datenjob, der legitimerweise mehr als 30 Minuten oder 5 MB Logausgabe braucht, muss gezielt geprüft werden. Standardmäßig wird er kontrolliert beendet; die Grenzen werden nicht still automatisch erhöht.
- Keine neue Trainings-/Strategiefreigabe durch diese Änderung. Die vorhandenen fest erlaubten Analysetasks behalten ihre bisherige Fachfunktion; deren wirtschaftliche Eignung folgt nicht aus erfolgreich abgeschlossenem Pythonprozess.

Einzelmodulverifikation ersetzt keine Gesamtfreigabe. Kein echter Brokerauftrag, keine fremde Datei und kein aktueller Handelszustand wurde für diese Tests verwendet.


## Nachprüfung des Fortschritts und Integration

Eine unabhängige Gegenprobe zeigte, dass `BufferedReader.read(65536)` auch nach einem `flush()` des Childs bis zu einem vollen 64-KB-Puffer warten kann. Das wäre eine irreführende scheinbar hängende Analyseanzeige. Der Reader verwendet jetzt `read1(65536)`; ein echter Child schreibt einen Fortschrittsmarker und wartet anschließend auf eine Datei. Der Test beweist, dass die WebUI-Ausgabe den Marker bereits zeigt, während der Child noch läuft. Erst danach wird der Child kontrolliert freigegeben.

Gezielter Nachprüfungslauf `review_risk_jobs_a694b29e`: 86/86 bestanden, keine Netzwerkereignisse. Die abschließende Integration deckte zusätzlich einen tatsächlichen PID-Namespace-Fehler auf; Ursache und Korrektur folgen unten.


## Aufgeklärter PID-Namespace-Fehler aus dem Gesamtprozess

Der erste große Lauf scheiterte in zwei Prozessidentitätsfällen, während kürzere Läufe teilweise grün waren. Das war keine ausreichende Entwarnung. Kernelbelege zeigten hier `os.getpid() == 5`, aber `/proc/self -> 449579`; `pidfd_open(os.getpid())` lieferte im fdinfo `Pid: 449579` und `NSpid: 449579 5`. Die Testumgebung besitzt einen inneren PID-Namespace bei einem /proc-Mount des äußeren Namespace. Die ursprüngliche direkte Lesung `/proc/5/stat` konnte deshalb den falschen Hostprozess bzw. keinen Prozess treffen. Ein früherer positiver Einzeltest beweist für diesen Randfall somit keine korrekte Identität.

Die Produktionsfunktion löst die lokale PID jetzt über `os.pidfd_open()` im Kernel auf und liest aus `/proc/self/fdinfo/<fd>` die dazu gehörende PID dieses Proc-Mounts. Erst dann werden Status, Boot-ID und Starttick gelesen. Der pidfd bleibt während der Abfrage offen und wird zuverlässig geschlossen. Kein vollständiger /proc-Scan, kein externer Prozessmanager und kein Signal sind nötig. Falls pidfd nicht unterstützt wird, ist der direkte Fallback ausschließlich zulässig, wenn `/proc/self` und `os.getpid()` tatsächlich denselben Namespace verwenden; andernfalls bleibt Identität unbekannt.

Der neue Testoracle vergleicht die ermittelte Identität mit dem tatsächlichen `/proc/self/stat`-Starttick und der dortigen PID. Ein weiterer Test verhindert bewusst geratenen Host-PID-Fallback bei fehlender pidfd-Unterstützung und abweichendem Namespace. Diese Korrektur ist für eingeschränkte Container relevant und bleibt auf dem Pi ein einfacher lokaler Kernelaufruf.

Zwischenlauf `jobs_order_probe_57a5f501`: 2370/2370 Tests plus 244 Subtests bestanden, keine Netzereignisse; dieser Lauf diente der Eingrenzung und liegt noch vor der PID-Namespace-Korrektur. Die endgültige Gesamtabnahme ist deshalb der spätere Release-Lauf, nicht dieser Zwischenstand.
