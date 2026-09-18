# Korrekturen in NEXUS 9.0.3

## Grundregel: Der Broker ist die Wahrheit

Ein lokaler Eintrag allein darf keine offene Position erzeugen. NEXUS trennt
jetzt ausdrücklich zwischen **Order ausgeführt**, **Position aktuell offen**
und **Position bereits geschlossen**.

Bei eToro wird eine gespeicherte Kaufausführung nur dann wieder in die aktive
Verwaltung übernommen, wenn dieselbe `positionId` im aktuellen Depot steht.
Taucht sie stattdessen in der vollständig gelesenen Trade-Historie auf, wird
sie als bereits geschlossen markiert. Ist weder das eine noch das andere
beweisbar, bleibt sie unter **Klärung nötig** und löst keinen automatischen
Verkauf aus. Symbol und Menge werden niemals als alleiniger Identitätsbeweis
verwendet.

Bei OKX wird die archivierte Fill-Historie seitenweise gelesen. Dadurch können
auch mehrere Verkäufe desselben Einstiegs und Teilverkäufe nach einem Neustart
rekonstruiert werden. Bereits verbuchte Mengen werden abgezogen. Coin-Guthaben
ohne bestätigte Botposition bleibt `RESIDUAL_EXPOSURE`: sichtbar und für neue
Krypto-Einstiege sperrend, aber nicht automatisch verkauft.

## Drei verständliche Trade-Zustände

| Anzeige | Bedeutung | Verhalten |
|---|---|---|
| Offen bestätigt | Aktuelle Brokerposition und lokaler Trade stimmen überein. | Verwaltung mit der beim Kauf gespeicherten Strategie. |
| Klärung nötig | Beweiskette unvollständig, Restbestand oder anderes Konto. | Kein automatischer Verkauf und kein erfundenes Ergebnis. |
| Geschlossen | Brokerverkauf oder administrativ bestätigtes Ende. | Ergebnis nur, wenn Preis und Gebühren wirklich bekannt sind. |

## Aktienkäufe nach einem Neustart

Neue Aktienkäufe sind nach jedem Start mindestens 15 Minuten blockiert. Danach
werden sie nur freigegeben, wenn alle erforderlichen Prüfungen erfolgreich
waren: Brokerverbindung, Instrumente, Universum, Kontowert, Kursdaten,
abgeschlossene 15-Minuten-Signalkerze, beschreibbares Protokoll und keine
ungeklärte eToro-Order. Diese Sperre betrifft ausschließlich neue Käufe.
Verkäufe und brokerseitiger Schutz laufen weiter.

Der aktuelle Status steht auf dem Dashboard unter
**Aktien-Handelsbereitschaft**.

## Telegram ohne Doppelmeldungen

NEXUS verwendet weiterhin zwei Schutzebenen:

1. Ein Queue-Eintrag wird atomar von genau einem Sender beansprucht.
2. Inhaltlich identische Nachrichten werden 25 Sekunden lang unterdrückt.

Eine pauschale Verzögerung jeder Meldung ist deshalb nicht nötig und würde
kritische Warnungen nur langsamer machen. eToro-Bestätigungen erhalten stabile
Ereignis-IDs und werden erst nach dem Positionsnachweis gesendet.

## Responsive WebUI

Ab einer Breite von 900 Pixeln und darunter ersetzt ein Dropdown die zu breite
Menüleiste. Es enthält Dashboard, Positionen, Trades, Universum, Einstellungen
und Logbuch. Auf größeren Bildschirmen bleibt die bisherige Navigation. Unter
600 Pixeln ordnen sich Kopfzeile, Abmeldung und Menü in zwei Zeilen an;
Bedienelemente sind mindestens 44 Pixel hoch. Die bestehenden Seiten und
Kacheln werden nicht fachlich verändert.

## Datenbankexport

Der vorhandene Telegram-Befehl für den Datenbankexport verwendet die SQLite-
Backup-API. Damit werden auch noch im WAL liegende Änderungen konsistent in
eine Exportdatei übernommen. Eine laufende `decision_history.sqlite` sollte
nicht mehr als rohe Einzeldatei kopiert werden.
