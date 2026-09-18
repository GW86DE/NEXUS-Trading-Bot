# NEXUS 10.1.10 – Pruefbericht

**Build:** `10.1.10-BROKER-STABILITY-AND-DYNAMIC-INTELLIGENCE`
**Stand:** 16. September 2026
**Pruefumgebung der Paketierung:** Windows 11 x86_64, Python 3.14.7 (ohne pandas/fastapi/requests-Laufzeitabhaengigkeiten).

## Ergebnis der Offline-Pruefung bei Paketierung

- **Syntax:** Alle 523 Python-Dateien des Pakets per AST geparst, 0 Fehler; keine UTF-8-BOMs im Baum.
- **Gezielte 10.1.10-Regressionen: 27 bestanden, 1 bedingt uebersprungen** (der pandas-abhaengige Routing-Verhaltenstest laeuft im Pi-Volltest):
  - FX-robuste Equity-Bremse (4 Tests): reiner FX-Drawdown haelt nicht, nativer Handelsverlust haelt auch bei FX-Maskierung, Fallback ohne native Reihe unveraendert, Waehrungswechsel startet die native Tagesbasis neu.
  - Marktkursbewertung: Hoechstkurs-Bewertung nachweislich entfernt.
  - Waehrungsfreigabe: Konfigurationsfilter, Bestaetigungsphrase, generisches Routing (+ Verhaltenstest der USD-Lane, pandas-gebunden).
  - Entscheidungs-Retention: loescht nur unverknuepfte alte Entscheidungen und Heartbeats; Order-verknuepfte und frische Zeilen bleiben; keine Belegtabelle betroffen.
  - Diagnose-Sicherung: WAL-Einschritt-Backup und erhoehtes Zeitfenster verankert.
  - Ergebnisabgleich-Meldung: genau eine Benachrichtigung je neuem unbekannten Verkaufsergebnis, idempotent.
  - X-Konten-Registry (8 Tests): Schwellen, Spam-/OTHER-Ausschluss, Tombstones, TTL-Verfall, Aktivierung nur mit Identitaetsvermerk, Kapazitaet 5, REMOVED nie erneut, Query-Einbindung nur im Finanz-Slot mit Kappung.
  - Quellen-Fixes (4 Tests): GDELT-Timeout konfiguriert und genutzt, nie kuerzer als global; Tradestie-Hauptdomain; kein TLS-Bypass im Baum.
  - Backtest-Oberflaeche (3 Tests): Routen, Template im Hausstil, Navigations-/CSS-Verdrahtung.
- **Browser-Abnahme:** Backtest-Seite, "Mehr"-Navigationsmenue und Quellen-Registry in Laptop- (1440 px), Tablet- und iPhone-Breite (375 px) gerendert und bedient (Start-Panel, Live-Protokoll, Download-/Berichtsknoepfe, Aufklappmenue, Registry-Aktionen).
- **Externe Messungen (16.09.2026):** GDELT HTTP 200 nach 13,5 s (Begruendung des 30-s-Timeouts); `api.tradestie.com` TLS "certificate has expired", `tradestie.com` HTTP 200 in 0,5 s mit gueltigem Zertifikat (Begruendung des Endpunktwechsels ohne TLS-Bypass).

## Ausdrueckliche Grenzen dieses Pruefstands

- Der **vollstaendige Volltest** (alle Testdateien inkl. pandas-/fastapi-abhaengiger Suiten, Self-Test, compileall, Pi-Preflight, Shell-Syntax) konnte in der Windows-Paketierumgebung nicht ausgefuehrt werden. Er laeuft wie bei jedem Release **waehrend der Pi-Installation** und muss bestehen, bevor Dienste starten; ein Fehlschlag bricht das Update ab und laesst den alten Stand unangetastet.
- Es wurde keine reale Brokerorder, keine Testorder, kein kostenpflichtiger GPT-/X-Aufruf und keine Telegram-Nachricht erzeugt.
- Reale ARM64-Installation, 30-Minuten-Diagnose 1.8.1 und das Verhalten der neuen nativen Equity-Reihe im echten Konto sind Laufzeitabnahmen nach Installation.
- Die USD/USDG-Freigabe wurde nur vertraglich geprueft; ob OKX nach der Parallelphase (ab 23.09.2026) EUR/USDC-Maerkte weiterfuehrt, ist eine Brokerentscheidung.

## Erste Pi-Volltestrunde (16.09.2026, Paket-Revision 1)

Der erste Installationsversuch auf dem realen Raspberry Pi 5 fuehrte den Volltest korrekt aus: **3.149 Tests bestanden, 9 schlugen fehl, das Update brach vertragsgemaess ab und liess 10.1.9 unveraendert lauffaehig.** Alle 9 Fehler waren Paketierungs-/Testvertragsfehler der Revision 1, keine Fehler der neuen Fachlogik:

1. Der historische Einstieg `NEXUS_10_1_9_Diagnose.py` verweigerte im Modul-Import-Test (Versionsbindung an 10.1.9). Behoben: alte versionsgebundene Diagnose-Einstiege sind nicht mehr Teil des Folgepakets.
2. `risk_basis_review` verlangte die fuenf neuen nativen Equity-Felder auch von unveraenderlich archivierten Checkpoints (3 Tests, `RISK_CHECKPOINT_FINANCIAL_FIELDS_MISSING`). Behoben: die Felder sind als optional-mit-Default klassifiziert und werden fuer den Zustandsvergleich normalisiert; abweichende WIRTSCHAFTLICHE Werte lehnen weiterhin ab (nachgeprueft), negative native Drawdowns sind als vorzeichenbehaftet zugelassen.
3. Der Settings-Vertragstest kannte die zwei neuen Waehrungsschalter nicht. Behoben: Erwartungsliste um `okx.allow_usd`/`okx.allow_usdg` fortgeschrieben.
4. `okx_entry_routing.routes()` griff hart auf `broker.quote_ccy` zu; die bestehenden Routing-Testdoubles besitzen das Attribut nicht (4 Tests). Behoben: toleranter `getattr`-Zugriff mit EUR-Standard; das Routingverhalten der Alt-Tests (EUR bevorzugt, Alternative nur bei belegtem Kostenvorteil) ist unveraendert.

Revision 2 dieses Pakets enthaelt genau diese vier Korrekturen; die 27 gezielten 10.1.10-Regressionen wurden danach erneut ausgefuehrt (gruen). Der massgebliche Nachweis bleibt der Pi-Volltest der Installation.

Maschinenlesbarer Nachweis: `validation/NEXUS_10.1.10_TEST_EVIDENCE.json`.
