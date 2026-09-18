# NEXUS 10.6.0 – Installation auf dem Raspberry Pi 5

**Build:** `10.6.0-RISK-LEVELS-BALANCE-RESTORE` · enthält vollständig 10.4.0 und 10.5.0 · Diagnose 1.9.0

Diese Version bringt drei Dinge: **Einsatz je Trade wird einstellbar**, getrennt
für OKX und eToro und ohne Neustart. Eine fehlende Bestandsmeldung wird erst
**bestätigt**, bevor sie gebucht wird. Und eine Bestandslücke hat endlich einen
**Rückweg**, wenn der Bestand nachweislich wieder da ist.

Da 10.4.0 und 10.5.0 auf dem Pi nie installiert wurden, sind deren Inhalte
vollständig enthalten: ECharts-Kerzenansicht, Existenzrisiko, Scan-Übersicht,
PULSAR-Quellen ohne Reddit-Schlüssel, Vorwärtsmessung und der aufbereitete
Diagnosebericht in der WebUI.

## Phase 1 – Paketprüfung

```bash
bash "$HOME/Downloads/NEXUS_10.6.0_Installieren.sh" --paket-pruefen
```

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.6.0_Installieren.sh"
```

Der Installer entpackt, führt den vollständigen Volltest aus und startet die
Dienste nur, wenn alle Tests bestehen. Bestehende Daten werden nicht umgebucht.

## Phase 3 – Einsatz je Trade wählen

WebUI → Einstellungen → **Einsatz je Trade · getrennt für OKX und eToro**.

Je Broker stehen drei Stufen mit ihren Zahlen und einer kurzen Beschreibung
bereit. Die Wahl gilt **ab dem nächsten Prüfzyklus ohne Neustart** und nur für
neue Einstiege; offene Positionen behalten ihre Größe.

| Broker | vorsichtig | mittel | erhöht |
|---|---|---|---|
| OKX | 0,3 % / max. 5 % | 0,6 % / max. 10 % | 1,2 % / max. 20 % |
| eToro | 0,5 % / max. 3 % | 1,0 % / max. 5 % | 2,0 % / max. 15 % |

Die erste Zahl ist das Risiko je Trade, die zweite der Positionsdeckel. Es gilt
immer die kleinere der beiden Grenzen.

**Wichtig zur Bezugsgröße.** Bei OKX rechnet der Bot mit dem freien Guthaben
**der Währung, in der gekauft wird**. Ein Kauf im EUR-Paar nimmt nur das
EUR-Cash als Basis, nicht das Gesamtkonto. Genau daher kamen die 20-Euro-Käufe
vom 18.09.2026: 649,86 EUR freies EUR-Guthaben bei 172.214,65 EUR Kontowert.
Bei eToro ist die Basis der Kontowert.

Solange keine Stufe gewählt ist, ändert sich **nichts**; es gelten die
bisherigen Werte. Die höchste Stufe verlangt die exakte Eingabe
`EINSATZ ERHOEHEN`.

Das Risikoprofil bleibt daneben bestehen. Es setzt weiterhin Stop-Abstände,
Signalschwellen, Tagesverlustgrenze sowie Trade- und Positionslimits. Einsatz
und Positionsdeckel kommen ab jetzt aus der Stufe.

## Phase 4 – Abnahme

1. **Einstellungen**: Der Abschnitt zeigt je Broker „Wirksam: …" mit den
   tatsächlich gerechneten Prozentwerten und der Bezugsgröße. Ohne eigene Wahl
   steht dort „Vorgabe (keine Stufe gewählt)".
2. **Stufe testen**: Eine Stufe wählen, dann im Systemprotokoll oder in der
   Entscheidungsansicht die nächste Kaufprüfung ansehen. Die Begründung nennt
   die wirksame Stufe, zum Beispiel „Einsatz Mittel (0,60 % Risiko, max. 10 %)".
   Ein Neustart ist nicht nötig.
3. **OKX-Positionen vom 18.09.**: XRP, BTC und ETH waren wegen zweier
   unvollständiger Guthaben-Schnappschüsse gesperrt. Nach dem Update lösen sie
   sich selbst auf, sobald der Bestand in zwei Messungen mit mindestens 120
   Sekunden Abstand wieder vollständig gemessen wurde. Erwartete Telegram-
   Meldung: „Bestand ist in zwei bestätigten Messungen wieder vollständig da …".
   Danach zeigt die Positionsansicht die drei Positionen wieder, und OKX-Käufe
   sind nicht mehr gesperrt.
4. **Diagnose**: Sofortdiagnose starten, „Bericht öffnen". `risiko_stufen.json`
   liegt in der ZIP, sodass die wirksame Stufe später nachvollziehbar ist.

## Wenn die Sperre bleibt

Zeigt die Bereitschaft weiter „OKX-Buchungs-/Bestandsbelege offen", dann ist der
Bestand entweder noch nicht zweimal vollständig gemessen worden oder es liegt
ein anderer Beleg vor. Der Diagnosebericht nennt unter „Blocker" die Art:
`BALANCE_REDUCTION` und `BROKER_STATE_UNKNOWN` lösen sich selbst auf,
`LEDGER_ANCHOR_MISSING` verlangt weiterhin eine belegbasierte Reparatur.

## Optionen (unverändert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
