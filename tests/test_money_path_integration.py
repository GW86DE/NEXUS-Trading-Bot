"""Fake-eToro Integration des kritischen Geldpfads ohne echte Netzwerkzugriffe."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import config
import decision_analytics as da
import notifier
from candidate_gate import bewerte_kandidat
from contracts import Instrument, SimpleContract
from instrument_identity import canonical_key
from order_execution import submit_protected_buy
from broker.base import OrderErgebnis

class FakeEtoro:
    name='etoro'
    def __init__(self): self.calls=0
    def kaufe_mit_absicherung(self,inst,qty,price,stop,take):
        self.calls+=1
        self.asserted=(inst.asset_type,inst.name,qty,price,stop,take)
        return OrderErgebnis(order_ids=['et-order'],status='filled',filled_quantity=qty,avg_fill_price=52.47,
            stop_order_platziert=True,take_order_platziert=True,reference_id='ref-e2e',position_ids=['3584875792'],paper=False)

class MoneyPathIntegrationTests(unittest.TestCase):
    def test_flr_stock_e2e_gate_submit_journal_and_telegram_queue(self):
        with tempfile.TemporaryDirectory() as td, patch.dict('os.environ',{'TRADINGBOT_TEST_STATE_DIR':td}), \
             patch.object(config,'NOTIFY_TELEGRAM',True),patch.object(config,'TELEGRAM_BOT_TOKEN','t'),patch.object(config,'TELEGRAM_CHAT_ID','c'), \
             patch.object(notifier,'_send_one',return_value=(False,'NETWORK test',5.0)):
            old=da.DB_PATH; da.DB_PATH=Path(td)/'decision.sqlite'
            try:
                inst=Instrument('FLR',SimpleContract('FLR'),'stock','USD','industrials','ETORO')
                self.assertEqual(canonical_key(inst.name,inst.asset_type),'stock:FLR')
                gate=bewerte_kandidat(state_allows_buy=True,broker_online=True,instrument_identity_ok=True,market_open=True,
                    position_already_open=False,duplicate_open_order=False,risk_allows_buy=True,portfolio_allows_buy=True,
                    cash_allows_buy=True,market_quality_ok=True,cost_quote_ok=True,net_edge_ok=True,
                    quantity=56.9273,price=52.67,stop=51.0687,take_profit=54.9991)
                self.assertTrue(gate.approved)
                did=da.record({'status':'APPROVED','symbol':'FLR','asset_type':'stock','broker':'etoro','paper':True,'price':52.67,'reason':'gate passed'})
                b=FakeEtoro(); out=submit_protected_buy(b,inst,56.9273,52.67,51.0687,54.9991,decision_id=did)
                self.assertEqual(out.status,'FILLED'); self.assertEqual(b.calls,1)
                notifier.notify_trade('KAUF',f'KAUF FLR.US · Position {out.result.position_ids[0]} · Fill {out.result.avg_fill_price:.2f}',bundle=True)
                row=da.latest(1)[0]
                self.assertEqual(row['execution_status'],'FILLED'); self.assertEqual(row['paper'],0)
                self.assertEqual(row['broker_reference_id'],'ref-e2e')
                import json
                q=json.loads((Path(td)/config.TELEGRAM_QUEUE_FILE).read_text())['queue']
                self.assertEqual(len(q),1); self.assertIn('3584875792',q[0]['message'])
            finally:
                da.DB_PATH=old

if __name__ == "__main__": unittest.main()
