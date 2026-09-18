# N10-D03 und D10: Massive und Quellenstatus

## Dateien / Eigentum

- `massive_service.py` neu: kleiner gemeinsamer SQLite-Dienst, keine zusätzlichen Prozesse/Server.
- `massive_api.py`: alle HTTP-Pfade und der Verbindungstest benutzen denselben Dienst.
- `news_sources.py`: FMP-News bevorzugen, Massive ergänzen, echte HTTP-Fehler von lokaler Abrufpause trennen; historische FMP-Aliasse kennzeichnen. Nasdaq-Diagnosecallback mit Root abgestimmt.
- `tests/test_v101_massive.py` neu; ein vorhandener Verbindungstest in `tests/test_v812_nexus.py` an korrekte zweistufige Free-Prüfung angepasst.

## Umgesetztes Verhalten

Die sechs Anfragen aus der Diagnose können nicht mehr als unkontrollierte Spitze gesendet werden. Vor HTTP wird atomar reserviert: vier Versuche je rollenden 60 Sekunden, mindestens 15 Sekunden Abstand. Beides verwendet eine über Prozesse geteilte monotone Uhr und Boot-ID. Alle Keys/Clients teilen die konservative Quote; Inhalte/Endpunktberechtigungen sind nach Key-Hash und Base-URL getrennt.

Kein Sleep und keine Netzwerk-Warteschlange im Tradingzyklus. Lokale SQLite-Sperren warten höchstens 150 ms; bei kaputtem/gesperrtem Zustand wird nur Massive lokal pausiert. Gleichzeitige identische Anfragen teilen den persistenten Cache; ein noch laufender Abruf meldet `MASSIVE_INFLIGHT`, statt ihn parallel zu wiederholen. Reservierte Versuche bleiben bei Timeout/Prozessabbruch gezählt. Verwaiste READ-Reservierungen besitzen eine begrenzte monotone Lease. Requests besitzen begrenzte Timeouts, 4 MiB Streaminggrenze und eine eigene Body-Zeitgrenze; Responses werden auch bei Fehlern geschlossen.

HTTP 429 setzt eine gemeinsame persistente Pause. Retry-After wird als Sekunden oder HTTP-Datum berücksichtigt (mindestens 60 s); ein Systemzeitsprung darf die vom Anbieter verlangte verbleibende Pause nicht verkürzen. Ein Boot-/großer Zeitsprung verursacht zusätzlich eine konservative 60-s-Pause. HTTP 402/403 pausiert den betroffenen Endpunkt, 401 den betroffenen Zugang. Ein kostenpflichtiger Tarif wird nicht unterstellt.

Beim erstmaligen Upgrade mit vorhandenem `news_source_status.json`, aber noch ohne gemeinsame Massive-DB, wird eine Minute pausiert: frühere per-Client-Aufrufe sind nicht rekonstruierbar. Die Pause wird einmal festgeschrieben und durch Wiederholungen/Neustarts nicht verschoben.

News-Cache 30 min, Tagesaggregate 1 h, Referenzdaten 24 h. Die häufigen News-Limits 1/5/10/20 teilen einen 20er-Abruf; jeder Caller erhält nur seine angeforderte Teilmenge. `news(..., cache_only=True)` erzeugt nie HTTP; ein Miss trägt `MASSIVE_CACHE_MISS`. Allgemeine News nutzen bei mindestens drei deduplizierten, frischen FMP-Meldungen mit Text und URL nur vorhandene Massive-Caches; sonst wird gezielt ergänzt. PULSAR integriert dieselbe Schnittstelle im separaten FMP/Worker-Arbeitspaket.

Der Verbindungstest prüft Nachrichten zuerst. Die Referenzprüfung kann zunächst **noch nicht geprüft – Free-Abrufpause** sein; nach Ablauf nutzt ein zweiter Test die gecachten News und prüft Referenzdaten. Ein bekannter verweigerter Endpunkt bleibt als verweigert sichtbar. Ein ungeprüfter Endpunkt wird nicht grün angezeigt.

## Status / Messgrenzen

`massive_service.readonly_status(api_key)` ist die WebUI-/Diagnoseschnittstelle: keine DB-Anlage, keine Schreiboperation, keine Netzabfrage. Liefert `minute_used`, konservatives `minute_limit=4`, `provider_minute_limit=5`, UTC-Tagesversuche, Cachetreffer, letzte echte HTTP-Antwort, nächste Freigabe, `observed_since`. Fehlende/kaputte DB meldet **unbekannt**, keine erfundene Null.

Die Zähler messen NEXUS-Aufrufe seit Einführung des Dienstes; ältere und externe Abrufe sind nicht messbar. Ein Cachetreffer ist keine neue HTTP-Antwort; eine lokale Pacingpause überschreibt den letzten HTTP-Erfolg nicht. Ein tatsächlich empfangener HTTP 429 wird als neuer HTTP-Fehler protokolliert. Die Übernahme in GPT-Pakete belegt erst die separate PULSAR-Verwendungsprovenienz, nicht der Providerzähler allein.

`FMP News` bleibt mit seinem ursprünglichen HTTP-402-Befund als ausdrücklich historischer Alias erhalten. Endpunkt unbekannt bleibt `UNKNOWN_LEGACY_NEWS`; keine Vermischung mit aktuellem Stable-News- oder Symbolsuche-Status. Auch alte fehlgeschlagene, nicht mehr aktuelle Prüfungen werden als veraltet gekennzeichnet. Bestehende Rohwerte bleiben erhalten.

## Persistenz / Integration

Neue additive Datei `massive_service.sqlite`; keine Handels-/Risikodaten werden migriert oder gelöscht. Beim Versionswechsel müssen SQLite-Datei und konsistenter WAL-Zustand vom allgemeinen Installer erhalten bleiben (Root integriert). Diagnosewerkzeug soll die DB regulär exportieren. Kein API-Key wird darin gespeichert. Datenretention für kleine Request-Zeilen: 30 Tage; Cachezeilen nach Ablauf begrenzt bereinigt.

## Tests / Belege

Finaler eingefrorener Lauf: **131/131 bestanden**, davon 37 neue Massive-/Quellenstatus-Verhaltenstests. Belege: `implementation/test_runs/v101-massive-frozen_5c07e8e3/{result.json,junit.xml,output.txt}`. Keine Netzereignisse. Geprüft wurden `test_v812_nexus.py`, `test_v512_fmp_behavior.py`, `test_v989_fmp.py` und `test_v101_massive.py` gemeinsam.

Abgedeckt: originales Sechser-Burst-Replay; exakte 60-s-Grenze; 12 konkurrierende Reservierungen über vier echte Prozesse; gleicher Request in zwei Threads; Cache nach Neustart; verschiedene Keys; Timeout ohne Rückbuchung; Crash nach Reservierung; 429 mit Sekunden/Datum/ungültigem Header; längerer Server-Cooldown trotz Vorwärtssprung; kleine Uhrkorrektur; rückwärts korrigierte Uhr; einmaliger Upgrade-Puffer; Endpunktverweigerung ohne globales Abschalten; Tageslimit; korruptes SQLite / beschädigte Clock-Metadaten / EXCLUSIVE-DB-Lock; 4-MiB-Limit und langsamer Antwortbody; read-only UI ohne Dateienanlage; Rohgeheimnisfreiheit; FMP-Lücken-/Duplikatpolitik; alte Aliasse; aktueller echter 429 versus lokale Pause.

## Grenzen

- Keine echten Provider-Anfragen ausgeführt; keine tatsächliche FMP-/Massive-Tarifänderung.
- Laufender Pi samt anderem externem API-Verbrauch muss nach Installation erneut beobachtet werden.
- Mehrseitiger Tickerkatalog bleibt bei noch nicht fälliger Folgeseite ausdrücklich unvollständig/pausiert. Bereits geladene Seiten sind gecacht; ein Folgelauf kann fortsetzen. Eine Teilmenge wird nicht als vollständiger Delisting-Katalog zurückgegeben.
- Netzwerk-DNS-Auflösung hängt weiterhin vom Betriebssystem ab; Streaming-/Socket-Zeitgrenzen begrenzen den normalen HTTP-Abruf, die optionale Worker-Isolation gehört zum bestehenden Laufzeitmodell.
