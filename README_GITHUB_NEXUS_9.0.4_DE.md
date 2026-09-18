# TradingBot NEXUS 9.0.4 – verständliche Projektbeschreibung

NEXUS ist ein regelbasierter Demo-/Live-Tradingbot für eToro-Aktien und
OKX-Spot-Kryptowährungen. Er trennt beide Broker technisch, schützt jede
Order mit einem nachvollziehbaren Order- und Positionsdatensatz und zeigt
Entscheidungen, Trades, Guthaben und Systemmeldungen in einer deutschen,
responsiven WebUI. Standardmäßig läuft der Bot im Demo-/Paper-Modus.

## Was GPT macht – und was nicht

GPT ist eine optionale Zusatzkomponente im normalen NEXUS-Betrieb. Es kann
Markt- oder Nachrichteninformationen zusammenfassen, Risiken klassifizieren
und die Reihenfolge von Kandidaten priorisieren. GPT darf jedoch weder eine
Order selbst senden noch einen harten Daten-, Risiko-, Kosten- oder
Brokerfilter überstimmen. Bei technischen Fehlern wird kein Kauf aus einer
GPT-Antwort erzeugt. Im separaten **Freqtrade-Sample-Modus** sind GPT, News
und KI-Priorisierung vollständig ausgeschaltet: Dort zählen ausschließlich
die fest programmierten Kerzen- und ROI-Regeln.

## Die beiden Krypto-Handelsmodi

* **NEXUS Standard:** dynamisches und festes Kernuniversum, normale
  Sicherheits- und Risikoregeln sowie – wenn aktiviert – GPT/News als
  zusätzliche Priorisierung.
* **Freqtrade SampleStrategy:** eine Clean-Room-Umsetzung der offiziellen
  Freqtrade-SampleStrategy. Sie handelt nur OKX-Spot, nur Long, auf
  abgeschlossenen 5-Minuten-Kerzen und ohne GPT oder News. Der Modus lässt
  sich in der WebUI ohne Neustart oder per Telegram umschalten. Der Wechsel
  betrifft nur neue Einstiege; eine offene Position behält unveränderlich
  ihren Einstiegsmodus, ihre Strategieversion und ihren Parameter-Hash.
  **Krypto pausiert** alle neuen Krypto-Einstiege, bestehende bestätigte
  Positionen werden weiterhin verwaltet.

## Kerzen, Einstieg und Backtest im Sample-Modus

Vor der ersten Auswertung müssen 200 vollständige 5-Minuten-Kerzen vorliegen.
Die laufende Kerze wird nicht verwendet; dadurch entstehen keine Signale aus
noch veränderlichen Werten. Aus den Kerzen werden Wilder-RSI(14), TEMA(9),
die SMA20-Bollinger-Mitte auf dem typischen Preis und ATR(14) berechnet.

Ein Einstiegssignal entsteht nur, wenn der RSI die 30 von unten nach oben
kreuzt, die TEMA höchstens auf der Bollinger-Mitte liegt, gegenüber der
Vorperiode steigt und das Volumen positiv ist. Das Signal der abgeschlossenen
Kerze wird – wie im Freqtrade-Ablauf – am nächsten Kerzenbeginn ausgeführt.

Der integrierte Backtest verwendet exakt dieselbe Signalberechnung. Er
simuliert Gebühren und Slippage, führt Signale am Folge-Open aus und prüft
Stop/ROI innerhalb der Folgekerze. Bei unklarer Reihenfolge einer 5-Minuten-
Kerze wird der Stop vor dem Gewinnziel angenommen (konservative Annahme).
Das Ergebnis ist eine historische Simulation, keine Gewinnzusage.

## Verkauf und Schutz

Ein Verkauf erfolgt im Sample-Modus durch das RSI-70-Aufwärtssignal, wenn die
TEMA über der Bollinger-Mitte liegt und fällt, oder durch die zeitabhängige
Minimal-ROI. Die unveränderten Sample-Werte sind: mindestens 4 % ab Einstieg,
2 % nach 30 Minuten, 1 % nach 60 Minuten sowie ein maximaler Stop-Loss von
10 %. Gebühren werden in der ROI-Berechnung berücksichtigt. NEXUS übernimmt
die Signallogik, aber nicht blind Freqtrades Exchange-Code: Die getestete
OKX-Ausführung bleibt Market-Spot mit sofortiger brokerseitiger OCO-Sicherung
(Stop-Loss und Take-Profit), einschließlich Fill-, Gebühren- und Statusprüfung.

## Harte Prüfungen vor jedem echten Einstieg

Unabhängig vom Modus müssen Daten und abgeschlossene Kerzen frisch sein, das
Instrument und ein handelbares OKX-Spot-Quote-Paar feststehen, Preis, Bid/Ask,
Spread, Liquidität und Mindestgröße passen, Cash-Reserve und Risikolimit
ausreichen, keine offene oder ungeklärte Doppel-/Restposition existieren und
keine aktive Order denselben Einstieg bereits bearbeitet. Stop, Ziel,
Gebühren und erwarteter Netto-Vorteil werden vor dem Absenden geprüft. Eine
fehlende oder widersprüchliche Brokerbestätigung führt zu **RECONCILING** bzw.
Beobachtung – niemals zu einer erfundenen Position oder einem zweiten Kauf.

## Was NEXUS von Freqtrade übernommen hat

Übernommen wurden Architekturprinzipien, kein GPL-Quellcode: getrennte Orders,
Fills, Trades und Positionen; stabile IDs und Einstiegssignale bzw.
Ausstiegsgründe; abgeschlossene Kerzen; reproduzierbare Backtests; Gebühren- und
ROI-Auswertung sowie Broker-Abgleich. Jede Entscheidung wird mit Modus,
Strategieversion, verwendeten Kerzen, Parametern und Ergebnis protokolliert,
damit ein späterer Fehler oder Trade eindeutig analysiert werden kann.

## Bedienung und Sicherheit

Dashboard, Positionen, Trades, Universum, Einstellungen und Logbuch sind über
die Desktopnavigation oder ein mobiles Dropdown erreichbar. Telegram meldet
wichtige Ereignisse, sammelt identische Meldungen 30 Sekunden und verhindert
Dopplungen. Telegram kann in der WebUI ohne Neustart aktiviert oder deaktiviert
werden. Vor Livebetrieb müssen API-Rechte, Demo-Status, Limits und Logs selbst
geprüft werden; der Bot ist experimentelle Software und keine Finanzberatung.

