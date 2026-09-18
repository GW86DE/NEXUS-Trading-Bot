# NEXUS 9.8.9 installieren und FMP nutzen

Build **9.8.9-FMP-AUTO**, auf Basis von **9.8.8 FIX3**. Enthalten sind die
historischen OKX-Belegkorrekturen, die PULSAR-Timeoutkorrektur und die tägliche
Universumsauswahl. Die FMP-Erweiterung verwendet keine Referenzpreise als
ausführbare Brokerkurse oder als Ersatz für historische Handelsbelege.

## Persönliches Update auf Georgs Pi

`NEXUS_9.8.9_Installieren.sh` im Downloads-Ordner speichern. Als **georg** ausführen:

```bash
bash "$HOME/Downloads/NEXUS_9.8.9_Installieren.sh"
```

Kein `sudo` vor diesen Befehl setzen. Der Installer enthält den vollständigen
Quellstand und die bereits geprüften Handels-/Kursarchive, keine API-Schlüssel
und keine alte Diagnose-Datenbank. Die Handelsbelege sind private Daten.

Der bisherige Core- und WebUI-Dienst bestimmt die tatsächlich verwendete Quelle.
Der Installer prüft den DEMO-Ausgangsstand und die Software, sichert den Zustand,
stoppt die Dienste für die Übernahme und repariert eine Kopie im Staging. Erst
nach erfolgreicher Prüfung startet er die Dienste aus dem neuen Ordner:

`/home/georg/Georg/TradingBot_v9.8.9_NEXUS`

Die normale Bot-Pause und alle Kaufprüfungen bleiben maßgeblich. Das Update nimmt
keinen eigenständigen Wechsel zu LIVE vor. Ein bereits befüllter Zielordner wird
nicht still überschrieben. Der frühere Versionsordner bleibt erhalten.

Nur das heruntergeladene Paket prüfen, ohne Dienstaktion:

```bash
bash "$HOME/Downloads/NEXUS_9.8.9_Installieren.sh" --paket-pruefen
```

## Starter und Free einstellen

Der persönliche Installer übernimmt den vorhandenen FMP-Schlüssel und stellt
unter **Einstellungen → Newsquellen** Folgendes ein:

- **FMP-Betrieb:** Automatisch mit Free-Rückfall.
- **Gebuchter Tarif für Automatik:** Starter.
- **Free-Tageslimit:** Der bereits eingestellte Wert bleibt erhalten, maximal 250.

Die tatsächlichen Berechtigungen werden beim nächsten fälligen Datenabruf
einzeln bestätigt. Ein erfolgreicher Profilabruf beweist kein Starter-Abo.
Der Bot kann den Vertragsstatus bei FMP nicht auslesen; die Tarifvorgabe setzt
deshalb die Obergrenze, während die Anbieterantworten den verfügbaren Umfang
bestimmen. Eine einzelne gesperrte Zusatzdatenart wird für 24 Stunden pausiert.
Werden Nachrichten und Jahresergebnis ohne späteren erfolgreichen Zusatzabruf
abgelehnt, arbeitet AUTO mit Free-Grenzen. Nach Ablauf der Pause werden benötigte
Zusatzberechtigungen erneut geprüft. Ein Timeout oder HTTP 429 gilt als Störung
beziehungsweise Abrufpause, nicht als Kündigungsbeleg.

Nach einem Wechsel zu Free bei FMP kann **Gebuchter Tarif für Automatik → Free**
gesetzt werden. Alternativ erzwingt **FMP-Betrieb → Free** den freien Umfang
sofort. Bereits gezählte Abrufe bleiben erhalten. Wird während eines Tages mit
mehr als 250 Starter-Abrufen heruntergestuft, erfolgen bis zum nächsten UTC-Tag
keine weiteren Abrufe. Der vorhandene zulässige Cache bleibt nutzbar.

Die Einstellung ändert kein FMP-Abo. Nach dem Speichern zeigen die FMP-Zeilen
die wirksame Grenze, Datenberechtigungen, letzte erfolgreiche Antwort, etwaige
Pausen und den gemeinsam gezählten Verbrauch. Der vollständige Datenbestand
entsteht schrittweise in den regulären Läufen, nicht beim Öffnen der Seite.

## Was die Daten bewirken

| Funktion | Mit Starter | Mit Free |
|---|---|---|
| Unternehmensprofile und Tageskurse | Häufigere Referenzpflege, bis zu fünf Jahre Tageshistorie | Bisherige sparsame Referenzpflege und kürzere aktuelle Historie |
| PULSAR-Finanzprüfung | Geprüfte Jahresdaten als Ergänzung/Alternative zu SEC | Bestehende SEC- und übrige Quellen |
| Nachrichten | FMP-Unternehmensnachrichten und verfügbare Markt-, Krypto-/Forex-Hinweise | Bestehende kostenlose Nachrichtenquellen |
| NEXUS-KI-Zweitmeinung | Vorhandene FMP-Fakten im selben geplanten KI-Aufruf | Verfügbare freie Tages-/Referenzdaten |
| Gehaltene Aktien | Eigener stündlicher Kurs-/Nachrichtenlauf | Eigener Lauf mit Positionsreserve für zulässige Daten |
| Historische Handelsbelege | Unveränderte Brokerbelege und geprüfte historische Bewertung | Derselbe beleggestützte Ablauf |

Alle Verbraucher auf diesem Pi teilen Cache und Abrufgrenzen. Unter Free gilt
weiter das automatische Teilbudget von höchstens 80 Abrufen pro Tag; die übrigen
Abrufe innerhalb des Tageslimits stehen Positionen und manueller Diagnose zur
Verfügung. Unter Starter gilt eine gemeinsame Grenze von 300 Abrufen je Minute.
Der Bot fordert Daten nach Bedarf an; wiederholte identische Daten werden
wiederverwendet. Er erzeugt keine zusätzlichen KI-Aufrufe allein wegen Starter.

Jahresdaten müssen zu Unternehmen, Währung und Berichtsperiode passen. Ein
Widerspruch zu passenden SEC-Daten bleibt sichtbar und wird nicht positiv
gewertet. Nachrichten ersetzen weder Originalbelege noch Beobachtungstage oder
den Nachweis unabhängiger Personen. Die erweiterten Charts verwenden vorhandene
Tagesdaten. Eine Ein-Tages-Ansicht bekommt keine erfundenen Intradaykurse.

## Nach dem Update prüfen

- Die Oberfläche zeigt **9.8.9**; `RELEASE_BUILD.txt` nennt **9.8.9-FMP-AUTO**.
- Die FMP-Anzeige bestätigt nach einem fälligen Lauf einzelne Datenarten.
  Ein abgelehnter optionaler Endpunkt darf andere Datenquellen nicht lahmlegen.
- `okx_verified_history_repair_report.json` im neuen Core-Ordner dokumentiert
  die angewendete Handelskorrektur. Neue Beleglücken bleiben regulär prüfpflichtig.
- Universumsmitglieder ändern sich automatisch höchstens einmal je lokalem
  Kalendertag, nicht zwingend zur gleichen Uhrzeit und nicht im rollenden
  24-Stunden-Abstand. Messungen und Positionskontrollen laufen weiter.

Die Prüfungen wurden offline und auf Kopien der gelieferten Daten durchgeführt.
Die Installation und echte FMP-/GPT-Antworten auf dem Raspberry Pi sind erst nach
Ausführen des Updates bestätigt. Der FMP-Volumenzähler erfasst nur Abrufe dieser
Integration; externe Programme und vor Einführung übertragenes Volumen kennt er
nicht. Verwendete Daten behalten ihren ursprünglichen Zeitstand.
