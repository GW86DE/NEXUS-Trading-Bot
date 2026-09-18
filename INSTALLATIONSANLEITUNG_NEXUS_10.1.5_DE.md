# NEXUS 10.1.5 – Installation und PEP-Klärung

Stand: 15. September 2026. Update für die vorhandene DEMO/Paper-Installation.

## Installieren

1. `NEXUS_10.1.5_Installieren.sh` auf den Raspberry Pi herunterladen.
2. Zusätzliche manuell gestartete Bot-/Sammlerfenster geordnet beenden. Bei einem noch laufenden Prozess zeigt der Installer dessen PID an.
3. Im Terminal starten:

```bash
bash "$HOME/Downloads/NEXUS_10.1.5_Installieren.sh"
```

Der Installer enthält das vollständige Programm. Er prüft das Paket, sichert die bisherige Installation, übernimmt Einstellungen und Daten einschließlich bestätigter SQLite-WAL-Inhalte, führt isolierte Tests aus und stellt die Dienste um. Bei Fehlern greift die vorhandene Wiederherstellung. Der Installer unterstützt weiterhin DEMO/Paper-Quellen; eine LIVE-Quelle wird abgewiesen.

Die reine Paketprüfung führt keine Installation oder Dienstaktion aus:

```bash
bash "$HOME/Downloads/NEXUS_10.1.5_Installieren.sh" --paket-pruefen
```

Nach dem Update die WebUI vollständig neu laden. Sie muss **10.1.5** anzeigen.

## PEP abschließen

Unter **Handel → Klärung → PEP → Abschlusskosten prüfen** erscheint die Abrechnung der Position **3597440106**, Trade **54**, im bisherigen eToro-DEMO-Konto. Falls der Eintrag inzwischen unter **Verkäufe** steht, ist er dort zu finden.

| Aus deinen Belegen | USD |
|---|---:|
| Barbestand am 14.09., 09:45:22 UTC | 84.669,33 |
| Barbestand am 14.09., 17:55:20 UTC | 99.774,64 |
| Zuwachs | 15.105,31 |
| 109 verkaufte Einheiten × 138,59 USD | 15.106,31 |
| Daraus abgeleitete Abschlusskosten | 1,00 |
| Bereits belegte Einstiegskosten | 1,00 |
| Ergebnis nach diesen Kosten | 242,16 |

**Die Zuordnung der Differenz setzt voraus, dass zwischen den beiden Barbeständen keine anderen Geldbewegungen lagen.** Prüfe Ein-/Auszahlungen, Dividenden, Zinsen, Finanzierungen und sonstige Buchungen. Wenn diese Voraussetzung stimmt, das entsprechende Kästchen aktivieren und **Abrechnung bestätigen** drücken. Die Oberfläche zeigt die Uhrzeiten in deiner Browserzeitzone an; die Tabelle oben verwendet UTC.

Die Bestätigung wird mit der Vorschau und dem vorherigen Datensatz gespeichert. Sie erhält die Kennzeichnung **vom Nutzer bestätigter Barbestandsabgleich**. Der Handelskern übernimmt das Ergebnis im folgenden regulären Abgleich zum tatsächlichen Verkaufstag. Die durch dieses ungeklärte Ergebnis verursachte Kaufsperre kann dann entfallen. Andere tatsächliche Risikosperren bleiben wirksam.

Der alte Sonntagsauftrag **380995258** zum Instrument **1043** bleibt getrennt nachvollziehbar. Sein fehlender Ausführungsbeleg wird nicht mit dem Montagsverkauf ersetzt. Im Zustand „Position geschlossen · Auftragsausgang ungeklärt“ ist dieser Altauftrag kein aktiver Mengenauftrag. Er muss für die Kostenklärung nicht gelöscht werden.

Wenn die Prüfung fehlende oder widersprüchliche Daten meldet, wird keine Kostenbuchung vorgenommen. In diesem Fall die aktuelle Diagnose exportieren; keine Datenbankzeilen löschen und keine Gebühren auf null setzen.

## Künftige Abschlüsse und Stornierungen

Die bestehenden vollständigen Broker-Kostenbelege werden automatisch verarbeitet. Zusätzlich speichert NEXUS passende USD-Barbestände aus ohnehin gelesenen Portfolioantworten. Änderungen des Barbestands oder Positionsbestands werden unmittelbar erfasst; bei unverändertem Bestand höchstens ein Punkt pro Minute. Die laufende Sammlung wird auf 45 Tage begrenzt. Bestätigte Abrechnungen behalten ihre vollständigen Belege.

Die Barbestandsprüfung unterstützt eindeutig zugeordnete, vollständig geschlossene Long-Positionen ohne konkurrierende Kontobewegung im Messintervall. Sie kann fehlende Broker-Kostenangaben nachvollziehbar ergänzen. Bei mehreren Positionen, Teilabschlüssen oder fehlenden Messpunkten ist eine automatische Zuordnung weiterhin nicht belegt.

Unter **Handel → Klärung** stehen außerdem **wartende Kaufaufträge und Stornierungen**. „Restauftrag stornieren“ stellt eine Anfrage an den Handelskern. Eine angenommene Stornoanfrage bleibt offen, bis der Broker ihren Ausgang bestätigt. Ausgeführte Teilmengen bleiben erhalten. Ein möglicherweise gesendeter Storno wird nach einem Neustart ausschließlich nachgelesen. Native SL/TP werden von dieser Funktion nicht storniert.

## Diagnose und Quellen

**Diagnose → Sofort / 30 Minuten** sowie lokale ZIP-Ablage und der gesonderte Telegram-Schalter bleiben verfügbar. Die Diagnose 1.6.0 ergänzt Barbestandsbelege, Nutzerabrechnungen, unklassifizierte `proceeds`-Beobachtungen und Stornovorgänge. Sie unterscheidet diese von offenen Positionen und ungeklärten Verkaufsergebnissen.

Die bestehende X-Kandidatensuche für PULSAR, das Zusammenspiel mit Reddit/FMP/News, die Anzeigen unter **Quellen & X** und die bisherigen Budgetgrenzen werden übernommen. Es entstehen durch dieses Update keine zusätzlichen kostenpflichtigen Quellenabfragen.
