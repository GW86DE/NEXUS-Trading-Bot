import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import decision_analytics as da
from order_execution import submit_protected_buy
from broker.base import BrokerFehler, OrderErgebnis, OrderStatusUnklar
from contracts import Instrument, SimpleContract

class FakeSuccessBroker:
    name="etoro"; paper=True
    def __init__(self): self.calls=0
    def kaufe_mit_absicherung(self,*args,**kwargs):
        self.calls+=1
        return OrderErgebnis(order_ids=["order-1"],status="filled",filled_quantity=56.9273,
            avg_fill_price=52.47,stop_order_platziert=True,take_order_platziert=True,
            reference_id="ref-flr-1",position_ids=["3584875792"],paper=False)

class FakeUncertainBroker:
    name="etoro"; paper=True
    def __init__(self): self.calls=0
    def kaufe_mit_absicherung(self,*args,**kwargs):
        self.calls+=1
        raise OrderStatusUnklar("transport after POST",reference_id="ref-unknown",order_ids=["maybe-1"])

class ExecutionJournalTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.env=patch.dict("os.environ", {"TRADINGBOT_TEST_STATE_DIR": self.td.name})
        self.env.start(); self.addCleanup(self.env.stop)
        self.old=da.DB_PATH; da.DB_PATH=Path(self.td.name)/"decisions.sqlite"; self.addCleanup(setattr,da,"DB_PATH",self.old)
        da.init_db()
        self.inst=Instrument("FLR",SimpleContract("FLR"),"stock","USD","industrials","ETORO")

    def approved_id(self):
        return da.record({"status":"APPROVED","symbol":"FLR","asset_type":"stock","broker":"etoro","paper":True,"price":52.67,"reason":"all gates passed"})

    def test_real_fill_persists_actual_broker_identity_and_live_flag(self):
        did=self.approved_id(); b=FakeSuccessBroker()
        out=submit_protected_buy(b,self.inst,56.9273,52.67,51.0687,54.9991,decision_id=did)
        self.assertEqual(out.status,"FILLED"); self.assertEqual(b.calls,1)
        row=da.latest(1)[0]
        self.assertEqual(row["execution_status"],"FILLED")
        self.assertEqual(row["order_ids"],["order-1"])
        self.assertEqual(row["broker_reference_id"],"ref-flr-1")
        self.assertEqual(row["position_ids_json"], '["3584875792"]')
        self.assertAlmostEqual(row["fill_price"],52.47)
        self.assertAlmostEqual(row["fill_qty"],56.9273)
        self.assertEqual(row["paper"],0)

    def test_ambiguous_submit_is_never_retried_and_is_recoverable(self):
        did=self.approved_id(); b=FakeUncertainBroker()
        with self.assertRaises(OrderStatusUnklar):
            submit_protected_buy(b,self.inst,1,52.67,51,55,decision_id=did)
        self.assertEqual(b.calls,1)
        row=da.latest(1)[0]
        self.assertEqual(row["execution_status"],"UNKNOWN_AFTER_SUBMIT")
        self.assertEqual(row["order_ids"],["maybe-1"])
        self.assertEqual(row["broker_reference_id"],"ref-unknown")

    def test_definitive_rejection_terminalizes_intent_and_clears_context(self):
        class RejectedBroker:
            name="etoro"; paper=True
            def kaufe_mit_absicherung(self,*args,**kwargs):
                raise BrokerFehler("validation rejected")
        did=self.approved_id(); broker=RejectedBroker()
        with self.assertRaises(BrokerFehler):
            submit_protected_buy(broker,self.inst,1,52.67,51,55,decision_id=did)
        import etoro_reconciliation as rec
        record=rec.status()["records"][0]
        self.assertEqual(record["state"],"FAILED")
        self.assertFalse(hasattr(broker,"_nexus_submit_context"))
        rec.assert_domain_available(paper=True,profile="any-profile")

    def test_uncertain_submit_clears_context_but_keeps_account_lock(self):
        did=self.approved_id(); broker=FakeUncertainBroker()
        with self.assertRaises(OrderStatusUnklar):
            submit_protected_buy(broker,self.inst,1,52.67,51,55,decision_id=did)
        self.assertFalse(hasattr(broker,"_nexus_submit_context"))
        import etoro_reconciliation as rec
        with self.assertRaises(BrokerFehler):
            rec.assert_domain_available(paper=True,profile="different-profile")

if __name__ == "__main__": unittest.main()
