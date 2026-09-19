"""Candidate-driven, bounded X research; never a trading decision.

Only PULSAR's dated, typed FMP identities can enter this queue. A sample is
neither a population sentiment measurement nor an independent confirmation.
No network calls here: the existing transactional X budget owns every request.
"""
import math
import re
import time

from . import store

CONTEXT = '__PULSAR_RESEARCH__'
# 10.8.0: Bestaetigungssuche auf Abruf -- eigener Kontext, eigenes Tageslimit.
CONFIRM_CONTEXT = '__PULSAR_CONFIRM__'
DAY = 86400
# 10.3.0: Fuenf Suchslots pro Tag (statt drei) -- das Budget der entfallenen
# X-Tageszaehlungen traegt die schnellere Hype-Erkennung.
SEARCHES_PER_DAY = 5
CONFIRMATIONS_PER_DAY = 8
INTERVAL = DAY // SEARCHES_PER_DAY
MAX_QUEUE = 12
POSITIVE = re.compile(r'\b(bullish|undervalued|upside|outperform|earnings beat|raises guidance|unterbewertet)\b', re.I)
NEGATIVE = re.compile(r'\b(bearish|overvalued|downside|underperform|earnings miss|cuts guidance|bankruptcy|ueberbewertet|überbewertet)\b', re.I)


def enqueue(selected, *, now=None):
    """Refresh current candidates without resetting their request history."""
    now = time.time() if now is None else now
    valid = []
    for row in selected[:5]:
        source = (row.get('profile_evidence') or {}).get('source') or {}
        profile = source.get('data') or {}
        symbol = (row.get('attention') or {}).get('symbol')
        stamp = source.get('observed_at')
        if (not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}', symbol)
                or source.get('provider') != 'FMP' or source.get('kind') != 'profile'
                or profile.get('symbol') != symbol
                or profile.get('isEtf') is not False or profile.get('isFund') is not False
                or type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not 0 <= now-stamp <= DAY
                or not re.fullmatch(r'[a-f0-9]{64}', str(source.get('id', '')))):
            continue
        # Never copy search operators from external profile text.
        name = re.sub(r'[^A-Za-z0-9 .&\-]', ' ', str(profile.get('companyName') or ''))
        name = ' '.join(name.split())[:70]
        if len(name) < 6 or not re.search('[A-Za-z]', name):
            name = ''
        valid.append({'symbol': symbol, 'company_name': name, 'selected_at': now,
            'profile_observed_at': stamp, 'profile_source_id': source['id'],
            'origin': str((row.get('selection') or {}).get('discovery_origin') or 'PULSAR')[:40]})
    with store.db() as con:
        old = {r['symbol']: r for r in store.value(con, 'pulsar_research_queue', [])}
        for row in valid:
            prior = old.get(row['symbol'], {})
            old[row['symbol']] = {**prior, **row, 'first_selected_at': prior.get('first_selected_at', now)}
        queue = sorted((r for r in old.values() if 0 <= now-r['selected_at'] <= DAY),
                       key=lambda r: -r['selected_at'])[:MAX_QUEUE]
        store.put(con, 'pulsar_research_queue', queue)
    return [r['symbol'] for r in valid]


def _queue(con, now):
    return [r for r in store.value(con, 'pulsar_research_queue', [])
            if 0 <= now-r['selected_at'] <= DAY and 0 <= now-r['profile_observed_at'] <= DAY]


def _validated_row(symbol, profile_source, now, origin):
    """Warteschlangenzeile nur aus einer datierten, typisierten FMP-Einzelaktien-Identitaet."""
    source = profile_source or {}
    profile = source.get('data') or {}
    stamp = source.get('observed_at')
    if (not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}', symbol)
            or source.get('provider') != 'FMP' or source.get('kind') != 'profile'
            or profile.get('symbol') != symbol
            or profile.get('isEtf') is not False or profile.get('isFund') is not False
            or type(stamp) not in (int, float) or not math.isfinite(stamp)
            or not 0 <= now-stamp <= DAY
            or not re.fullmatch(r'[a-f0-9]{64}', str(source.get('id', '')))):
        return None
    name = re.sub(r'[^A-Za-z0-9 .&\-]', ' ', str(profile.get('companyName') or ''))
    name = ' '.join(name.split())[:70]
    if len(name) < 6 or not re.search('[A-Za-z]', name):
        name = ''
    return {'symbol': symbol, 'company_name': name, 'selected_at': now,
            'profile_observed_at': stamp, 'profile_source_id': source['id'], 'origin': str(origin)[:40]}


def request_confirmation(symbol, profile_source, *, now=None):
    """Punkt 3 (10.8.0): EINE X-Bestaetigungssuche fuer ein Symbol anfordern.

    PULSAR ruft das fuer einen Ausloeser mit genau einer Bestaetigung im
    Einstiegsfenster. Hier wird nichts abgerufen: Die Zeile kommt mit
    ``confirm_requested_at`` in die bestehende Warteschlange; ``plan`` zieht
    sie beim naechsten Tick des X-Workers vor die Slot-Suchen. Tageslimit
    ``CONFIRMATIONS_PER_DAY``; das Monatsbudget bleibt die harte Grenze
    (``service._reserve``). Ohne gueltige Identitaet: nicht angenommen.
    """
    now = time.time() if now is None else now
    row = _validated_row(symbol, profile_source, now, 'PULSAR_BESTAETIGUNG')
    if row is None:
        return {'accepted': False, 'reason': 'FMP-Einzelaktien-Identitaet nicht belegt'}
    with store.db() as con:
        day_start = now - (now % DAY)
        used = con.execute("SELECT count(*) FROM requests WHERE context=? AND started>=?",
                           (CONFIRM_CONTEXT, day_start)).fetchone()[0]
        old = {r['symbol']: r for r in store.value(con, 'pulsar_research_queue', [])}
        pending = sum(1 for r in old.values() if r.get('confirm_requested_at')
                      and not r.get('confirm_attempted_at') and 0 <= now-r['confirm_requested_at'] <= DAY)
        if used + pending >= CONFIRMATIONS_PER_DAY:
            return {'accepted': False, 'reason': f'X-Bestaetigungssuchen heute ausgeschoepft ({CONFIRMATIONS_PER_DAY})'}
        prior = old.get(symbol, {})
        if prior.get('confirm_requested_at') and 0 <= now-prior['confirm_requested_at'] <= DAY:
            return {'accepted': False, 'reason': 'heute bereits angefordert'}
        old[symbol] = {**prior, **row, 'first_selected_at': prior.get('first_selected_at', now),
                       'confirm_requested_at': now, 'confirm_attempted_at': None}
        queue = sorted((r for r in old.values() if 0 <= now-r['selected_at'] <= DAY),
                       key=lambda r: -r['selected_at'])[:MAX_QUEUE]
        store.put(con, 'pulsar_research_queue', queue)
    return {'accepted': True, 'reason': 'angefordert; der X-Worker sucht beim naechsten Tick',
            'daily_used': used + pending + 1, 'daily_limit': CONFIRMATIONS_PER_DAY}


def _confirm_plan(con, now):
    due = [r for r in _queue(con, now) if r.get('confirm_requested_at') and not r.get('confirm_attempted_at')
           and 0 <= now-r['confirm_requested_at'] <= DAY]
    if not due:
        return None
    day_start = now - (now % DAY)
    used = con.execute("SELECT count(*) FROM requests WHERE context=? AND started>=?",
                       (CONFIRM_CONTEXT, day_start)).fetchone()[0]
    if used >= CONFIRMATIONS_PER_DAY:
        return None
    due.sort(key=lambda r: (r['confirm_requested_at'], r['symbol']))
    row = due[0]
    terms = ['$' + row['symbol']]
    if row['company_name']:
        terms.append('"' + row['company_name'] + '"')
    return {'query': '(' + ' OR '.join(terms) + ') -is:retweet', 'symbols': [row['symbol']],
            'candidates': [row], 'slot': 'confirm:' + row['symbol'] + ':' + str(int(row['confirm_requested_at'])),
            'context': CONFIRM_CONTEXT}


def plan(con, now):
    # 10.8.0: eine angeforderte Bestaetigungssuche geht vor die Slot-Suchen.
    confirm = _confirm_plan(con, now)
    if confirm:
        return confirm
    slot_start = int(now // INTERVAL) * INTERVAL
    if con.execute("SELECT 1 FROM requests WHERE context=? AND started>=?", (CONTEXT, slot_start)).fetchone():
        return None
    due = [r for r in _queue(con, now) if now-r.get('last_attempt', 0) >= INTERVAL]
    due.sort(key=lambda r: (r.get('last_attempt', 0), r['first_selected_at'], r['symbol']))
    candidates = due[:2]
    if not candidates:
        return None
    terms = []
    for row in candidates:
        terms.append('$' + row['symbol'])
        if row['company_name']:
            terms.append('"' + row['company_name'] + '"')
    return {'query': '(' + ' OR '.join(terms) + ') -is:retweet',
            'symbols': [r['symbol'] for r in candidates], 'candidates': candidates,
            'slot': str(int(now // INTERVAL)), 'context': CONTEXT}


def reserve_receipt(con, request, query, now):
    """Called inside the same transaction as the budget reservation."""
    # The caller has already inserted this reservation. Validate the query
    # against current identities rather than trusting context alone.
    queue = store.value(con, 'pulsar_research_queue', [])
    chosen = [r for r in _queue(con, now) if '$'+r['symbol'] in re.findall(r'\$[A-Z][A-Z0-9.\-]*', query)]
    if not 1 <= len(chosen) <= 2:
        raise ValueError('X-Kandidatenzuordnung ist nicht mehr gültig')
    receipt = {'request_id': request['id'], 'query_hash': request['query_hash'],
        'query': query, 'symbols': [r['symbol'] for r in chosen],
        'identities': {r['symbol']: r['company_name'] for r in chosen}, 'started': now}
    receipts = store.value(con, 'pulsar_research_requests', [])
    store.put(con, 'pulsar_research_requests', (receipts + [receipt])[-100:])
    for row in queue:
        if row['symbol'] in receipt['symbols']:
            row.update(last_attempt=now, request_id=request['id'], query_hash=request['query_hash'])
            if request.get('context') == CONFIRM_CONTEXT:
                row['confirm_attempted_at'] = now
                row['confirm_request_id'] = request['id']
    store.put(con, 'pulsar_research_queue', queue)


def matched_symbols(con, request, text, cashtags):
    receipt = next((r for r in store.value(con, 'pulsar_research_requests', [])
                    if r['request_id'] == request['id']), {})
    result = []
    for symbol, name in receipt.get('identities', {}).items():
        if symbol in cashtags or (name and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I)):
            result.append(symbol)
    return result


def for_symbol(con, symbol, now):
    row = next((r for r in store.value(con, 'pulsar_research_queue', []) if r['symbol'] == symbol), None)
    if row is None:
        return None
    request = con.execute('SELECT * FROM requests WHERE id=?', (row.get('request_id', ''),)).fetchone()
    receipt = next((r for r in store.value(con, 'pulsar_research_requests', [])
                    if r['request_id'] == row.get('request_id')), {})
    raw = list(con.execute('''SELECT DISTINCT p.* FROM posts p JOIN post_context c ON c.post_id=p.id
        WHERE c.symbol=? AND c.query_hash=? AND c.kind=? AND p.created>=? AND p.created<=?
        ORDER BY p.created DESC LIMIT 10''', (symbol, row.get('query_hash', ''), 'candidate:' + row.get('request_id', ''), now-DAY, now)))
    counts = {'positive': 0, 'negative': 0, 'mixed': 0, 'unclassified': 0}
    seen, accepted, excluded = set(), [], 0
    for post in raw:
        if post['spam'] or post['text_hash'] in seen:
            excluded += 1
            continue
        seen.add(post['text_hash'])
        positive, negative = bool(POSITIVE.search(post['text'])), bool(NEGATIVE.search(post['text']))
        if re.search(r'\b(not|never|kein|keine|nicht)\b|/s\b', post['text'], re.I):
            positive = negative = False  # Negation/sarcasm requires semantic review, not a directional claim.
        label = 'mixed' if positive and negative else 'positive' if positive else 'negative' if negative else 'unclassified'
        counts[label] += 1
        accepted.append({'post_id': post['id'], 'url': 'https://x.com/i/web/status/'+post['id'],
            'created_at': post['created'], 'evidence_hash': post['text_hash'], 'category': label,
            'topic': post['topic'], 'author': post['author']})
    fresh = bool(request and request['status'] == 'OK' and 0 <= now-request['started'] <= DAY)
    usable = fresh and bool(accepted)
    sentiment = ('MIXED' if counts['mixed'] or (counts['positive'] and counts['negative']) else
                 'POSITIVE_HINT' if counts['positive'] else 'NEGATIVE_HINT' if counts['negative'] else 'UNCLASSIFIED') if usable else 'UNKNOWN'
    state = ('STALE_CANDIDATE' if now-row['selected_at'] > DAY else
             'QUEUED' if not request else 'COLLECTING' if request['status'] == 'RESERVED' else
             'STALE' if request['status'] == 'OK' and not fresh else
             'PROCESSED' if usable else 'NO_MATCHING_SAMPLE' if fresh else request['status'])
    return {'symbol': symbol, 'company_name': row['company_name'], 'origin': row['origin'],
        'selected_at': row['selected_at'], 'profile_source_id': row['profile_source_id'],
        'confirmation': {k: row.get(k) for k in ('confirm_requested_at', 'confirm_attempted_at', 'confirm_request_id')}
        if row.get('confirm_requested_at') else None,
        'state': state, 'request_id': row.get('request_id'), 'query': receipt.get('query'),
        'query_hash': row.get('query_hash'), 'last_attempt': row.get('last_attempt'),
        'processed_at': request['finished'] if request else None,
        'request_posts_received': request['received'] if request else 0,
        'matched_posts': len(raw), 'usable_posts': len(accepted) if fresh else 0,
        'excluded_duplicates_or_spam': excluded, 'sentiment': sentiment, 'categories': counts,
        'distinct_accounts_in_sample': len({p['author'] for p in accepted}),
        'evidence': accepted[:5] if fresh else [], 'source_family': 'X',
        'coverage': 'BOUNDED_SAMPLE', 'trade_effect': False, 'primary_source_confirmed': False,
        'assessment': 'Unbestätigter Recherchehinweis; unabhängig abgleichen' if usable else 'Keine aktuelle auswertbare Stichprobe',
        'method': 'Konservative Schlagwortkategorien; keine repräsentative Marktstimmung, keine Kauf-/Verkaufsentscheidung'}


def snapshot(con, now):
    queue = _queue(con, now)
    receipts = store.value(con, 'pulsar_research_requests', [])
    return {'mode': 'PULSAR_CANDIDATE_RESEARCH', 'source_family': 'X', 'trade_effect': False,
        'queue_size': len(queue), 'max_queue': MAX_QUEUE, 'searches_per_day_max': SEARCHES_PER_DAY,
        'confirmations_per_day_max': CONFIRMATIONS_PER_DAY,
        'symbols_per_search_max': 2, 'next_slot_at': (int(now//INTERVAL)+1)*INTERVAL,
        'plan_due': plan(con, now),
        'candidates': [for_symbol(con, r['symbol'], now) for r in queue],
        'recent_queries': [{k: r[k] for k in ('request_id', 'query', 'symbols', 'started')} for r in receipts[-6:]],
        'detail': 'Auswahl aus PULSAR mit geprüftem FMP-Profil. Fünf zusätzliche gebündelte Suchen pro Tag; alle Abrufe teilen sich das X-Monatsbudget.'}
