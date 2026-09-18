# NEXUS 9.0: Freqtrade-SampleStrategy-Modus

## Was dieser Modus ist

Der Modus `FREQTRADE_SAMPLE` bildet die Handelsentscheidungen der offiziellen
Freqtrade-`SampleStrategy` für OKX-Spot als eigenständige NEXUS-Strategie nach.
Er ist Long-only, arbeitet nur mit abgeschlossenen 5-Minuten-Kerzen und nutzt
keine News, GPT-Zweitmeinung oder KI-Aufmerksamkeitsreihenfolge.
Die hyperoptimierbaren Schwellen der Vorlage werden nicht automatisch
optimiert; NEXUS verwendet reproduzierbar deren offizielle Standardwerte
30 und 70.

Die SampleStrategy ist laut Freqtrade eine Vorlage zur Inspiration, keine
erprobte Gewinnstrategie. Der Modus ist deshalb für den kontrollierten
Demo-Vergleich gedacht und keine Gewinnzusage.

## Kerzen und Indikatoren

| Eigenschaft | Feste Regel |
|---|---|
| Markt | OKX EEA Spot, Long-only |
| Zeitfenster | 5 Minuten |
| Kerzen | nur abgeschlossene Kerzen |
| Auswertung | einmal je neuer Kerze |
| Startphase | mindestens 200 Kerzen |
| RSI | TA-Lib-kompatibel, Periode 14 |
| TEMA | TA-Lib-kompatibel, Periode 9 |
| Bollinger-Mitte | SMA(20) des typischen Preises `(Hoch+Tief+Schluss)/3` |

NEXUS wertet im SampleStrategy-Modus deterministisch alle aktuell durch die
harten OKX-Instrument-, Alters-, Liquiditäts-, Spread- und Produktfilter
zugelassenen Paare aus. GPT und News werden auch nicht indirekt zur Sortierung
oder Auswahl herangezogen.

## Kaufentscheidung

Ein Long-Einstieg entsteht nur, wenn auf derselben abgeschlossenen 5m-Kerze
alle Bedingungen wahr sind:

1. RSI kreuzt die Schwelle 30 von unten nach oben;
2. TEMA liegt auf oder unter der Bollinger-Mitte;
3. TEMA steigt gegenüber der vorherigen Kerze;
4. Volumen ist größer als null.

Danach gelten weiterhin die NEXUS-Sicherheitsregeln: handelbares Spotpaar,
Datenfrische, Spread, Kosten/Edge, Orderbuch, verfügbare Mittel, Cashreserve,
Positionslimit, Risikotopf, Doppelorder-Schutz und Broker-Reconciliation.
Diese Regeln dürfen einen SampleStrategy-Kauf blockieren, aber niemals selbst
ein SampleStrategy-Signal erzeugen.

## Stop, Take-Profit und Verkaufsentscheidung

| Zeitpunkt | Regel |
|---|---|
| sofort nach Fill | Stop-Loss 10 % unter dem Einstieg |
| sofort nach Fill | brokerseitiges Sicherheitsziel für 4 % Nettogewinn nach geschätzten Gebühren |
| 0–29 Minuten | Verkauf ab 4 % Nettogewinn nach Gebühren |
| 30–59 Minuten | Verkauf ab 2 % Nettogewinn nach Gebühren |
| ab 60 Minuten | Verkauf ab 1 % Nettogewinn nach Gebühren |
| jederzeit bei neuer 5m-Kerze | Verkaufssignal, wenn RSI 70 aufwärts kreuzt, TEMA über der Bollinger-Mitte liegt und fällt sowie Volumen > 0 ist |

Die zeitabhängigen 2-%- und 1-%-ROI-Ausstiege sowie das Indikator-Ausstiegssignal
werden vom laufenden NEXUS-Prozess verwaltet. Fällt der Pi oder die Verbindung
aus, bleiben der sofort bei OKX hinterlegte −10-%-Stop und das +4-%-Sicherheitsziel
als Broker-Schutz bestehen.

Die offizielle Vorlage nennt Limitorders sowie keinen Stop auf der Börse.
NEXUS übernimmt diese Orderverwaltung absichtlich nicht vollständig: Einstieg
und clientseitiger Ausstieg verwenden preisbegrenzte FOK-Spot-Orders auf Basis
eines frischen Full-size-Orderbuch-VWAP. Anschließend wird der Broker-OCO-Schutz
gesetzt. Damit werden nicht zwei konkurrierende Order- und Ownership-Systeme
vermischt. Die Signal-, ROI- und Stopregeln entsprechen der SampleStrategy; die
Ausführung bleibt die NEXUS-Sicherheitsschicht.

Stop und anfängliches +4-%-Nettogewinnziel werden nach dem echten
Durchschnittsfill neu berechnet. Für Kursprüfung, Schutz und Anzeige gilt immer
dieselbe persistierte OKX-`instId`; ein Kurs aus einem EUR-, USD-, USDC- oder
USDT-Schwestermarkt darf nicht verwendet werden.

## Offene Positionen beim Moduswechsel

Bei jedem Kauf werden unveränderlich gespeichert:

- `entry_strategy_mode`
- Strategiename und Strategieversion
- Parameter-Hash
- vollständiger Parameter-Snapshot
- `decision_id`, Orders, Fills und Ausstiegsgrund

Der globale Schalter wirkt ausschließlich auf neue Einstiege. Beispiel:

- BTC wurde in `NEXUS_STANDARD` gekauft und bleibt dort verwaltet;
- danach wird `FREQTRADE_SAMPLE` aktiviert;
- ein neuer ETH-Kauf wird nach SampleStrategy verwaltet;
- BTC behält weiterhin seine Standardstrategie, ETH ihre SampleStrategy.

Stimmt bei einer SampleStrategy-Position Version oder Hash nicht mehr exakt,
wird nicht geraten: NEXUS setzt sie auf `BEOBACHTEN`, belässt den brokerseitigen
Schutz und sendet eine kritische Meldung. Eine Alt-/Fremdposition ohne sicheren
Strategienachweis wird ebenfalls nur beobachtet.

## Umschalten ohne Neustart

In der WebUI unter **Einstellungen → OKX-Kryptostrategie**:

- **NEXUS Standard** – normales bisheriges Krypto-Handeln;
- **Freqtrade SampleStrategy** – Aktivierung verlangt den Text
  `FREQTRADE AKTIVIEREN`;
- **Krypto pausieren** – keine neuen OKX-Käufe; eToro und bestehender
  OKX-Schutz/Positionsausstieg laufen weiter.

Telegram:

```text
/crypto status
/crypto standard
/crypto freqtrade
/cryptopause
```

`/crypto freqtrade` verlangt eine sitzungsgebundene Bestätigung. `/cryptopause`
wirkt sofort als Notbremse für neue Krypto-Einstiege und lässt eToro weiterlaufen.
Jeder Wechsel wird persistent protokolliert und per Telegram bestätigt.

## Backtest

CSV-Spalten: `timestamp` (oder `date`/`datetime`/`time`), `open`, `high`,
`low`, `close`, `volume`.

```bash
./.venv/bin/python freqtrade_sample_backtest.py kerzen_5m.csv \
  --capital 10000 --stake-pct 0.10 --fee-pct 0.001 --slippage-pct 0.0005
```

Der Backtest verwendet dieselben Indikator- und Signalregeln. Ein Signal der
abgeschlossenen Kerze wird frühestens am Open der nächsten Kerze ausgeführt,
damit kein Zukunftswissen verwendet wird. Wenn innerhalb derselben OHLC-Kerze
Stop und ROI erreichbar gewesen wären, wird konservativ zuerst der Stop
angenommen. Gebühren und Slippage sind Parameter und werden ausgewiesen.

Quellenstand der fachlichen Regeln: offizielle Freqtrade-
[SampleStrategy](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/templates/sample_strategy.py)
und [Strategiedokumentation](https://www.freqtrade.io/en/stable/strategy-customization/),
abgeglichen am 30.08.2026. Die NEXUS-Implementierung ist eigenständig erstellt;
es wurde kein Freqtrade-Quellcode in NEXUS kopiert.
