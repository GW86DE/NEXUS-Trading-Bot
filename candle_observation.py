"""Bounded observations of existing OKX reads, never a market-data fetcher.

Only public candle numbers and whitelisted routing context are retained. Runtime
quality is scoped to environment, instrument, timeframe and consumer. Evidence
writes are best effort/non-blocking and cannot change an order decision.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading

MAX_RECEIPTS = 128
MAX_ROWS = 10
MAX_FILE_BYTES = 2_000_000
logger = logging.getLogger(__name__)
_LOCK = threading.RLock()
_QUALITY: OrderedDict = OrderedDict()
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_LABEL = re.compile(r"[A-Za-z0-9_.:-]{1,80}\Z")


def domain(base_url, environment):
    return hashlib.sha256((str(base_url) + ':' + str(environment)).encode()).hexdigest()


def _utc(seconds):
    return datetime.fromtimestamp(float(seconds), timezone.utc).isoformat()


def _label(value):
    text = str(value)
    return text if _LABEL.fullmatch(text) else 'UNKNOWN'


def _numeric(value):
    """Retain literal numeric representation; reject arbitrary text/credentials."""
    text = str(value)
    return text if len(text) <= 60 and _NUMBER.fullmatch(text) else '[INVALID]'


def _path():
    return Path(os.environ.get('TRADINGBOT_TEST_STATE_DIR') or Path(__file__).resolve().parent) / 'candle_observations.json'


def _persist(receipt):
    # The global cap applies across clients/processes. An occupied file lock is
    # skipped rather than delaying trading/position management.
    import fcntl
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix('.lock')
    if target.is_symlink() or lock_path.is_symlink():
        return
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    temporary = None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        previous = []
        try:
            if target.stat().st_size <= MAX_FILE_BYTES:
                previous = json.loads(target.read_text()).get('receipts', [])
        except (OSError, ValueError, AttributeError):
            pass
        # Keep one newest response per exact scope/endpoint. Old observations
        # cannot grow forever and must never be confused with the next response.
        key = lambda r: (r.get('domain'), r.get('instrument'), r.get('timeframe'), r.get('endpoint'))
        previous = [r for r in previous if isinstance(r, dict) and key(r) != key(receipt)]
        payload = {'schema': 1, 'mode': 'EXISTING_PUBLIC_REQUESTS_ONLY',
                   'max_receipts': MAX_RECEIPTS, 'receipts': (previous + [receipt])[-MAX_RECEIPTS:]}
        encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()
        if len(encoded) > MAX_FILE_BYTES:
            return
        out_fd, temporary = tempfile.mkstemp(prefix='.candle_observations-', dir=target.parent)
        with os.fdopen(out_fd, 'wb') as stream:
            stream.write(encoded)
        os.replace(temporary, target)
        temporary = None
        return True
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        os.close(fd)


def _is_zero(value):
    try:
        return math.isfinite(float(value)) and float(value) == 0
    except (ValueError, TypeError, OverflowError):
        return False


def record_response(*, base_url, environment, endpoint, instrument, timeframe,
                    started, received, http_status, code, rows):
    """Only call with a response already obtained by the normal client."""
    if endpoint not in {'/market/candles', '/market/history-candles'}:
        return None
    data = rows if isinstance(rows, list) else []
    safe = [[_numeric(v) for v in row[:9]] if isinstance(row, (list, tuple)) else ['[INVALID_ROW]']
            for row in data[:300]]
    digest = hashlib.sha256(json.dumps(safe, separators=(',', ':')).encode()).hexdigest()
    selected = list(range(min(8, len(safe))))
    selected += [i for i in range(max(8, len(safe)-2), len(safe)) if i not in selected]
    receipt = {'schema': 1, 'broker': 'okx', 'domain': domain(base_url, environment),
               'environment': _label(environment), 'instrument': _label(instrument),
               'timeframe': _label(timeframe), 'endpoint': '/api/v5' + endpoint,
               'request_started_utc': _utc(started), 'response_received_utc': _utc(received),
               'duration_seconds': max(0.0, float(received)-float(started)),
               'http_status': int(http_status), 'broker_code': _label(code),
               'response_rows': len(data), 'hashed_rows': len(safe),
               'confirmation_counts_cover_rows': len(safe), 'response_truncated_before_hash': len(data) > len(safe),
               'selected_fields_sha256': digest,
               'row_fields': ['ts', 'open', 'high', 'low', 'close', 'volume', 'volume_currency', 'volume_quote', 'confirm'],
               'raw_zero_volume_rows': sum(len(r) > 5 and r[5] is not None and _is_zero(r[5]) for r in safe),
               'volume_provenance': 'UNMODIFIED_BROKER_RESPONSE',
               'confirmed_rows': sum(len(r) > 8 and r[8] == '1' for r in safe),
               'unconfirmed_rows': sum(len(r) > 8 and r[8] == '0' for r in safe),
               'confirmation_missing_or_invalid_rows': sum(len(r) <= 8 or r[8] not in {'0','1'} for r in safe),
               'sample': [{'response_row_index': i, 'values': safe[i]} for i in selected[:MAX_ROWS]],
               'sample_truncated': len(data) > MAX_ROWS}
    receipt['receipt_id'] = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    persisted = False
    try:
        persisted = _persist(receipt) is True
    except Exception as exc:
        # Evidence is optional; never log transport data or reflected secrets.
        logger.debug("Kerzenbeleg nicht speicherbar (%s)", type(exc).__name__)
    return {**{k: receipt[k] for k in ('receipt_id', 'selected_fields_sha256', 'response_received_utc',
                                     'endpoint', 'domain', 'environment', 'instrument', 'timeframe',
                                     'raw_zero_volume_rows', 'hashed_rows', 'volume_provenance')},
            'diagnostic_receipt_saved': persisted}


def quality(frame, *, base_url, environment, instrument, timeframe='5m', seconds=300,
            now=None, consumer='signal_history'):
    """Describe observed rows; freshness and activity are independent facts."""
    import time
    now = time.time() if now is None else float(now)
    n = 0 if frame is None else len(frame)
    result = {'broker': 'okx', 'domain': domain(base_url, environment),
              'environment': str(environment), 'instrument': str(instrument),
              'timeframe': str(timeframe), 'timeframe_seconds': seconds, 'consumer': consumer, 'observed_at': _utc(now),
              'rows': n, 'real_rows': None, 'synthetic_gap_rows': None,
              'zero_volume_rows': None, 'zero_volume_ratio': None,
              'recent_rows': min(n, 12), 'recent_active_rows': None, 'flat_close': None,
              'latest_open_utc': None, 'latest_close_utc': None, 'latest_age_seconds': None,
              'freshness': 'MISSING', 'reasons': [],
              'entry_effect': 'EXISTING_STRATEGY_VOLUME_AND_FRESHNESS_GATES',
              'confirmation': 'UNKNOWN'}
    if not n:
        result['reasons'] = ['CANDLES_MISSING']
        return result
    try:
        ts = frame.index[-1]
        if ts.tzinfo is None:
            raise ValueError('no timezone')
        last = ts.timestamp()
        volume = frame['volume'].astype(float)
        closes = frame['close'].astype(float)
        if not all(math.isfinite(float(v)) and float(v) >= 0 for v in volume):
            raise ValueError('invalid volume')
        if not all(math.isfinite(float(v)) and float(v) > 0 for v in closes):
            raise ValueError('invalid close')
        synthetic = frame.get('synthetic_gap')
        result.update(latest_open_utc=_utc(last), latest_close_utc=_utc(last+seconds),
                      latest_age_seconds=max(0.0, now-last),
                      zero_volume_rows=int((volume == 0).sum()),
                      zero_volume_ratio=float((volume == 0).sum())/n,
                      recent_active_rows=int((volume.tail(12) > 0).sum()),
                      flat_close=int(closes.nunique()) == 1)
        if synthetic is not None:
            result.update(synthetic_gap_rows=int(synthetic.sum()), real_rows=int((~synthetic.astype(bool)).sum()))
        elif frame.attrs.get('broker_rows_only') is True:
            result.update(synthetic_gap_rows=0, real_rows=n)
        result['confirmation'] = frame.attrs.get('confirmation', 'UNKNOWN')
        result['source_receipt'] = frame.attrs.get('source_receipt')
        result['freshness'] = 'FUTURE_OR_OPEN' if last+seconds > now else ('CURRENT' if now-last <= 3*seconds else 'STALE')
        if result['freshness'] != 'CURRENT':
            result['reasons'].append('CANDLES_' + result['freshness'])
        if result['recent_active_rows'] == 0:
            result['reasons'].append('NO_RECENT_CANDLE_VOLUME')
        if result['flat_close']:
            result['reasons'].append('FLAT_CANDLE_HISTORY')
        if result['synthetic_gap_rows']:
            result['reasons'].append('SYNTHETIC_GAP_CANDLES')
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        result['freshness'] = 'INVALID'
        result['reasons'].append('CANDLE_QUALITY_UNAVAILABLE')
    return result


def publish(result):
    key = (result['domain'], result['instrument'], result['timeframe'], result['consumer'])
    with _LOCK:
        _QUALITY[key] = dict(result)
        _QUALITY.move_to_end(key)
        while len(_QUALITY) > MAX_RECEIPTS:
            _QUALITY.popitem(last=False)


def snapshot(*, base_url, environment, consumer='signal_history', now=None):
    import time
    now = time.time() if now is None else float(now)
    expected = domain(base_url, environment)
    with _LOCK:
        result = [dict(v) for v in _QUALITY.values() if v['domain'] == expected and v['consumer'] == consumer]
    for item in result:
        # A stopped scan must not stay CURRENT forever just because the last
        # quality observation was fresh when it was made.
        if item.get('latest_open_utc') and item.get('freshness') != 'INVALID':
            last = datetime.fromisoformat(item['latest_open_utc']).timestamp()
            seconds = item['timeframe_seconds']
            item['latest_age_seconds'] = max(0.0, now-last)
            item['freshness'] = 'FUTURE_OR_OPEN' if last+seconds > now else ('CURRENT' if now-last <= 3*seconds else 'STALE')
            item['reasons'] = [r for r in item['reasons'] if not r.startswith('CANDLES_')]
            if item['freshness'] != 'CURRENT':
                item['reasons'].append('CANDLES_' + item['freshness'])
    return result
