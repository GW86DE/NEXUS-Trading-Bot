# Installation und Abnahme – NEXUS 9.5.1

NEXUS 9.5.1 aktualisiert den eToro-Zustands-, Kauf- und Verkaufspfad. Das
Update uebernimmt keine Zugangsdaten aus dem Paket und aktiviert weder eToro
LIVE noch OKX LIVE automatisch.

## 1. Vor dem Update sichern

Den laufenden Dienst zuerst sauber stoppen und mindestens diese lokalen
Dateien sichern, soweit vorhanden:

```text
settings.json
.env beziehungsweise verschluesselte Zugangsdaten
decision_history.sqlite einschließlich -wal/-shm, falls vorhanden
position_state.json
etoro_reconciliation.json
bot_order_registry.json
broker_exit_journal.sqlite
fill_progress.json
```

SQLite-Dateien nicht einzeln kopieren, solange NEXUS noch schreibt. Entweder
den Dienst vorher stoppen oder ein SQLite-Backup verwenden.

## 2. Release installieren

Das ZIP in ein neues Verzeichnis entpacken und dort ausfuehren:

```bash
chmod +x Pi_Installieren.sh
./Pi_Installieren.sh
./.venv/bin/python settings_migration.py --auto
```

Danach die zuvor gesicherten Laufzeitdaten entsprechend der bestehenden
Installation uebernehmen. Keine IDs in den JSON-Dateien manuell umschreiben;
9.5.1 migriert nur exakte Brokerketten automatisch und laesst Konflikte
sichtbar gesperrt.

Bereits in einer alten Version terminal gespeicherte eToro-CLOSED-Saetze ohne
Kontofingerabdruck bleiben als kontolose Auditspur erhalten. Sie werden keinem
aktuellen Konto zugeschrieben und blockieren dieses Konto nicht erneut. Das
gilt nicht fuer offene oder ungeklaerte Altauftraege: Diese bleiben gesperrt,
bis eToro eine exakte Order-/`positionId`-Kette fuer dasselbe Konto liefert.

## 3. Offline-Pruefung

```bash
./.venv/bin/python self_test.py
./.venv/bin/python volltest.py
```

Beide Pruefungen muessen ohne Fehler enden. Die Versionsanzeige muss
`9.5.1-NEXUS` lauten.

## 4. Verpflichtende eToro-DEMO-Abnahme

LIVE erst nach einer vollstaendigen DEMO-Abnahme wieder freigeben:

1. eToro-DEMO verbinden und die Brokerdiagnose ausfuehren.
2. Pruefen, dass der Reconciliation-Worker als laufend angezeigt wird und
   einen aktuellen erfolgreichen Tick besitzt.
3. Einen kleinen, liquiden DEMO-Testkauf ausloesen.
4. Im Log die Kette `decisionId -> referenceId -> orderId -> positionId`
   kontrollieren.
5. Pruefen, dass genau ein Positionsdatensatz entsteht und der Schutz fuer
   genau diese positionId bestaetigt wird.
6. Einen vollstaendigen DEMO-Verkauf ausfuehren und kontrollieren, dass Trade,
   Position, Reconciliation und Orderregister gemeinsam CLOSED/FILLED werden.
7. Einen Teilverkauf pruefen: Die Restposition muss unter derselben positionId
   OPEN bleiben und darf nicht terminalisiert werden.
8. Erst danach einen zweiten Kauf zulassen. Die WebUI darf keinen
   widerspruechlichen Text wie "keine ungeklaerte Order" bei rotem Status
   anzeigen.
9. Den Bot zwischen einem weiteren kleinen Fill und dessen naechstem
   Abgleich einmal kontrolliert neu starten. Derselbe Fill darf danach weder
   einen zweiten Trade noch eine zweite Exitbuchung erzeugen.

## 5. Altdaten wie ADBE

Beim ersten erfolgreichen Depot-/History-Abgleich verbindet 9.5.1 einen
bereits geschlossenen 9.5-Altdatensatz nur, wenn Konto, DEMO/LIVE,
`positionId` und Entry-`orderId` exakt passen. Preise, Zeiten und realisiertes
Ergebnis bleiben unveraendert. Eine faelschlich als Exit-ID gespeicherte
Entry-ID wird entfernt.

Bleibt `LEDGER_BACKFILL_PENDING` oder `MANUAL_REVIEW` sichtbar, keine JSON-
Datei von Hand freigeben. Stattdessen Log, Reconciliation-Datei und die
betroffene Broker-History sichern.

`CLOSED_ACCOUNTING_PENDING` bedeutet dagegen: Der Broker-Close ist bereits
bewiesen, aber mindestens eine lokale, wiederholbare Geldbuchung fehlt noch.
In diesem Zustand ist die Kaufsperre beabsichtigt. Sie verschwindet automatisch
erst nach dem erfolgreichen exakten Ledger-Replay; weder Order noch Verkauf
werden dafuer erneut an eToro gesendet.

## 6. LIVE-Freigabe

LIVE nur bewusst ueber den vorhandenen Arming-Prozess aktivieren. Vorher in
der WebUI kontrollieren:

- Brokerkonto und DEMO/LIVE stimmen;
- Worker und letzter synchroner Depotabgleich sind aktuell;
- keine blockierende eToro-Ausfuehrung ist offen;
- keine Ledgermigration wartet;
- alle vorhandenen Botpositionen besitzen exakte positionIds und bestaetigten
  Schutz.

Die mitgelieferten automatisierten Tests verwenden echte Strukturbeispiele
und den anonymisierten ADBE-Laufzeitfall, aber keine produktive eToro-
Netzwerkverbindung. Die DEMO-Abnahme ist daher verpflichtend und keine reine
Formalitaet.
