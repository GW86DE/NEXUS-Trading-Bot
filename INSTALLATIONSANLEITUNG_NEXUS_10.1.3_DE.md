# NEXUS 10.1.3 – Installation und Diagnose

Die neue Version enthält die PEP-Auftragskorrektur, eine getrennte Prüfung der alten und aktuellen Ergebnisperiode, aktuelle Ausstiegskostenprüfungen sowie optionale X-Recherche. Der Installer übernimmt die vorhandene DEMO/Paper-Installation und ihre Zustände. Er löst keine neue Brokerorder zur Reparatur aus.

1. `NEXUS_10.1.3_Installieren.sh` in den Download-Ordner des Raspberry Pi laden.
2. Laufende manuelle Sammler/Botfenster geordnet beenden. Der Installer nennt einen zusätzlichen Prozess, falls er die Übernahme verhindert. Kein pauschales Beenden aller Python-Prozesse.
3. Im Terminal starten:

```bash
bash "$HOME/Downloads/NEXUS_10.1.3_Installieren.sh"
```

Der Installer prüft das Paket, sichert den bisherigen Ordner, übernimmt Einstellungen und belegte Zustände, führt isolierte Tests aus und stellt beide Dienste auf den neuen Ordner um. Bei einem Fehler erfolgt die bestehende Wiederherstellung. LIVE-Quellen weist dieser Installer weiterhin ab. Das neue Programm selbst behält die bestehenden getrennten LIVE-Freigaben bei.

Nur das heruntergeladene Paket prüfen, ohne Installation:

```bash
bash "$HOME/Downloads/NEXUS_10.1.3_Installieren.sh" --paket-pruefen
```

**Diagnose über die WebUI:** oben im Menü **Diagnose**. Auswahl **Sofort** oder **30 Minuten**; ZIP wie bisher ablegen/herunterladen. Der getrennte Telegram-Schalter sendet die fertige ZIP über den bereits eingerichteten Zugang. Ein Versandfehler entfernt die lokale ZIP nicht.

**Diagnose über das Terminal, 30 Minuten:**

```bash
bash "$HOME/Georg/TradingBot_v10.1.3_NEXUS/NEXUS_Diagnose_Starten.sh" --minuten 30
```

**Sofortdiagnose:**

```bash
bash "$HOME/Georg/TradingBot_v10.1.3_NEXUS/NEXUS_Diagnose_Starten.sh" --sofort
```

Den tatsächlich ausgegebenen Ablagepfad verwenden. Nach der Installation muss die WebUI 10.1.3 anzeigen. Falls noch die vorherige Oberfläche erscheint, die Seite vollständig neu laden.

**X einrichten:** Menü **Quellen & X**. Eigenen X-Bearer-Token eingeben, bis zu zwölf Aktien und optional bis zu fünf bevorzugte Accounts auswählen, Monatsbudget höchstens 15 EUR. Die angezeigte Preisgrundlage im X-Portal prüfen und dort zusätzlich ein Ausgabenlimit setzen. Erst anschließend X aktivieren. Es werden keine Schlüssel mitgeliefert und keine bestehenden GPT-Budgets erhöht. X startet standardmäßig ausgeschaltet.

**PEP prüfen:** Unter Handel/Klärung muss nach frischem vollständigem Brokerabgleich die geschlossene Position vom ungeklärten Ausgang des alten Auftrags getrennt angezeigt werden. „Position geschlossen · Auftragsausgang ungeklärt“ ist ein belegter Zwischenzustand. Die ursprüngliche Schließorder wird nicht erneut gesendet. Eine noch fehlende Nettoabrechnung wird weiter angezeigt; dafür nicht „Käufe aktivieren“ als Ersatz für einen Gebührenbeleg verwenden.
