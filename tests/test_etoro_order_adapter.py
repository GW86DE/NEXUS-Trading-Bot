import unittest
from unittest.mock import patch

import config
from broker.etoro import EtoroBroker
from broker.base import BrokerFehler, OrderStatusUnklar, VerbindungVerloren
from contracts import Instrument, SimpleContract


def stock(symbol="FLR"):
    return Instrument(symbol, SimpleContract(symbol), "stock", "USD", "industrials", "ETORO")


class EtoroOrderAdapterTests(unittest.TestCase):
    def _broker(self):
        b = EtoroBroker(paper=False, api_key="key", user_key="user")
        b._connected = True
        b._resolve = lambda inst: {"instrumentId": 101, "symbol": "FLR.US", "symbolFull": "FLR.US"}
        b._settlement = lambda inst: "real"
        b._validate_protection = lambda *a, **k: None
        return b

    def test_real_fill_is_parsed_with_position_id_and_no_retry(self):
        b = self._broker()
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/api/v3/trading/execution/orders")
            self.assertFalse(kwargs.get("safe_retry"))
            return {"orderId": 555}

        b._request = fake_request
        b._wait_open_order = lambda **kwargs: {
            "status": {"id": 3, "name": "filled"},
            "positionExecutions": [{
                "positionId": 3584875792,
                "openingData": {
                    "units": 56.9273,
                    "avgPrice": 52.47,
                    "fees": 0.1,
                    "taxes": 0.0,
                    "executionTime": "2026-08-20T18:58:00Z",
                },
            }],
        }
        # Dieser Test prueft ausschliesslich Order-/Fill-Parsing. Der
        # produktive Adapter liest den Schutz danach zwingend per positionId
        # zurueck; das wird in den Identitaets-Regressionen separat getestet.
        b.reconcile_position_protection = lambda *_a, **_k: {
            "protection_confirmed": True}
        with patch.object(config, "ETORO_REQUIRE_COST_QUOTE", False), \
                patch.object(config, "ETORO_REQUIRE_LIVE_PROTECTION_QUOTE", False):
            result = b.kaufe_mit_absicherung(stock(), 56.9273, 52.67, 51.0687, 54.9991)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.position_ids, ["3584875792"])
        self.assertAlmostEqual(result.filled_quantity, 56.9273)
        self.assertAlmostEqual(result.avg_fill_price, 52.47)
        self.assertFalse(result.paper)
        self.assertEqual(len(b._fill_queue), 1)
        self.assertEqual(b._fill_queue[0].symbol, "FLR.US")
        self.assertEqual(b._fill_queue[0].broker_id, "3584875792")

    def test_transport_ambiguity_never_reposts(self):
        b = self._broker()
        post_calls = 0

        def fail_post(method, path, **kwargs):
            nonlocal post_calls
            post_calls += 1
            raise VerbindungVerloren("lost after POST")

        b._request = fail_post
        b._lookup_order = lambda **kwargs: (_ for _ in ()).throw(BrokerFehler("not found yet"))
        with patch.object(config, "ETORO_REQUIRE_COST_QUOTE", False), \
                patch.object(config, "ETORO_REQUIRE_LIVE_PROTECTION_QUOTE", False), \
                patch("broker.etoro.time.sleep", return_value=None):
            with self.assertRaises(OrderStatusUnklar) as ctx:
                b.kaufe_mit_absicherung(stock(), 1.0, 52.67, 51.0, 55.0)
        self.assertEqual(post_calls, 1)
        self.assertTrue(ctx.exception.reference_id)


if __name__ == "__main__":
    unittest.main()
