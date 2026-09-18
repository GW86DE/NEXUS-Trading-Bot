# NEXUS 9.8.3 – geprüfte Codekorrekturen, Broker-Abnahme offen

Dieser Stand baut auf 9.8.2 auf. Er enthält die anhand des DOGE-Vorfalls nachgewiesenen Codekorrekturen. **Er ist noch kein Nachweis, dass OKX die DOGE-Schutzorder akzeptiert oder ZAMA erfolgreich verkauft.** Eine abschließende Freigabe als vollständig reparierte Version steht deshalb aus.

## Was das neue Log beweist

- DOGE wurde am 09.09.2026 um 17:40:06,812 MESZ mit einer Order in sechs Fills gekauft: 533,522547 DOGE brutto, 1,8673289145 DOGE Gebühren, 531,6552180855 DOGE netto. Die USD-„Verkäufe“ in der Buchungsansicht sind die Zahlungen für diese Käufe.
- NEXUS meldet den Auftrag erst um 18:39:52 MESZ als geklärt. Die offene Nettoposition stimmt mit den Brokerbelegen überein.
- Von 18:39:49 bis 20:23:00 enthält das neue Log 94 Schutzablehnungen 51634 ohne erklärenden Text.
- ZAMA hat einen eigenen Fehler: Die Schutzstornierung für ZAMA-USDC wird um 19:34:18 und 19:56:59 mit 51001 abgelehnt. Der geplante Verkauf wurde nicht gesendet. Dieser Fehler ist noch offen.

## Enthaltene Korrekturen

1. Leeres oder nur aus Leerzeichen bestehendes `sMsg` unterdrückt die übergeordnete Brokerfehlermeldung nicht mehr. Schutzablehnungen protokollieren ausgewählte Anfrage- und Antwortfelder unter `OKX-Schutzantwort`, ohne Zugangsdaten oder Signaturen.
2. Schutzorders erhalten ausdrücklich die tatsächliche Positionswährung als `tradeQuoteCcy`, geprüft gegen die Instrumentregeln. Das ist für diesen DOGE-Kauf USD. Die offizielle [OKX-Referenz](https://my.okx.com/docs-v5/en/#order-book-trading-algo-trading-post-place-algo-order) führt diesen Parameter für SPOT-Algo-Orders auf.
3. Die Wiederaufnahme eines Kaufs ermittelt zunächst nur die Belege. Der Engine-Pfad verbucht die Position vor dem ersten Schutzauftrag. Die bisherigen doppelten Versuche aus beiden Pfaden entfallen.
4. Ein fehlender ausführbarer Verkaufspreis überspringt den Schutzabgleich nicht mehr. Preisabhängige Ausstiege benötigen weiter einen brauchbaren Kurs.
5. Eindeutig abgelehnte, unveränderte Schutzaufträge haben fünf Minuten Abstand zwischen erneuten Sendungen. Rücklesen und Client-Stop laufen weiter.
6. Ab diesem Prüfstand gesendete, unbestätigte Schutzaufträge werden vor dem POST dauerhaft reserviert. Eine verlorene Antwort, ein Neustart oder geänderte Parameter erlauben keinen zweiten POST für dieselbe unklare Schutzkennung. Eine frische positive Brokerbestätigung klärt die Reservierung. Bereits vor dieser Version verlorene Antworten werden dadurch nicht rückwirkend rekonstruiert.
7. Scheitert das Rücklesen nach einer angenommenen Schutzorder, bleibt der Zustand unbestätigt und die Algo-ID erhalten. Das wird nicht als eindeutige Ablehnung behandelt.
8. Ein nicht bestätigter Schutz wird auch dann ausdrücklich und mit begrenzter Wiederholung gemeldet, wenn der Adapter lediglich ein negatives Ergebnis zurückgibt. Fehlende Fill- und Orderbelege werden mit dem betroffenen Abfrageschritt protokolliert.

Die zusätzliche Reservierungstabelle liegt in der vorhandenen Entscheidungsdatenbank. Konten und DEMO/LIVE bleiben getrennt. Eine fehlende Kontoidentität oder nicht dauerhaft schreibbare Reservierung verhindert das Senden eines neuen Schutzauftrags.

## Prüfung

| Prüfung | Ergebnis |
| --- | --- |
| Python 3.12.14, x86_64, isolierter Volltest | 1.749 Tests und 223 Untertests bestanden |
| Python 3.11.16, x86_64, isolierter Volltest | 1.749 Tests und 223 Untertests bestanden |
| Selbsttest, Syntax, Shell-Syntax, dateibasierter Pi-Preflight | Bestanden in beiden Läufen |
| Netzwerk und Benutzerzustand | Tests ausschließlich in isolierten Kopien; Netzaufrufe gesperrt |
| Echte DOGE-Fills | Beträge, Gebühren, Nettomenge, USD-Zuordnung und sechs eindeutige IDs geprüft |
| Fehlerfälle | Fehlende Belege, Wiederaufnahme, Preisfehler, falsche Währung, Antwortverlust, Neustart, parallele Reservierung, Schreibfehler und Geheimnisfilter geprüft |

Eine DeprecationWarning aus Starlette/AnyIO bleibt in beiden Testumgebungen bestehen. Sie ist kein fehlgeschlagener Test. Zwei historische Tests verlangten aufgrund der alten API-Annahme das Weglassen von `tradeQuoteCcy`; sie prüfen jetzt die nachgewiesene Positionswährung. Der frühere Adaptertest zur Schutzplatzierung wurde auf die neue Reihenfolge umgestellt und durch einen Engine-Test zur dauerhaften Weitergabe der Algo-ID ergänzt. Ein alter Testadapter erhielt eine ausdrückliche Testkontokennung.

Nicht geprüft: ein neuer Schutz-POST auf dem tatsächlichen OKX-Konto, ein erfolgreicher ZAMA-Verkauf, ARM64-Hardware und Python 3.13. Es wurden keine Brokerorders und keine Änderungen an den laufenden Raspberry-Pi-Diensten ausgelöst.

## Was vor der endgültigen Freigabe noch fehlt

**DOGE 51634:** Die genaue Ablehnungsursache ist aus den vorhandenen leeren Fehlermeldungen nicht bestimmbar. Die neuen Diagnosefelder können bei einer regulären DEMO-Schutzanfrage die fehlenden Angaben liefern. Als Erfolg zählt ausschließlich eine anschließende Brokerabfrage mit passender Algo-ID, Instrumentkennung, Währung, Menge und Schutzpreisen. Eine grüne allgemeine Bereitschaftsanzeige genügt nicht.

**Verspätete Kaufzuordnung:** Der Übernahmezeitpunkt ist bekannt. Die Ursache der zuvor fehlenden Antworten bleibt offen. Bei einem erneuten Fall sind die neuen Meldungen zu Orderstatus, Fills und Archiv relevant. Unvollständige Belege dürfen keine erneute Kauforder auslösen.

**ZAMA 51001:** Erforderlich sind aktuelle Brokerdetails zur vorhandenen ZAMA-Schutzorder und die gültigen Kontoinstrumentregeln. Die Kennung wird nicht pauschal von USDC auf USD umgeschrieben; die fehlgeschlagene Stornoprüfung bleibt bestehen.

Für diese Punkte werden keine Positionen, Fill-Register oder Datenbanken gelöscht oder auf alte Stände zurückgesetzt. Die alten Installationsanleitungen im Paket belegen keine Freigabe dieses Prüfstands. Die laufende Installation wurde durch die Erstellung dieses Pakets nicht verändert.

## Nächster Schritt im bestehenden DEMO-Betrieb

Das Paket ist für die beobachtete DEMO-Abnahme vorbereitet. Nach dem Entpacken in einen eigenen neuen Ordner `TradingBot_v9.8.3_NEXUS` unter `/home/georg/Georg` erfolgt die Übernahme über den vorhandenen phasenweisen Updater:

```bash
bash "$HOME/Georg/TradingBot_v9.8.3_NEXUS/Nexus_Update.sh"
```

Der Updater prüft den Ausgangsstand, sichert und übernimmt die vorhandenen Einstellungen und Zustände und prüft die neue Installation. Kein zweiter Core und keine manuelle Neuerfassung der bereits gekauften DOGE-Position. Bei einem Fehler den tatsächlichen Updater-Ausgang auswerten; keine alte Zustandsdatei zurückspielen.

Bei der nächsten regulären Schutzprüfung sind die Zeilen `OKX-Schutzantwort`, die zugehörige Schutzmeldung und der anschließend angezeigte Schutzstatus entscheidend. Dafür keinen zusätzlichen Kauf oder Verkauf auslösen. Als nächste Rückmeldung genügt das danach erzeugte `trading_bot.log` aus dem neuen Dienstordner; Zugangsdaten werden nicht benötigt. Wenn die Schutzorder dann angenommen und korrekt rückgelesen wird, ist dieser Abnahmepunkt belegt. Der separate ZAMA-Fall bleibt bis zu passenden Brokerbelegen offen.
