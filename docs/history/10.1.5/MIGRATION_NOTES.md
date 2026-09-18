# Aktueller Stand: NEXUS 10.1.5

Siehe `INSTALLATIONSANLEITUNG_NEXUS_10.1.5_DE.md` und `docs/NEXUS_10.1.5_Aenderungen.md`. Die folgenden Abschnitte dokumentieren frühere Versionen.

# NEXUS 10.1.4 – Zustandsübernahme

Zusätzlich zu den bestehenden Journalen werden `etoro_stream_inbox.sqlite` und `etoro_http_budget.sqlite` über konsistentes SQLite-Backup übernommen, einschließlich bestätigter WAL-Daten. X-Budgets, Reservierungen, Originalbelege, Risikoperioden, Positionen und Einstellungen werden nicht zurückgesetzt. Private Zugangsdaten bleiben in der vorhandenen Installation.

Die neue History-Belegtabelle und sechs additive Broker-Ergebnisfelder werden beim Öffnen des Ledgers angelegt. Wiederholte Verarbeitung derselben Position erzeugt keine zweite Verkaufsmenge. Ein Historienabschluss, die Stornierung eines älteren Auftrags und die tatsächliche Kostenabrechnung bleiben getrennte Ereignisse.

Native Abschlussereignisse werden künftig vor Verarbeitung kontogebunden abgelegt. Nicht zugeordnete Ereignisse überleben einen Neustart. Bereits in der Vergangenheit verlorene WebSocket-Ereignisse lassen sich durch diese Migration nicht rekonstruieren. Eine neue, belegte Schließorder-ID kann den v2-Kostenabruf ermöglichen; fehlt sie weiterhin, bleibt die genaue Ursache sichtbar.

X startet ohne Nutzervorgabe mit automatischer Kandidatensuche. Vorhandene optionale Beobachtungssymbole bleiben erhalten. Automatische Counts-Plätze erfordern vorher einen frischen typisierten FMP-Einzelaktienbeleg. Die gemeinsame Vergleichsgruppe ist auf zwölf Aktien begrenzt und wird für 35 Tage stabil gehalten. Änderungen erzeugen eine neue gemeinsame Normierung, während unveränderte Einzelreihen erhalten bleiben. PULSAR-Prüfungen verwenden die neue Quellenrevision, um ältere abgeleitete Urteile nicht ungeprüft zu übernehmen; die bestehenden GPT-Budgets gelten weiter.

Bestehende SL-/TP-Werte, manuelle Sperren, Verlustgrenzen und Strategieeinstellungen bleiben erhalten. Keine neue 1-%-Verkaufsregel, keine pauschale Freigabe ungeklärter Ergebnisse, kein erneutes Absenden einer alten Schließorder.
