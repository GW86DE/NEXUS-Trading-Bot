# NEXUS 9.8.2

Vollständiger Bot für eToro und OKX. Diese Version ergänzt die gemeinsame Auftragsverfolgung und die zusammengefasste Handelsseite aus 9.8.0 um die BTC-Preisbandkorrektur, strengere Verkaufsbelege, die TXN-Instrumentkorrektur und eine Tages-/Gesamtergebnisgrafik.

9.8.2 ersetzt die noch nicht installierte 9.8.1. Ein direktes Update vom laufenden 9.8.0 ist vorgesehen. Für dieses Update gilt ausschließlich **INSTALLATIONSANLEITUNG_NEXUS_9.8.2_DE.md**. Der automatische Updateweg verlangt einen bestätigten Demo-/Paper-Ausgangsstand und übernimmt den vorhandenen Zustand aus dem tatsächlichen Dienstordner. Keine zweite Botinstanz parallel starten und keine Laufzeitdaten löschen. Offline geprüft; die echte Broker- und Geräteabnahme bleibt vor einem Liveeinsatz erforderlich.

- **NEXUS_9.8.2_Pruefbericht.md:** Befunde, Korrekturen, Referenzbots und Prüfgrenzen.
- **TEST_REPORT_v9.8.2_NEXUS.txt:** Umfang der Offlineprüfungen.
- **CHANGELOG_v9.8.2_NEXUS.txt:** Änderungen dieser Version.

Die zusätzlichen Korrekturen betreffen manuelle Verkäufe, Reservierung vor Schutzstorno, Mengen-/Umgebungsprüfung im Ledger, Nachklärung von Orders und Ergebnissen, eindeutige News-Zuordnung sowie KI-Aus-Schalter, Zeitgrenzen und prozessübergreifendes Budget. MASSIVE und FMP bleiben erhalten; der offizielle Fed-RSS-Feed ergänzt Makronachrichten ohne API-Key. Fehlende historische Gebühren oder Wechselkurse bleiben offen und sperren neue Käufe im betroffenen Brokerrisikotopf, bis ein belastbarer Ergebnisbeleg vorliegt.
- **Nexus_SUI_Diagnose.py --symbol BTC:** ausschließlich lesender Export der lokalen BTC-Belege; der alte Skriptname bleibt aus Kompatibilitätsgründen.
- **Nexus_Externe_Verkaufe.py:** explizite, beleggestützte Nachpflege des historischen manuellen Fremdwährungsverkaufs. Standard ist nur Vorschau; keine Brokerorder.

Die Oberfläche enthält Übersicht, Handel, Analyse, Einstellungen und Logbuch. Unter Handel liegen offene Botpositionen, Verkäufe, ungeklärte Ausführungen sowie Guthaben und Verwaltung. Alle bisherigen Einstellungsfunktionen und der OKX-Kerzenchart bleiben vorhanden. Die Übersicht zeigt bestätigte realisierte Tages- und Gesamtergebnisse in Geld oder Prozent. Konten, Demo/Live und Währungen werden nicht zusammengerechnet; fehlende Ergebnisse sind keine Nullgewinne.

Die Versionsdatei benennt den Softwarestand. Sie ist kein Nachweis einer erfolgreichen Installation auf deinem Pi oder eines tatsächlichen Brokerfills. Historische Anleitungen und Prüfberichte bleiben als Entwicklungsnachweis im Paket; sie beschreiben frühere Versionen.
