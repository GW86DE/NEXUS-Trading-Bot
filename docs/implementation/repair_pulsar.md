# PULSAR: Aktienauswahl vor Marktanreicherung und GPT

Arbeitsstand auf Basis NEXUS 10.1.0; kein eigenstaendiges Release.

## D19 – Ursache und Korrektur

Bisher schnitt `Worker.cycle()` die nach Aufmerksamkeit geordneten Symbole auf
fuenf ab, bevor das FMP-Unternehmensprofil geladen wurde. Daher konnten SPY/QQQ
zwei Aktienplaetze und zwei Plaetze in der gemeinsamen GPT-Vorpruefung belegen,
obwohl der spaetere Fonds-/ETF-Block ihre Nominierung ausschloss.

`pulsar/candidate_selection.py` prueft jetzt den Instrumenttyp vor dem
Fuenferschnitt und vor Kurs-, Nachrichten-, SEC-/Massive- und GPT-Recherche.

- Ein frischer, dem angefragten Symbol exakt zugeordneter FMP-Profilbeleg mit
  `isEtf=false` und `isFund=false` bestaetigt den Einzelaktientyp. Die Flags
  muessen echte boolesche Werte sein. Fehlende Flags, Strings oder numerische
  Ersatzwerte bestaetigen ihn nicht.
- Ein passendes Profil mit mindestens einem expliziten `true` schliesst ETF
  oder Fonds aus den Aktienplaetzen aus. Profilfehler werden als `UNKNOWN`
  ausgewiesen und behaupten keinen ETF-Befund.
- Bereits vorhandene FMP-Profile werden aus dem gemeinsamen, nach API-Kontext
  getrennten Cache verwendet. Abrufdatum und Ablaufdatum bleiben erhalten,
  maximales Klassifikationsalter ist 24 Stunden. Es gibt keine dauerhafte
  Ticker-Typtabelle ohne Ablaufdatum. Ein nach Ablauf neu gelieferter anderer
  Typ ersetzt die alte Klassifikation; Symbol-/CIK-/ISIN-/CUSIP-Angaben bleiben
  im Auswahlbeleg nachvollziehbar. Dies ist noch keine Broker-Identitaetspruefung.
- Fehlende Profile werden nur fuer einen noch freien Platz aufgeloest.
  Hoechstens 20 unterschiedliche gueltige Symbole und hoechstens zehn fehlende
  Profilabfragen werden pro Lauf behandelt. Sind fuenf Aktien gefunden, enden
  die Profilabfragen. Bereits bekannte Aktien koennen auch nach Erreichen der
  zehn Profilversuche nachruecken, solange die Inspektionsgrenze nicht erreicht ist.
- Das bestehende Limit von 20 neuen Anreicherungskandidaten pro Tag sowie die
  gemeinsamen FMP-Konto-, Tarif- und Abrufgrenzen bleiben aktiv. Die zehn
  Profilversuche sind eine Obergrenze, kein neues Kontingent und keine Pflicht,
  bei ausreichend bekannten Aktien zusaetzliche Abrufe auszufuehren. Bei einem
  Fehler verhindert ein datierter, nach FMP-Kontext getrennter Pausenbeleg
  unmittelbare Wiederholungen fuer denselben Ticker.
- Das ausgewaehlte Profil samt originalem Quellenbeleg wird an `gather_market`
  weitergereicht. Die Klassifikation erzeugt keinen zweiten Profilabruf.
- Die bestehende Aufmerksamkeitsreihenfolge und ihr Entdeckungsplatz fuer einen
  Kandidaten ohne frueheren Zaehler bleiben erhalten. Es werden weder Wachstum,
  Baseline, Primaerquellen noch eine Handelsfreigabe aus dem Instrumenttyp abgeleitet.
- Ein vor dem Update gespeicherter GPT-Wiederholungsauftrag ohne Kennzeichnung
  der neuen Auswahl wird nicht mehr versandt: Er koennte noch SPY/QQQ enthalten.

Eine volle Liste mit fuenf Aktien ist bei fehlenden Typbelegen oder erreichten
Grenzen nicht garantiert. Verbleibende Plaetze bleiben frei; es gibt keine
unbegrenzte Such-/Abrufschleife und keine erhoehte GPT-Frequenz. Bereits gehaltene
Positionen behalten ihren bisherigen, getrennten Aktualisierungspfad.

## Anzeige und Diagnose

Der datierte Cacheeintrag `candidate_selection` enthaelt Auswahl, Ausschluesse,
Gruende, urspruengliche Profilquellen-IDs, Identitaetsangaben und Grenzen.
`UNKNOWN` und `ETF_OR_FUND` sind verschiedene Befunde. Jede ausgewaehlte Karte
traegt `instrument_type_evidence`; diese Referenz bleibt auch nach der
Originalquellenanreicherung erhalten.

`presentation.snapshot()` reicht `candidate_selection` mit einer ausdruecklichen
`stale`-Kennzeichnung sowie `optional_sources` aus dem Quellenstatus an die WebUI
weiter. Der Auswahlbeleg kann vollstaendig in die Diagnose aufgenommen werden;
seine Anzeige allein aktualisiert weder Profile noch Nachrichten oder GPT.

## D20 – vorhandene FMP-/GPT-Verwendungsbelege

Die bestehende `pulsar.analysis.input_sources()` protokolliert je erfolgreicher,
validierter Antwort die im Eingabepaket enthaltenen Quellen. Die Karten tragen
bereits `precheck`, `analysis` und `countercheck` mit `input_hash`, `input_sources`
und Ausfuehrungsbelegen. Die gemeinsame Vorpruefung hat einen gemeinsamen Hash,
keine fuenf separaten Modellaufrufe. Die FMP-Quellen-IDs der Karte liefern die
Verbindung zur Tages-/Jahresdatenherkunft.

Der neue Worker-Test prueft, dass die fuenf verbleibenden Aktien tatsaechlich
FMP-Quellen in `input_sources[symbol].sources` mit `included=true` und dem Status
`INPUT_OF_VALIDATED_RESPONSE` haben. Dafuer ist kein neuer Modellaufruftyp und
keine Aenderung an `analysis.py` notwendig. Eine leere separate Tabelle
`evidence_uses` widerlegt diese belegte Nutzung nicht. Die Anzeige muss weiterhin
die Uebergabe von Daten, eine validierte Antwort und eine Handelswirkung
auseinanderhalten. Fehlende oder abgeschnittene Quellen werden nicht als
tatsaechlich vollstaendig verarbeitet ausgewiesen.

## Separater Restpunkt: Aufmerksamkeitsbaseline fuer seltene Aktien

Die Ergaenzung `Loesungen_offene_Punkte_NEXUS_10_1_0_1(2).md` benennt zu Recht ein
Problem der beobachteten Stichprobe: Ein spaet erstmals in einer Topliste
auftauchender Wert erhaelt aus seiner frueheren Abwesenheit bisher keine
14-Tage-Baseline. Die aktuelle Baseline verwendet nur tatsaechlich vorhandene
Instrumentzeilen (`PRESENT_ROWS_ONLY`, `absence_inferred=false`).

Der vorgeschlagene Schluss "400 Zeilen ohne Abruffehler, kleinster Wert 30,
deshalb weniger als 30 fuer jeden fehlenden Ticker" ist mit den vorliegenden
Belegen nicht gerechtfertigt. Ein fehlerfreier Seitenabruf belegt weder die
vollstaendige Ranking-Abdeckung noch dieselbe Filterdefinition, eine bekannte
Grundgesamtheit, korrekte Pagination oder den Ausschluss von Ranggleichstaenden.
Selbst bei nachgewiesener Top-N-Abdeckung waere die genaue Schranke einschliesslich
Gleichstaenden erst aus dem Quellenvertrag abzuleiten. Abwesenheit ist daher
weder null noch automatisch eine belegte obere Schranke.

Ein kuenftiger Baseline-Umbau benoetigt pro Messzeitpunkt einen belegten
Ranking-/Abdeckungsvertrag, getrennte Filter und Zeitfenster, Behandlung von
Pagination/Teilausfaellen und Ranggleichstaenden sowie einen ausdruecklichen
Vertrag fuer gemessene und zensierte Daten. Er ist ein eigener offener Punkt.
Dieser Patch erfindet keine historischen Tage oder Zaehler und lockert die
bestehenden Baseline-/Handelsbedingungen nicht.

## Offline-Pruefnachweis

Isolierter Testlauf `pulsar_629f63b5bb`: **223 bestanden**, keine Netzwerkversuche.
Enthalten sind die neue Auswahltestsuite sowie bestehende PULSAR-Fluss-,
Quellen-/Baseline-, GPT-Timeout-, FMP-Tarif-/Cache- und Eingabebelegtests.
Die einzige Warnung betrifft die bestehende Starlette/AnyIO-Deprecation.

Die neuen Tests decken ab: SPY/QQQ bereits im Cache und erstmals abgefragt,
Nachruecken im selben Lauf, Stop nach fuenf Aktien, Profil-/Inspektionsgrenzen,
Cachewiederverwendung, veralteten/geaenderten Typ, falschen Ticker, fehlende oder
falsch typisierte Flags, Zukunftszeitstempel, Abrufausfaelle/Pausen, Moduswechsel,
Entdeckungsplatz, ungeeignete alte GPT-Wiederholungsauftraege, exakte
Profilweitergabe ohne doppelten HTTP-Abruf und FMP-Belege am tatsaechlichen
Worker-/GPT-Pfad. Keine Brokeraktionen oder echten Provideranfragen wurden ausgefuehrt.
