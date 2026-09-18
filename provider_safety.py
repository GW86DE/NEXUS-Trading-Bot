"""Provider messages are untrusted and can echo credentials."""
from __future__ import annotations
import re
import logging
import math
from urllib.parse import quote


def redact(value, secrets=(), *, max_chars=800):
    text = str(value or '')
    values = list(secrets)
    try:
        import live_settings as ls
        values.extend(fn() for fn in (ls.alpha_vantage_key, ls.finnhub_key,
                                      ls.fmp_key, ls.massive_key))
        import config
        values.append(getattr(config, 'OPENAI_API_KEY', ''))
    except Exception as exc:
        logging.getLogger(__name__).debug("Credential lookup unavailable: %s", type(exc).__name__)
    for value in sorted({str(x) for x in values if x and len(str(x)) >= 4}, key=len, reverse=True):
        text = text.replace(value, '[MASKIERT]').replace(quote(value, safe=''), '[MASKIERT]')
    text = re.sub(r'(?i)([?&](?:api_?key|token|access_token|secret)=)[^\s&#]+', r'\1[MASKIERT]', text)
    text = re.sub(r'(?i)(\b(?:api[ _-]?key|access[ _-]?token|authorization)\s*(?:is|as|ist|:|=)\s*)(?:bearer\s+)?[^\s,;]+', r'\1[MASKIERT]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{10,}', '[MASKIERT]', text)
    # Apply masking before truncation. The bounded log-tail reader may request
    # more text; all existing provider/error callers retain the 800-char limit.
    try:
        limit = max(0, min(250_000, int(max_chars)))
    except (ValueError, TypeError, OverflowError):
        limit = 800
    return text[:limit]


def validate_json(value, schema):
    """Validate the limited JSON-schema vocabulary used by NEXUS locally."""
    typ = schema.get('type')
    expected = {'object': dict, 'array': list, 'string': str, 'boolean': bool,
                'number': (int, float), 'integer': int, 'null': type(None)}
    if isinstance(typ, list):
        for t in typ:
            try:
                validate_json(value, {**schema, 'type': t})
                return
            except ValueError:
                pass
        raise ValueError('Antworttyp entspricht nicht dem Schema')
    if typ in expected and (not isinstance(value, expected[typ]) or
            typ in ('number', 'integer') and isinstance(value, bool)):
        raise ValueError('Antworttyp entspricht nicht dem Schema')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Antwortwert ausserhalb des Schemas')
    if isinstance(value, dict):
        if any(k not in value for k in schema.get('required', [])):
            raise ValueError('Pflichtfelder fehlen in der KI-Antwort')
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is False and set(value) - set(props):
            raise ValueError('Unerlaubte Antwortfelder')
        for k, v in value.items():
            if k in props:
                validate_json(v, props[k])
    if isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10000):
            raise ValueError('Ungueltige Antwortlaenge')
        for v in value:
            validate_json(v, schema.get('items', {}))
    if isinstance(value, str):
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 100000):
            raise ValueError('Ungueltige Textlaenge')

    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if not math.isfinite(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ValueError("Ungueltiger Zahlenwert in der KI-Antwort")
