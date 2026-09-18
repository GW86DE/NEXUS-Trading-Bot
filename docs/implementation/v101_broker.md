# NEXUS 10.1 – Broker-/Risk-Implementierung D01/D02

Arbeitskopie: `implementation/TradingBot_v10.1.0_NEXUS`. 10.0.0 blieb unverändert. Keine Broker-/Paid-API-Aufrufe. Öffentliche eToro-Dokumentation geprüft.

## Tatsächlich implementiert

- `risk_basis_review.py`: reine Reviewprojektion, lesender CLI und eng begrenzte Wartungsfunktion `apply_checkpoint`. Ein unabhängig aufbewahrter, korrekt kontogebundener Risiko-Checkpoint muss am selben Tag dieselbe Equitybasis, **alle** Finanz-/Zähler-/Receiptfelder und die beabsichtigte Konto-/Umgebungs-/Währungsbasis belegen. Fremdes Konto, anderer Tag, anderer P&L, fehlende oder pending Belege werden verworfen. Original-SHA schützt gegen zwischenzeitliche Änderung. Dauerhafter inhaltsadressierter Backup vor atomarer Metadatenänderung, Validierung, idempotenter Migrationsbeleg. Geld, unbekannte Gebühren/PNL und Historie werden nicht zurückgesetzt.
- `risk_manager.py`: additives `basis_review_receipt` bleibt über spätere Transaktionen erhalten. `basis_review()` liefert Status, alle Gründe, gespeicherte/beobachtete Basis/Scope und benötigte Nachweise.
- `live_trader.py`: `runtime.risk_review` und eigene Readiness-Bedingung `Risikobasis und Kontozuordnung`; Markt geschlossen verdeckt damit die zusätzliche Risikosperre nicht mehr. Die Sperre bleibt brokerlokal.
- `etoro_protection_evidence.py`: identitätsgebundene Preisnormalisierung **nur mit explizitem verifiziertem Regelbeleg**; keine Standardregel aus Assettyp oder dargestellten Nachkommastellen. Getrennte Rundungsmodi für SL/TP, keine automatische Vertiefung eines Long-Stops. Strukturiertes Requested/Normalized/Sent/Observed/Precision-Ergebnis. Reale PEP-Raten ohne Nachweis bleiben unbestätigt und nennen `ETORO_PROTECTION_PRICE_RULE_UNPROVEN`.
- `broker/etoro.py`: Positionsanzahl/IDs, Instrument und tatsächliche Restmenge werden vor PATCH geprüft. Doppelte Positionszeilen können keine vollständige Bestätigung vortäuschen. Explizite No-Stop/No-TP-Flags müssen false sein; unbekannte Stringflags werden abgelehnt. Historisch optionale fehlende Flags erlauben weiterhin nur exakt gleiche positive Raten; bei regelbasiert normalisierten Preisen werden explizit aktive Schutzflags verlangt. Reine numerische Serialisierungstoleranz, keine willkürliche breite Preisabweichung.
- `etoro_protection_journal.py`: persistenter konto-/umgebungs-/positionsgebundener PATCH-Intent vor Versand. Timeout/unklare Antwort erzeugt keinen zweiten PATCH; passender späterer Broker-Snapshot bestätigt den gespeicherten Request. Bewiesene typisierte Ablehnung wird REJECTED. Ohne dauerhaftes Journal kein PATCH. Wiederholtes Lesen eines bereits bestätigten Requests schreibt nicht ständig erneut. 5.000 Positionseinträge maximal, danach explizite Reviewpflicht.
- `live_trader.py`: aktuelle Schutzbelege in `runtime.protection_evidence`; alte Positionen werden aus dieser flüchtigen Map entfernt.

## Wichtige Grenze – die ursprünglichen zwei P0-Fälle sind NICHT pauschal entsperrt

**D01:** Die gelieferte Diagnose besitzt keinen unabhängigen korrekt gebundenen früheren Risiko-Checkpoint. Ein aktueller Kontowert von 99.524,94 allein beweist weder historische Kontozuordnung noch die Tagesbasis. Deshalb bleibt dieser Originalfall REVIEW_REQUIRED. Eine neue Tagesepoche ließe sich gesondert entwickeln, benötigt aber vollständigen Broker-/Tageshistorienbeleg und klar getrennte Legacy-Historie. Der bestehende Tagesreset läuft schon beim Laden/anderen Writes; ein nachträglich vermuteter Tagesanfangsstand wäre keine belastbare Recovery. Die neue Version erzeugt daher keine scheinbar reparierten historischen Werte.

**D02:** Die veröffentlichten eToro-v2-Dokumente für Eligibility und Positions-PATCH liefern keine Tick-/Rundungsregel. Wichtig: **135,8946 auf zwei Stellen mit gewöhnlichem HALF_UP ergibt 135,89, nicht die beobachteten 135,90.** Eine bloße Zwei-Dezimal-Annahme wäre also sogar rechnerisch unzureichend. Ein synthetischer Testbeleg für SL aufrunden/TP abrunden kann die PEP-Zahlen normalisieren; dieser Testbeleg wird ausdrücklich **nicht** als echte eToro-Regel installiert. Bestehender PEP-Schutz bleibt bis zum echten Beleg unbestätigt. Kein UI-/Config-Schalter kann eine Regel als Beweis erfinden.

Der Adapter besitzt dafür den optionalen internen Parameter `price_rule`. Es existiert absichtlich kein automatisch vertrauender Dateilader. Der Aufrufer muss einen echten kontogültigen Instrumentpreisvertrag überprüfen und darf erst dann dessen Instrument-ID, Konto, Umgebung, Tickgröße, Rundungsmodi, Quelle und SHA256 übergeben. Der bestehende automatische Kaufpfad verwendet weiterhin unveränderte, persistierte Orderpayloads; mangels echter Regel wird dort keine Schutzpreisänderung eingeführt.

**Releaseaussage:** D01/D02 sind sichere Infrastruktur-/Diagnose-/Retryverbesserungen mit geprüften bedingten Korrekturwegen. Die automatische Lösung der beiden konkreten Produktionsaltfälle bleibt mangels Belegen **teilweise umgesetzt**, nicht als erfolgreich freigeschaltet melden. Der gesamte Release bleibt ohne Auflösung der P0-Restpunkte nicht uneingeschränkt livefreigabefähig.

## Bedienbarer nächster Review

Im laufenden NEXUS-10.1-Verzeichnis ist dieser Aufruf rein lesend:

```bash
python3 risk_basis_review.py --runtime runtime_status.json
```

Er zeigt Original-SHA256, gespeicherte/festgestellte Konto-/Währungsbasis, alle Gründe und die erforderlichen Belege. Optional einen tatsächlich unabhängig vorhandenen Checkpoint prüfen:

```bash
python3 risk_basis_review.py --runtime runtime_status.json --checkpoint /pfad/zum/risk-checkpoint.json
```

Der CLI verändert grundsätzlich nichts. `apply_checkpoint` ist ein wartungsseitiger, getesteter Commitpfad nach positiver Review, nicht ein periodischer Autoreparaturjob. Ohne echten passenden Checkpoint keine Freigabe. Für den offenen PEP-Fall benötigen wir von eToro eine instrument-/kontogültige SL-/TP-Preispräzision **einschließlich Rundungsverfahren** sowie einen frischen Positionsnachweis mit aktiven Schutzparametern. Brokerseitig sichtbare 135,90/138,52 allein sind kein solcher Regelbeleg.

## Schnittstellen für WebUI/Diagnose

`runtime.risk_review`:

```text
schema_version, status=OK|REVIEW_REQUIRED, blocks_entries, reason_codes,
stored_basis, observed_basis, stored_scope, observed_scope,
day, day_start_equity, observed_equity, automatic_rebase_allowed=false,
required_evidence[], detail
```

`runtime.protection_evidence`: Map `con_id -> Beleg`. Voller Beleg:

```text
schema_version, source=ETORO_REST_POSITION_SNAPSHOT, snapshot_id,
requested={stop,take_profit}, normalized=null|{stop,take_profit,tick_size,
source_reference,source_sha256,rounding:{stop,take_profit}},
sent=null|{stop,take_profit,reference_id,state},
observed=[{position_id,stop,take_profit,flags}],
precision={status=UNPROVEN|PROVEN,source}, confirmed, reason_code
```

Frühe Mengen-/Scopefehler besitzen nur schema_version, confirmed, reason_code, snapshot_id. UI muss fehlende Zusatzfelder vertragen. Die Bestätigung eines identischen Preiswertes bedeutet nicht, dass eine allgemeine Rundungsregel bewiesen ist.

## Persistenz / Installation

Neues Statefile: **`etoro_protection_journal.json`**; muss in STRICT_STATE_FILES, Backup/Migration und Diagnoseexport aufgenommen werden. Enthält keine Zugangsdaten. Fehlende Datei bedeutet bisher keinen PATCH-Intent; vorhandene beschädigte Datei darf niemals stillschweigend ersetzt werden. `risk_state_*` erhält nur `basis_review_receipt`; Schema bleibt 2, ältere Bestandsfelder werden erhalten. Reviewbackups enden auf `.basis-review-<hash>.bak` und bleiben erhalten. Kein Auto-Reset beim Installieren.

## Tests

Neue Tests: `tests/test_v101_risk_basis_review.py`, `tests/test_v101_etoro_protection_evidence.py`. Echte Diagnosewerte reduziert und Identitäten anonymisiert; synthetische hypothetische Regelbelege ausdrücklich markiert. Geprüft: realer unbekannter Legacy-Stand, falsches Konto/Tag/Finanzwerte, idempotente Metadatenmigration, Backup-/Writefehler, zwischenzeitlich geänderte Datei, Receipt-Erhalt; reales PEP-Mismatch ohne Regel, explizite per-leg-Regeln, falsche Regeln, Verschlechterung Stop, Identität/Menge/Flags, doppelte Position, Timeout + Neustart + verspätete Bestätigung, bewiesene Ablehnung, kein PATCH bei Journalfehler und keine unnötigen Wiederholungswrites.

Gezielte gemeinsame Regression mit bestehenden Risk-/eToro-Fill-/Entry-Cap-/Identitätstests: **101/101 bestanden, 4,96 Sekunden, keine Netzereignisse**. Siehe isolierten Lauf `implementation/test_runs/broker_v101_gate_8c8fdc0a` und dessen `junit.xml`, `output.txt`, `result.json`. Alle sechs neuen/geänderten Broker-/Riskmodule kompilieren. Ein erster Test verwendete irrtümlich HALF_UP als PEP-Modell; der Test deckte die falsche Annahme auf und wurde durch ausdrücklich hypothetische getrennte SL-/TP-Rundungsregeln ersetzt. Der Produktcode übernimmt die Hypothese nicht automatisch.

## Primärquellen (13.09.2026)

- https://api-portal.etoro.com/api-reference/trading--demo/check-instrument-trading-eligibility : Kontogültige Grenzen und SL-/TP-/Leverage-Konfiguration, keine veröffentlichte Preis-Tickregel.
- https://api-portal.etoro.com/api-reference/trading--demo/modify-stop-loss-and-take-profit-settings-on-an-open-position : decimal SL-/TP-Raten; HTTP 202 bedeutet nur asynchrone Annahme mit Korrelationsdaten, keine Positionsbestätigung.
- https://api-portal.etoro.com/api-reference/market-data/get-instrument-display-data : Display-/Identitätsfelder sind kein Preispräzisionsvertrag.
