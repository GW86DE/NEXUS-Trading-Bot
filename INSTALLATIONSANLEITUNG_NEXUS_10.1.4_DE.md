# NEXUS 10.1.4 – Installation und Diagnose

Diese Version ergänzt die eToro-Belegverarbeitung und die automatische X-Kandidatensuche. PEP ist als geschlossene Position belegt; der Kostenkonflikt wird separat ausgewiesen. Die bestehende Anforderung vollständig belegter Ergebnisse bleibt wirksam. Die Installation allein garantiert daher keine Aufhebung der PEP-Kaufsperre.

1. `NEXUS_10.1.4_Installieren.sh` in den Download-Ordner des Raspberry Pi laden.
2. Zusätzliche manuelle Bot-/Sammlerfenster geordnet beenden. Der Installer benennt störende Prozesse; keine pauschalen Python-Abschüsse.
3. Im Terminal ausführen:

```bash
bash "$HOME/Downloads/NEXUS_10.1.4_Installieren.sh"
```

Der eigenständige Installer enthält das vollständige ZIP. Er prüft Prüfsummen, sichert die vorhandene Installation, übernimmt ihre Einstellungen und Zustände, führt isolierte Tests aus und stellt die Dienste um. Die bisherige Wiederherstellung bei Fehlern bleibt erhalten. Dieser Installer übernimmt DEMO/Paper-Quellen; LIVE-Quellen werden weiterhin abgewiesen. Kein Brokerauftrag wird zur Reparatur gesendet.

Paket ohne Installation prüfen:

```bash
bash "$HOME/Downloads/NEXUS_10.1.4_Installieren.sh" --paket-pruefen
```

Nach dem Update muss die WebUI **10.1.4** anzeigen. Die Seite gegebenenfalls vollständig neu laden.

## X einrichten und prüfen

Unter **Quellen & X** den vorhandenen oder eigenen X-Bearer-Token verwenden, die zu diesem Zugang passende Preisgrundlage bestätigen und den Sammler aktivieren. Das Monatslimit ist höchstens 15 EUR. Bestehende Einstellungen werden übernommen; eine Neuinstallation startet mit ausgeschaltetem X. Im X-Anbieterportal zusätzlich das eigene Ausgabenlimit setzen. Die lokale Anzeige ist eine konservative Kostenreservierung, keine Rechnung.

Eine Aktienliste ist nicht erforderlich. Automatisch werden politische Konten, Finanzbehörden und eine offene Unternehmenssuche abgefragt. Gefundene Cashtags durchlaufen die FMP-Aktienprüfung und anschließend PULSAR. Regierungsposts ohne Ticker bleiben Themenhinweise. Die feste Stichprobe erfasst nicht sämtliche Posts.

Die Seite zeigt Verbindung, empfangene/verarbeitete Daten, Kandidaten, tatsächlich recherchierte Kandidaten, beobachtete Konten, Messlücken und den Beginn der aktuellen Vergleichsgruppe. **PULSAR** zeigt die Quellenrollen und die noch fehlenden Belege je Kandidat. X ist keine eigenständige Order- oder Krisenfreigabe.

## PEP prüfen

Unter **Handel** wird das Broker-Historienergebnis neben dem NEXUS-Abrechnungsergebnis ausgewiesen. Bei Position 3597440106 sind 244,16 USD Brokerergebnis und 0 USD History-Gebühren vorhanden; der originale v2-Einstiegsbeleg nennt jedoch 1 USD Kosten. `BROKER_HISTORY_COST_SCOPE_CONFLICT` zeigt diesen Unterschied. Ohne passenden tatsächlichen Abschlusskostenbeleg bleibt das vollständig abgerechnete Netto unbekannt. Die alte Sonntagsorder wird nicht als Montags-TP umgedeutet.

## Diagnose

Im Menü **Diagnose**: **Sofort** oder **30 Minuten** wählen. Die lokale ZIP-Ablage und der getrennte Telegram-Schalter bleiben verfügbar. Ein Versandfehler löscht die ZIP nicht. Die Diagnose liest gespeicherte Belege und erzeugt keine zusätzlichen X-/GPT-/Brokerabrufe.

30 Minuten über das Terminal:

```bash
bash "$HOME/Georg/TradingBot_v10.1.4_NEXUS/NEXUS_Diagnose_Starten.sh" --minuten 30
```

Sofort:

```bash
bash "$HOME/Georg/TradingBot_v10.1.4_NEXUS/NEXUS_Diagnose_Starten.sh" --sofort
```

Alternativ kann `NEXUS_10.1.4_Diagnose_Starten.sh` aus dem Download-Ordner eigenständig gestartet werden. Den tatsächlich ausgegebenen Ablagepfad verwenden; Standard: `~/Downloads/NEXUS_Diagnosen`. Für eine technische Gegenprüfung nach der Installation eignet sich ein 30-Minuten-Bericht. Dieser kann keine Monatsbasis oder Handelsprofitabilität beweisen.
