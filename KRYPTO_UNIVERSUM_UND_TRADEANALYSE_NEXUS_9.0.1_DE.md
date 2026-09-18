# Krypto-Universum und Trade-Analyse in NEXUS 9.0.1

## Das Universum: 20 fest + 30 dynamisch

Das Ziel sind 50 beobachtete OKX-Spotbasiswerte. „Im Universum“ bedeutet
nicht „wird gekauft“, sondern nur „wird regelmäßig geprüft“.

### Fester Kern

Der Kern besteht aus:

`BTC, ETH, SOL, XRP, ADA, DOGE, TRX, LINK, AVAX, LTC, DOT, ATOM, XLM, UNI, AAVE, BCH, HBAR, ICP, ETC, SUI`

Die Auswahl bevorzugt große, etablierte und liquide Netzwerke und verteilt
den Kern über Zahlungs-/Wertspeicher, Smart-Contract-Plattformen,
Interoperabilität und DeFi. Die Namen bleiben fest. NEXUS prüft aber bei jedem
Lauf, ob für den konkreten Benutzerbereich ein erlaubtes, aktuelles
EUR-/USDC-Spotpaar mit vollständigen Handelsregeln existiert. Scheitert das,
bleibt der Wert sichtbar und kaufgesperrt.

### Dynamische 30

Beim ersten vollständigen Lauf wählt NEXUS sofort bis zu 30 weitere Werte aus
dem aktuellen OKX-SPOT-Snapshot. Danach gilt:

1. höchstens eine Volumenmessung pro UTC-Tag;
2. Normalisierung des Quote-Notionals nach EUR;
3. pro Basiswert nur das liquideste vergleichbare EUR-/USDC-Paar;
4. monatliche Rangfolge nach dem Median der täglichen Werte der letzten
   30 Tage;
5. mindestens 20 belegte Tage und 30 qualifizierte Basiswerte;
6. bei unvollständigen Daten bleibt die alte Liste unverändert und erhält
   den Zustand STALE.

Stablecoins, gehebelte Tokens, nicht live geschaltete Produkte, zu junge
Listings und Instrumente ohne Tick-/Lot-/Mindestgrößen werden ausgeschlossen.
GPT und Nachrichten beeinflussen diese Rangfolge nicht.

## Gelockert wurde nur die Aufnahme in die Beobachtung

Damit Krypto nicht an aktienähnlich strengen Vorfiltern scheitert, reichen für
die Universumsaufnahme 250.000 Quote-Tagesvolumen und höchstens 1,2 % Spread.
Das ist ausdrücklich **keine Orderfreigabe**.

Vor einer tatsächlichen Order gelten weiterhin unter anderem:

- vollständige abgeschlossene Signal- und Bestätigungskerzen;
- 15 Minuten Anlaufsperre nach Prozessstart;
- frischer Ticker und beidseitiges Orderbuch;
- höchstens 0,6 % tatsächlicher Krypto-Spread;
- positiver Netto-Vorteil nach Gebühren und Slippage;
- Positions-, Tagesverlust-, Cash-, Risiko- und Doppelordergrenzen;
- gültige Tick-, Lot- und Mindestgrößen;
- bestehende Ownership-, Reconciliation- und Schutzorderregeln.

Neue dynamische Werte beginnen zusätzlich in BEOBACHTUNG und werden nicht
direkt nach dem Erstaufbau gekauft.

## Neue WebUI-Seite „Trades“

Die Seite ist rein lesend. Sie kann keine Order erzeugen oder LIVE aktivieren.

Sie zeigt:

- offene und geschlossene Trades;
- Einstieg, Ausstieg, Menge, Ergebnis, Gebühren und Ausstiegsgrund;
- den beim Einstieg gespeicherten Strategie-Modus;
- Trefferquote und kumuliertes realisiertes Nettoergebnis;
- unbekannte Werte ausdrücklich als unbekannt statt als `0,00`;
- für OKX eine Kerzengrafik mit grüner KAUF- und roter VERKAUF-Markierung;
- bei offenen OKX-Positionen bekannte Stop-Loss- und Take-Profit-Linien.

Die Kerzengrafik ruft nur den öffentlichen OKX-Endpunkt für historische
Kerzen ab. Laufende Kerzen (`confirm=0`) werden verworfen. Abhängig von der
Haltedauer wählt die Anzeige 5-Minuten-, 15-Minuten-, 1-Stunden-, 4-Stunden-
oder Tageskerzen, damit der vollständige Trade in höchstens 100 Kerzen passt.

Für eToro gibt es in NEXUS derzeit keinen gleichwertigen belegten historischen
Kerzenendpunkt. eToro-Trades bleiben in den Tabellen sichtbar; die Grafik
nennt diese Begrenzung, statt Kurse zu erraten oder eine fremde Quelle mit
abweichenden Zeitstempeln einzumischen.

## Darstellung nach Gerät

- PC: Kerzen- und Ergebnischart nebeneinander;
- Tablet: Charts untereinander, Kennzahlen in mehreren Spalten;
- Smartphone: kompakte Kennzahlen, horizontal scrollbar bleibende
  Detailtabellen und skalierende SVG-Charts.

Kauf und Verkauf stammen aus dem NEXUS-Trade-Ledger. Dadurch bleibt später
nachvollziehbar, in welchem Modus der Trade eröffnet wurde und warum er
geschlossen wurde.
