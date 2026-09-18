# NEXUS 10 – N01/N02 Risiko und Persistenz

## Status und Umfang

N01-Kernkorrektur und N02 implementiert und durch isolierte Verhaltensprüfungen verifiziert. Kein Brokerauftrag und kein Netzwerkzugriff. Keine bestehenden Releasequellen verändert; alle Änderungen liegen in der Arbeitskopie.

Betroffene Dateien: `risk_manager.py`, `risk_pots.py`, `safe_persistence.py`, `tests/test_v100_risk_persistence.py`, die beiden Equity-Aufrufer plus naher Helper in `live_trader.py`, gezielte Oracle-Korrektur in `tests/test_v9010_equity_scan_visibility.py`. Keine Änderung an State-Pfaden, Risikoprofilen, Brokerorders, Stops oder globalen Limits.

## NEXUS-IMP-001 – Tagesbasis und Tagesstopp

Problem: Die 9.9.0-FIX1-Basis enthielt das gehaltene Symbolset. Eine Änderung von leer auf SUI setzte den Startwert und eine bestehende Equity-Sperre zurück. Die reproduzierte Folge 1000 → 980 → 980 mit SUI → 960,40 zeigte nur -2 % statt -3,96 % und keine 3-%-Bremse.

Lösung: Bewertungsidentität `handelbares_kapital:v3:<broker>:<quote>` ist unabhängig vom Positionsbestand. Alte v2-Schlüssel werden auf genau diese Identität normalisiert. Ein erster/neuer Schlüssel löscht weder Tagesstart noch Equity-Latch. Der reguläre lokale Handelstagswechsel bleibt die einzige automatische Tagesrücksetzung. Rückkehr zu höheren Kursen löscht die für diesen Tag erreichte Sperre ebenfalls nicht.

Unterschiedliche Währungen/Methoden bzw. ein späterer belegter Kontowechsel werden nicht mathematisch miteinander verrechnet. Sie setzen `equity_basis_review_required` mit `RISK_EQUITY_BASIS_REVIEW`, bewahren den bisherigen Wert und sperren nur den betroffenen Risikotopf. Dieser Reviewzustand überlebt Mitternacht und Neustart. Eine Statusabfrage ist keine Freigabe.

Kontozuordnung: RiskPotManager übernimmt ausschließlich vorhandene gehashte Brokeridentität und die Adapterumgebung; kein zusätzlicher API-Aufruf. eToros vorläufiger Hash vor erfolgreicher `/me`-Bindung wird nicht als Kontoanker übernommen. Aktuelle Bewertung und historischer Receipt-Scope werden nicht gleichgesetzt: migrierte Historie ist `LEGACY_UNASSIGNED`; bestehende Belege und Zähler bleiben unverändert. Eine vollständige Aufteilung historischer Kontostände wird ausdrücklich nicht behauptet. Andere Konten/Umgebungen in derselben Installation erfordern weiter eine belegte Migration bzw. eine getrennte Installation; ein stilles Nullsetzen ist ausgeschlossen.

Kapitalflüsse: Es wurde kein unbewiesener Ein-/Auszahlungsdetektor eingebaut. Eine Auszahlung kann weiterhin eine konservative Equity-Sperre auslösen; ein geänderter Methodenschlüssel darf diese nicht automatisch aufheben. Für nachträgliche Basisberichtigungen fehlt eine vollständige, kontogebundene Kapitalflussquelle. Dieser Punkt bleibt gezielt offen, statt willkürliche Schwellwerte als Brokerbeleg auszugeben.

## NEXUS-IMP-002 – Dauerhaftigkeit ehrlich quittieren

Vorher: Datei- und Verzeichnis-fsync unterdrückten jedes OSError. Die Gegenprobe mit zweimal EIO kehrte erfolgreich zurück. Nachher: `durable=True` bestätigt auf POSIX nur Datei-fsync → replace → Verzeichnis-fsync. EIO, ENOSPC, EROFS, EINVAL und ähnliche echte Fehler werden weitergegeben. Temporäre EBUSY/EAGAIN/EINTR sowie passende Windows-Dateisperren können vor replace begrenzt wiederholt werden. Nach erfolgtem replace gibt es keine stille Wiederholung. Ein Fehler nach replace bedeutet ausdrücklich: neue Datei möglicherweise sichtbar, Dauerhaftigkeit unbestätigt; kein garantierter Rollback.

Windows-Vertrag: Datei-fsync und atomarer Replace, ohne nicht verfügbares POSIX-Verzeichnis-fsync. `durable=False` ist der ausdrücklich schwächere Vertrag für rekonstruierbare Telemetrie. `best_effort_json` fängt Fehler weiter ab, liefert nun tatsächlich False bei nicht bestätigtem durable Write und lässt den Prozess laufen.

Zusätzlicher tatsächlicher Fehler: `RiskPot.setze_kontowert` setzte bei Schreibfehler einen RAM-Halt; `darf_kaufen()->refresh()` konnte danach wieder den alten offenen Diskzustand laden. Ein eigener Persistenzstatus verhindert diese Freigabe. Nur der qualifizierte idempotente Replay genau der fehlgeschlagenen Operation quittiert deren Wiederherstellung. Eine Bewertung, Positionszählung, Tagesrücksetzung oder ein erfolgreicher anderer Beleg quittiert kein fehlendes Verkaufsergebnis. Ein reiner Lesezugriff tut das ebenfalls nicht. Eine wiederhergestellte Persistenz löscht eine echte Tagesverlustsperre nicht.

Aufruferprüfung: Ownership-Registry und Positionsstores propagieren bereits Schreibfehler. RiskState-Transaktionen bleiben rollback-/receipt-idempotent. Die Geldpfadprüfung des Gesamtpakets muss zusätzlich bestätigen, dass ein Fehler nach Brokerfill den bestehenden Broker-Schutzpfad weiterhin erreicht; diese Prüfung ist nicht durch den hier genannten Modulnachweis ersetzt.

## Migration und korrupte Daten

RiskState-Schema 2 ergänzt Felder, ohne bestehende Felder umzudeuten. Vor der ersten Migration wird der genaue ursprüngliche Text in `<risk_state>.pre-v10-<sha256-prefix>.bak` dauerhaft gesichert. Eine gleichnamige Sicherung muss inhaltlich exakt passen und wird vor Verwendung erneut dauerhaft bestätigt. Anschließend erfolgt die additive Migration unter derselben Prozess-/Thread-Sperre; Wiederholung erzeugt keine weitere Sicherung und setzt keine Limits zurück.

Load und transaktionale Änderung laden den neuesten Stand innerhalb der Sperre. Mögliche zwischenzeitliche Receipts werden nicht durch einen beim ersten Lesen veralteten Snapshot überschrieben. Alte UNKNOWN-Klassifikation bleibt monoton. Fehlende/korrupte/unlesbare Sicherung oder nicht unterstützte Risikoschemata führen zu einem lesbaren Persistenz-Sperrgrund, nicht zu einem erfolgreichen Nullzustand. Eine korrupte Originaldatei wird nicht mit leeren Risikozählern überschrieben. Der gesperrte RAM-Fallback ist ausdrücklich kein rekonstruierter Geldzustand.

Validierung prüft Schema, wesentliche endliche Equity-/P&L-Zahlen, boolesche Sperrwerte und Receipt-Objekte. Es wurde keine allgemeine historische Ledgerreparatur eingebaut.

## Tests und Belege

Baseline-Datei: `implementation/evidence/risk_persistence_baseline.json`: beide ursprünglichen Fehler vor den Änderungen erneut reproduziert, synthetische Daten und isolierte temporäre Schreibpfade.

1. `risk_existing_91c197d4`: 30 bestehende Tests bestanden, ohne Netzereignis. Dieser Lauf enthält die damalige v9010-Datei noch mit schwachem Alt-Oracle.
2. `risk_v10_0961eeeb`: 23 neue Verhaltensfälle bestanden, ohne Netzereignis.
3. `risk_regressions_7d3ab80b`: 347/347 bestanden, ohne Netzereignis. Enthält neue Fälle sowie bestehende Equity-, Meldungs-, Tagesbuch-, Lifecycle-, Orderpipeline-, Akzeptanz-, Ergebnis-, Accounting- und Installationsregressionen. JUnit und Kommando liegen jeweils im angegebenen test_runs-Verzeichnis.

Neue Verhaltensfälle: ursprünglicher Symbolsetverlust; vier Positionswechselvarianten mit bestehendem Latch; Währungs-/Methodenwechsel; Scopewechsel mit Brokertrennung/Neustart/Mitternacht; regulärer Tageswechsel; exakte Sicherung und idempotente Migration; fehlgeschlagene Sicherung; korrupte Originaldatei erhalten; echte Manager-Schlüsselerzeugung; Datei-fsync EIO/ENOSPC/EROFS/EINVAL; Verzeichnis-fsync nach bereits sichtbarem Replace; explizite Telemetrie; best_effort-False; begrenztes EBUSY-Retry; Statusabfrage nach Schreibfehler; verspätete idempotente P&L-Nachbuchung; zwei zuvor geladene Risikozustände.

Alt-Testkorrektur: `test_v9010_equity_scan_visibility` setzte nur RAM-Werte und speicherte sie nicht. Die transaktionale Produktionsmethode las danach einen leeren Diskzustand; der Test prüfte somit den behaupteten Altzustand nicht. Er speichert jetzt den konkreten alten Stand und verlangt unveränderte Tagesbasis und bestehenden Halt. Die Änderung folgt der fachlichen Abnahme aus der Analyse, nicht einer kosmetischen Grünstellung.

## Restrisiken und Releasegrenze

- Keine echte SD-Karte, kein Stromausfall und kein Pi-5-Lasttest durchgeführt.
- POSIX-fsync nicht unterstützende Dateisysteme führen jetzt bewusst zu einer erkennbaren Sperre kritischer Writes; nicht zu behaupteter Dauerhaftigkeit.
- Scope-Erkennung verhindert künftige unbemerkte Wechsel; historische Zähler sind nicht vollständig kontobezogen migriert. `LEGACY_UNASSIGNED` bleibt sichtbar.
- Unbelegte Ein-/Auszahlungen oder geänderte Eigentumsklassifikation werden nicht automatisch bereinigt. Vor einer manuellen Basisberichtigung sind Konto-/Kapitalflussbelege erforderlich.
- Die allgemeinen separaten Ledger-/Positions-/Risikocommitgrenzen bleiben bestehen. Hier wurde kein zweiter Scheduler oder Eventbus eingeführt.

Die genannten Teilmaßnahmen sind lokal geprüft. Die Freigabe des Gesamtpakets hängt zusätzlich von Geldpfad-, WebUI-, Recovery- und Gesamttests ab; aus 347 erfolgreichen Tests folgt keine unbelegte Livefreigabe.


## Unabhängige Gegenprüfung und zusätzliche Korrekturen

1. **Fehlgeschlagener P&L-Beleg plus erfolgreiche Nebenmutation:** Die erste Implementierung quittierte Persistenz pauschal bei erfolgreicher Transaktion. Die unabhängige Probe zeigte `register_realized_pnl(-50, ledger:X)` mit EIO, danach erfolgreiches `set_open_positions(0)` und fälschlich freie Käufe trotz weiterhin fehlendem P&L. Die korrigierte Implementierung führt `pending_persistence_operations` als additive, bei nächster erfolgreicher Schreibung dauerhaft gespeicherte Liste. Mehrere Fehler akkumulieren. Ein vollständiger Beleg mit derselben ID/P&L/Grosswerte bestätigt seinen eigenen Replay; UNKNOWN ist keine Bestätigung von CONFIRMED. Unklassifizierte oder anonyme wirtschaftliche Fehler bleiben prüfpflichtig. Ein positiver Read oder erfolgreicher Zählerwrite löst diese Liste nicht auf. Auf 256 unterscheidbare Einträge begrenzt; Überlauf trägt einen bleibenden Reviewmarker statt stiller Freigabe.
2. **Tatsächlicher eToro-Aktienworker:** eToro verwendet einen eigenen Adapter außerhalb des Krypto-Hubs. Die beiden echten Equity-Aufrufer in normalem Zyklus und Reconnect verwenden jetzt `update_account_equity_guard(risk, broker, equity)`. Der Helper verlangt die gebundene eToro-CID-Identität, berücksichtigt tatsächliche Umgebung und Kontowährung und übergibt den Scope an RiskState. Ein provisorischer eToro-Hash wird abgelehnt. Ein Test verwendet den tatsächlichen EtoroBroker und die tatsächliche neue Produktionsfunktion, wechselt DEMO zu LIVE und beweist die eToro-Sperre bei unverändert erlaubtem OKX-Topf. Es werden keine alten Receipts der heutigen Kontoidentität zugewiesen.
3. **Migration nach fehlgeschlagenem Backup:** Die Gegenprobe zeigte einen weiteren möglichen Weg: Legacy-Load bleibt wegen Sicherungsfehler gesperrt, spätere gewöhnliche Mutation könnte jedoch über Dataclass-Defaults Schema 2 ohne Backup schreiben. `_transaction` und `save` lehnen deshalb jede bestehende Legacy-Datei mit `RISK_MIGRATION_REQUIRED` ab. Erst ein erneut vollständig erfolgreicher `RiskState.load` darf die Sicherung/Migration durchführen. Der Test prüft Originalbytes nach fehlgeschlagenem Load, Equity-Update und direktem Save.
4. **Gleichzeitige Migration:** Zwei Leser können beide zunächst Schema 1 sehen. Nach Erwerb derselben Sperre wird die frische Schemaversion erneut geprüft; der zweite Prozess/Thread sichert keinen bereits migrierten Stand erneut. Der konkrete Concurrent-Loader-Test erzeugt genau eine identische Ursprungsicherung und erhält den Halt in beiden geladenen Objekten.

Aktueller gezielter Nachprüfungslauf `review_risk_jobs_a694b29e`: **86/86 bestanden**, keine Netzwerkereignisse. Er enthält Risiko-/Persistenz-/Scope-/Migrations- und Analysejob-Gegenfälle sowie vorhandene Equity- und Tagesbuchregressionen. Die vollständige abschließende Suite wird separat im Gesamt-Testbericht ausgewiesen.
