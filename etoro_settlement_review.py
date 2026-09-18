"""Account-bound cash evidence and an explicit, auditable settlement review.

Cash differences are candidates, not native commission receipts. Until 10.3.0
they always required the user's confirmation that the interval has no other
cash flows. No broker orders are sent and no old orders, quantities or risk
periods are deleted.

10.3.1 (Befund 17.09.2026, AAPL Trade 79): Die eToro-History meldet fuer
geschlossene Positionen ``netProfit`` = reiner Kursgewinn und ``fees = 0``,
obwohl der Einstieg nachweislich 1,00 USD kostete; einen Endpunkt mit
Abschlusskosten geschlossener Positionen gibt es laut API-Dokumentation nicht.
Der einzige belastbare Beleg ist die authentifizierte Barbestandsbewegung des
Kontos. Die fruehere Zuordnung verlangte dafuer "genau eine offene Position
davor, keine danach" -- mit zwei gleichzeitig offenen Aktien (AAPL + META) konnte
deshalb weder NEXUS noch der Nutzer die Abrechnung jemals abschliessen, und
jede Verkaufsrunde sperrte die eToro-Kaeufe. Jetzt gilt: Die Positionsmenge vor
dem Abschluss abzueglich der geschlossenen Position muss exakt der Menge danach
entsprechen (bei neueren Belegen auch je Stueckzahl), keine offenen Orders auf
beiden Seiten, keine andere Handelsbewegung im Intervall. Unter engen
Plausibilitaetsgrenzen (kurzes Intervall, Abschlusskosten hoechstens
max(5 USD, 0,5 % des Erloeses)) protokolliert NEXUS die Abrechnung automatisch
als CASH_DELTA_CONFIRMED -- aus zwei Brokerbelegen, nicht aus einer Annahme.
Alles andere bleibt UNKNOWN und wartet auf den manuellen Dialog.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib
import json
import sqlite3

import trade_ledger as ledger

REVIEW_BINDING = ('trade_id','broker_account_fingerprint','paper','broker_position_id',
    'entry_order_id','entry_fill_ids_json','exit_fill_ids_json','exit_order_id','menge','einstieg_preis',
    'ausstieg_preis','ausgestiegen_am','einstieg_gebuehr','entry_fee_quality','broker_result_receipt_hash')
# Automatische Verbuchung nur bei kurzem, sauberem Messintervall und
# plausiblen Abschlusskosten. Alles darueber ist kein Beweis einer Gebuehr,
# sondern ein Hinweis auf eine nicht beobachtete Kontobewegung.
AUTO_MAX_INTERVAL_SECONDS = 1800
AUTO_MAX_EXIT_COST_ABS = Decimal('5.00')
AUTO_MAX_EXIT_COST_PCT = Decimal('0.005')
AUTO_ACTOR = 'nexus:cash-delta-auto'
CASH_DELTA_QUALITIES = ('USER_CONFIRMED', 'CASH_DELTA_CONFIRMED')
# 10.7.0 Teil E (Freigabe Georg 18.09.2026: "lockere die Regel und trage einen
# erwartungswert ein"): Wenn kein Barbestandsbeleg den Abschluss erklaert,
# wird die Abschlussgebuehr aus den BESTAETIGTEN Abrechnungen desselben Kontos
# erwartet -- gekennzeichnet, nicht bestaetigt, und durch den naechsten
# Cash-Delta-Beleg ueberschreibbar. Bei diesem Konto: 12 von 12 belegte Seiten
# exakt 1,00 USD.
EXPECTED_ACTOR = 'nexus:expected-fee'
EXPECTED_QUALITY = 'EXPECTED_UNVERIFIED'
EXPECTED_MIN_SAMPLES = 3
EXPECTED_MIN_AGE_SECONDS = 900     # dem Cash-Delta 15 Minuten Vorrang lassen
INTERVAL_ACTOR = 'nexus:cash-delta-interval'


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def amount(value):
    if value is None or isinstance(value, bool):
        raise ValueError('Betrag fehlt oder ist ungültig')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Betrag ungültig') from None
    if not result.is_finite():
        raise ValueError('Betrag ungültig')
    return result


def stamp(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Belegzeit ohne Zeitzone')
    return dt.astimezone(timezone.utc)


def tables(con):
    con.execute('''CREATE TABLE IF NOT EXISTS etoro_cash_receipts (
        receipt_hash TEXT PRIMARY KEY, account TEXT NOT NULL, paper INTEGER NOT NULL,
        observed_at TEXT NOT NULL, cash_usd TEXT NOT NULL, receipt_json TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS etoro_settlement_reviews (
        trade_id INTEGER PRIMARY KEY, review_token TEXT NOT NULL, actor TEXT NOT NULL,
        reviewed_at TEXT NOT NULL, before_json TEXT NOT NULL, proof_json TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS etoro_proceeds_observations (
        receipt_hash TEXT PRIMARY KEY, account TEXT NOT NULL, paper INTEGER NOT NULL,
        order_id TEXT NOT NULL, observed_at TEXT NOT NULL, receipt_json TEXT NOT NULL)''')
    con.execute('CREATE INDEX IF NOT EXISTS ix_etoro_cash_scope_time ON etoro_cash_receipts(account,paper,observed_at)')


def save_cash(*, account, paper, observed_at, cash, positions, source, source_hash='', clean_orders=False,
              position_units=None):
    if not account or not isinstance(paper, bool) or not isinstance(positions, list):
        raise ValueError('Barbestand ohne eindeutiges Konto')
    val = amount(cash)
    if val < 0:
        raise ValueError('Negativer Barbestand wird nicht automatisch zugeordnet')
    proof = dict(account=account, paper=paper, observed_at=stamp(observed_at).isoformat(),
        cash_usd=str(val), position_ids=sorted(str(p) for p in positions),
        source=source, source_hash=source_hash, clean_orders=clean_orders is True)
    if isinstance(position_units, dict) and position_units:
        # 10.3.1: Stueckzahlen je Position, damit eine Teilschliessung einer
        # ANDEREN Position im Messintervall erkennbar bleibt. Aeltere Belege
        # ohne dieses Feld werden nicht nachtraeglich erfunden.
        proof['position_units'] = {str(k): str(v) for k, v in sorted(position_units.items())}
    h = digest(proof)
    ledger.init_ledger()
    with ledger._connect() as con:
        tables(con)
        con.execute('INSERT OR IGNORE INTO etoro_cash_receipts VALUES (?,?,?,?,?,?)',
                    (h, account, int(paper), proof['observed_at'], str(val), encoded(proof)))
        # Reviews carry their complete immutable cash evidence. Limit the live
        # sampling store to 45 days; bundled historical evidence is not pruned.
        if source == 'ETORO_AUTHENTICATED_PNL_CREDIT':
            cutoff = (stamp(observed_at)-timedelta(days=45)).isoformat()
            con.execute("DELETE FROM etoro_cash_receipts WHERE account=? AND paper=? AND observed_at<? AND json_extract(receipt_json,'$.source')='ETORO_AUTHENTICATED_PNL_CREDIT'",
                        (account,int(paper),cutoff))
    return h


def capture_pnl(broker, data):
    """Reuse an authenticated USD P&L response; never make another API call."""
    p = data.get('clientPortfolio') or {}
    if (p.get('accountCurrencyId') != 1 or isinstance(p.get('accountCurrencyId'), bool)
            or not isinstance(p.get('positions'), list) or p.get('mirrors')):
        return None
    ids = [str(x.get('positionId') or x.get('positionID') or '') for x in p['positions']]
    if '' in ids:
        return None
    account = str(broker.account_fingerprint() or '')
    if not account:
        return None
    # One point per minute is enough; preserve points before/after changes.
    compact = {k: p.get(k) for k in ('credit', 'accountCurrencyId', 'mirrors',
        'orders', 'stockOrders', 'entryOrders', 'exitOrders', 'ordersForOpen', 'ordersForClose', 'ordersForCloseMultiple')}
    # Floating P&L/rates change every tick without changing the cash interval.
    compact['positions'] = sorted((str(x.get('positionId') or x.get('positionID')),
        str(x.get('units',x.get('Units')))) for x in p['positions'])
    key = digest(compact)
    now = stamp(data['_snapshot_at'])
    previous = getattr(broker, '_cash_receipt_last', None)
    if previous and previous[0] == key and (now-previous[1]).total_seconds() < 60:
        return None
    clean = all(p.get(k) == [] for k in ('orders', 'ordersForOpen', 'ordersForClose'))
    units = {}
    for x in p['positions']:
        pid = str(x.get('positionId') or x.get('positionID'))
        raw_units = x.get('units', x.get('Units'))
        try:
            units[pid] = str(amount(raw_units))
        except ValueError:
            units = {}
            break
    result = save_cash(account=account, paper=broker.paper, observed_at=data['_snapshot_at'],
        cash=p.get('credit'), positions=ids, source='ETORO_AUTHENTICATED_PNL_CREDIT',
        source_hash=key, clean_orders=clean, position_units=units or None)
    broker._cash_receipt_last = (key, now)
    return result


def import_pep_evidence(row):
    """Read-only historical evidence, imported only into the exact matching lineage."""
    path = Path(__file__).parent/'docs'/'PEP_Klaerungsbelege_20260914.json'
    if not path.exists():
        return
    package = json.loads(path.read_text(encoding='utf-8'))
    binding = package['binding']
    if any(str(row.get(k)) != str(v) for k, v in binding.items()):
        return
    for item in package['cash_receipts']:
        save_cash(**item)


def _gate(con, row):
    """Vorbedingungen aller Rechenwege plus eine bereits protokollierte Abrechnung.

    Rueckgabe: (fertige Abrechnung oder None, Abschlusszeit, vorlaeufiger
    Erwartungswert-Eintrag oder None). Ein Erwartungswert (10.7.0) gilt NICHT
    als fertig: Er darf und soll durch einen Barbestandsbeleg ersetzt werden.
    """
    from ledger_result import finite_number
    if (not row or row.get('broker') != 'etoro' or row.get('waehrung') != 'USD'
            or not row.get('ausgestiegen_am') or row.get('superseded_by')
            or row.get('entry_fee_quality') != 'CONFIRMED'
            or row.get('accounting_kind', 'TRADE') != 'TRADE'
            or row.get('ownership_status') != 'BOT_VERIFIED'
            or not row.get('entry_order_id') or not row.get('broker_position_id')):
        raise ValueError('Vollständig geschlossene, eindeutig zugeordnete USD-Position mit Einstiegskosten erforderlich')
    for k in ('menge','einstieg_preis','ausstieg_preis','einstieg_gebuehr'):
        if (finite_number(row.get(k)) is None or amount(row[k]) < 0
                or (k != 'einstieg_gebuehr' and amount(row[k]) == 0)):
            raise ValueError('Einstands-/Abschlusswerte unvollständig')
    done = con.execute('SELECT * FROM etoro_settlement_reviews WHERE trade_id=?',(row['trade_id'],)).fetchone()
    vorlaeufig = None
    if done:
        previous, calculation = json.loads(done['before_json']), json.loads(done['proof_json'])
        bound = {k:previous.get(k) for k in REVIEW_BINDING}
        original = {k:v for k,v in calculation.items() if k not in ('token','completed','reviewed_at')}
        if digest(dict(binding=bound,calculation=original)) != done['review_token'] or calculation.get('token') != done['review_token']:
            raise ValueError('Prüfsumme der bestätigten Abrechnung stimmt nicht')
        identity = ('broker','broker_account_fingerprint','paper','broker_position_id',
            'entry_order_id','entry_fill_ids_json','exit_fill_ids_json','exit_order_id','menge',
            'einstieg_preis','ausstieg_preis','ausgestiegen_am','einstieg_gebuehr','entry_fee_quality')
        erwartet = done['actor'] == EXPECTED_ACTOR
        erlaubt = CASH_DELTA_QUALITIES + ((EXPECTED_QUALITY,) if erwartet else ())
        if (any(row.get(k) != previous.get(k) for k in identity)
                or row.get('fee_quality') not in erlaubt
                or amount(row.get('netto_pnl')) != amount(calculation['proposed_net'])
                or amount(row.get('gebuehren')) != amount(calculation['entry_cost'])+amount(calculation['proposed_exit_cost'])):
            raise ValueError('Trade und protokollierte Abrechnung widersprechen sich; Prüfung erforderlich')
        if not erwartet:
            return dict(calculation, completed=True, reviewed_at=done['reviewed_at']), None, None
        vorlaeufig = dict(calculation, reviewed_at=done['reviewed_at'])
    history = con.execute('SELECT receipt_json FROM etoro_history_result_receipts WHERE trade_id=? AND receipt_hash=?',
                          (row['trade_id'], row.get('broker_result_receipt_hash'))).fetchone()
    if not history:
        raise ValueError('Passender Broker-Historienbeleg fehlt')
    proof = json.loads(history[0]); h = proof['row']
    if (digest(proof) != row.get('broker_result_receipt_hash')
            or proof['account'] != row['broker_account_fingerprint']
            or proof['environment'] != ('DEMO' if row['paper'] else 'LIVE')
            or str(h['positionId']) != row['broker_position_id']
            or str(h['orderId']) != row['entry_order_id'] or h.get('isBuy') is not True
            or isinstance(h.get('leverage'), bool) or h.get('leverage') != 1
            or stamp(h['closeTimestamp']) != stamp(row['ausgestiegen_am'])
            or any(amount(h[a]) != amount(row[b]) for a,b in (
                ('units','menge'),('openRate','einstieg_preis'),('closeRate','ausstieg_preis')))):
        raise ValueError('Historie und gespeicherter Trade widersprechen sich')
    peers = con.execute('''SELECT COUNT(*) FROM trades WHERE broker='etoro'
        AND broker_account_fingerprint=? AND paper=? AND broker_position_id=? AND superseded_by IS NULL''',
        (row['broker_account_fingerprint'],row['paper'],row['broker_position_id'])).fetchone()[0]
    if peers != 1:
        raise ValueError('Teilpositionen müssen zuerst vollständig abgeglichen werden')
    return None, stamp(row['ausgestiegen_am']), vorlaeufig


def _preview(con, row):
    done, closed, _ = _gate(con, row)
    if done:
        return done
    records = con.execute('SELECT receipt_hash,receipt_json FROM etoro_cash_receipts WHERE account=? AND paper=? AND observed_at BETWEEN ? AND ? ORDER BY observed_at',
        (row['broker_account_fingerprint'],row['paper'],(closed-timedelta(hours=12)).isoformat(),(closed+timedelta(hours=12)).isoformat())).fetchall()
    before, after = [], []
    pid = str(row['broker_position_id'])
    for record in records:
        data = json.loads(record['receipt_json'])
        if digest(data) != record['receipt_hash']:
            raise ValueError('Prüfsumme eines Barbestandsbelegs stimmt nicht')
        item = dict(data, receipt_hash=record['receipt_hash'])
        ids = set(str(x) for x in data['position_ids'])
        # 10.3.1: Andere gleichzeitig offene Positionen sind erlaubt -- sie
        # muessen nur vor und nach dem Abschluss identisch sein. Der
        # Barbestand (credit) enthaelt keine schwebenden Gewinne, er aendert
        # sich nur durch Ein-/Ausstiege, Orders oder Ein-/Auszahlungen.
        if stamp(data['observed_at']) < closed and pid in ids:
            before.append(item)
        if stamp(data['observed_at']) > closed and pid not in ids and data['clean_orders']:
            after.append(item)
    if not before or not after:
        raise ValueError('Passende Barbestände vor und nach dem Abschluss fehlen; keine Gebühr geschätzt')
    before, after = before[-1], after[0]
    if set(before['position_ids']) - {pid} != set(after['position_ids']):
        raise ValueError('Positionsbestand hat sich im Messzeitraum über den Abschluss hinaus verändert; Differenz nicht eindeutig')
    units_before, units_after = before.get('position_units'), after.get('position_units')
    if isinstance(units_before, dict) and isinstance(units_after, dict):
        if {k: v for k, v in units_before.items() if k != pid} != units_after:
            raise ValueError('Stückzahlen anderer Positionen haben sich im Messzeitraum verändert; Differenz nicht eindeutig')
    # Any known other trade in the interval defeats the single-close attribution.
    for other in con.execute("SELECT * FROM trades WHERE broker='etoro' AND broker_account_fingerprint=? AND paper=? AND trade_id<>? AND superseded_by IS NULL", (row['broker_account_fingerprint'],row['paper'],row['trade_id'])):
        for key in ('eingestiegen_am','ausgestiegen_am'):
            if other[key]:
                try:
                    moment = stamp(other[key])
                except ValueError:
                    # Old imports may lack a timezone. A different calendar
                    # date by more than a day cannot enter this short interval.
                    try:
                        day = datetime.fromisoformat(str(other[key]).replace('Z','+00:00')).date()
                        if day < stamp(before['observed_at']).date()-timedelta(days=1) or day > stamp(after['observed_at']).date()+timedelta(days=1):
                            continue
                    except ValueError:
                        pass
                    raise ValueError('Zeit einer weiteren Kontobewegung ist ungeklärt') from None
                if stamp(before['observed_at']) <= moment <= stamp(after['observed_at']):
                    raise ValueError('Weitere Handelsbewegung im Messzeitraum; Differenz nicht eindeutig')
    gross_proceeds = amount(row['menge'])*amount(row['ausstieg_preis'])
    cash_delta = amount(after['cash_usd'])-amount(before['cash_usd'])
    exit_cost = (gross_proceeds-cash_delta).quantize(Decimal('.01'))
    if exit_cost < 0 or exit_cost > gross_proceeds:
        raise ValueError('Barbestandsdifferenz passt nicht zum Abschluss')
    gross = amount(row['menge'])*(amount(row['ausstieg_preis'])-amount(row['einstieg_preis']))
    entry = amount(row['einstieg_gebuehr'])
    # 10.3.1: Automatische Freigabe nur unter engen, dokumentierten Grenzen.
    # Eine Ablehnung hier erfindet nichts -- sie laesst den Fall im
    # manuellen Dialog mit Nutzerbestaetigung.
    interval = (stamp(after['observed_at'])-stamp(before['observed_at'])).total_seconds()
    auto_limit = max(AUTO_MAX_EXIT_COST_ABS, (gross_proceeds*AUTO_MAX_EXIT_COST_PCT).quantize(Decimal('.01')))
    auto_block = ''
    if not before.get('clean_orders'):
        auto_block = 'Offene Orders im Barbestandsbeleg vor dem Abschluss'
    elif interval > AUTO_MAX_INTERVAL_SECONDS:
        auto_block = f'Messintervall {int(interval)} s ueberschreitet {AUTO_MAX_INTERVAL_SECONDS} s'
    elif exit_cost > auto_limit:
        auto_block = f'Abschlusskosten {exit_cost} USD ueber Plausibilitaetsgrenze {auto_limit} USD'
    automatic = not auto_block
    binding = {k:row.get(k) for k in REVIEW_BINDING}
    result = dict(trade_id=row['trade_id'], symbol=row['symbol'], position_id=row['broker_position_id'],
        account=row['broker_account_fingerprint'], paper=bool(row['paper']), currency='USD',
        before=before, after=after, gross_proceeds=str(gross_proceeds),cash_delta=str(cash_delta),
        entry_cost=str(entry), proposed_exit_cost=str(exit_cost),
        proposed_net=str((gross-entry-exit_cost).quantize(Decimal('.01'))),
        source='BROKER_CASH_DELTA_AUTOMATIC' if automatic else 'USER_VERIFIED_CASH_DELTA',
        automatic_release=automatic, automatic_block_reason=auto_block,
        interval_seconds=int(interval), automatic_cost_limit=str(auto_limit),
        confirmation_required='Keine anderen Geldbewegungen im angezeigten Zeitraum; Differenz vollständig dem Abschluss zugeordnet.')
    result['token'] = digest(dict(binding=binding, calculation=result))
    done = con.execute('SELECT review_token FROM etoro_settlement_reviews WHERE trade_id=?',(row['trade_id'],)).fetchone()
    result['completed'] = bool(done and done[0] == result['token'])
    return result


def _cash_records(con, row, closed):
    records = con.execute('SELECT receipt_hash,receipt_json FROM etoro_cash_receipts WHERE account=? AND paper=? AND observed_at BETWEEN ? AND ? ORDER BY observed_at',
        (row['broker_account_fingerprint'],row['paper'],(closed-timedelta(hours=12)).isoformat(),(closed+timedelta(hours=12)).isoformat())).fetchall()
    out = []
    for record in records:
        data = json.loads(record['receipt_json'])
        if digest(data) != record['receipt_hash']:
            raise ValueError('Prüfsumme eines Barbestandsbelegs stimmt nicht')
        out.append(dict(data, receipt_hash=record['receipt_hash']))
    return out


def _preview_interval(con, row):
    """10.7.0 Teil E, Stufe 2: Cash-Delta ueber ALLE bekannten Ereignisse im Intervall.

    ``_preview`` verlangt "genau ein Verkauf zwischen zwei Belegen, sonst
    nichts". CSCO am 18.09.2026 lebte 1,5 Sekunden: Kauf und Verkauf lagen
    zwischen zwei Barbestandsbelegen, AMD war gleichzeitig offen -- der
    einfache Weg fand keinen Beleg "mit Position davor". Dabei ist die
    Rechnung eindeutig, sobald jedes Ereignis im Intervall bekannt ist:

        cash_delta = -Σ(Kaeufe: Menge*Kurs + belegte Einstiegsgebuehr)
                     +Σ(bekannte Verkaeufe: Menge*Kurs - belegte Gebuehr)
                     +(Erloes des Zieltrades - GESUCHTE Abschlussgebuehr)

    Genau EIN Verkauf mit unbekannter Gebuehr ist erlaubt (der Zieltrade).
    Jedes weitere unbekannte Ereignis bricht ab: Dann ist die Differenz kein
    Beweis. Schwebende Orders (frozenAmount) verfaelschen den Barbestand,
    deshalb muessen beide Belege ``clean_orders`` tragen.
    """
    from ledger_result import fees_confirmed, finite_number
    done, closed, _ = _gate(con, row)
    if done:
        return done
    pid = str(row['broker_position_id'])
    belege = _cash_records(con, row, closed)
    before = [b for b in belege if stamp(b['observed_at']) < closed and b.get('clean_orders')]
    after = [b for b in belege if stamp(b['observed_at']) > closed and pid not in set(str(x) for x in b['position_ids']) and b.get('clean_orders')]
    if not before or not after:
        raise ValueError('Keine sauberen Barbestandsbelege vor und nach dem Abschluss')
    before, after = before[-1], after[0]
    t0, t1 = stamp(before['observed_at']), stamp(after['observed_at'])
    interval = (t1 - t0).total_seconds()
    account, paper = row['broker_account_fingerprint'], row['paper']
    others = [dict(r) for r in con.execute(
        "SELECT * FROM trades WHERE broker='etoro' AND broker_account_fingerprint=? AND paper=? AND superseded_by IS NULL",
        (account, paper))]

    def inside(value):
        if not value:
            return False
        try:
            moment = stamp(value)
        except ValueError:
            raise ValueError('Zeit einer Kontobewegung im Messzeitraum ist ungeklärt') from None
        return t0 < moment <= t1

    events, known = [], Decimal(0)
    seen_entries = set()
    for other in others:
        ist_ziel = int(other['trade_id']) == int(row['trade_id'])
        if inside(other.get('eingestiegen_am')):
            schluessel = (str(other.get('entry_order_id') or ''), str(other.get('broker_position_id') or ''))
            if schluessel in seen_entries or str(other.get('notiz') or '').startswith('Rest nach Teilverkauf'):
                raise ValueError('Teilpositionen im Messzeitraum; Kaufbetrag nicht eindeutig')
            seen_entries.add(schluessel)
            if (other.get('entry_fee_quality') != 'CONFIRMED'
                    or finite_number(other.get('einstieg_gebuehr')) is None
                    or finite_number(other.get('einstieg_preis')) is None):
                raise ValueError(f"Kauf {other.get('symbol')} im Messzeitraum ohne belegte Einstiegskosten")
            betrag = amount(other['menge']) * amount(other['einstieg_preis']) + amount(other['einstieg_gebuehr'])
            known -= betrag
            events.append(dict(art='KAUF', trade_id=int(other['trade_id']), symbol=other.get('symbol'),
                               zeit=other['eingestiegen_am'], betrag=str(-betrag)))
        if inside(other.get('ausgestiegen_am')) and not ist_ziel:
            if (not fees_confirmed(other) or finite_number(other.get('gebuehren')) is None
                    or finite_number(other.get('einstieg_gebuehr')) is None
                    or finite_number(other.get('ausstieg_preis')) is None):
                raise ValueError(f"Weiterer Verkauf {other.get('symbol')} im Messzeitraum ohne bestaetigte Gebuehr; Differenz nicht eindeutig")
            exit_fee = amount(other['gebuehren']) - amount(other['einstieg_gebuehr'])
            erloes = amount(other['menge']) * amount(other['ausstieg_preis']) - exit_fee
            known += erloes
            events.append(dict(art='VERKAUF', trade_id=int(other['trade_id']), symbol=other.get('symbol'),
                               zeit=other['ausgestiegen_am'], betrag=str(erloes)))
    if not inside(row['ausgestiegen_am']):
        raise ValueError('Abschluss liegt nicht im Messzeitraum')
    # Jede Veraenderung des Positionsbestands laut Beleg muss durch ein
    # Ledger-Ereignis im Intervall erklaert sein. Eine Position, die
    # verschwindet oder ihre Stueckzahl aendert, ohne dass das Ledger davon
    # weiss, ist eine unbeobachtete Geldbewegung -- dann ist die Differenz
    # kein Beweis.
    before_ids = set(str(x) for x in before['position_ids'])
    after_ids = set(str(x) for x in after['position_ids'])
    verkauft_ids = {str(o.get('broker_position_id') or '') for o in others if inside(o.get('ausgestiegen_am'))}
    gekauft_ids = {str(o.get('broker_position_id') or '') for o in others if inside(o.get('eingestiegen_am'))}
    for fehlend in sorted(before_ids - after_ids):
        if fehlend not in verkauft_ids:
            raise ValueError(f'Positionsbestand hat sich im Messzeitraum ueber den Abschluss hinaus veraendert; Position {fehlend} verschwand ohne Ledgerbeleg')
    for neu in sorted(after_ids - before_ids):
        if neu not in gekauft_ids:
            raise ValueError(f'Positionsbestand hat sich im Messzeitraum veraendert; Position {neu} erschien ohne Ledgerbeleg')
    units_before, units_after = before.get('position_units'), after.get('position_units')
    if isinstance(units_before, dict) and isinstance(units_after, dict):
        for gemeinsam in before_ids & after_ids:
            if units_before.get(gemeinsam) != units_after.get(gemeinsam):
                raise ValueError('Stückzahlen anderer Positionen haben sich im Messzeitraum verändert; Differenz nicht eindeutig')
    gross_proceeds = amount(row['menge']) * amount(row['ausstieg_preis'])
    cash_delta = amount(after['cash_usd']) - amount(before['cash_usd'])
    exit_cost = (known + gross_proceeds - cash_delta).quantize(Decimal('.01'))
    if exit_cost < 0 or exit_cost > gross_proceeds:
        raise ValueError('Barbestandsdifferenz passt nicht zu den bekannten Ereignissen; unbeobachtete Geldbewegung')
    gross = amount(row['menge']) * (amount(row['ausstieg_preis']) - amount(row['einstieg_preis']))
    entry = amount(row['einstieg_gebuehr'])
    auto_limit = max(AUTO_MAX_EXIT_COST_ABS, (gross_proceeds*AUTO_MAX_EXIT_COST_PCT).quantize(Decimal('.01')))
    auto_block = ''
    if interval > AUTO_MAX_INTERVAL_SECONDS:
        auto_block = f'Messintervall {int(interval)} s ueberschreitet {AUTO_MAX_INTERVAL_SECONDS} s'
    elif exit_cost > auto_limit:
        auto_block = f'Abschlusskosten {exit_cost} USD ueber Plausibilitaetsgrenze {auto_limit} USD'
    automatic = not auto_block
    binding = {k:row.get(k) for k in REVIEW_BINDING}
    result = dict(trade_id=row['trade_id'], symbol=row['symbol'], position_id=pid,
        account=account, paper=bool(paper), currency='USD', method='INTERVAL',
        before=before, after=after, gross_proceeds=str(gross_proceeds), cash_delta=str(cash_delta),
        known_flow=str(known), events=events,
        entry_cost=str(entry), proposed_exit_cost=str(exit_cost),
        proposed_net=str((gross-entry-exit_cost).quantize(Decimal('.01'))),
        source='BROKER_CASH_DELTA_INTERVAL_AUTOMATIC' if automatic else 'USER_VERIFIED_CASH_DELTA_INTERVAL',
        automatic_release=automatic, automatic_block_reason=auto_block,
        interval_seconds=int(interval), automatic_cost_limit=str(auto_limit),
        confirmation_required='Alle Kaeufe und Verkaeufe im angezeigten Zeitraum sind belegt; die Restdifferenz gehoert vollstaendig diesem Abschluss.')
    result['token'] = digest(dict(binding=binding, calculation=result))
    done = con.execute('SELECT review_token FROM etoro_settlement_reviews WHERE trade_id=?',(row['trade_id'],)).fetchone()
    result['completed'] = bool(done and done[0] == result['token'])
    return result


def _preview_any(con, row):
    """Erst der einfache Weg (ein Verkauf, sonst nichts), dann das Intervall."""
    try:
        return _preview(con, row)
    except ValueError as exakt:
        try:
            return _preview_interval(con, row)
        except ValueError as intervall:
            raise ValueError(f'{exakt} | Intervall: {intervall}') from None


def _expected_samples(con, account, paper):
    """Bestaetigte Abschlusskosten desselben Kontos -- die Grundlage des Erwartungswerts."""
    rows = con.execute('''SELECT r.trade_id, r.proof_json, t.fee_quality FROM etoro_settlement_reviews r
        JOIN trades t ON t.trade_id=r.trade_id
        WHERE t.broker='etoro' AND t.broker_account_fingerprint=? AND t.paper=? AND r.actor<>?
        ORDER BY r.reviewed_at''', (account, paper, EXPECTED_ACTOR)).fetchall()
    samples = []
    for r in rows:
        if str(r['fee_quality'] or '') not in CASH_DELTA_QUALITIES:
            continue
        try:
            calc = json.loads(r['proof_json'])
            samples.append((int(r['trade_id']), amount(calc['proposed_exit_cost'])))
        except (ValueError, KeyError, TypeError):
            continue
    return samples


def _median(values):
    values = sorted(values)
    n = len(values)
    if n == 0:
        raise ValueError('Keine Werte')
    mitte = n // 2
    if n % 2:
        return values[mitte].quantize(Decimal('.01'))
    return ((values[mitte - 1] + values[mitte]) / 2).quantize(Decimal('.01'))


def expected_fee_settle(trade_id, *, now=None):
    """10.7.0 Teil E, Stufe 3: Abschlussgebuehr als gekennzeichneter Erwartungswert.

    Freigabe Georg 18.09.2026: "lockere die Regel und trage einen
    erwartungswert ein." Grundlage sind ausschliesslich BESTAETIGTE
    Abrechnungen desselben Kontos (mindestens drei), Wert ist der Median.
    Das Ergebnis heisst EXPECTED_UNVERIFIED -- es ist kein Beleg, es wird
    im Bericht so genannt, und der naechste Barbestandsbeleg ersetzt es.
    Dem Cash-Delta bleiben 15 Minuten Vorrang.
    """
    from ledger_result import fees_confirmed
    now = now or datetime.now(timezone.utc)
    ledger.init_ledger()
    with ledger._LOCK, ledger._connect() as con:
        tables(con)
        con.commit()
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM trades WHERE trade_id=?',(int(trade_id),)).fetchone()
        if not row:
            raise ValueError('Trade nicht gefunden')
        row = dict(row)
        done, closed, vorlaeufig = _gate(con, row)
        if done:
            return dict(updated=0, trade_id=int(trade_id), detail='Bereits protokolliert')
        if vorlaeufig:
            return dict(updated=0, trade_id=int(trade_id), detail='Erwartungswert bereits eingetragen')
        if fees_confirmed(row):
            raise ValueError('Bereits bestätigte Kosten werden nicht überschrieben')
        alter = (now - closed).total_seconds()
        if alter < EXPECTED_MIN_AGE_SECONDS:
            raise ValueError(f'Barbestandsbeleg hat Vorrang; Abschluss erst {int(alter)} s alt')
        samples = _expected_samples(con, row['broker_account_fingerprint'], row['paper'])
        if len(samples) < EXPECTED_MIN_SAMPLES:
            raise ValueError(f'Nur {len(samples)} bestaetigte Abrechnung(en) auf diesem Konto; '
                             f'mindestens {EXPECTED_MIN_SAMPLES} noetig')
        werte = [v for _, v in samples]
        expected = _median(werte)
        gross = amount(row['menge']) * (amount(row['ausstieg_preis']) - amount(row['einstieg_preis']))
        entry = amount(row['einstieg_gebuehr'])
        binding = {k:row.get(k) for k in REVIEW_BINDING}
        result = dict(trade_id=row['trade_id'], symbol=row['symbol'], position_id=row['broker_position_id'],
            account=row['broker_account_fingerprint'], paper=bool(row['paper']), currency='USD',
            method='EXPECTED_FEE', sample_trade_ids=[t for t, _ in samples], sample_count=len(samples),
            sample_min=str(min(werte)), sample_max=str(max(werte)), expected_exit_cost=str(expected),
            entry_cost=str(entry), proposed_exit_cost=str(expected),
            proposed_net=str((gross-entry-expected).quantize(Decimal('.01'))),
            source='ETORO_EXPECTED_FEE_MODEL', automatic_release=True, automatic_block_reason='',
            confirmation_required='Erwartungswert aus bestaetigten Abrechnungen desselben Kontos; '
                                  'kein Beleg, durch Barbestandsbeleg nachzupruefen.')
        result['token'] = digest(dict(binding=binding, calculation=result))
        now_iso = now.isoformat()
        con.execute('INSERT INTO etoro_settlement_reviews VALUES (?,?,?,?,?,?)',
            (int(trade_id), result['token'], EXPECTED_ACTOR, now_iso, encoded(row), encoded(result)))
        total = entry + expected
        con.execute('''UPDATE trades SET gebuehren=?,netto_pnl=?,
            fee_quality=?,exit_fee_quality=?,reconciliation_updated_at=?,
            notiz=? WHERE trade_id=?''',
            (float(total), float(result['proposed_net']), EXPECTED_QUALITY, EXPECTED_QUALITY, now_iso,
             (f"Abschlussgebuehr {expected} USD als Erwartungswert aus {len(samples)} bestaetigten Abrechnungen "
              f"eingetragen (nicht belegt). Vorherige Notiz: " + str(row.get('notiz') or ''))[:300],
             int(trade_id)))
    return dict(updated=1, trade_id=int(trade_id), net=result['proposed_net'], quality=EXPECTED_QUALITY,
        exit_cost=str(expected), token=result['token'], samples=len(samples),
        detail=f'Abschlussgebuehr als Erwartungswert ({expected} USD, Median aus {len(samples)} bestaetigten '
               'Abrechnungen) eingetragen; gekennzeichnet als nicht belegt.')


def _review_token_for(trade_id, *, expected=False):
    """Token des protokollierten Reviews -- fuer den Risikobeleg des Erwartungswerts."""
    ledger.init_ledger()
    with ledger._connect() as con:
        tables(con)
        row = con.execute('SELECT review_token, actor FROM etoro_settlement_reviews WHERE trade_id=?',
                          (int(trade_id),)).fetchone()
    if not row:
        return ''
    if expected != (row['actor'] == EXPECTED_ACTOR):
        return ''
    return str(row['review_token'])


def preview(trade_id):
    row = ledger.trade_detail(int(trade_id))
    if not row:
        raise ValueError('Trade nicht gefunden')
    import_pep_evidence(row)
    with ledger._connect() as con:
        tables(con)
        return _preview_any(con, row)


def confirm(trade_id, *, token, actor, no_other_cashflows):
    if no_other_cashflows is not True or not str(actor).strip() or not token:
        raise ValueError('Die Prüfung weiterer Geldbewegungen muss ausdrücklich bestätigt werden')
    ledger.init_ledger()
    with ledger._LOCK, ledger._connect() as con:
        tables(con)
        con.commit()
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM trades WHERE trade_id=?',(int(trade_id),)).fetchone()
        if not row:
            raise ValueError('Trade nicht gefunden')
        row = dict(row)
        p = _preview_any(con, row)
        if p['token'] != token:
            raise ValueError('Belege haben sich geändert; Vorschau erneut prüfen')
        if p['completed']:
            return dict(updated=0, trade_id=int(trade_id), detail='Bereits protokolliert')
        from ledger_result import fees_confirmed
        if fees_confirmed(row):
            raise ValueError('Bereits bestätigte Kosten werden nicht überschrieben')
        now = datetime.now(timezone.utc).isoformat()
        abweichung = _ersetze_erwartungswert(con, row, p, str(actor)[:160], token, now)
        total = amount(p['entry_cost'])+amount(p['proposed_exit_cost'])
        con.execute('''UPDATE trades SET gebuehren=?,netto_pnl=?,
            fee_quality='USER_CONFIRMED',exit_fee_quality='USER_CONFIRMED',
            reconciliation_updated_at=? WHERE trade_id=?''',
            (float(total),float(p['proposed_net']),now,int(trade_id)))
    return dict(updated=1, trade_id=int(trade_id), net=p['proposed_net'], quality='USER_CONFIRMED',
        expected_deviation=abweichung,
        detail='Abrechnung protokolliert. Der Handelskern übernimmt das Ergebnis einmalig zum ursprünglichen Verkaufstag.')


def _ersetze_erwartungswert(con, row, p, actor, token, now):
    """Protokolliert die Abrechnung; ein vorlaeufiger Erwartungswert wird ersetzt.

    Rueckgabe: Abweichung (Cash-Delta minus Erwartungswert) als String oder
    None, wenn kein Erwartungswert vorlag. Eine Abweichung ist ein Befund,
    kein Fehler: Sie wird protokolliert und in der Notiz festgehalten.
    """
    alt = con.execute('SELECT actor, proof_json FROM etoro_settlement_reviews WHERE trade_id=?',
                      (int(row['trade_id']),)).fetchone()
    abweichung = None
    if alt:
        if alt['actor'] != EXPECTED_ACTOR:
            raise ValueError('Bereits protokollierte Abrechnung wird nicht ueberschrieben')
        try:
            erwartet = amount(json.loads(alt['proof_json'])['proposed_exit_cost'])
            abweichung = str((amount(p['proposed_exit_cost']) - erwartet).quantize(Decimal('.01')))
        except (ValueError, KeyError, TypeError):
            abweichung = 'unbekannt'
        # proof_json bleibt exakt die Rechnung, aus der der Token stammt --
        # der Pruefsummenvergleich in _gate verlangt das. Die Ersetzung steht
        # in Notiz und Protokoll.
        con.execute('DELETE FROM etoro_settlement_reviews WHERE trade_id=?', (int(row['trade_id']),))
        import logging
        (logging.getLogger(__name__).warning if abweichung not in ('0.00', None) else logging.getLogger(__name__).info)(
            'eToro Trade %s: Erwartungswert durch Barbestandsbeleg ersetzt; Abweichung %s USD',
            row['trade_id'], abweichung)
        con.execute("UPDATE trades SET notiz=? WHERE trade_id=?", (
            (f"Erwartungswert durch Barbestandsbeleg ersetzt (Abweichung {abweichung} USD). "
             + str(row.get('notiz') or ''))[:300], int(row['trade_id'])))
    con.execute('INSERT INTO etoro_settlement_reviews VALUES (?,?,?,?,?,?)',
        (int(row['trade_id']), token, actor, now, encoded(row), encoded(p)))
    return abweichung


def auto_settle(trade_id):
    """10.3.1: Abrechnung ohne Nutzer protokollieren, wenn die Belege eindeutig sind.

    Gleiche Beweiskette wie ``confirm`` (zwei authentifizierte Barbestandsbelege,
    identischer uebriger Positionsbestand, keine andere Handelsbewegung), aber nur
    innerhalb der engen automatischen Grenzen aus ``_preview``. Es wird keine
    Gebuehr angenommen: Faellt eine Grenze, bleibt der Trade UNKNOWN und wartet
    auf den manuellen Dialog.
    """
    ledger.init_ledger()
    with ledger._LOCK, ledger._connect() as con:
        tables(con)
        con.commit()
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM trades WHERE trade_id=?',(int(trade_id),)).fetchone()
        if not row:
            raise ValueError('Trade nicht gefunden')
        row = dict(row)
        # 10.7.0: erst der einfache Weg, dann die Intervallrechnung ueber alle
        # bekannten Ereignisse. Ein vorlaeufiger Erwartungswert wird dabei
        # durch den Barbestandsbeleg ersetzt, nie umgekehrt.
        p = _preview_any(con, row)
        if p['completed']:
            return dict(updated=0, trade_id=int(trade_id), detail='Bereits protokolliert')
        if not p['automatic_release']:
            raise ValueError('Keine automatische Freigabe: ' + str(p.get('automatic_block_reason') or 'Grenze verletzt'))
        from ledger_result import fees_confirmed
        if fees_confirmed(row):
            raise ValueError('Bereits bestätigte Kosten werden nicht überschrieben')
        now = datetime.now(timezone.utc).isoformat()
        actor = INTERVAL_ACTOR if p.get('method') == 'INTERVAL' else AUTO_ACTOR
        abweichung = _ersetze_erwartungswert(con, row, p, actor, p['token'], now)
        total = amount(p['entry_cost'])+amount(p['proposed_exit_cost'])
        con.execute('''UPDATE trades SET gebuehren=?,netto_pnl=?,
            fee_quality='CASH_DELTA_CONFIRMED',exit_fee_quality='CASH_DELTA_CONFIRMED',
            reconciliation_updated_at=? WHERE trade_id=?''',
            (float(total),float(p['proposed_net']),now,int(trade_id)))
    return dict(updated=1, trade_id=int(trade_id), net=p['proposed_net'], quality='CASH_DELTA_CONFIRMED',
        exit_cost=p['proposed_exit_cost'], token=p['token'], method=p.get('method') or 'SINGLE',
        expected_deviation=abweichung,
        detail=('Abrechnung automatisch aus den Barbestandsbelegen und allen bekannten Ereignissen im '
                'Messzeitraum protokolliert; Ergebnis wird zum Verkaufstag uebernommen.'
                if p.get('method') == 'INTERVAL' else
                'Abrechnung automatisch aus zwei Barbestandsbelegen protokolliert; Ergebnis wird zum Verkaufstag uebernommen.'))


def observe_proceeds(*, account, paper, order_id, raw):
    """Retain the complete monetary scope; do not assume proceeds is net cash."""
    if not account or not isinstance(paper,bool) or not isinstance(raw,dict):
        raise ValueError('Erlösbeleg ohne eindeutigen Kontokontext')
    if str(raw.get('orderID',raw.get('orderId',''))) != str(order_id):
        raise ValueError('Erlösbeleg gehört zu einem anderen Auftrag')
    cid = str(raw.get('CID',raw.get('accountId','')))
    if cid and hashlib.sha256(f"etoro|{'demo' if paper else 'live'}|cid:{cid}".encode()).hexdigest()[:24] != account:
        raise ValueError('Erlösbeleg gehört zu einem anderen Konto')
    proof = {k:raw[k] for k in ('orderID','orderId','CID','accountId','assetCurrencyID',
        'accountCurrencyID','proceeds','positions','requestOccurred','statusID') if k in raw}
    proof.update(account=account, paper=paper, source='ETORO_V1_CLOSE_PROCEEDS_UNCLASSIFIED',
        fee_scope_confirmed=False)
    proof['gross_executed_value_usd'] = None
    proof['difference_to_proceeds_usd'] = None
    positions = raw.get('positions')
    if (positions and isinstance(positions,list) and cid
            and type(raw.get('accountCurrencyID')) is int and raw['accountCurrencyID']==1
            and type(raw.get('assetCurrencyID')) is int and raw['assetCurrencyID']==1):
        try:
            ids=[str(p.get('positionID',p.get('positionId',''))) for p in positions]
            if '' in ids or len(set(ids)) != len(ids):
                raise ValueError('Mehrdeutige Erlöszeilen')
            total=Decimal(0)
            for p in positions:
                units,rate=amount(p.get('units')),amount(p.get('rate'))
                if units<=0 or rate<=0 or amount(p.get('conversionRate'))!=1:
                    raise ValueError('Erlöswährung oder Ausführung unvollständig')
                total+=units*rate
            proof['gross_executed_value_usd']=str(total)
            proof['difference_to_proceeds_usd']=str(total-amount(raw.get('proceeds')))
        except (ValueError,TypeError,AttributeError):
            pass
    ledger.init_ledger()
    with ledger._connect() as con:
        tables(con)
        con.execute('INSERT OR IGNORE INTO etoro_proceeds_observations VALUES (?,?,?,?,?,?)',
            (digest(proof),account,int(paper),str(order_id),datetime.now(timezone.utc).isoformat(),encoded(proof)))
