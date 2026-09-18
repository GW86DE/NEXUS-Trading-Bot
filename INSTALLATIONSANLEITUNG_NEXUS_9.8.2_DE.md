# NEXUS 9.8.2 installieren und abnehmen

9.8.2 ersetzt die noch nicht installierte 9.8.1. Direkt vom laufenden 9.8.0 aktualisieren; 9.8.1 nicht vorher installieren. Das Paket enthält den vollständigen Bot. Voraussetzung: Raspberry Pi OS 64 Bit/ARM64, Python ab 3.11 und ein bestätigter DEMO-/Paper-Ausgangsstand. Der automatische Updateweg startet keinen Livebetrieb und stellt keine Zugangsdaten um.

## Installation

Speichere `NEXUS_9.8.2_Installieren.sh` unter `/home/georg/Georg`. Starte als Benutzer `georg`, nicht mit `sudo` davor:

```bash
bash "$HOME/Georg/NEXUS_9.8.2_Installieren.sh" --paket-pruefen
bash "$HOME/Georg/NEXUS_9.8.2_Installieren.sh"
```

Der erste Befehl prüft nur das Paket. Der zweite entpackt in den neuen Ordner `TradingBot_v9.8.2_NEXUS` und startet den bestehenden phasenweisen Updater. Er ermittelt die tatsächlichen Dienstordner, prüft Voraussetzungen, stoppt und sichert den alten Zustand, übernimmt Einstellungen und Laufzeitdaten, führt beleggestützte Migration und isolierte Tests aus und kontrolliert den Neustart. Bei Systemschritten kann dein sudo-Passwort nötig sein.

Kein zweiter Core, kein erneuter alter Erstinstaller, keine manuelle Löschung von Positions-, Registry- oder Datenbankdateien. Ein schon belegter Zielordner wird nicht einfach überschrieben. Nach einem Abbruch Sicherung, `nexus_update.log` und Phasenjournal behalten; nicht blind wiederholen oder alte Zustände zurückspielen.

Alternativ die vollständige ZIP als eigenen neuen Ordner unter `/home/georg/Georg` entpacken und ausführen:

```bash
bash "$HOME/Georg/TradingBot_v9.8.2_NEXUS/Nexus_Update.sh"
```

Nur einen Installationsweg verwenden. Die historischen Anleitungen im Paket gelten nicht für dieses Update.

## Kontrolle nach dem Update

Der Installer muss `UPDATE UND STARTKONTROLLE ERFOLGREICH` melden. Prüfe:

```bash
systemctl show tradingbot-pi5.service tradingbot-webui.service --no-pager -p ActiveState -p SubState -p WorkingDirectory
```

Beide Dienste sollen `active/running` und `/home/georg/Georg/TradingBot_v9.8.2_NEXUS` zeigen. WebUI vollständig neu laden; Versionsanzeige 9.8.2. Die Ergebnisgrafik befindet sich unter „Übersicht“. Konto/Währung und Darstellungsart auswählen. Bei fehlenden historischen Belegen sind Lücken oder bestätigte Teilsummen richtig, nicht ein Darstellungsfehler.

## Nachrichten und offene Abgleiche

Unter „Analyse“ den Quellencheck starten; unter „Übersicht“ die aktuellen Quellenkarten öffnen. Erreichbarkeit, Prüfzeit, letzte Meldung und verbleibende Pause getrennt beurteilen. Der neue Fed-Feed liefert Makronachrichten ohne API-Key. Eine erreichbare Quelle ohne neue Meldung ist kein Fehler. MASSIVE und FMP bleiben samt Einstellungen erhalten. Ein FMP-Fehler 402 erfordert eine passende Anbieterberechtigung; die Software kann diese nicht freischalten.

Der Alpha-Vantage-Schlüssel war im alten Screenshot im Fehlertext sichtbar. Diesen Schlüssel beim Anbieter ersetzen und den neuen Wert in den NEXUS-Einstellungen speichern. Neue Fehlertexte und alte Analyseausgaben in der WebUI werden maskiert. Bereits gespeicherte Screenshots werden dadurch nicht verändert.

Fünf Nachrichtenschwellen und der Fed-Schalter stehen in den erweiterten Einstellungen. Bestehende Funktionen bleiben erhalten. Die Anzeige eines nicht verfügbaren Earnings-Kalenders bedeutet nicht „keine Termine“.

Unbekannte historische Nettoergebnisse sperren neue Käufe im betroffenen Brokerrisikotopf, bis eindeutige Belege vorliegen. Ausstiege und Schutz bleiben aktiv. Unter „Handel“ sind Ursache und letzter Abgleich eines ungeklärten Auftrags sichtbar. „Belege neu abgleichen“ liest Brokerbelege; es erzeugt keinen neuen Verkaufsauftrag. Ohne eindeutigen terminalen Beleg bleibt eine Order gesperrt. Solche Sperren nicht durch Löschen von Zustandsdateien umgehen.

Bei einer nachweislich nie übermittelten alten manuellen Verkaufsanfrage kann 9.8.2 den festhängenden Bedienauftrag korrigieren. Fehlt die vorherige Verwaltungsart im Altbestand, bleibt die Position MANUELL; dann einen gewünschten Verkauf ausdrücklich neu anfordern. Ein möglicherweise bereits übermittelter Auftrag wird nicht pauschal freigegeben.

## BTC-Abnahme hat Priorität

Beim nächsten regulären BTC-Ausgang müssen aktuelles DEMO-Orderbuch, `okx_sell_min`, Preisbandzeit und gesendetes Limit zusammenpassen. Der im alten Log abgelehnte Wert 78.624 darf beim damaligen Mindestlimit 79.027 nicht erneut gesendet werden. 79.027 ist ein historischer Prüfwert, kein fest programmierter neuer Verkaufspreis.

Ein Verkauf gilt erst mit terminalem Orderstatus, echten Fill-IDs und passender Ledger-/Risikobuchung als abgeschlossen. Bei Teilausführung muss die Restmenge stimmen und deren Schutz bestätigt werden. Bei unklarem Status darf keine zweite Verkaufsorder entstehen. Nur „Dienste laufen“ oder eine grüne Schutzanzeige beweist keinen Verkaufsfill.

Falls weiter eine Sperre auftritt, exportiere gezielt BTC:

```bash
python3 "$HOME/Georg/TradingBot_v9.8.2_NEXUS/Nexus_SUI_Diagnose.py" --symbol BTC
```

Der historische Skriptname bleibt erhalten. Der Export findet den aktuellen Dienstordner selbst, liest nur lokale Belege und schreibt eine neue Diagnose-ZIP in dein Benutzerverzeichnis. Zusätzlich die relevanten Logzeilen seit dem Update mitsenden, ohne API-Schlüssel, Passphrase, Cookies oder Zugangsdaten. Es werden keine Brokeranfragen oder Dienstaktionen ausgelöst.

## Historischen manuellen SUI-Verkauf nachpflegen

Diese optionale Nachpflege ist getrennt von der BTC-Ausführung. Sie sendet keine Order und verändert keine offene BTC-Position. Das normale Update schreibt bereits geschlossene historische Trades nicht stillschweigend um.

Lege den bereits erstellten Beleg `NEXUS_OKX_SUI_Belege_20260909_094020_365709UTC.json` unter `/home/georg/Georg` ab. Zeige zuerst nur die Vorschau an:

```bash
"$HOME/Georg/TradingBot_v9.8.2_NEXUS/.venv/bin/python" \
  "$HOME/Georg/TradingBot_v9.8.2_NEXUS/Nexus_Externe_Verkaufe.py" \
  --beleg "$HOME/Georg/NEXUS_OKX_SUI_Belege_20260909_094020_365709UTC.json" \
  --datenbank "$HOME/Georg/TradingBot_v9.8.2_NEXUS/decision_history.sqlite" \
  --trade-id 45 --verkaufsorder 3904817860665094145 \
  --max-staub 0.01 --manuell-bestaetigt
```

Erwartet: SUI-EUR, 124,77 verkaufte SUI, 0,001765 Rest, Netto-Verkaufserlös 84,9180658695 EUR, Gewinn unbekannt. Prüfe dies vor dem Anwenden. Führe denselben Befehl zusätzlich mit `--anwenden` aus, wenn die Vorschau passt. Das Skript legt zuerst eine konsistente SQLite-Sicherung an und nennt ihren Pfad. Ein wiederholter identischer Beleg erzeugt keine zweite Buchung. Abweichende Konten, Entry-IDs, Mengen oder bereits bezifferte Ergebnisse werden nicht überschrieben.

Ohne Beleg oder bei abweichender Vorschau nicht mit geänderten IDs „passend machen“. Rückfrage mit der Fehlermeldung. Niemals die komplette Sicherungsdatenbank über einen inzwischen weiterhandelnden Bot kopieren.

## Noch nicht als live abgenommen

Offline geprüft wurden Fachlogik, Migration, synthetische API-Antworten, WebUI-Schnittstellen und JavaScript, einschließlich vollständiger Läufe unter Python 3.11.16 und 3.12.14. Tatsächliches OKX-/eToro-Matching, ARM64/Python 3.13 und Safari auf iPhone/iPad wurden hier nicht ausgeführt. Auf deinen Geräten bitte Hoch-/Querformat, Kontoauswahl, Diagrammwechsel, Tabellenaufklappen, Kerzenchart und Einstellungen kontrollieren. Vor Livebetrieb ist die dokumentierte DEMO-Abnahme erforderlich.
