# NEXUS 10.1.1

Freigegeben zur Erstellung des kontrollierten DEMO/Paper-Updates. Die neue Version enthält den geprüften Reparaturstand zu PEP, eToro-Risikobelegen, Diagnose, PULSAR-Auswahl und Quellenanzeigen. Eine uneingeschränkte Live-Freigabe besteht weiterhin nicht.

## Auf dem Raspberry Pi installieren

Die eigenständige Datei `NEXUS_10.1.1_Installieren.sh` im Downloadordner speichern und als normaler Benutzer starten:

```bash
bash ~/Downloads/NEXUS_10.1.1_Installieren.sh
```

Der Installer enthält das vollständige ZIP. Er erkennt den bestehenden Dienstordner, prüft und sichert den Zustand und richtet `~/Georg/TradingBot_v10.1.1_NEXUS` ein. Im regulären Updatepfad werden die neuen DEMO/Paper-Dienste gestartet. Den alten Botordner vorher nicht löschen oder von Hand kopieren. Die neue Version erzeugt keine unabhängige zweite Installation mit fremden Zustandsdaten.

Vorab nur Paket und Quellhashes prüfen:

```bash
bash ~/Downloads/NEXUS_10.1.1_Installieren.sh --paket-pruefen
```

Alle Details, Optionen, Fehlerfälle und die sichere Rückkehr stehen in `INSTALLATIONSANLEITUNG_NEXUS_10.1.1_DE.md`. Bereits eingestellter FMP-Starter-Tarif bleibt erhalten.

## Danach Diagnose sammeln

```bash
bash ~/Downloads/NEXUS_10.1.1_Diagnose_Starten.sh
```

Dieser separate eigenständige Starter enthält Diagnosewerkzeug **1.2.0**. Standard: 30 Minuten passive Beobachtung, danach eine eindeutige ZIP in `~/Downloads/NEXUS_Diagnosen`. Im installierten Ordner ist dasselbe Werkzeug mit `bash NEXUS_Diagnose_Starten.sh` aufrufbar. `--sofort` sammelt ausschließlich den vorhandenen Zustand ohne 30-Minuten-Beobachtung.

## PEP und Kontorisiko getrennt abnehmen

PEP ist im ursprünglichen Beleg als BOT/VERIFIED zugeordnet. Der neue Wartungsweg kann einen ausdrücklich ausgewählten, schon beim Broker exakt vorhandenen Schutzplan übernehmen und seine Originalhistorie erhalten. Das Update selbst führt diese Übernahme nicht automatisch aus. Anleitung: `docs/implementation/repair_pep.md`; Starter: `NEXUS_eToro_Schutzplan.sh`.

Die fehlende historische eToro-Risikobasis wird nicht erfunden. `NEXUS_eToro_Risikopruefung.sh` sammelt lesend aktuelle Belege; der fehlerhafte Legacy-Fall bleibt ohne passenden unabhängigen historischen Nachweis gesperrt. Anleitung: `docs/implementation/repair_risk.md`.

## Berichte

- `IMPLEMENTATION_REPORT.md`: tatsächliche Änderungen und ursprüngliche Diagnosebefunde.
- `TEST_REPORT.md`: Prüfstand und praktische Grenzen.
- `KNOWN_ISSUES.md`: verbleibende Abnahmepunkte.
- `MIGRATION_NOTES.md`: Zustände, Sicherung und Rückkehrgrenzen.
- `CHANGELOG.md`: Änderungen gegenüber 10.1.0.

Frühere Versionsberichte unter `docs/history/` sind historische Nachweise. Die Teilberichte `docs/implementation/repair_*.md` dokumentieren die Entwicklung des übernommenen Reparaturstands auf Basis 10.1.0.
