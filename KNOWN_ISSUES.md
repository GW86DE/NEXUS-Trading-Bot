# NEXUS 10.5.0 – bekannte Grenzen

StockTwits liefert je Abruf eine Stichprobe von 30 Nachrichten; sind alle juenger als eine Stunde, ist die Stundenzahl eine Untergrenze und wird so gekennzeichnet. Auf dem Pruefstand loeste der lokale DNS-Server (Filter/VPN) api.stocktwits.com nicht auf; ueber oeffentliche Resolver ist die Quelle erreichbar -- ein DNS-Fehler auf dem Pi ist UNKNOWN (Abrufpause), nie ein Nullwert. FINRA Short Interest erscheint etwa zehn Tage nach dem Stichtag (15./Monatsletzter); der Anteil bezieht sich auf ausstehende Aktien aus dem FMP-Quote, nicht auf den Free Float. Relatives Volumen aus dem Quote nutzt das Anbieter-Durchschnittsvolumen und eine lineare Sitzungsverteilung; aus Stundenkerzen braucht es zehn Vergleichssitzungen. Die Vorwaertsmessung ist ein Papierergebnis ohne Spread und Slippage (pauschal 0,7 % Rundreise-Kosten); Boersenfeiertage nehmen den letzten Schluss davor; ein Urteil gibt es erst ab 30 vollstaendigen 5-Tage-Messungen, und ein Urteil ist keine Handelsfreigabe. Die eigene Reddit-Basislinie zaehlt erst nach 14 Beobachtungstagen. Im Modus AUS ruft PULSAR keine Quellen ab und misst nichts. Der WebUI-Diagnosebericht liest ZUSAMMENFASSUNG.json aus der verifizierten ZIP; aeltere ZIPs zeigen nur Befunde und Metadaten. Der Offline-Pruefstand dieses Pakets lief auf Windows x86_64 mit Node 18.4; der vollstaendige Volltest laeuft beim Pi-Update und muss dort bestehen, bevor Dienste starten.

# NEXUS 10.4.0 – bekannte Grenzen

Der Existenzrisiko-Block der PULSAR-Hype-Spur erkennt Insolvenz, Chapter 7/11/15, Going-Concern-Warnungen, Delisting, Handelsaussetzung und Betrugsermittlungen nur in den gelieferten Quellen (FMP-News der letzten 90 Tage, SEC-Originaldokumente, FMP-Profil `isActivelyTrading`); eine Meldung, die kein Anbieter liefert, kann nicht erkannt werden. Eine schwache Bilanz ist ausdruecklich kein Block und bleibt nur Information im Alarm. Die Quote-Kursbestaetigung setzt einen frischen FMP-Starter-Quote (hoechstens 15 Minuten alt) voraus; ohne ihn gilt weiterhin die letzte abgeschlossene Tageskerze, ein Ausbruch wird dann erst am Folgetag sichtbar. Die Scan-Uebersicht zaehlt je Zyklus, sie ersetzt keine Einzelbelege im Entscheidungsjournal. eToro-Kerzen in der WebUI stammen ausschliesslich aus dem Kern-Snapshot (Stundenkerzen bei jedem Scan, 15-Minuten- und Tageskerzen nur fuer Instrumente mit Position/Trade der letzten 7 Tage, alle 15 Minuten); vor dem ersten Scan nach dem Update gibt es keine eToro-Kerzen, und Luecken der eToro-Historie werden nicht gefuellt. Die Kerzenansicht laeuft mit lokal ausgeliefertem Apache ECharts 5.5.1 (kein CDN, CSP `script-src 'self'` unveraendert); ohne JavaScript-Canvas bleibt die bisherige statische SVG-Darstellung. Der Offline-Pruefstand dieses Pakets lief auf Windows x86_64; der vollstaendige Volltest laeuft beim Pi-Update und muss dort bestehen, bevor Dienste starten.

# NEXUS 10.3.1 – bekannte Grenzen

Die eToro-Abrechnung wird jetzt aus zwei authentifizierten Barbestandsbelegen automatisch protokolliert, wenn der uebrige Positionsbestand identisch ist, keine Orders offen sind, das Intervall hoechstens 30 Minuten dauert und die Abschlusskosten hoechstens max(5 USD, 0,5 % des Erloeses) betragen. Ausserhalb dieser Grenzen bleibt das Ergebnis UNKNOWN und wartet auf die Nutzerbestaetigung im Dialog -- es wird weiterhin keine Gebuehr angenommen. Ein-/Auszahlungen oder Dividenden im Messintervall kann NEXUS nicht sehen; die engen Grenzen und der Positionsabgleich begrenzen dieses Risiko, schliessen es aber nicht aus. Alte UNKNOWN-Zeilen ohne Barbestandsbelege bleiben historisch offen.

# NEXUS 10.1.10 – bekannte Grenzen

Die USD/USDG-Freigabe ist eine Konfigurationsoption; ob nach der OKX-Instrumentumstellung (ab 23.09.2026) EUR/USDC-Maerkte bestehen bleiben, entscheidet der Broker. Ohne beobachteten FX-Kurs bleibt jede Nicht-Basiswaehrung gesperrt statt geschaetzt. Der eToro-Ergebnisabgleich fuer extern geschlossene Verkaeufe benoetigt weiterhin die Nutzerbestaetigung im Abschlussabrechnungsdialog: Die offizielle eToro-API liefert Gebuehren (`totalFees`/`totalExternalFees`) nur fuer OFFENE Positionen, die History meldet `fees=0`; ein automatischer Abschluss waere Gebuehren-Erfindung (dokumentierte Vorarbeit fuer eine spaetere Snapshot-Persistenz). Die dynamische X-Konten-Registry schlaegt nur vor; Identitaet bestaetigt ausschliesslich der Nutzer. Historische eToro-Abschlusskosten, UNMATCHED-Private-Stream-Ereignisse und externe Quellenfehler bleiben ohne neue Originalbelege offen. Der Offline-Pruefstand dieses Pakets lief auf Windows x86_64 ohne pandas/fastapi; der vollstaendige Volltest laeuft beim Pi-Update und muss dort bestehen, bevor Dienste starten.

# NEXUS 10.1.9 – bekannte Grenzen

Die Softwarekorrektur beweist nicht, welche Waehrung im konkreten OKX-Konto aktuell finanziert ist. Fehlt ein beobachteter FX-Kurs zur Risikowaehrung, bleibt der Entry gesperrt. Guthaben in nicht freigegebenen Waehrungen wird nicht automatisch umklassifiziert. Historische eToro-Abschlusskosten, UNMATCHED-Private-Stream-Ereignisse und externe Quellenfehler bleiben ohne neue Originalbelege offen. ARM64-/Pi-Dauerbetrieb und echte Providerantworten sind nach Installation separat abzunehmen.

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

# NEXUS 10.1.6 – belegte Grenzen

- Die bereitgestellte Diagnose zeigt PEP bereits mit 242,16 USD netto und bestätigter Nutzerabrechnung. Das ist keine neue native Gebührenbestätigung von eToro. Dieser Ursprung bleibt sichtbar; er wird nicht umetikettiert.
- OKX 54092 verlangt eine eigene Bestätigung beim Broker. NEXUS kann den Fehler erklären, Mehrfachversuche vermeiden und danach einen regulären Versuch erlauben. Es kann die Brokerbestätigung nicht ersetzen und keine erfolgreiche Ausführung garantieren.
- Nullvolumen stammt in der geprüften OKX-Stichprobe aus den originalen Antworten. Die Qualitätsprüfung bleibt streng. Daten werden nicht durch erfundenes Volumen oder eine andere Preisquelle ersetzt.
- GDELT-Timeouts und der Tradestie-Zertifikatsfehler sind externe Quellenprobleme. Vorhandene Rückoffzeiten bleiben aktiv; TLS-Prüfung wird nicht umgangen. Eine funktionierende andere Quelle beweist keine erfolgreiche Abfrage dieser Anbieter.
- X-Recherche ist eine kleine kostenbegrenzte Stichprobe: drei zusätzliche Achtstundenfenster, maximal zwei Aktien/zehn Beiträge je Suche. Sie ist kein Echtzeit-Warnsystem und deckt nicht alle Kandidaten oder Tweets ab. Firmen ohne frischen typisierten Einzelaktienbeleg werden nicht automatisch recherchiert.
- Stimmungsangaben beruhen auf konservativen positiven/negativen Schlagworten. Erkennbare Negation/Sarkasmus wird nicht als Richtung ausgelegt; darüber hinaus bleiben Sprach-, Kontext- und Manipulationsgrenzen. Unklare Daten werden als unbekannt/unklassifiziert gezeigt. X ist stets nur eine Quellenfamilie.
- Die gezielte X-Recherche entsteht nach der PULSAR-Auswahl. Ihre Antwort kann erst eine spätere reguläre Bewertung ergänzen. Vergangene KI-Entscheidungen werden nicht rückwirkend als mit neuen X-Daten getroffen dargestellt.
- Der lokale X-Kostenplan reserviert bei den derzeit bestätigten Defaults 14,7095 EUR für 31 Tage voller Nutzung (13 Zählabfragen und bis zu sechs Suchen mit zehn Beiträgen täglich, 30 % Reserve, 1 USD = 1 EUR). Preisänderungen, Steuern, Wechselkurse und fremde API-Nutzer können die Rechnung ändern. Anbieter-Ausgabenlimit zusätzlich setzen. Kosten anderer bestehender Quellen/GPT sind nicht Teil des X-Budgets.
- Neue Ausführungsbelege speichern den endgültigen Fehler separat. Früher nicht gespeicherte Details werden nicht erfunden. Große Entscheidungs-IDs bleiben für neue Schreibvorgänge exakt; alte gerundete Datensätze werden nicht pauschal umgeschrieben.
- Die Tests laufen isoliert auf Linux x86_64/Python 3.12. Es wurde keine neue Brokerorder, bezahlte X-/GPT-Abfrage oder Installation auf dem Raspberry Pi ausgeführt. ARM64/Python 3.13 und das tatsächliche Zielkonto sind hier nicht live geprüft.
