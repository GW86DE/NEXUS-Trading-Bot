import unittest
from candidate_gate import bewerte_kandidat

BASE=dict(
 state_allows_buy=True,broker_online=True,instrument_identity_ok=True,market_open=True,
 position_already_open=False,duplicate_open_order=False,risk_allows_buy=True,
 portfolio_allows_buy=True,cash_allows_buy=True,market_quality_ok=True,
 cost_quote_ok=True,net_edge_ok=True,quantity=10,price=100,stop=97,take_profit=106,
)

class CandidateGateTests(unittest.TestCase):
    def test_valid_candidate_passes(self):
        d=bewerte_kandidat(**BASE)
        self.assertTrue(d.approved)
        self.assertEqual(d.blocked_by,"")

    def test_failures_are_deterministic_and_ordered(self):
        x=dict(BASE); x["broker_online"]=False; x["net_edge_ok"]=False
        d=bewerte_kandidat(**x)
        self.assertFalse(d.approved); self.assertEqual(d.blocked_by,"broker_online")

    def test_all_money_path_gates_can_block(self):
        fields={
            "state_allows_buy":"state","broker_online":"broker_online","instrument_identity_ok":"instrument_identity",
            "market_open":"market_session","position_already_open":"existing_position","duplicate_open_order":"duplicate_open_order",
            "risk_allows_buy":"risk_manager","portfolio_allows_buy":"portfolio_guard","cash_allows_buy":"cash_reserve",
            "market_quality_ok":"market_quality","cost_quote_ok":"cost_quote","net_edge_ok":"net_edge",
        }
        true_means_block={"position_already_open","duplicate_open_order"}
        for field,reason in fields.items():
            with self.subTest(field=field):
                x=dict(BASE); x[field]=True if field in true_means_block else False
                d=bewerte_kandidat(**x)
                self.assertFalse(d.approved); self.assertEqual(d.blocked_by,reason)

    def test_bad_stop_take_geometry_is_blocked(self):
        x=dict(BASE); x["stop"]=101
        d=bewerte_kandidat(**x)
        self.assertFalse(d.approved); self.assertEqual(d.blocked_by,"order_geometry")

if __name__ == "__main__": unittest.main()
