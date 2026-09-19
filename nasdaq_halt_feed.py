"""Structured halt parsing with bounded evidence; incomplete never means no halts."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import re
import xml.etree.ElementTree as ET

FIELDS = ('issuesymbol', 'issuename', 'haltdate', 'halttime', 'reasoncode',
          'resumptiondate', 'resumptionquotetime', 'resumptiontradetime')


class FeedIncomplete(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        count = diagnostics['invalid_count']
        super().__init__(f'NASDAQ_HALTS_INCOMPLETE: {count} nicht eindeutig pruefbare '
                         f'Halt-Zeilen; {diagnostics["active_count"]} gueltige aktive Belege; '
                         'keine vollstaendige Entwarnung moeglich')


def _time(day, clock):
    for form in ('%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(f'{day} {clock}', form).replace(
                tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def inspect_feed(root, now):
    """Keep usable rows and concrete rejection reasons separate, for diagnosis.

    The returned items MUST NOT imply a complete feed when complete=False.
    Trading consumers use trading_evidence(); parse() remains the strict API.
    """
    from news_model import NewsItem, _clean_text, _safe_dt
    if now.tzinfo is None:
        raise ValueError('Nasdaq-Pruefzeit benoetigt Zeitzone')
    build = _safe_dt(root.findtext('./channel/lastBuildDate'))
    published = _safe_dt(root.findtext('./channel/pubDate'))
    clock_source = 'lastBuildDate' if build else 'pubDate' if published else None
    if build is None:
        build = published
    nodes = root.findall('.//item')
    report = {'schema_version': 1, 'provider': 'Nasdaq Halts',
              'observed_at': now.astimezone(timezone.utc).isoformat(),
              'feed_build_at': build.isoformat() if build else None,
              'feed_timestamp_source': clock_source,
              'feed_sha256': hashlib.sha256(ET.tostring(root, encoding='utf-8')).hexdigest(),
              'total_rows': len(nodes), 'checked_rows': min(len(nodes), 300),
              'truncated': len(nodes) > 300, 'complete': False,
              'absence_is_evidence': False, 'completeness_scope': 'STRUCTURED_ROWS_ONLY',
              'feed_freshness': 'UNKNOWN' if not build else 'CURRENT' if
                  now-timedelta(minutes=10) <= build <= now+timedelta(minutes=5) else 'STALE_OR_FUTURE',
              'active_count': 0, 'resumed_count': 0,
              'invalid_count': 0, 'invalid_rows': [], 'invalid_reason_counts': {},
              'active_rows': [], 'resumed_rows': [], 'feed_errors': []}
    report['unconfirmed_multiday_rows'] = []
    if root.tag != 'rss' or root.find('channel') is None:
        report['feed_errors'].append('INVALID_RSS')
    if build and (now - build > timedelta(minutes=10) or build > now + timedelta(minutes=5)):
        report['feed_errors'].append('STALE_OR_FUTURE_FEED_TIMESTAMP')
    if report['truncated']:
        report['feed_errors'].append('ROW_LIMIT_EXCEEDED')
    out = []
    for index, node in enumerate(nodes[:300]):
        fields = {c.tag.rsplit('}', 1)[-1].lower(): _clean_text(c.text) for c in node}
        symbol = fields.get('issuesymbol', '').upper()
        start = _time(fields.get('haltdate', ''), fields.get('halttime', ''))
        code = fields.get('reasoncode', '').upper()
        end = _time(fields.get('resumptiondate', ''), fields.get('resumptiontradetime', ''))
        reason = ''
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}', symbol):
            reason = 'INVALID_STRUCTURED_SYMBOL'
        elif not start:
            reason = 'INVALID_HALT_TIMESTAMP'
        elif not code:
            reason = 'MISSING_REASON_CODE'
        elif start > now + timedelta(minutes=5):
            reason = 'FUTURE_HALT_TIMESTAMP'
        elif fields.get('resumptiontradetime') and not end:
            reason = 'INVALID_RESUMPTION_TIMESTAMP'
        elif end and end < start:
            reason = 'RESUMPTION_BEFORE_HALT'
        elif not end and now - start > timedelta(hours=48) and report['feed_freshness'] != 'CURRENT':
            reason = 'MULTIDAY_HALT_WITHOUT_FRESH_FEED'
        if reason:
            if reason == 'MULTIDAY_HALT_WITHOUT_FRESH_FEED':
                report['unconfirmed_multiday_rows'].append({'symbol': symbol,
                    'halt_at': start.isoformat(), 'reason_code': code,
                    'detail': 'Datiertes Haltereignis vorhanden; Fortdauer aktuell nicht bestaetigt'})
            report['invalid_count'] += 1
            report['invalid_reason_counts'][reason] = report['invalid_reason_counts'].get(reason, 0) + 1
            if len(report['invalid_rows']) < 30:
                report['invalid_rows'].append({'row': index, 'code': reason,
                    'fields': {key: str(fields.get(key, ''))[:160] for key in FIELDS}})
            continue
        if end and end <= now:
            report['resumed_count'] += 1
            report['resumed_rows'].append({'symbol': symbol, 'halt_at': start.isoformat(),
                                          'resumes_at': end.isoformat()})
            continue
        item = NewsItem('Nasdaq Halts', f'{symbol}: trading halt ({code})',
            fields.get('issuename', ''), fields.get('link', ''), start, [symbol],
            1.0, official=True, metadata={'hard_signal': True, 'active_halt': True,
                'observed_at': now.isoformat(), 'resumes_at': end.isoformat() if end else ''})
        out.append(item)
        report['active_rows'].append({'symbol': symbol, 'reason_code': code,
            'halt_at': start.isoformat(), 'resumes_at': end.isoformat() if end else None})
    # A duplicate active row for the same event must not outlive its explicit
    # resumption. A later, distinct halt for that symbol remains a hard signal.
    resumed = {(r['symbol'], r['halt_at']) for r in report['resumed_rows']}
    out = [x for x in out if (x.symbols[0], x.published_at.isoformat()) not in resumed]
    report['active_rows'] = [r for r in report['active_rows'] if (r['symbol'], r['halt_at']) not in resumed]
    report['active_count'] = len(out)
    report['complete'] = not report['invalid_count'] and not report['feed_errors']
    report['status'] = ('OK' if build else 'TIME_UNKNOWN') if report['complete'] else 'INCOMPLETE'
    # Even a fully parsed RSS subset is not a guaranteed market-wide inventory.
    report['detail'] = ('Strukturierte Halt-/Wiederaufnahmefelder geprueft' if report['complete'] else
                        'Halt-Abdeckung unvollstaendig; gueltige Belege und verworfene Zeilen getrennt')
    return {'items': out, 'diagnostics': report}


def trading_evidence(root, now):
    """Return positive row evidence independently of inventory completeness.

    Missing build time permits recent dated rows, never old multiday rows.
    An explicitly stale/future feed cannot become current by being downloaded.
    Missing symbols never constitute clearance, even in a parsed RSS subset.
    """
    result = inspect_feed(root, now)
    report = result['diagnostics']
    if any(code in report['feed_errors'] for code in
           ('INVALID_RSS', 'STALE_OR_FUTURE_FEED_TIMESTAMP')):
        result['items'] = []
    report['usable_count'] = len(result['items'])
    for item in result['items']:
        item.metadata.update(feed_complete=report['complete'], absence_is_evidence=False,
                             feed_freshness=report['feed_freshness'],
                             feed_sha256=report['feed_sha256'])
    return result


def parse(root, now, *, on_diagnostics=None):
    result = inspect_feed(root, now)
    report = result['diagnostics']
    if on_diagnostics is not None:
        on_diagnostics(report)
    if not report['complete']:
        raise FeedIncomplete(report)
    return result['items']
