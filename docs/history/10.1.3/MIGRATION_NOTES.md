# NEXUS 10.1.3 – Zustandsübernahme

Der vorhandene Installer sichert zuerst den bisherigen Ordner und verlangt gestoppte zusätzliche Schreiber. Entscheidungshistorie, Broker-Exit-Journal und X-Datenbank werden über SQLite-Backup übernommen. X-Reservierungen und bereits veranschlagte Kosten werden niemals durch ein Update zurückgesetzt. Token und Zugangsdaten bleiben privat.

Zusätzlich übernommen werden die originalen `risk_state_etoro.json.legacy-*.bak` und `etoro_risk_period_*.json`. Diese Unterlagen begründen die frühere Umstellung auf eine neue Risikoperiode; sie werden unverändert übernommen, auf reguläre Dateien/JSON-Objektformat geprüft und später anhand ihrer Prüfsummen/Kontozuordnung validiert. Keine allgemeine Übernahme beliebiger Sicherungen.

PEP wird erst bei einem frischen, vollständigen, zum selben Konto und derselben Umgebung gehörenden Depot- und Historienabgleich korrigiert. Der alte Journalzustand wird im Audit erhalten. Positionsabschluss und Orderausgang bleiben getrennt: keine erfundene Stornierung, keine erfundenen Fills und keine erneute Schließorder. Beide Datenbankprojektionen können nach einem unterbrochenen Update wiederholt werden.

Vorhandene SL-/TP-Werte und manuelle Beobachtungs-/Verkaufssperren bleiben erhalten. Ein neuer, unbekannter Nettobetrag bleibt Teil der aktuellen Ergebnisprüfung. Alte Verlustwerte, Tagesgrenzen und Cooldowns werden nicht zurückgesetzt.
