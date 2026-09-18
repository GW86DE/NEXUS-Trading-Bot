# NEXUS 10.7.0 – Umsetzungsbericht

**Build:** `10.7.0-BUY-GATE-SCOPE-AND-FEE-EVIDENCE` · 18.09.2026 · Basis 10.6.0

## Auftrag

Georg, 18.09.2026: „Du sollst nicht ständig einen fix ausführen und es entstehen
andere probleme sondern wir müssen das problem sauber verstehen und von Grund
auf fixen. … Analysiere das problem grundlegend und wenn nötig baue es komplett
um. bevor du mit der neuen version startest sag wie du es umsetzen willst."

Analyse und Vorschlag: `NEXUS_Analyse_Kaufsperren_2026-09-18.md`. Entscheidungen:
„Ja, baue es so." (Ergebnissperre nur am Verkaufstag) und „lockere die Regel und
trage einen erwartungswert ein." (Gebühren-Erwartungswert).

## Die drei Wurzeln und was dagegen gebaut wurde

| Wurzel | Befund | Umsetzung |
|---|---|---|
| Fließkomma-Mengenvergleich ohne Lot-Bewusstsein | 3× derselbe Fehler (10.2.1 Rev 2, 10.3.0, 18.09.) | `quantity_explained` + eine Auflösung `resolve_balance_gap_on` + Reparaturlauf `repair_explained_gaps` |
| Sperren domänenweit und unbegrenzt | 9 Sperrfälle in 10 Tagen; Gebührenloch vom 08.09. sperrte am 18.09. | `GAP_SCOPE` (Reichweite/Ablauf je Belegart), `offene_ergebnisse_heute`, `require_tradable`, Wächter ohne Sofortbuchung |
| eToro-Gebührenbeleg strukturell unerfüllbar | 27 von 32 Trades ohne Beleg; Automat versagt bei gleichzeitigen Positionen | Intervallrechnung über alle Ereignisse + gekennzeichneter Erwartungswert |

Dazu der Auslöser der schnellen Verluste: Aktien-Stop ohne Mindestabstand und ohne
Bezug zum Kaufkurs (CSCO 3 Cent, 1,5 Sekunden) → `aktien_stop_plan`.

## Reihenfolge und Umfang

C (Lot-Rechnung) → D (Aktien-Stop) → E (Gebührenbeleg) → B (Sperrregeln) → A
(Sperrliste). 21 geänderte, 5 neue Dateien. 78 neue Tests in vier Suiten; 26
bestehende Tests auf die neue Regel umgestellt, jeweils mit Kommentar, warum.

## Was bewusst nicht gemacht wurde

- **Stop-Änderung nach dem Fill** bei eToro (Position editieren): zu tief in
  Reconciliation-Intent, PULSAR-Planvergleich und Schutzbestätigung verzahnt, um es
  ohne Laufzeitprüfung sicher zu bauen. Stattdessen Mindestabstand vor der Order,
  Bezug auf den frischen Kaufkurs, und eine sofortige Meldung, falls der Fill
  trotzdem zu nah liegt.
- **Eine einzige Entscheidungsstelle** für alle Sperren: Die Regeln liegen jetzt
  zentral (`GAP_SCOPE`, `offene_ergebnisse_heute`), die Entscheidung fällt
  weiterhin in den Kaufpfaden aus diesen Regeln. Die Sperrliste macht sie
  vollständig sichtbar. Ein vollständiger Umbau der Kaufpfade auf ein
  Entscheidungsmodul wäre ein weiterer, eigener Schritt.
- **Erwartungswert ohne Belege**: Unter drei bestätigten Abrechnungen desselben
  Kontos wird kein Erwartungswert eingetragen.

## Grenzen und offene Punkte für den Pi

- Die Intervallrechnung braucht saubere Barbestandsbelege ohne schwebende Orders
  vor und nach dem Abschluss. Wie oft das bei schnellen Abfolgen gelingt, zeigt
  erst der Betrieb; das Sicherheitsnetz ist der Erwartungswert.
- Ein Erwartungswert, der später durch einen abweichenden Cash-Delta ersetzt wird,
  korrigiert das Ledger, nicht den bereits gebuchten Risikozustand. Die Abweichung
  steht in Notiz und Protokoll; bei diesem Konto war sie in 12 von 12 Fällen null.
