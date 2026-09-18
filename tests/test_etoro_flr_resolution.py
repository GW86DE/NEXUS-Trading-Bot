import unittest
from broker.etoro import EtoroBroker
from broker.base import NichtUnterstuetzt
from contracts import Instrument, SimpleContract


def inst(symbol, kind):
    return Instrument(symbol, SimpleContract(symbol), kind, "USD", "crypto" if kind=="crypto" else "industrials", "ETORO")

class EtoroFLRResolutionTests(unittest.TestCase):
    def setUp(self):
        self.b=EtoroBroker(paper=True,api_key="x",user_key="y")
        self.b._all_instruments_loaded=True
        self.b._instrument_candidates={
            "FLR":[
                {"instrumentId":101,"symbolFull":"FLR.US","symbol":"FLR.US","_detected_asset_type":"stock","instrumentType":"Stock"},
                {"instrumentId":202,"symbolFull":"FLR","symbol":"FLR","_detected_asset_type":"crypto","instrumentType":"Crypto"},
            ]
        }

    def test_flr_stock_resolves_only_fluor(self):
        row=self.b._resolve(inst("FLR","stock"))
        self.assertEqual(row["instrumentId"],101)
        self.assertEqual(row["symbolFull"],"FLR.US")
        self.assertEqual(row["_bot_asset_type"],"stock")

    def test_flr_crypto_resolves_only_crypto(self):
        row=self.b._resolve(inst("FLR","crypto"))
        self.assertEqual(row["instrumentId"],202)
        self.assertEqual(row["symbolFull"],"FLR")
        self.assertEqual(row["_bot_asset_type"],"crypto")

    def test_crypto_cannot_fall_back_to_flr_us(self):
        b=EtoroBroker(paper=True,api_key="x",user_key="y")
        b._all_instruments_loaded=True
        b._instrument_candidates={"FLR":[{"instrumentId":101,"symbolFull":"FLR.US","_detected_asset_type":"stock"}]}
        b._request=lambda *a,**k:{"items":[{"instrumentId":101,"symbolFull":"FLR.US","instrumentType":"Stock"}]}
        with self.assertRaises(NichtUnterstuetzt):
            b._resolve(inst("FLR","crypto"))

if __name__ == "__main__": unittest.main()
