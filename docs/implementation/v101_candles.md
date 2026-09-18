# NEXUS 10.1.0 – D08/D09 Candle-Qualität und konsistenter Signaltakt

## Änderungen und Zweck

Diagnose 1.0.1 zeigte 5.608/6.000 Nullvolumen-Kerzen (93,47 %), sieben vollständig flache Reihen und zugleich frische Ticker. Die neun letzten Sample-Entscheidungen waren fachlich korrekt reproduzierbar. Deshalb wurden Beobachtung, Rohdatenbelege und Status verbessert; die Sample-Entry-/Exit-Semantik und das Volumengate wurden nicht geändert.

| Datei | Änderung |
|---|---|
| `candle_observation.py` (neu) | Instrument-/Environment-/Endpoint-/Consumer-getrennte Beobachtungen; getrennte Aktualität und Aktivität; begrenzte persistente Belege schon erfolgter öffentlicher Kerzenabfragen. |
| `broker/okx.py` | Reale Request-/Responsezeiten, HTTP-Status und numerische Kerzenfelder aus bestehenden Candle-Requests erfassen; strikter Parser für bestätigte Kerzen. Fehlende/ungültige OHLCV-/Confirm-Daten werden nicht mehr still als Nullvolumen/fehlende Zeile interpretiert. |
| `freqtrade_candles.py` | Qualitätsdaten der tatsächlich verwendeten 500er Arbeitsbasis, reale gegenüber synthetischen Zeilen, Cache-Provenienz; vorhandene SQLite-Daten und Gap-Fill-/Candle-Close-Regeln erhalten. |
| `crypto_engine.py` | Vollständige Instrumentidentität im Qualitätsstatus; Qualitätsdaten im Entscheidungsprotokoll und OKX-Runtime; aktive Strategie steuert Signalkerzenprüfung. |
| `crypto_strategy_mode.py` | Öffentliche Funktion `signal_timeframe(mode=None, standard_timeframe=None)` als zentrale aktive Zeitrasterquelle. |
| `trading_ready.py` | `safety_wait` zeigt bestehende Sicherheits-Anlaufsperre getrennt von Signalkerzen-Takt; Schutzdauer bleibt unverändert. |
| `universe/crypto_selector.py` | `kandidat.zusatz.candle_quality` und gegebenenfalls `candle_quality_warning` aus der bereits ohnehin abgerufenen Historie. Keine neue stille Score- oder Pairlist-Regel. |
| `tests/test_v101_candle_observation.py` | 31 neue Verhaltenstests einschließlich der neun Original-Replays. |
| `tests/fixtures/candles_20260913.json.gz` | Minimaler numerischer Diagnoseausschnitt für neun reproduzierbare Originalentscheidungen; keine Zugangsdaten/Kontodaten. |

## Schnittstellen für WebUI/Diagnose

- `runtime_status_okx.json.signal_timeframe`: `5m` für Sample, konfigurierter Wert für NEXUS_STANDARD, `null` bei Pausierung/ungültigem Modus.
- `runtime_status_okx.json.candle_quality`: Liste je vollständigem Instrument und DEMO/LIVE-Domain. Felder `observed_at`, `timeframe`, `timeframe_seconds`, `consumer`, `latest_open_utc`, `latest_close_utc`, `latest_age_seconds`, `freshness`, `rows`, `real_rows`, `synthetic_gap_rows`, `zero_volume_rows`, `zero_volume_ratio`, `recent_rows`, `recent_active_rows`, `flat_close`, `confirmation`, `source_receipt`, `reasons`, `entry_effect`.
- `freshness`: CURRENT/STALE/MISSING/FUTURE_OR_OPEN/INVALID. Alter wird beim Statusabruf neu berechnet: ein stehen gebliebener Scan bleibt nicht dauerhaft CURRENT.
- Frische Zeitstempel können gleichzeitig `NO_RECENT_CANDLE_VOLUME` oder `FLAT_CANDLE_HISTORY` melden. Das ist eine erklärte Kombination, keine widersprüchliche Online-Ampel.
- `handelsbereitschaft.safety_wait`: `configured_seconds`, `remaining_seconds`, `active`, `detail`. Sample-Signalkerze ist nach 5 Minuten erfüllt, die bewusst konfigurierte 15-minütige Sicherheits-Anlaufsperre bleibt bis zum eigenen Ablauf aktiv.
- Neue lokale Datei `candle_observations.json`: Diagnose-Collector muss diese zusätzlich erfassen. Keine neue Datenbank oder Migration erforderlich.

## Begrenzung und Datenschutz der Kerzenbelege

Erfasst werden nur bestehende GET-Antworten von `/api/v5/market/candles` bzw. `/api/v5/market/history-candles`. Keine zusätzlichen Anfragen, keine Broker-/GPT-Schreibaktionen, keine Zugangsdaten, keine Header und keine beliebigen Fehlermeldungen.

Maximal 128 letzte Antworten über alle Prozesse, jeweils neueste pro Domain/Instrument/Zeitraster/Endpoint. Pro Antwort maximal zehn Zeilen (acht vordere und zwei hintere Zeilen der Brokerantwort), nur die ersten neun numerischen Candle-Felder; nicht numerische Inhalte werden als `[INVALID]` markiert. Hash bezieht sich explizit auf ausgewählte numerische Felder von maximal 300 Antwortzeilen; Umfang/Trunkierung werden ausgewiesen. Der Cachebeleg behauptet nicht, die aktuelle Antwort bestätige alle 500 früher gespeicherten Zeilen. `diagnostic_receipt_saved` zeigt, ob der referenzierte Beleg tatsächlich persistiert wurde.

Dateigröße maximal 2 MB, Datei/temporäre Datei 0600, atomarer Austausch. Nicht blockierendes Dateilock: konkurrierende oder fehlgeschlagene Diagnose-Schreibversuche werden übersprungen und beeinflussen keine Handelsentscheidung. Erhebung kann auch bei read-only Dateisystem weiterlaufen; dann fehlt ausdrücklich der persistierte Beleg.

## Testnachweis

Isolierter finaler Testlauf:

`implementation/test_runs/candles_v101_verified_022ec9e6/`

- **170/170 Tests bestanden**, 4,97 Sekunden; **keine Netzwerkereignisse**.
- 31/31 neue D08/D09-Tests.
- 41/41 bestehende Freqtrade-Tests (`v990`:11, `v900`:16, `v910`:14).
- 34/34 DEMO-/LIVE-Marktdaten-Trennung.
- 11/11 Anlaufsperre.
- 31/31 bestehender OKX-Adapter.
- 17/17 bestehende CryptoEngine.
- 5/5 Universums-/Quote-Lanes.

Neue Tests verifizieren: echte Nullvolumenwerte gegenüber ungültigen Feldern; reale gegenüber synthetischen Zeilen; aktuelle aber inaktive Kerzen; fehlende/offene/veraltete Daten; Domain-/Instrumenttrennung; fortschreitendes Alter ohne erneuten Fetch; Maskierung, Datei-/Zeilenlimits, konkurrierendes Lock, schreibgeschützte Ablage; keine zusätzlichen Requests beim Cachetreffer; vollständige Contract-ID statt Symbol allein; tatsächliche CryptoEngine-Readiness mit 5m/15m-Moduswechsel und unveränderter 15m-Sicherheitswartezeit.

Die neun Original-Replays stimmen weiter in RSI, TEMA, Bollinger-Mitte, Volumen, Entry-Ergebnis, Ablehnungsgrund, Cursorzeit und nachgewiesenem Candle-Schluss überein. Kein Regelwechsel, um Nullvolumen-Kandidaten künstlich zu kaufen.

## Bei Prüfung entdeckte und korrigierte Implementierungsrisiken

- Qualitätsstatus durfte nicht beim letzten Scan dauerhaft CURRENT bleiben: Statusalter wird deshalb aus der gespeicherten Kerzenzeit neu abgeleitet; durch Stale-Test gesichert.
- NEXUS-Instrumentobjekte tragen die vollständige Paar-ID in `contract.localSymbol`, nicht zwingend `.inst_id`: Auflösung verwendet Historien-Metadaten/Contract-ID, niemals bloß Symbol oder Objekt-Repräsentation; eigener Integrationstest.
- Eine numerische Fehlerspalte durfte nicht durch `float`-Fallback als 0 akzeptiert werden: strikter Adapter und sieben Parametervarianten im Test.
- Fehlgeschlagene Belegpersistenz durfte nicht als vorhandener Dateibeleg behauptet werden: `diagnostic_receipt_saved` ausgewiesen.

## Grenzen und Releaseeinordnung

Kein echter Brokerzugriff, Pi-Dauertest oder 30-Minuten-Lauf in dieser Entwicklungsumgebung. Die Ursache der alten flachen Brokerkerzen ist weiterhin nicht durch alte Rohantworten beweisbar; neue begrenzte Belege machen die nächste Diagnose aussagekräftiger. Die Änderung beseitigt keine tatsächlich fehlende DEMO-Marktliquidität und ersetzt DEMO-Daten nicht still durch LIVE-Daten.

Ein fehlendes Qualitätsfeld bei unbekannter Quelle wird als unbekannt belassen. Das Universum erhält lokale nachvollziehbare Warnungen, keine pauschale neue Handelsblockade. Bestehende Freshness-/Volumen-/Broker-/Risikogates bleiben maßgeblich. Die Gesamtfreigabe hängt zusätzlich von den übrigen Maßnahmen, dem gemeinsamen Regressionstest und anschließendem realen Diagnosefenster ab.

Nach dem gemeinsamen Hygiene-Gate wurde der optionale Belegpersistenz-Fehlerpfad auf begrenztes Debuglogging nur des Exceptiontyps umgestellt; keine Transport- oder Secretwerte. Danach erneut **31/31 Candle-Verhaltenstests**, keine Netzwerkereignisse (`implementation/test_runs/candles_hygiene_6dd1008c`).
