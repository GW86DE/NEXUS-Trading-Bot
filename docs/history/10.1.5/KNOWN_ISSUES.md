# Aktueller Stand: NEXUS 10.1.5

Siehe `INSTALLATIONSANLEITUNG_NEXUS_10.1.5_DE.md` und `docs/NEXUS_10.1.5_Aenderungen.md`. Die folgenden Abschnitte dokumentieren frühere Versionen.

# NEXUS 10.1.4 – verbleibende Grenzen

- **PEP bleibt möglicherweise für neue Käufe sperrend.** Der Kostenumfang der History stimmt nicht mit dem bestätigten Einstiegskostenbeleg überein. Ein mathematisch passendes `netProfit`/`fees`-Paar derselben Antwort ist keine unabhängige Bestätigung sämtlicher Kosten. Ein tatsächlich passender Abschlusskostenbeleg muss vorliegen; es wird kein Nullwert erfunden.
- Alte, bereits verlorene private eToro-Ereignisse werden nicht nachträglich erzeugt. Neue Ereignisse werden dauerhaft gespeichert und über REST geprüft. Ein nicht mehr abrufbarer tatsächlicher Abschluss kann weiter eine Abrechnungslücke erzeugen.
- Die weiterhin strikte Ergebnisregel kann eine Kaufpause auslösen. Schutz-/Verkaufsprüfung bleibt getrennt; eine erfolgreiche REST-/WebSocket-Verbindung beweist weder vollständige Buchhaltung noch einen erlaubten Auftrag.
- Native Broker-TP/SL wirken nach Brokerregeln. Ein Broker-TP kann vor einer Software-Kostenprüfung auslösen. Es gibt deshalb keine neue Garantie eines Mindestnettogewinns. 24/5 ist nicht 24/7; ein pauschaler Wochenendgebührenfaktor wäre unbelegt.
- X liefert höchstens 30 Posts aus drei täglichen Suchstichproben. Neue Aktien werden anhand ausdrücklicher Cashtags extrahiert. Namen ohne Cashtag und politische Aussagen ohne konkreten Ticker werden nicht spekulativ Aktien zugeordnet. Englische Suchbegriffe schränken die Abdeckung zusätzlich ein.
- Höchstens zwölf geprüfte Aktien werden automatisch über Tageszählungen verfolgt. Weitere Kandidaten können untersucht werden, erhalten aber bei voller Vergleichsgruppe zunächst keinen neuen Counts-Platz. 35 Tage Stabilität schützen den Aufbau, begrenzen jedoch die Rotation.
- Die X-Langzeitnormierung verlangt vollständige 28-Tage-Daten. PULSAR kann eine gesonderte echte Tageszählbasis nach mindestens 14 vorherigen vollständigen Tagen verwenden. Fehlende API-Tage werden niemals als null ergänzt. Dauerlücken erscheinen ausdrücklich als Abdeckungsproblem.
- Die lokale X-Kostenreservierung ist konservativ und kann über der Rechnung liegen. Anbieterpreise, Wechselkurs, Steuern und Abrufe anderer Programme können abweichen. Die Preisbestätigung verfällt nach 30 Tagen. Der Anbieter muss den benötigten Suchzugang erlauben; dies wurde ohne bezahlten Aufruf nicht live geprüft.
- News-Ursprünge mit gemeinsamer Risikokategorie sind vorsorgliche Risikohinweise, keine Bestätigung identischer Ereignisse. Bekannte Reposts, identische Artikel, reine Anbieternamen und X allein erfüllen die Mehrquellenprüfung nicht. Nicht gekennzeichnete indirekte Abschriften bleiben eine Grenze automatischer Provenienzprüfung.
- Die Tests verwenden isolierte Belege und Testtreiber. Keine Installation auf Georgs Raspberry Pi, keine Echtgeld-/Demoorder, keine kostenpflichtige X-/GPT-Abfrage und kein Telegram-Versand wurden hier ausgeführt.
