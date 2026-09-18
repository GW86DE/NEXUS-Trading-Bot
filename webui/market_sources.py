"""Passive, bounded source status. Opening a page never queries a provider."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time

from NEXUS_10_Diagnose import Scrubber, load_json, source_pipeline_report


def snapshot():
    root = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or Path(__file__).resolve().parents[1])
    scrub = Scrubber()
    news = load_json(root / "news_source_status.json", scrub).get("data", {})
    runtime = load_json(root / "runtime_status.json", scrub).get("data", {})
    rows = []
    cache_quality = "MISSING"
    path = root / "pulsar_research.sqlite"
    if path.is_file() and not path.is_symlink():
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as con:
                con.execute("PRAGMA query_only=ON")
                con.execute("PRAGMA trusted_schema=OFF")
                con.row_factory = sqlite3.Row
                deadline = time.monotonic() + 1.5
                con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                rows = [dict(r) for r in con.execute("SELECT key,saved,expires,"
                    "CASE WHEN length(payload)<=CASE WHEN key='top5' THEN 16777216 ELSE 262144 END "
                    "THEN payload ELSE NULL END AS payload "
                    "FROM cache WHERE key IN ('top5','coverage','status','candidate_selection') OR key LIKE 'source_status:%' "
                    "OR key LIKE 'backoff:%' OR key LIKE 'social:%' "
                    "ORDER BY CASE WHEN key IN ('top5','coverage','status','candidate_selection') THEN 0 ELSE 1 END,key LIMIT 50")]
                cache_quality = "BOUNDED_CACHE_SNAPSHOT"
        except (OSError, sqlite3.Error):
            cache_quality = "UNREADABLE"
    from market_intelligence import public_status
    x = public_status()
    out = source_pipeline_report(news, rows, x, now=time.time(),
        news_risk_evidence=runtime.get("news_risk_evidence") if isinstance(runtime, dict) else None)
    out["cache_quality"] = cache_quality
    return scrub.clean(out)
