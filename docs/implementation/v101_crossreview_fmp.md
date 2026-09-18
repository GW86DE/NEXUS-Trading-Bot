# Unabhängiges Crossreview: Massive-Integration in NEXUS 10.1.0

Prüfer: FMP/PULSAR-Agent. Ausschließlich Quellcode-Review, keine Produktdateien verändert, keine zusätzlichen privaten API-Aufrufe oder doppelten Tests.

Geprüft: `massive_service.py`, `massive_api.py`, relevante Aufrufer in `news_sources.py`/`pulsar/worker.py`, vorhandene `test_v101_massive.py`-Fälle und Callsite-Suche im neuen Releasebaum. Stand während Root-Integration; letzter Massive-Agent-Schritt kann die unten gemeldeten Punkte noch ändern.

## Tragfähige Schutzmechanismen

- Alle Netzwerkpfade des MassiveClients laufen durch `Store.acquire` vor HTTP. Suche im Produktcode fand keine parallele direkte Massive-/Polygon-Requests-Implementierung, die den Begrenzer umgeht.
- `calls`-Zähler und `last_clock` sind absichtlich NICHT nach API-Key-Scope getrennt. Andere Instanzen/Prozesse/Keys im selben NEXUS-Zustandsverzeichnis schaffen deshalb kein zusätzliches Minutenkontingent.
- API-Antwortcache und Berechtigungen sind nach Schlüssel+Base gehasht und getrennt. Daten eines anderen Schlüssels werden nicht als gemeinsamer Antwortcache verwendet.
- Reservierung wird vor HTTP committed; Timeout/Prozessabbruch erstattet die Minute nicht. Noch laufende identische Abfragen erhalten eine lokale Pause, statt in einen zweiten gleichzeitigen Call zu laufen.
- HTTP429 schreibt eine globale Pause; Retry-After-Sekunden und HTTP-Datum werden verarbeitet, fehlende/ungültige Werte mindestens60s. Neustart hebt sie nicht auf.
- Positive gültige Cachetreffer passieren vor dem Abruflimit und benötigen kein neues HTTP. Datum `saved_at` bleibt erhalten. `cache_only=True` reserviert keine Netzwerkabfrage.
- Lesender Status erzeugt keine DB/keinen Netzwerkcheck. Noch fehlende Zustandsdaten werden als unbekannt dargestellt, nicht als0fehlerfrei.
- FMP-first in Newsaggregation und PULSAR reduziert redundante Massive-Abfragen. Fehlende/abgelaufene optionale Massive-Caches bleiben bei ausreichenden FMP-News ohne HTTP. Echte Datenlücken dürfen weiterhin durch den gemeinsamen Gate ergänzt werden.
- Einmalige Upgradepause wird bei bestehendem Legacy-Newsstatus initial60s gespeichert; ein neuer Prozess setzt diese Pause nicht wieder zurück oder erneut an.

## An Massive-Agenten gemeldete Punkte

1. **Vorwärtssprung der Systemzeit:** Der zuerst geprüfte Stand benutzt für die Minuten-/15s-Fenster nur Wallclock. `last_clock > now` behandelt Rückwärtssprünge, Vorwärtssprünge reifen dagegen Anfragen vorzeitig aus. So kann die reale Frequenz kurzfristig höher sein als die berechnete. Für ein ausdrückliches Uhrsprung-Versprechen muss ein monotones Intervall/Bootvergleich bzw. eine konservative Pause bei Uhrdrift hinzukommen. An Massive-Agent und Root gemeldet; finalen Agentenbefund berücksichtigen.
2. **Antwortgröße und Laufzeit:** Der zuerst geprüfte Client lädt mittels `requests.get`/`.json()` zunächst den ganzen Body. `MAX_CACHE_BYTES` begrenzt die Persistenz, nicht den vorgelagerten RAM-Verbrauch. Ein ungewöhnlich großer/langsam gestreamter Body kann den Pi trotz nominellem Requesttimeout belasten. Bounded Streaming mit Gesamtzeit-/Größenlimit nach FMP-Vorbild empfohlen. Kein beobachteter Runtimefehler in der Diagnose, aber konkrete technische Begrenzungslücke. An Massive-Agent gemeldet.
3. **Tickerpagination:** Die zweite Seite kann unmittelbar am15s-Gate scheitern; dann erhält der Aufrufer keine bereits gesammelten Ergebnisse, obwohl die erste Seite dauerhaft gecacht ist. Ein späterer Aufruf kann über die zwischengespeicherten Seiten fortschreiten. Keine aktive Produkt-Callsite für `MassiveClient.tickers()` gefunden; daher derzeit kein Kernblocker. Bei künftiger Verwendung einen expliziten partiellen Catalogstatus/Continuation vorsehen, nicht eine Teilmenge als vollständigen Broker-/Universekatalog interpretieren.

## Bewertung

Keine durch Keywechsel, gewöhnlichen Neustart, direkte Aufruferumgehung oder FMP-first nachgewiesene Umgehung des neuen Gates. Cache-/Transportstatus sind ausreichend getrennt, wenn die UI vorhandene Datenverfügbarkeit nicht als neue erfolgreiche API-Prüfung bezeichnet. Die zwei ersten Nachbesserungspunkte sollen vor der endgültigen Behauptung robuster Uhrsprung-/Pi-Ressourcenbehandlung geschlossen oder ausdrücklich begrenzt werden. Keine Freigabeaussage für realen Providerbetrieb aus diesem reinen Review.
