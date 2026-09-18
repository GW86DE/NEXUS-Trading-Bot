# NEXUS 10.1.9 – Umsetzungsbericht

**Stand:** 15. September 2026  
**Basis:** NEXUS 10.1.8, `BERICHT(1).md` und `AUSWERTUNG(1).json` aus der 30-Minuten-Diagnose.  
**Build:** `10.1.9-OKX-RISK-CAPITAL-AND-DIAGNOSIS-EVIDENCE`

## Ausgangsbefund

Die Diagnose enthielt 1.811 Entscheidungen im Beobachtungsfenster. 1.807 davon gehoerten zu OKX und endeten am `RISK_GATE` mit dem gespeicherten Grund `Kontostand und freies Guthaben abrufbar (handelbar 0.00)`. Gleichzeitig war OKX verbunden. Das ist kein Beweis, dass ein Brokerkonto tatsaechlich EUR/USDC enthielt; es zeigt aber, dass die Risikobewertung der entscheidende lokale Blocker war.

Der 10.1.8-Code erlaubte beim Entry mehrere freigegebene Abrechnungswaehrungen, waehrend `handelbares_kapital()` die Cash-Risikobasis nicht fuer alle erlaubten Lanes konsistent in eine gemeinsame Risikowaehrung bewertete. Dadurch konnte insbesondere ein freigegebener USDC-Bestand vom Routing verwendbar sein, waehrend der Risikotopf weiterhin null meldete.

Zusaetzlich konnte die Diagnose den PULSAR-Top-5-Cache wegen einer uebergrossen Zelle nicht auswerten (`OMITTED_OVERSIZE_CELL`). Mehrere SQLite-Tabellen waren absichtlich auf eine Maximalzahl Zeilen begrenzt, wurden aber in den Findings wie ein unvollstaendiger technischer Export behandelt.

## Umgesetzte Korrekturen

### 1. OKX-Risikokapital

`broker/okx.py` bewertet freies Cash ausschliesslich aus bereits konfigurierten `allowed_quotes`. Die primaere Risikowaehrung bleibt die konfigurierte `quote_ccy`. Nebenwaehrungen werden nur mit einem frischen, beobachteten Spot-Kreuzkurs in die Risikowaehrung umgerechnet.

Es gibt keine implizite EUR/USD/USDC-Paritaet. Fehlt ein belastbarer Kurs, erzeugt die Risikobewertung `RISK_CAPITAL_FX_UNKNOWN` und neue Kaeufe bleiben gesperrt. Positive Guthaben in nicht freigegebenen Waehrungen werden im Kapitalbeleg aufgefuehrt, aber nicht zum handelbaren Kapital gerechnet.

Eigene Positionen werden mit ihrer belegten Markt-/Abrechnungswaehrung bewertet. Ein nativer USDC-Positionswert wird nicht numerisch als EUR interpretiert.

### 2. Readiness und Konto-Vorpruefung

`crypto_engine.py` trennt nun zwei Aussagen: Ist der Kontosnapshot technisch gueltig, und ist in den freigegebenen Entry-Waehrungen bewertbares Kapital vorhanden? Ein gueltiger Nullbestand wird nicht mehr als Abruffehler beschrieben.

`okx_account_switch.py` meldet bei der rein lesenden Vorpruefung zusaetzlich `acctLv`, `posMode` und einen Fundingstatus (`SUPPORTED_FUNDS_AVAILABLE`, `UNSUPPORTED_FUNDS_ONLY`, `NO_FREE_BALANCE`). Die Vorpruefung schaltet weder Account-Modus noch Waehrung um.

### 3. Diagnose 1.8.0

Runtime-Export und Bericht enthalten fuer OKX die gespeicherten positiven Guthabenwaehrungen, primaere Risikowaehrung, handelbares Kapital und den dazugehoerigen Kapitalbeleg. Entscheidungsblocker werden im Bericht aggregiert dargestellt.

PULSAR schreibt parallel zum vollstaendigen `top5`-Cache eine begrenzte `diagnostic_top5`-Projektion. Beide erhalten denselben Revisionszeitpunkt. Die Projektion enthaelt Quellenidentitaeten, Feld-Praesenz, Eingabe-/Execution-Belege und KI-Stufen, aber keine News-/Post-Rohtexte oder Autorenkennungen. Die Diagnose akzeptiert sie nur, wenn ihre Revision zum exportierten Top-5-Stand passt.

Bewusst zeilenbegrenzte, ansonsten fehlerfrei gelesene Tabellen werden als `TABLE_EXPORT_BOUNDED`/INFO behandelt. Ausgelassene Zellen oder echte Tabellenfehler bleiben UNKNOWN.

### 4. Nicht veraendert

- Keine Freqtrade-Entry-/Exit-Bedingung wurde veraendert.
- Keine Sicherheits-, Risiko-, Spread-, Liquiditaets- oder Schutzregel wurde gelockert.
- Kein unbekannter Orderausgang wird automatisch erneut gesendet.
- PULSAR/X/GPT erhalten keine Orderfreigabe.
- Fehlende eToro-Abschlusskosten werden nicht als null angenommen.
- GDELT/Tradestie-Providerprobleme werden nicht durch unsichere TLS-/Retry-Umgehungen kaschiert.

## Testharness-Korrektur

Ein vorhandener POSIX-Prozessgruppen-Test verwendete in 10.1.8 ein kuenstliches Timeout von 0,3 Sekunden. Auf belasteten Testumgebungen konnte der Testprozess beendet werden, bevor sein absichtlich erzeugter Enkelprozess seine PID ins Log geschrieben hatte. Der Produktionswert bleibt unveraendert bei 1.800 Sekunden; nur das Testfenster wurde auf 1,0 Sekunde erhoeht. Die getestete Eigenschaft – Timeout beendet Leader und Descendant der eigenen Prozessgruppe – bleibt unveraendert.

## Offene Beleggrenzen

Die Diagnose beweist nicht, welche Waehrung aktuell auf dem konkreten OKX-Konto finanziert ist. Genau das soll die neue Runtime-/Diagnoseprojektion nach Installation sichtbar machen. eToro-Abschlusskosten, zwei nicht zugeordnete private Stream-Ereignisse sowie externe Providerfehler bleiben ohne neue Originalbelege offen.
