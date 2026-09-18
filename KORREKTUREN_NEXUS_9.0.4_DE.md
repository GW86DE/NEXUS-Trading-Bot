# Korrekturen in NEXUS 9.0.4

## eToro: Orderausführung ist noch keine offene Position

Der MSFT-Fall entstand, weil eine beim Order-Lookup gemeldete Fill-Menge als
Trade übernommen werden konnte, obwohl eToro keine aktuelle MSFT-Position
führte. NEXUS verlangt jetzt dieselbe, exakte `positionId` im aktuellen Depot.
Symbol, Stückzahl oder eine Fill-Menge reichen nicht als Beweis.

| Brokerbefund | Lokaler Zustand | Verhalten |
|---|---|---|
| Exakte `positionId` aktuell offen | bestätigt offen | Strategie verwaltet die Position |
| Order gefüllt, Position noch nicht bestätigt | Abgleich läuft | kein Doppel-Kauf, kein Phantom-Verkauf |
| `positionId` nur in geschlossener Historie | geschlossen | nicht erneut als offen einspielen |
| Weder aktuell noch historisch beweisbar | ungeklärt | kein Trade und keine automatische Aktion |

## OKX: verfügbare Handelswährung aus Brokerdaten

Ein OKX-Instrument besitzt neben seinem Symbol eine Liste erlaubter
Handelswährungen (`tradeQuoteCcyList`). NEXUS schneidet diese Information nicht
mehr auf den sichtbaren Symbolzusatz zusammen. Bei einem Unified-USD-Paar wird
die Handelswährung aus der Schnittmenge von Brokerfreigabe und verfügbarem
Kontoguthaben gewählt: EUR, USD, USDC oder USDG.

Coins wie SOL, ETH oder XRP sind weiterhin Bestände und niemals Cash. Der Bot
verkauft sie nicht automatisch, um einen anderen Coin zu finanzieren.

## SOL: zwei verschiedene Dinge sauber trennen

Das auf dem OKX-Konto vorhandene SOL ist ein echter Kontobestand. Der alte
offene SOL-Datensatz war dagegen ein lokaler Ledger-Eintrag ohne vollständige
Beweiskette. NEXUS 9.0.4 behandelt beides getrennt:

1. Der erste vollständige OKX-Abgleich markiert den unbelegbaren Alt-Eintrag
   nur als ausstehende Bereinigung.
2. Erst ein zweiter vollständiger, übereinstimmender Abgleich schließt genau
   diesen Ledger-Eintrag administrativ.
3. Menge, Verkaufskurs und Gewinn werden nicht erfunden.
4. Das SOL-Guthaben bleibt vollständig auf OKX und wird danach als externer
   Bestand angezeigt.

Diese Sonderbereinigung gilt ausschließlich für alte, eindeutig unbelegbare
`LEGACY_UNLINKED`-Einträge. Ein neuer, verknüpfter SOL-Trade wird niemals auf
diese Weise geschlossen.

## Telegram ohne Mehrfachmeldungen

Nicht nur der bereits versendete Text wird geprüft. Gleiche Nachrichten werden
jetzt schon vor dem ersten Versand 30 Sekunden zusammengeführt. Dadurch können
parallel laufende Zyklen keine zwei identischen Meldungen mehr in die Queue
stellen. Schaltflächen- und Callback-Antworten werden weiterhin sofort
gesendet.

Telegram lässt sich unter **Einstellungen** sofort an- oder ausschalten. Token
und Chat-ID müssen vor dem Einschalten vorhanden sein. Ein Neustart des Bots
oder der WebUI ist nicht erforderlich.

## Sicherheitsgrenzen

- Keine automatische Umdeutung externer Bestände zu Botpositionen.
- Keine Zuordnung nur anhand Symbol und Menge.
- Keine erfundenen Stop-/Zielwerte, Verkaufskurse oder Gewinne.
- Keine Wiederholung einer bereits angenommenen Order im Abgleich.
- Keine echten Orders oder Telegram-Nachrichten während der Offline-Tests.
