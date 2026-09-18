from pathlib import Path

import config
import decision_analytics as da
from strategy_analyst import StrategyAnalyst


def test_strategy_snapshot_uses_sample_sizes_and_ai_can_be_off(monkeypatch,tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR",str(tmp_path))
    old=da.DB_PATH; da.DB_PATH=Path(tmp_path)/"decisions.sqlite"
    try:
        for i in range(25):
            did=da.record({"status":"BLOCKED","blocked_by":"net_edge","symbol":f"X{i%3}","asset_type":"stock","broker":"etoro","paper":True,"price":100.0,"sector":"Industrie","regime":"NEUTRAL","evaluated_filters":[{"name":"market_session","status":"PASS_OR_NOT_APPLICABLE"},{"name":"net_edge","status":"BLOCKED"}]})
            da.set_outcome(did,"1d",102.0,source="test")
        snap=da.strategy_snapshot(90)
        row=next(x for x in snap["forward_performance"] if x["group"]=="net_edge" and x["horizon"]=="1d")
        assert row["n"]==25
        assert row["sample_quality"]=="hinweis"
        assert row["median_return_pct"]>1.9
        monkeypatch.setattr(config,"STRATEGY_ANALYST_USE_AI",False)
        analyst=StrategyAnalyst(); report,meta=analyst.run()
        assert report.exists()
        text=report.read_text(encoding="utf-8")
        assert "keinerlei Tradingparameter automatisch aendern" in text
        assert meta["interpretation"] is None
    finally:
        da.DB_PATH=old
