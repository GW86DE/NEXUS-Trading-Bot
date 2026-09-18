"""Audited EUR reference valuation, separate from native broker cash receipts.

USDC: latest traded LIVE market candle completed before a fill, at most 5 min old.
USD: ECB daily USD/EUR reference for the fill's UTC calendar date (daily quality,
not a contemporaneous conversion). EUR: identity. Never USD/USDC parity, DEMO
candles, current FX, or a fictitious FX execution. Every result retains raw fills,
chosen references, method and the unchanged native-currency ledger context.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import urllib.parse
import xml.etree.ElementTree as ET

from okx_receipt_math import EvidenceError, number, prove_order, same_quantity, stamp

METHOD = 'EUR_REFERENCE_CASHFLOWS_V1'
# 10.2.1: EIN Einstieg, MEHRERE terminale Verkaufsorders (auch verschiedene
# Quote-Paare desselben Basiswerts). Jede Order bleibt einzeln prove_order-
# belegt; die EUR-Bewertung jedes Fills nutzt exakt dieselben Referenzregeln.
METHOD_COMPOSITE = 'EUR_REFERENCE_CASHFLOWS_COMPOSITE_V1'
METHODS = frozenset({METHOD, METHOD_COMPOSITE})
ECB_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml'
ECB_NS = 'http://www.ecb.int/vocabulary/2002-08-01/eurofxref'
FIELDS = ('trade_id','broker','broker_account_fingerprint','paper','broker_position_id',
          'entry_order_id','client_order_id','waehrung','menge','einstieg_preis',
          'entry_cost_basis','einstieg_gebuehr','eingestiegen_am','ausgestiegen_am',
          'exit_order_id','exit_fill_ids_json','ausstieg_preis','netto_pnl','brutto_pnl',
          'gebuehren','fee_quality','entry_fee_quality','exit_fee_quality',
          'ownership_status','accounting_kind','superseded_by','exit_native_json')


def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def snapshot(row):
    result = {k:row.get(k) for k in FIELDS}
    for key in ('exit_fill_ids_json','exit_native_json'):
        result[key] = json.loads(result.get(key) or ('[]' if 'ids' in key else '{}'))
    return result


class Rates:
    def __init__(self, document):
        if document.get('schema') != 'nexus-public-fx-followup-v1':
            raise EvidenceError('Unbekanntes historisches FX-Belegformat')
        self.document = document
        self.source_hash = sha(encode(document))
        self.candles = {}
        self.daily = {}
        for request in document.get('requests') or []:
            body = request.get('body_utf8')
            if request.get('http_status') != 200 or not isinstance(body,str):
                continue
            if sha(body) != request.get('body_sha256') or len(body.encode()) != request.get('body_bytes'):
                raise EvidenceError('FX-Rohantwort stimmt nicht mit ihrer Pruefsumme ueberein')
            url = request.get('url',''); parts = urllib.parse.urlsplit(url)
            if request.get('environment') == 'LIVE_REFERENCE':
                pairs = urllib.parse.parse_qsl(parts.query)
                params = dict(pairs)
                if (parts.scheme != 'https' or parts.netloc != 'eea.okx.com'
                        or parts.path != '/api/v5/market/history-candles' or parts.fragment
                        or len(pairs) != 4 or set(params) != {'instId','bar','after','limit'}
                        or params['instId'] != 'USDC-EUR' or params['bar'] != '1m'
                        or params['limit'] != '5' or not params['after'].isdigit()):
                    raise EvidenceError('Kein exakter oeffentlicher USDC/EUR-Kerzenabruf')
                payload = json.loads(body)
                if str(payload.get('code')) != '0' or not isinstance(payload.get('data'),list):
                    raise EvidenceError('Historische Kerzenantwort nicht erfolgreich')
                for raw in payload['data']:
                    if not isinstance(raw,list) or len(raw) != 9 or not str(raw[0]).isdigit():
                        raise EvidenceError('Ungueltiger Kerzenbeleg')
                    millis = int(raw[0])
                    if millis % 60000 or millis >= int(params['after']):
                        raise EvidenceError('Kerzenzeit widerspricht der historischen Abfrage')
                    o,h,l,c = [number(x,positive=True) for x in raw[1:5]]
                    if not l <= min(o,c) <= max(o,c) <= h or any(number(x)<0 for x in raw[5:8]):
                        raise EvidenceError('Unmoeglicher Preis-/Volumenbeleg')
                    previous = self.candles.get(millis)
                    if previous and previous['raw'] != raw:
                        raise EvidenceError('Widerspruechliche historische Kerzen')
                    self.candles[millis] = dict(raw=raw,url=url,body_sha256=request['body_sha256'])
            elif request.get('environment') == 'ECB_DAILY_REFERENCE':
                if url != ECB_URL or '<!DOCTYPE' in body.upper() or '<!ENTITY' in body.upper():
                    raise EvidenceError('Kein freigegebener EZB-Tagesbeleg')
                root = ET.fromstring(body)
                for day in root.iter('{'+ECB_NS+'}Cube'):
                    date = day.get('time')
                    if not date: continue
                    datetime.strptime(date,'%Y-%m-%d')
                    for item in day:
                        if item.tag == '{'+ECB_NS+'}Cube' and item.get('currency') == 'USD':
                            rate = number(item.get('rate'),positive=True)
                            if date in self.daily:
                                raise EvidenceError('Doppelte EZB-Tagesreferenz')
                            self.daily[date] = dict(usd_per_eur=str(rate),url=url,
                                                   body_sha256=request['body_sha256'])

    def for_fill(self, currency, fill):
        # Do not substitute record creation time for execution time in FX work.
        text = str(fill.get('fillTime') or '')
        if not text.isdigit() or int(text) <= 0:
            raise EvidenceError('Exakter fillTime fuer historische Bewertung fehlt')
        millis = int(text)
        if currency == 'EUR':
            return dict(currency='EUR',eur_per_unit='1',quality='IDENTITY',fill_time_ms=text)
        if currency == 'USDC':
            candidates = [(t,r) for t,r in self.candles.items()
                if t+60000 <= millis and millis-(t+60000) <= 300000
                and str(r['raw'][8]) == '1' and number(r['raw'][5]) > 0]
            if not candidates:
                raise EvidenceError('Kein abgeschlossener gehandelter USDC/EUR-Beleg innerhalb von 5 Minuten')
            t,row = max(candidates,key=lambda item:item[0])
            return dict(currency='USDC',eur_per_unit=str(number(row['raw'][4],positive=True)),
                quality='LIVE_MARKET_PRECEDING_TRADED_MINUTE',fill_time_ms=text,
                candle_open_ms=str(t),candle=row['raw'],url=row['url'],body_sha256=row['body_sha256'])
        if currency == 'USD':
            date = datetime.fromtimestamp(millis/1000,timezone.utc).date().isoformat()
            if date not in self.daily:
                raise EvidenceError('EZB-Tagesreferenz fuer das UTC-Ausfuehrungsdatum fehlt')
            row = self.daily[date]
            return dict(currency='USD',eur_per_unit=str(Decimal(1)/number(row['usd_per_eur'])),
                quality='ECB_DAILY_REFERENCE',date=date,fill_time_ms=text,**row)
        raise EvidenceError('Kein belegter Bewertungsweg fuer '+str(currency))


def original_proof(proof, account):
    result = prove_order(proof['status'],proof['fills'],inst=proof['inst_id'],oid=proof['order_id'],
        side=proof['side'],base=proof['base'],currency=proof['currency'],account=account)
    if encode(result) != encode(proof):
        raise EvidenceError('Abgeleiteter Orderbeleg widerspricht den Original-Fills')
    return result


def calculate(entry, sale, rates, *, account, environment):
    if rates.document.get('source_account') != account or rates.document.get('source_environment') != environment:
        raise EvidenceError('Kursnachlieferung gehoert nicht zur Handelsdomaene')
    entry = original_proof(entry,account); sale = original_proof(sale,account)
    if entry['side'] != 'buy' or sale['side'] != 'sell' or entry['base'] != sale['base']:
        raise EvidenceError('Unpassende Kauf-/Verkaufskette')
    if max(stamp(f) for f in entry['fills']) > min(stamp(f) for f in sale['fills']):
        raise EvidenceError('Verkauf vor vollstaendigem Kauf')
    quantity = number(sale['quantity'],positive=True)
    ratio = quantity/number(entry['quantity'],positive=True)
    if ratio > 1 and not same_quantity(quantity,entry['quantity']):
        raise EvidenceError('Mehr Bestand verkauft als gekauft')
    flows = []
    totals = {}
    for proof in (entry,sale):
        cash = value = Decimal(0)
        for fill in proof['fills']:
            quote = proof['currency']; raw_value = number(fill['fillSz'])*number(fill['fillPx'])
            fee = number(fill['fee'])
            raw_cash = raw_value
            if fill['feeCcy'] == quote:
                raw_cash += -fee if proof['side']=='buy' else fee
            elif fill['feeCcy'] != proof['base'] and fee:
                raise EvidenceError('Drittwaehrungsgebuehr ohne eigenen Bewertungsbeleg')
            reference = rates.for_fill(quote,fill)
            rate = number(reference['eur_per_unit'],positive=True)
            value += raw_value*rate; cash += raw_cash*rate
            flows.append(dict(side=proof['side'],order_id=proof['order_id'],fill_id=str(fill['tradeId']),
                native_currency=quote,native_cash=str(raw_cash),eur_cash=str(raw_cash*rate),reference=reference))
        totals[proof['side']] = dict(cash=cash,value=value)
    cost = totals['buy']['cash']*ratio
    proceeds = totals['sell']['cash']
    entry_price = totals['buy']['value']/number(entry['gross_quantity'],positive=True)
    exit_price = totals['sell']['value']/number(sale['gross_quantity'],positive=True)
    gross = (exit_price-entry_price)*quantity; net = proceeds-cost
    return dict(method=METHOD,result_currency='EUR',account=account,environment=environment,
        entry_instrument=entry['inst_id'],exit_instrument=sale['inst_id'],
        entry_order_id=entry['order_id'],exit_order_id=sale['order_id'],
        quantity=str(quantity),entry_allocation=str(ratio),entry_cost_eur=str(cost),
        proceeds_eur=str(proceeds),entry_price_eur=str(entry_price),exit_price_eur=str(exit_price),
        gross_eur=str(gross),fees_eur=str(gross-net),net_eur=str(net),
        entry_fee_eur=str(cost-entry_price*quantity),exit_fee_eur=str(exit_price*quantity-proceeds),
        entered_at=min(stamp(f) for f in entry['fills']).isoformat(),
        closed_at=max(stamp(f) for f in sale['fills']).isoformat(),
        cross_currency=entry['currency'] != sale['currency'],flows=flows,
        fx_source_hash=rates.source_hash,entry_proof=entry,exit_proof=sale,
        quality='REFERENCE_VALUATION',executed_fx_conversion=False)


def init_on(con):
    con.execute('''CREATE TABLE IF NOT EXISTS okx_reference_sources (
        source_hash TEXT PRIMARY KEY,source_json TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS okx_reference_results (
        trade_id INTEGER PRIMARY KEY,account TEXT NOT NULL,environment TEXT NOT NULL,
        currency TEXT NOT NULL,method TEXT NOT NULL,receipt_hash TEXT NOT NULL,
        row_json TEXT NOT NULL,receipt_json TEXT NOT NULL,source_hash TEXT NOT NULL,
        created_at TEXT NOT NULL)''')


def _native_consistency(con,row,receipt):
    from okx_residual_inventory import entry_from_ledger
    from ledger_result import confirmed_net
    entry,sale = receipt['entry_proof'],receipt['exit_proof']
    if (row.get('broker')!='okx' or row.get('broker_account_fingerprint')!=receipt['account']
            or bool(row.get('paper'))!=(receipt['environment']=='DEMO') or row.get('superseded_by') is not None
            or row.get('accounting_kind')!='TRADE' or not row.get('ausgestiegen_am')
            or row.get('ownership_status') not in {'VERIFIED','BOT_VERIFIED','VERIFIED_BROKER_FILL_CHAIN'}
            or row.get('entry_order_id')!=entry['order_id'] or row.get('exit_order_id')!=sale['order_id']
            or row.get('waehrung')!=entry['currency'] or row.get('broker_position_id')!=entry['inst_id']
            or not same_quantity(row['menge'],receipt['quantity'])):
        raise EvidenceError('EUR-Beleg passt nicht zur aktuellen abgeschlossenen Handelszeile')
    local,_ = entry_from_ledger(con,row,proven_order_currency=entry['currency'])
    if set(local['fill_ids'])!=set(entry['fill_ids']) or not same_quantity(local['cash'],entry['cash']):
        raise EvidenceError('EUR-Beleg widerspricht lokalen Kauf-Fills')
    ids = set(sale['fill_ids'])
    if set(json.loads(row.get('exit_fill_ids_json') or '[]')) != ids:
        raise EvidenceError('EUR-Beleg widerspricht den zugeordneten Verkaufsfills')
    for fid in ids:
        claims=con.execute('''SELECT trade_id FROM trade_native_exit_fills WHERE broker='okx'
            AND account=? AND environment=? AND fill_id=?''',(receipt['account'],receipt['environment'],fid)).fetchall()
        if len(claims)!=1 or claims[0][0]!=row['trade_id']:
            raise EvidenceError('Verkaufsfill ohne exklusiven Beleganspruch')
    if row['ausgestiegen_am'] != receipt['closed_at']:
        raise EvidenceError('Abschlusszeit widerspricht dem Ausfuehrungsbeleg')
    if receipt['cross_currency']:
        native = json.loads(row.get('exit_native_json') or '{}').get('receipt',{})
        if (native.get('native_currency') != sale['currency'] or set(native.get('fill_ids') or [])!=ids
                or not same_quantity(native.get('net_proceeds'),sale['cash']) or not native.get('manual_confirmed')):
            raise EvidenceError('Manueller Fremdwaehrungsverkauf nicht vollstaendig belegt')
    else:
        expected = number(sale['cash'])-number(entry['cash'])*number(sale['quantity'])/number(entry['quantity'])
        if not confirmed_net(row) or abs(number(row['netto_pnl'])-expected)>Decimal('1e-8'):
            raise EvidenceError('Natives Ergebnis ist nicht unabhaengig bestaetigt')


def store_on(con,row,receipt,rates):
    init_on(con)
    _native_consistency(con,row,receipt)
    verified=calculate(receipt['entry_proof'],receipt['exit_proof'],rates,
        account=receipt['account'],environment=receipt['environment'])
    if encode(verified)!=encode(receipt):
        raise EvidenceError('EUR-Berechnung stimmt nicht mit ihrem Nachweis ueberein')
    text=encode(receipt);digest=sha(text)
    prior=con.execute('SELECT receipt_hash,row_json FROM okx_reference_results WHERE trade_id=?',(row['trade_id'],)).fetchone()
    if prior:
        if prior['receipt_hash']!=digest or prior['row_json']!=encode(snapshot(row)):
            raise EvidenceError('Abweichende erneute EUR-Bewertung')
        return False
    source=encode(rates.document)
    con.execute('INSERT OR IGNORE INTO okx_reference_sources VALUES(?,?)',(rates.source_hash,source))
    saved=con.execute('SELECT source_json FROM okx_reference_sources WHERE source_hash=?',(rates.source_hash,)).fetchone()
    if saved[0]!=source:raise EvidenceError('Widerspruechlicher historischer Kursbeleg')
    con.execute('INSERT INTO okx_reference_results VALUES(?,?,?,?,?,?,?,?,?,?)',
        (row['trade_id'],receipt['account'],receipt['environment'],'EUR',METHOD,digest,
         encode(snapshot(row)),text,rates.source_hash,datetime.now(timezone.utc).isoformat()))
    return True


def calculate_composite(entry, sales, rates, *, account, environment):
    """EIN belegter Kauf, MEHRERE belegte Verkaufsorders, EUR-Summenbewertung.

    Jede Verkaufsorder wird wie in ``calculate`` einzeln behandelt (Beweis via
    prove_order, Bewertung jedes Fills ueber die identischen Referenzregeln);
    zusaetzlich muss die SUMME der Verkaufsmengen exakt zur Einstiegsmenge
    passen. Mehr verkauft als gekauft, doppelte Orders, Verkaeufe vor dem
    vollstaendigen Kauf oder Drittwaehrungsgebuehren brechen ab.
    """
    if rates.document.get('source_account') != account or rates.document.get('source_environment') != environment:
        raise EvidenceError('Kursnachlieferung gehoert nicht zur Handelsdomaene')
    entry = original_proof(entry, account)
    if entry['side'] != 'buy':
        raise EvidenceError('Unpassende Kaufkette')
    proofs = [original_proof(s, account) for s in sales]
    if not proofs:
        raise EvidenceError('Kein Verkaufsbeleg')
    if len({p['order_id'] for p in proofs}) != len(proofs):
        raise EvidenceError('Doppelte Verkaufsorder im Beleg')
    entry_start = max(stamp(f) for f in entry['fills'])
    for sale in proofs:
        if sale['side'] != 'sell' or sale['base'] != entry['base']:
            raise EvidenceError('Unpassende Kauf-/Verkaufskette')
        if entry_start > min(stamp(f) for f in sale['fills']):
            raise EvidenceError('Verkauf vor vollstaendigem Kauf')
    quantity = sum(number(p['quantity'], positive=True) for p in proofs)
    entry_qty = number(entry['quantity'], positive=True)
    ratio = quantity / entry_qty
    if ratio > 1 and not same_quantity(quantity, entry['quantity']):
        raise EvidenceError('Mehr Bestand verkauft als gekauft')
    flows = []

    def bewerte(proof):
        cash = value = Decimal(0)
        for fill in proof['fills']:
            quote = proof['currency']
            raw_value = number(fill['fillSz']) * number(fill['fillPx'])
            fee = number(fill['fee'])
            raw_cash = raw_value
            if fill['feeCcy'] == quote:
                raw_cash += -fee if proof['side'] == 'buy' else fee
            elif fill['feeCcy'] != proof['base'] and fee:
                raise EvidenceError('Drittwaehrungsgebuehr ohne eigenen Bewertungsbeleg')
            reference = rates.for_fill(quote, fill)
            rate = number(reference['eur_per_unit'], positive=True)
            value += raw_value * rate
            cash += raw_cash * rate
            flows.append(dict(side=proof['side'], order_id=proof['order_id'],
                fill_id=str(fill['tradeId']), native_currency=quote,
                native_cash=str(raw_cash), eur_cash=str(raw_cash * rate),
                reference=reference))
        return cash, value

    entry_cash, entry_value = bewerte(entry)
    sale_cash = sale_value = Decimal(0)
    gross_sale_qty = Decimal(0)
    for sale in proofs:
        c, v = bewerte(sale)
        sale_cash += c; sale_value += v
        gross_sale_qty += number(sale['gross_quantity'], positive=True)
    cost = entry_cash * ratio
    proceeds = sale_cash
    entry_price = entry_value / number(entry['gross_quantity'], positive=True)
    exit_price = sale_value / gross_sale_qty
    gross = (exit_price - entry_price) * quantity
    net = proceeds - cost
    currencies = sorted({entry['currency']} | {p['currency'] for p in proofs})
    return dict(method=METHOD_COMPOSITE, result_currency='EUR', account=account,
        environment=environment, entry_instrument=entry['inst_id'],
        exit_instruments=sorted({p['inst_id'] for p in proofs}),
        entry_order_id=entry['order_id'],
        exit_order_ids=sorted(p['order_id'] for p in proofs),
        quantity=str(quantity), entry_allocation=str(ratio),
        entry_cost_eur=str(cost), proceeds_eur=str(proceeds),
        entry_price_eur=str(entry_price), exit_price_eur=str(exit_price),
        gross_eur=str(gross), fees_eur=str(gross - net), net_eur=str(net),
        entered_at=min(stamp(f) for f in entry['fills']).isoformat(),
        closed_at=max(stamp(f) for p in proofs for f in p['fills']).isoformat(),
        cross_currency=(len(currencies) > 1), currencies=currencies,
        flows=flows, fx_source_hash=rates.source_hash,
        entry_proof=entry, exit_proofs=proofs,
        quality='REFERENCE_VALUATION', executed_fx_conversion=False)


def _native_consistency_composite(con, row, receipt):
    from okx_residual_inventory import entry_from_ledger
    entry = receipt['entry_proof']; sales = receipt['exit_proofs']
    ids = {fid for p in sales for fid in p['fill_ids']}
    # 10.2.1 Rev 2: Die Buchmenge darf um den im NATIVEN Beleg dokumentierten
    # Lot-Staub groesser sein als die verkaufte Menge (DOGE 17.09.: 7334,18847
    # im Buch, 7334,18 verkauft, 0,00847 verbleiben als Kontostaub). Der
    # Verbucher laesst genau diesen Rest zu; die EUR-Konsistenz muss ihn
    # einrechnen statt jede Bewertung mit 'passt nicht' abzuweisen.
    native = json.loads(row.get('exit_native_json') or '{}') if isinstance(
        row.get('exit_native_json'), str) else (row.get('exit_native_json') or {})
    native = (native or {}).get('receipt', {})
    dokumentierter_staub = number(native.get('residual') or 0)
    if dokumentierter_staub < 0:
        raise EvidenceError('Negativer Bestandsrest im nativen Beleg')
    if (row.get('broker') != 'okx' or row.get('broker_account_fingerprint') != receipt['account']
            or bool(row.get('paper')) != (receipt['environment'] == 'DEMO') or row.get('superseded_by') is not None
            or row.get('accounting_kind') != 'TRADE' or not row.get('ausgestiegen_am')
            or row.get('ownership_status') not in {'VERIFIED', 'BOT_VERIFIED', 'VERIFIED_BROKER_FILL_CHAIN'}
            or row.get('entry_order_id') != entry['order_id']
            or row.get('exit_order_id') != receipt['exit_order_ids'][0]
            or row.get('waehrung') != entry['currency'] or row.get('broker_position_id') != entry['inst_id']
            or not same_quantity(number(row['menge']),
                                 number(receipt['quantity']) + dokumentierter_staub)):
        raise EvidenceError('EUR-Beleg passt nicht zur aktuellen abgeschlossenen Handelszeile')
    local, _ = entry_from_ledger(con, row, proven_order_currency=entry['currency'])
    if set(local['fill_ids']) != set(entry['fill_ids']) or not same_quantity(local['cash'], entry['cash']):
        raise EvidenceError('EUR-Beleg widerspricht lokalen Kauf-Fills')
    if set(json.loads(row.get('exit_fill_ids_json') or '[]')) != ids:
        raise EvidenceError('EUR-Beleg widerspricht den zugeordneten Verkaufsfills')
    for fid in ids:
        claims = con.execute('''SELECT trade_id FROM trade_native_exit_fills WHERE broker='okx'
            AND account=? AND environment=? AND fill_id=?''',
            (receipt['account'], receipt['environment'], fid)).fetchall()
        if len(claims) != 1 or claims[0][0] != row['trade_id']:
            raise EvidenceError('Verkaufsfill ohne exklusiven Beleganspruch')
    if row['ausgestiegen_am'] != receipt['closed_at']:
        raise EvidenceError('Abschlusszeit widerspricht dem Ausfuehrungsbeleg')
    if native.get('beweisart') != 'ZUSAMMENGESETZTER_VERKAUF':
        raise EvidenceError('Zusammengesetzter Verkauf ohne nativen Beleg')
    if set(native.get('fill_ids') or []) != ids:
        raise EvidenceError('Nativer Beleg widerspricht den Verkaufsfills')
    legs = native.get('legs') or {}
    per_ccy = {}
    for p in sales:
        per_ccy[p['currency']] = per_ccy.get(p['currency'], Decimal(0)) + number(p['cash'])
    if set(legs) != set(per_ccy):
        raise EvidenceError('Native Waehrungs-Legs widersprechen den Verkaufsbelegen')
    for ccy, cash in per_ccy.items():
        if not same_quantity(legs[ccy].get('net_proceeds'), cash):
            raise EvidenceError('Nativer Erloes widerspricht dem Verkaufsbeleg')


def store_on_composite(con, row, receipt, rates):
    init_on(con)
    _native_consistency_composite(con, row, receipt)
    verified = calculate_composite(receipt['entry_proof'], receipt['exit_proofs'], rates,
        account=receipt['account'], environment=receipt['environment'])
    if encode(verified) != encode(receipt):
        raise EvidenceError('EUR-Berechnung stimmt nicht mit ihrem Nachweis ueberein')
    text = encode(receipt); digest = sha(text)
    prior = con.execute('SELECT receipt_hash,row_json FROM okx_reference_results WHERE trade_id=?', (row['trade_id'],)).fetchone()
    if prior:
        if prior['receipt_hash'] != digest or prior['row_json'] != encode(snapshot(row)):
            raise EvidenceError('Abweichende erneute EUR-Bewertung')
        return False
    source = encode(rates.document)
    con.execute('INSERT OR IGNORE INTO okx_reference_sources VALUES(?,?)', (rates.source_hash, source))
    saved = con.execute('SELECT source_json FROM okx_reference_sources WHERE source_hash=?', (rates.source_hash,)).fetchone()
    if saved[0] != source:
        raise EvidenceError('Widerspruechlicher historischer Kursbeleg')
    con.execute('INSERT INTO okx_reference_results VALUES(?,?,?,?,?,?,?,?,?,?)',
        (row['trade_id'], receipt['account'], receipt['environment'], 'EUR', METHOD_COMPOSITE,
         digest, encode(snapshot(row)), text, rates.source_hash,
         datetime.now(timezone.utc).isoformat()))
    return True


def load_on(con,row,*,currency='EUR'):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='okx_reference_results'").fetchone():
        return None
    saved=con.execute('SELECT * FROM okx_reference_results WHERE trade_id=?',(row['trade_id'],)).fetchone()
    if not saved:return None
    if (saved['account']!=row.get('broker_account_fingerprint') or saved['environment']!=('DEMO' if row.get('paper') else 'LIVE')
            or saved['currency']!=currency or saved['method'] not in METHODS or sha(saved['receipt_json'])!=saved['receipt_hash']
            or saved['row_json']!=encode(snapshot(row))):
        raise EvidenceError('EUR-Nachbeleg oder zugrunde liegende Handelszeile wurde veraendert')
    source=con.execute('SELECT source_json FROM okx_reference_sources WHERE source_hash=?',(saved['source_hash'],)).fetchone()
    if not source or sha(source[0])!=saved['source_hash']:
        raise EvidenceError('Historischer Kursnachweis fehlt oder wurde veraendert')
    receipt=json.loads(saved['receipt_json']);rates=Rates(json.loads(source[0]))
    if saved['method'] == METHOD_COMPOSITE:
        verified=calculate_composite(receipt['entry_proof'],receipt['exit_proofs'],rates,
            account=saved['account'],environment=saved['environment'])
        if encode(verified)!=saved['receipt_json']:
            raise EvidenceError('EUR-Nachbeleg laesst sich nicht aus Originaldaten nachrechnen')
        _native_consistency_composite(con,row,receipt)
        return {**receipt,'receipt_hash':saved['receipt_hash']}
    verified=calculate(receipt['entry_proof'],receipt['exit_proof'],rates,
        account=saved['account'],environment=saved['environment'])
    if encode(verified)!=saved['receipt_json']:
        raise EvidenceError('EUR-Nachbeleg laesst sich nicht aus Originaldaten nachrechnen')
    _native_consistency(con,row,receipt)
    return {**receipt,'receipt_hash':saved['receipt_hash']}
