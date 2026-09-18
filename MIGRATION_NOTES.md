# NEXUS 10.1.10 – Migration

Keine Handels-, Risiko- oder PULSAR-Datenbank wird umgeschrieben. Neue RiskState-Felder (native Equity-Reihe) haben Standardwerte; alte Zustandsdateien laden unveraendert. Die Entscheidungs-Retention loescht ausschliesslich alte Kandidatenentscheidungen ohne Order-/Eventbeleg und Heartbeat-Telemetrie (die Datenbank erzwingt das zusaetzlich per Fremdschluessel); Orders, Fills, Trades, Belege und Ergebnisse bleiben vollstaendig erhalten. Die X-Konten-Registry und der Backtest legen nur zusaetzliche Zustaende an. Bestehende `okx_credentials.json` bleiben gueltig; `allowed_quote_ccy` akzeptiert nun zusaetzlich USD/USDG nach ausdruecklicher WebUI-Bestaetigung. Unbekannte Orderausgaenge bleiben unbekannt.

# NEXUS 10.1.9 – Migration

Keine bestehende Handels-/PULSAR-Datenbank wird fuer die Korrektur umgeschrieben. Die neue PULSAR-Diagnoseprojektion ist ein zusaetzlicher Cacheeintrag und entsteht bei regulaeren PULSAR-Läufen. Bestehende Settings, Positionen, Orders/Fills, Risiko-, PULSAR-, GPT-Budget- und Cachezustaende werden ueber den vorhandenen sicheren Updater uebernommen. Unbekannte Orderausgaenge bleiben unbekannt.

# 10.1.8 – Korrektur der OKX-Kontovorpruefung

Die Kontovorpruefung verwendet fuer Trailing-Stop-Orders nun `move_order_stop`.
Fehler nennen GET-Endpunkt, freigegebene Pruefparameter, HTTP-Status und numerischen OKX-Code.
Freitextantworten, Zugangsdaten und Kontodaten werden nicht ausgegeben.
`--nur-pruefen` im Kontowechselwerkzeug fuehrt dieselben GETs ohne Aktivierung aus.
Alle sonstigen Konto-, Archivierungs- und Bestandssperren bleiben bestehen.
Offline-Tests ersetzen keinen Abruf mit dem neuen OKX-Demoschluessel.

# NEXUS 10.1.7

Neuer OKX-Kontokontext, expliziter archivierter Demo-Kontowechsel, konservativer Bestandsabgleich und vorgelagerte Risikoprüfung. Kein Abschluss und kein Storno allein wegen fehlenden Guthabens. Historische Schutzinformationen bleiben erhalten; aktuelle Bestätigung wird bei ungeklärtem Bestand zurückgenommen.

Grundlage: vollständiger Release 10.1.6, nicht der unverfügbare Zwischenstand aus dem alten Chat. Installation und Grenzen siehe INSTALLATIONSANLEITUNG_NEXUS_10.1.7_DE.md. Die konkreten Abschlussprüfungen werden im separat ausgelieferten NEXUS_10.1.7_Pruefbericht.md dokumentiert.


---

## Historischer Stand bis 10.1.6

# NEXUS 10.1.6 – Zustandsübernahme

Die bestehende sichere Migration wird übernommen. Brokerkonten, DEMO/LIVE-Zuordnung, Positionen, PEP-Nutzerabrechnung, Risikotage, Ausführungsjournale, Stornos, X-Budget und Reservierungen bleiben erhalten. SQLite-Datenbanken werden konsistent gesichert; bestätigte WAL-Daten sind eingeschlossen. Es gibt keinen Löschbefehl für Instrument 1043, keine doppelte Kostenbuchung und keine neue SL-/TP- oder 1-%-Verkaufsregel.

Neu: `okx_account_action.json` gehört zum kritischen persistenten Zustand. Ein alter passender 54092-Fehler kann aus dem begrenzt gelesenen, kontogebundenen Journal übernommen werden; ein neuerer erfolgreicher Abschluss verhindert die Wiederaufnahme eines älteren Fehlers. Nutzerfreigaben bleiben an die aktuelle Fehlerrevision gebunden.

PULSAR-Recherche und X-Datenbank verwenden WAL. PULSAR-Lesezugriffe erhalten eine echte Lesetransaktion und reservieren keinen Schreibzugriff. Tabelleninitialisierung wird je Dateiinode einmal ausgeführt. Workerfehler bleiben als Historie sichtbar; ein wieder laufender Worker erhält einen aktuellen Wartestatus.

Die Entscheidungstabelle bekommt additiv `execution_result_json`. Ursprüngliche Entscheidung und endgültige Ausführungsantwort bleiben getrennt. Diagnose und WebUI berücksichtigen den endgültigen Ausführungsstatus. Ältere Tabellen ohne dieses Feld bleiben lesbar.

X-Kandidaten und Suchbelege liegen in der vorhandenen X-Datenbank. Drei zusätzliche Suchfenster teilen sich dieselbe Kostenreservierung mit bisherigen Abrufen. Profilidentität, Zeitgrenzen und Abfragebeleg binden die Zuordnung; eine leere neue Abfrage übernimmt keine Treffer eines alten Laufs. Neue X-Metadaten ändern die PULSAR-Paketrevision; bereits getroffene Entscheidungen bleiben unverändert erhalten und die vorhandenen GPT-Budgets gelten weiter.
