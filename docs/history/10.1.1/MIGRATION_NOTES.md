# NEXUS 10.1.1 – Migration und Datenerhalt

## Quelle und Ziel

Der eigenständige Installer enthält ein durch SHA-256 und Dateimanifest geprüftes Quellpaket. Ziel: `~/Georg/TradingBot_v10.1.1_NEXUS`. Als Quelle gilt ausschließlich der übereinstimmende bestehende Ordner der beiden systemd-Dienste. Ein widersprüchlicher Quellbezug blockiert, bevor Dienste umgestellt werden. Der automatische Weg akzeptiert nur die bestehende DEMO/Paper-Konfiguration.

Wiederholtes Entpacken überschreibt keine vorhandenen Quelländerungen oder Laufzeitdaten. Ein bereits abgeschlossener Updatevorgang importiert bei Wiederholung nicht erneut. Ein unterbrochener Vorgang wird nicht durch blindes Kopieren überspielt.

## Zustände

`position_state.json` wird inklusive `protection_plan_history`, ursprünglicher Herkunft und Eigentumsnachweise übernommen. `risk_state_etoro.json` behält seine wirtschaftlichen Werte und Prüfmetadaten. Ein Update setzt weder die PEP-Planübernahme um noch repariert es automatisch eine unbelegte Risikobasis.

Trade-/Order-/Fill-Ledger, Registry, Reconciliation, Schutzjournal, OKX-Bestände, Brokerzugänge, Einstellungen, Modus, Benutzerentscheidungen, Universum und PULSAR-Daten folgen der vorhandenen strikten Migration. SQLite-Sicherungen schließen WAL-Daten ein. PULSAR-Auswahl und optionale Quellenpausen liegen in bereits migrierten Datenbanken/Statusdateien; es wird kein zweites neues Kontingent angelegt.

Lokale Wartungssicherungen wie `*.protection-plan-*.bak` und `*.basis-review-*.bak` werden nicht einzeln ins neue Verzeichnis kopiert. Sie bleiben im alten Ordner und in dessen vollständiger Archiv-Sicherung erhalten. Bereits in Belegen gespeicherte historische Backup-Pfade bleiben historische Referenzen.

Die neuen Prüfstarter sind Programme, keine vorbefüllten Kontobelege. Ausgabedateien der Wartung werden erst beim ausdrücklichen Aufruf auf dem Pi erzeugt. Der Collector sendet keine Orders und verändert keine Risiko- oder Positionsdateien.

## Abbruch und Rückkehr

Vor einer Dienstumstellung werden die bisherigen Dienst- und Autostartzustände gesichert. Während des Übergangs dürfen die Units bei einem Rechnerneustart nicht selbstständig starten. Der erste kontrollierte Start ist eine dauerhaft protokollierte Grenze.

Vor dieser Grenze kann ein behandelter Fehler die bisherigen Units und ihren Aktivierungszustand wiederherstellen. Nach dieser Grenze könnten neue Brokerereignisse bereits erfasst sein: Dann werden die neuen Dienste gestoppt/deaktiviert, der neueste Zustand bleibt erhalten. Es gibt keine automatische Rückkehr zu alten Handelsdateien. Ein Stromausfall kann eine manuelle Zustandsprüfung erfordern; der Installer behandelt eine unvollständige Transaktion ausdrücklich als solche.

Bei einem nach dem Start gescheiterten Update zuerst `nexus_update.log`, `nexus_update_state.json` und eine passive Diagnose sichern. Nicht manuell den alten Dienst starten, nicht den neuen Ordner löschen und keine Sicherung über den neuesten Handelszustand kopieren. Die genaue Fehlermeldung bestimmt die belegte Wiederherstellung.

## Nachkontrolle

Neue Diagnose mindestens 30 Minuten sammeln. PEP-Übernahme und eToro-Tages-/Kontorisikobasis getrennt bewerten. Fehlende historische Abschlusskosten bleiben unbekannt. Die Laufzeitabnahme von 10.1.0 ist kein Nachweis für die laufende Installation von 10.1.1.
