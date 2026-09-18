# NEXUS 10.1.0 – bekannte Restpunkte und Releaseentscheidung

**Uneingeschränkt releasefähig für unbeaufsichtigten Echtgeldhandel: NEIN.** Ein bestandener Offline-Testlauf ersetzt die fehlenden realen Broker-/Bestandsbelege und den noch ausstehenden Pi-Verlaufstest nicht. Das Paket dient dem kontrollierten Demo/Paper-Update mit bestehender lokaler Absicherung.

| Schwere | Restpunkt | Auswirkung / nächster belastbarer Nachweis |
|---|---|---|
| Hoch, P0-Freigabeblocker | Historische eToro-Risikobasis ist nicht unabhängig an Konto/Tag/Währung gebunden | Betroffene neue Käufe bleiben gesperrt. Benötigt: korrekt gebundener historischer Risiko-Checkpoint oder gesondert belegtes neues Bewertungsperiodenkonzept; kein Reset auf vermutete Werte. |
| Hoch, P0-Freigabeblocker | PEP 135,8946/138,5207 gegenüber Broker 135,90/138,52 ohne dokumentierte Präzisionsregel | Bestätigung bleibt offen; bestehender Schutz-/Exitpfad bleibt separat. Normales HALF_UP auf zwei Stellen ergäbe 135,89; eine pauschale Zwei-Dezimal-Regel wäre falsch. Benötigt: tatsächlicher instrument-/kontogültiger SL-/TP-Preisvertrag und frischer Positionsbeleg. |
| Mittel | Sechs historische kontogebundene eToro-Trades mit unbekannten Exitgebühren | Netto-P&L bleibt unbekannt; Mengenabgleich kann bereits vollständig sein. Passende Original-Gebührenbelege fehlen. |
| Mittel | Viele tatsächlich gespeicherte DEMO-Kerzen ohne Volumen/flache Reihen | Kein Signal bei fehlendem erforderlichem Volumen ist korrekt. Ursprüngliche Broker-Rohantworten fehlen; neue Belegsammlung ermöglicht die Ursachenprüfung. Keine heimliche Umstellung auf LIVE-Daten. |
| Mittel | Nasdaq-Feedlücken, GDELT-Timeouts, Tradestie-TLS-Verfügbarkeit | Neue Diagnose klassifiziert Nasdaq; bestehende Pausen/Alternativen bleiben. Externe Erreichbarkeit und Zertifikate können nicht offline gesundgemeldet werden. |
| Mittel | Neuer kompletter 30-Minuten-Pi-Lauf und Broker-/GPT-Ende-zu-Ende-Abnahme stehen aus | Laufzeit/RAM/CPU/Lockverhalten auf dem echten Pi sowie echte Verarbeitung neuer Antworten erst anhand der nächsten ZIP bewerten. Kein Geld-/GPT-/Telegramauftrag wurde hier ausgelöst. |
| Mittel | Visuelle Desktop-/Tablet-/Smartphone-Abnahme offen | Cloudbrowser blockierte die lokale Vorschau mit URL-Policy. Echter JS-Renderer und CSS-/Escaping-/API-Tests gemäß TEST_REPORT; keine Pixel-/Touchprüfung behauptet. |
| Niedrig | Noch nicht klassifizierter alter Entscheidungsgrund | Verständlicher ehrlicher Hinweissatz plus vollständige Details; keine erfundene Ursache. |
| Niedrig | Externe Massive-Key-Nutzung nicht vollständig im NEXUS-Budget sichtbar | Vier statt fünf Abrufe/Minute als Reserve und gemeinsame 429-/Retry-After-Behandlung. Andere Apps können trotzdem 429 auslösen. |

Keine diagnostizierte lokale Sperre wurde einfach entfernt, keine historische Gebührenlücke mit 0 gefüllt. Die konkret umgesetzten Korrekturen und Tests stehen im IMPLEMENTATION_REPORT.md. Alte 10.0.0-Berichte unter docs/history sind historisch.
