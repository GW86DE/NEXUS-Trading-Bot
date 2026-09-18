# NEXUS 10.1.6 – Installation und Kontrolle

Stand: 15.09.2026. Dieses Update behebt die diagnostizierten Softwarefehler und ergänzt die dynamische X-Recherche für PULSAR. Es ist für den bestehenden DEMO/Paper-Updateablauf vorgesehen.

## 1. Auf dem Raspberry Pi installieren

`NEXUS_10.1.6_Installieren.sh` in den Ordner **Downloads auf dem Raspberry Pi** speichern. Die Datei enthält bereits das komplette Programm. Dann im Pi-Terminal als normaler Benutzer starten:

```bash
bash "$HOME/Downloads/NEXUS_10.1.6_Installieren.sh"
```

Eine reine Paketprüfung ist zuvor möglich:

```bash
bash "$HOME/Downloads/NEXUS_10.1.6_Installieren.sh" --paket-pruefen
```

Der Installer übernimmt den bestehenden Updateablauf mit Sicherung, konsistenter SQLite-Übernahme einschließlich bestätigter WAL-Daten, Tests und Dienstumschaltung. Die alte Installation und Backups aufbewahren. Das neue Programm liegt unter `~/Georg/TradingBot_v10.1.6_NEXUS`. Einstellungen, Risikodaten, Handelsbelege und bereits verbrauchtes X-Budget werden übernommen. LIVE wird vom Installer abgewiesen.

Falls ein zusätzliches Python-Programm im alten Botordner die Daten verändern könnte, bricht der Installer mit dessen PID ab. Das zugehörige Programm regulär beenden und anschließend den Installer erneut starten. Die vorhandenen Sicherungs- und Prüfschritte bleiben erforderlich.

Nach erfolgreichem Abschluss die WebUI neu laden. Im Kopf muss **10.1.6 NEXUS** stehen.

## 2. PEP und eToro kontrollieren

In der bereitgestellten Diagnose ist PEP bereits abgerechnet: Position **3597440106**, NEXUS-Trade **54**, Netto **242,16 USD**, Herkunft **USER_CONFIRMED**. Das Ergebnis gehört zum Verkaufstag 14.09.2026. Es werden weder erneut Kosten eingetragen noch alte Risikotage zurückgesetzt.

Der alte Sonntagsauftrag **380995258** zu Instrument **1043** bleibt als ungeklärter Auftrag einer nachweislich geschlossenen Position dokumentiert. Er wird nicht zu einem zusätzlichen Verkauf umgebucht. In dieser Diagnose sperrt er die Käufe nicht. Eine eventuell angezeigte neue Kaufsperre hat deshalb anhand ihres aktuellen konkreten Grundes geprüft zu werden; Verbindungsgrün allein ist keine Handelsfreigabe.

## 3. OKX-Meldung 54092 behandeln

Die Diagnose enthält eine echte Ablehnung von OKX: Für das betroffene Haupt-/Unterkonto fehlt eine von OKX verlangte Bestätigung. Diese Bestätigung kann NEXUS nicht stellvertretend vornehmen.

1. Direkt bei OKX die verlangte Bestätigung für das tatsächlich verwendete Konto prüfen und abgeben.
2. In NEXUS unter **Übersicht → OKX → Verbindungen, Abgleich & Bedingungen** die Kontobestätigung öffnen.
3. Nach der eigenen Bestätigung bei OKX **In OKX geprüft – einen regulären Kaufversuch zulassen** wählen. Der Knopf gibt genau einen nächsten regulären Kaufversuch frei und sendet selbst keine Order. Die normale Strategie sowie sämtliche Geld- und Risikoprüfungen entscheiden weiterhin, ob überhaupt ein Auftrag entsteht.

Eine erneute Ablehnung wird wieder sichtbar gesperrt. Die Freigabe ist an Konto, DEMO/LIVE und den aktuellen Fehlerbeleg gebunden. Die Kaufpause aus diesem Kontofehler blockiert nicht den lokalen Verkaufspfad; OKX selbst kann einzelne Aufträge weiterhin ablehnen.

## 4. Dynamische X-Recherche sehen

Auf **PULSAR**, **Quellen & X** und **Diagnose** erscheint die zusätzliche Kandidatenrecherche. Zu jeder aktuell ausgewählten Aktie werden Suchanfrage, Auswahlherkunft, Bearbeitungsstand, empfangene/zugeordnete/verwertbare Beiträge, Stimmungs-Hinweise und öffentliche Beleglinks angezeigt. Die PULSAR-Karte hält zusätzlich den X-Stand ihrer letzten Bewertung fest; dieser kann älter sein als die aktuelle Quellenanzeige.

PULSAR meldet seine ausgewählten Kandidaten mit geprüftem FMP-Einzelaktienprofil automatisch an die Warteschlange. Die zusätzliche Suche verwendet deren Cashtags und geprüfte Firmennamen; sie ist nicht auf die sechs beobachteten Konten beschränkt. Die bisherigen Konten- und Entdeckungssuchen bleiben bestehen.

Die neue Recherche erhält höchstens **drei gebündelte Suchanfragen täglich**, jeweils für maximal zwei Aktien, in Achtstundenfenstern. Insgesamt sind mit der bisherigen Suche höchstens sechs Post-Suchen pro Tag möglich. Je Suche werden höchstens zehn Beiträge angefordert. Die Warteschlange enthält maximal zwölf frische Kandidaten; sie wird fair nach dem letzten Abruf bedient. Leere, fehlgeschlagene oder veraltete Stichproben bedeuten **unbekannt**, nicht neutral. Ergebnisse können erst nach dem nächsten Recherchefenster und der nächsten regulären PULSAR-Bewertung dort einfließen.

X bleibt eine einzige soziale Quellenfamilie, auch bei mehreren Autoren. Die Kategorien sind vorsichtige Schlagwort-Hinweise, keine repräsentative Marktstimmung. X allein kann weder einen Kauf/Verkauf freigeben noch eine Krise bestätigen. FMP, unabhängige Nachrichten, Kursdaten und die bisherigen Prüfungen bleiben erforderlich. Das bestehende GPT-Budget und die Freigaberegeln für PULSAR-Handel bleiben unverändert.

## 5. Budget und Diagnose

X muss wie bisher eingerichtet und aktiviert sein; Preisbestätigung und API-Zugriff müssen gültig sein. Alle alten und neuen Abrufe laufen durch dieselbe persistente Monatsreservierung von maximal **15 EUR**. Bei ausgeschöpftem Budget, Anbieterfehlern oder abgelaufener Preisbestätigung werden keine zusätzlichen bezahlten Abrufe erzwungen. Andere Programme, Anbieterpreise, Steuern und Wechselkurse beeinflussen die tatsächliche Rechnung; für eine anbieterweite Grenze zusätzlich das Ausgabenlimit im X-Portal setzen.

Nach dem Update **Diagnose → 30 Minuten beobachten** verwenden. Die Sammlung ist passiv und löst selbst keine Extra-Abfragen bei X, GPT oder dem Broker aus. Sie dokumentiert jetzt auch die X-Recherche, die OKX-Kontobestätigung, endgültige Auftragsfehler und Nullvolumen aus den originalen Brokerantworten.

Als unabhängiger Starter kann `NEXUS_10.1.6_Diagnose_Starten.sh` aus Downloads genutzt werden:

```bash
bash "$HOME/Downloads/NEXUS_10.1.6_Diagnose_Starten.sh" --minuten 30
```

Die Entwicklung wurde offline geprüft. Eine erfolgreiche Installation auf diesem Pi sowie echte neue OKX-/X-Antworten sind erst nach der Installation belegbar.
