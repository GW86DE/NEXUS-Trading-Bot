import pandas as pd
from datetime import datetime, timezone, timedelta

import config
from universe_review import review_proposal


class FakeReviewBroker:
    name="etoro"
    def __init__(self,bars=220,kind="Stock",display="Example Corp"):
        self.bars=bars; self.kind=kind; self.display=display
    def is_connected(self): return True
    def instrument_metadata(self,inst):
        return {"instrument_id":77,"symbol_full":f"{inst.name}.US","asset_type":"stock" if "stock" in self.kind.lower() else "crypto","instrument_type":self.kind,"display_name":self.display}
    def instrument_handelbar(self,inst): return True,""
    def latest_bid_ask(self,inst): return {"bid":99.9,"ask":100.1,"source":"eToro"}
    def historie(self,inst,dauer,bar,nur_handelszeiten=True):
        idx=pd.date_range(end=pd.Timestamp.now(tz="UTC"),periods=self.bars,freq="B")
        return pd.DataFrame({"open":100.0,"high":101.0,"low":99.0,"close":100.0,"volume":300_000.0},index=idx)
    def dynamic_cost_quote(self,inst,quantity,price,action="open"):
        return {"components":{"marketSpread":1.0,"markup":0.0,"transactionFee":0.0},"last_updated":"now"}


def _proposal(): return {"symbol":"XYZ","company":"Example Corp","sector":"Industrie"}


def test_deterministic_review_passes_liquid_established_etoro_stock(monkeypatch):
    monkeypatch.setattr(config,"UNIVERSE_APPROVAL_MIN_AVG_DOLLAR_VOLUME",20_000_000.0)
    monkeypatch.setattr(config,"UNIVERSE_APPROVAL_MIN_MEDIAN_DOLLAR_VOLUME",10_000_000.0)
    r=review_proposal(FakeReviewBroker(),_proposal(),set())
    assert r.passed
    assert r.etoro_symbol_full=="XYZ.US"
    assert r.history_bars>=180
    assert r.avg_dollar_volume>=20_000_000
    assert r.cost_source.startswith("etoro_what_if")


def test_review_blocks_fresh_ipo_by_history_length():
    r=review_proposal(FakeReviewBroker(bars=80),_proposal(),set())
    assert not r.passed and "frische IPOs" in r.summary


def test_review_blocks_warrant_or_spac_metadata():
    r=review_proposal(FakeReviewBroker(kind="Stock Warrant",display="Example Warrant"),_proposal(),set())
    assert not r.passed and "SPAC/Warrant" in r.summary


def test_review_blocks_existing_universe_without_broker_calls():
    r=review_proposal(FakeReviewBroker(),_proposal(),{"stock:XYZ"})
    assert not r.passed and "bereits" in r.summary
