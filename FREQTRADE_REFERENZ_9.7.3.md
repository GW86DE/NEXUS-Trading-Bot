# Referenz und Grenzen der 9.7.3

Geprüfte Referenz: Freqtrade 2026.8, Commit `9f10e357a93c1dcf10c2a2b367659214d89c073e`, Datei `freqtrade/persistence/trade_model.py`, insbesondere `Order` und `recalc_trade_from_orders()`.

Die Referenz hält Orders mit einer festen Trade-Beziehung vor und leitet Mengen und Ergebnisse daraus ab. Übernommen wird das Prinzip, einen bereits belegten wirtschaftlichen Vorgang zu erkennen und Anzeigezustände aus persistenten Belegen erneut abzuleiten. Ein Warnstatus ist kein zusätzlicher Trade und keine neue Ausführung.

NEXUS 9.7.3 prüft für die eToro-Buchungslücke die vorhandene Order-/Positions-Lineage und den finanziellen Ledgerabschluss, statt den Warnungseintrag pauschal zu löschen. Die WebUI berechnet gruppierte Anzeigen aus den gespeicherten Ledgerzeilen. Wiederholtes Lesen oder Auflösen erzeugt keine zusätzliche Gewinnbuchung. Unbekannte Gebühren bleiben unbekannt.

NEXUS betreibt zwei Broker und mehrere Umgebungen. Deshalb ist seine Zuordnung ausdrücklich strenger als ein Symbol- oder Walletsaldo-Vergleich: Broker, gespeicherte DEMO/LIVE-Umgebung, Account, Position/Instrument und Entry-Order werden getrennt behandelt. Manuelle Konto-Assets werden nicht allein wegen eines passenden Symbols zu Bottrades.

Es wurde kein Freqtrade-Quellcode in diese Korrektur kopiert. Die bisherige NEXUS-Strategieauswahl, der optionale SampleStrategy-Modus und die Brokeradapter bleiben bestehen. Diese Version behauptet nicht, sämtliche historisch gewachsenen Positions- und Risikospeicher bereits auf einen vollständig neuen, transaktionalen Orderkern umgestellt zu haben. Gegenstand ist die belegbasierte Auflösung, die klare Anzeige und deren getestete Grenzen.

Referenzlink: https://github.com/freqtrade/freqtrade/blob/9f10e357a93c1dcf10c2a2b367659214d89c073e/freqtrade/persistence/trade_model.py
