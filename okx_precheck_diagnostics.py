"""Bounded GET-only inspection diagnostics; no response text or credentials."""
import re


PATHS = frozenset({
    '/public/time', '/account/config', '/account/balance', '/account/positions',
    '/trade/orders-pending', '/trade/orders-algo-pending', '/trade/fills',
    '/trade/fills-history', '/trade/orders-history-archive',
})
VALUES = {
    'instType': {'SPOT', 'MARGIN', 'SWAP', 'FUTURES', 'OPTION'},
    'ordType': {'conditional', 'oco', 'trigger', 'move_order_stop'},
    'limit': {'1'},
}


def instrument_client(client, error_type):
    """Wrap one dedicated precheck client; never wrap a trading client."""
    original = client.request
    response_meta = {}

    def capture(response, *args, **kwargs):
        status = getattr(response, 'status_code', None)
        if isinstance(status, int) and 100 <= status <= 599:
            response_meta['http'] = status
        try:
            payload = response.json()
            code = payload.get('code') if isinstance(payload, dict) else None
            if re.fullmatch(r'[0-9]{1,6}', str(code)):
                response_meta['code'] = str(code)
        except (ValueError, TypeError):
            pass
        return response

    client.session.hooks.setdefault('response', []).append(capture)

    def request(method, path, **kwargs):
        if (method != 'GET' or path not in PATHS or kwargs.get('body') is not None
                or kwargs.get('is_order')):
            raise error_type('Vorpruefung blockiert: nur freigegebene GET-Abfragen erlaubt')
        params = kwargs.get('params') or {}
        if any(k not in VALUES or str(v) not in VALUES[k] for k, v in params.items()):
            raise error_type('Vorpruefung blockiert: unbekannte Pruefparameter')
        response_meta.clear()
        label = 'GET /api/v5' + path
        if params:
            label += ' [' + ', '.join(k+'='+str(v) for k, v in params.items()) + ']'
        try:
            return original(method, path, **kwargs)
        except Exception as exc:
            # Deliberately exclude str(exc), response msg/data, headers and URLs.
            kind = type(exc).__name__
            if not re.fullmatch(r'[A-Za-z_]{1,64}', kind):
                kind = 'Anfragefehler'
            raise error_type(
                label + ': HTTP=' + str(response_meta.get('http', 'unbekannt'))
                + ', OKX-Code=' + str(response_meta.get('code', 'unbekannt'))
                + ', Fehlerart=' + kind
            ) from None

    client.request = request
    return client
