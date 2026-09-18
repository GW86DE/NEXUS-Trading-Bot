"""Automatic discovery is sampled research, with persistent spending bounds."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from market_intelligence import service as x, store, discovery_candidates
from market_intelligence.source_registry import ACCOUNTS, search_plan

NOW = 1789401600.0


def post(identity="201", symbol="AGI", author="501", text=None, created=NOW-300):
    return {"id": identity, "author_id": author, "created_at": x.iso(created),
            "text": text or "$"+symbol+" earnings guidance improves",
            "entities": {"urls": [{"expanded_url": "http://www.example.com/earnings/?secret=hidden#fragment"}]}}


def profile(symbol, now=NOW, **changes):
    return {"id": "f"*64, "provider": "FMP", "kind": "profile", "observed_at": now,
            "data": {"symbol": symbol, "isEtf": False, "isFund": False, **changes}}


class DiscoveryTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.root = mock.patch.object(store, "ROOT", self.path)
        self.root.start()
        self.clock = mock.patch.object(x.time, "time", return_value=NOW)
        self.clock.start()
        x.save_settings({"enabled": True, "pricing_acknowledged": True}, bearer_token="fake-offline-token")

    def tearDown(self):
        self.clock.stop()
        self.root.stop()
        self.tmp.cleanup()

    def insert(self, rows, context="__STOCK_DISCOVERY__", now=NOW):
        return x._posts({"context": context, "query_hash": "sample"}, {"data": rows}, now)

    def test_plan_discovers_without_manual_stock_selection(self):
        plans = [search_plan(i) for i in range(3)]
        self.assertIn("from:realDonaldTrump", plans[0][0])
        self.assertIn("from:WhiteHouse", plans[0][0])
        self.assertIn("from:SECGov", plans[1][0])
        self.assertIn("from:USTreasury", plans[1][0])
        self.assertEqual(plans[2][1], "__STOCK_DISCOVERY__")
        self.assertNotIn("$NVDA", plans[2][0])
        self.assertEqual(x.get_settings()["symbols"], [])
        self.assertFalse(x.get_settings()["manual_symbols_required"])
        self.assertLess(x.get_settings()["estimated_month_eur"], 15)
        self.assertTrue(all(row["reference"].startswith("https://") for row in ACCOUNTS))

    def test_real_sample_yields_unverified_candidate_then_stable_automatic_cohort(self):
        self.insert([post()])
        seed = discovery_candidates(now=NOW)[0]
        self.assertEqual(seed["symbol"], "AGI")
        self.assertEqual(seed["sampled_post_count"], 1)
        self.assertEqual(seed["status"], "UNVERIFIED")
        self.assertFalse(seed["direct_trade_effect"])
        self.assertFalse(seed["calibrated_spike"])
        self.assertNotIn("mentions", seed)
        self.assertNotIn("earnings guidance improves", json.dumps(seed))
        with store.db() as con:
            effective = x._refresh_monitoring(con, x._settings(con), NOW)
        self.assertEqual(effective["symbols"], [])  # unverified cashtags cannot consume counts slots
        self.assertTrue(x.record_candidate_validation("AGI", profile("AGI"), now=NOW)["monitoring"])
        result = x.for_symbol("AGI", now=NOW)
        self.assertIsNone(result["attention"]["count"])
        self.assertFalse(result["attention"]["baseline_ready"])
        self.assertEqual(x.public_status()["discovery"]["candidate_count"], 1)

    def test_duplicate_spam_stuffing_and_expired_posts_do_not_inflate_discovery(self):
        self.insert([post(), post(identity="202", author="502"),
            post(identity="203", symbol="BAD", text="$BAD guaranteed profit join my telegram group"),
            post(identity="204", text="$AAA $BBB $CCC $DDD $EEE $FFF $GGG earnings"),
            post(identity="205", symbol="OLD", created=NOW-2*x.DAY)])
        seeds = discovery_candidates(now=NOW)
        self.assertEqual([r["symbol"] for r in seeds], ["AGI"])
        self.assertEqual(seeds[0]["sampled_post_count"], 1)
        self.assertEqual(seeds[0]["duplicate_text_count"], 1)
        self.assertFalse(seeds[0]["independence_confirmed"])
        self.assertEqual(discovery_candidates(now=NOW+2*x.DAY), [])

    def test_deleted_or_disabled_source_cannot_keep_discovery_seeds(self):
        self.insert([post()])
        self.assertEqual(len(discovery_candidates(now=NOW)), 1)
        x.save_settings({"enabled": False})
        self.assertEqual(discovery_candidates(now=NOW), [])
        x.save_settings({"enabled": True})
        x.delete_evidence(post_id="201")
        self.assertEqual(discovery_candidates(now=NOW), [])

    def test_edited_post_drops_old_symbol_links(self):
        self.insert([post()])
        self.insert([post(symbol="MSFT")])
        self.assertEqual([r["symbol"] for r in discovery_candidates(now=NOW)], ["MSFT"])
        self.assertEqual(x.for_symbol("AGI", now=NOW), {})

    def test_link_fingerprint_has_no_query_fragment_credentials_or_private_host(self):
        self.insert([post()])
        seed = discovery_candidates(now=NOW)[0]
        self.assertEqual(seed["linked_url_hashes"], [sha256(b"https://example.com/earnings").hexdigest()])
        self.assertNotIn("secret", json.dumps(x.export_diagnostics()))
        for url in ("http://127.0.0.1/a", "http://localhost/a", "http://user:pw@example.com/a"):
            self.assertIsNone(x.canonical_url_hash(url))

    def test_daily_counts_are_retired_even_under_parallel_query_churn(self):
        # 10.3.0: Die taeglichen Zaehlungsabrufe (14-Tage-Baseline) sind
        # abgeschaltet; keine Reservierung kommt mehr durch.
        with ThreadPoolExecutor(max_workers=8) as pool:
            reservations = list(pool.map(lambda i: x._reserve("counts", "$S"+str(i), "S"+str(i), str(i), NOW), range(30)))
        self.assertEqual(sum(r is not None for r in reservations), 0)
        x.save_settings({"symbols": ["PEP"]})
        self.assertIsNone(x._reserve("counts", "$PEP", "PEP", "changed", NOW))
        self.assertAlmostEqual(x.public_status()["budget"]["reserved_eur"], 0.0)

    def test_repeated_missing_day_is_diagnostic_error_not_zero_or_warming(self):
        end = int(NOW//x.DAY)*x.DAY
        req = {"query_hash": "coverage", "context": "AGI", "id": "offline"}
        body = {"data": [{"start": x.iso(end-x.DAY), "end": x.iso(end), "tweet_count": 0}]}
        for now in (NOW, NOW+1, NOW+x.DAY):
            self.assertEqual(x._counts(req, body, end-2*x.DAY, end, now)[0], "TEILWEISE")
        row = x.public_status()["counts_coverage"][0]
        self.assertEqual(row["state"], "MISSING_DAYS")
        self.assertEqual(row["consecutive_incomplete_days"], 2)
        self.assertEqual(len(row["missing_days"]), 1)
        with store.db(readonly=True) as con:
            values = con.execute("SELECT count FROM attention ORDER BY day").fetchall()
        self.assertEqual([r[0] for r in values], [None, 0])

    def test_control_inconsistency_and_epoch_are_explicit(self):
        x.save_settings({"symbols": ["AGI"]})
        end = int(NOW//x.DAY)*x.DAY
        for symbol, query in x._queries(x.get_settings()):
            req = {"query_hash": x.digest({"query": query, "version": 1}), "context": symbol, "id": symbol}
            body = {"data": [{"start": x.iso(end-x.DAY), "end": x.iso(end), "tweet_count": 10 if symbol.startswith("__") else 20}]}
            x._counts(req, body, end-x.DAY, end, NOW)
        attention = x.for_symbol("AGI", now=NOW)["attention"]
        self.assertEqual(attention["normalization_status"], "INCONSISTENT_CONTROL")
        self.assertEqual(len(attention["inconsistent_control_days"]), 1)
        self.assertIsNone(attention["normalized_ratio"])
        self.assertEqual(attention["daily_samples"][0]["count"], 20)
        before = x.public_status()["normalization"]
        x.save_settings({"symbols": ["AGI", "PEP"]})
        after = x.public_status()["normalization"]
        self.assertNotEqual(before["control_query_hash"], after["control_query_hash"])
        self.assertEqual(after["reason"], "MONITORING_COHORT_CHANGED")
        self.assertEqual(after["previous_control_query_hash"], before["control_query_hash"])

    def test_counts_normalization_requires_control_for_all_28_calendar_days(self):
        x.save_settings({"symbols": ["AGI"]})
        end = int(NOW//x.DAY)*x.DAY
        hashes = {}
        for symbol, query in x._queries(x.get_settings()):
            qhash = hashes[symbol] = x.digest({"query": query, "version": 1})
            req = {"query_hash": qhash, "context": symbol, "id": symbol}
            body = {"data": [{"start": x.iso(day), "end": x.iso(day+x.DAY),
                "tweet_count": 100 if symbol.startswith("__") else 10}
                for day in range(end-29*x.DAY, end, x.DAY)]}
            x._counts(req, body, end-29*x.DAY, end, NOW)
        self.assertEqual(x.for_symbol("AGI", now=NOW)["attention"]["normalized_ratio"], 1)
        # Remove a weekday control while the newest measurement is a weekend:
        # same-day-type comparisons alone must not hide this coverage hole.
        missing = next(x.iso(end-d*x.DAY)[:10] for d in range(2, 10)
            if x.utc(end-d*x.DAY).weekday() < 5)
        with store.db() as con:
            con.execute("DELETE FROM attention WHERE symbol='__MARKET_CONTROL__' AND day=?", (missing,))
        result = x.for_symbol("AGI", now=NOW)["attention"]
        self.assertTrue(result["baseline_ready"])
        self.assertEqual(result["long_ratio"], 1)
        self.assertIsNone(result["normalized_ratio"])
        self.assertIn(missing, result["missing_control_days"])

    def test_real_tick_schedule_is_bounded_with_automatic_symbols(self):
        calls = []
        tick_now = NOW

        def fake_http(session, endpoint, params, token):
            calls.append((endpoint, params))
            if endpoint == x.COUNTS:
                start, end = int(x._parse_time(params["start_time"])), int(x._parse_time(params["end_time"]))
                body = {"data": [{"start": x.iso(d), "end": x.iso(d+x.DAY), "tweet_count": 1}
                    for d in range(start, end, x.DAY)]}
            else:
                body = {"data": [post(created=tick_now-300)]}
            return 200, body, "offline-hash", {}

        next_day = int(NOW//x.DAY)*x.DAY+x.DAY
        # 10.3.0: 12-Stunden-Slots (2 allgemeine Suchen pro Tag), keine
        # Zaehlungsabrufe mehr; die drei Kategorien rotieren ueber die Slots.
        with mock.patch.object(x, "_http", side_effect=fake_http):
            for slot in range(3):
                for tick in range(20):
                    tick_now = next_day+slot*12*3600+60+tick*30
                    x.tick(now=tick_now, session=object())
                    if discovery_candidates(now=tick_now):
                        x.record_candidate_validation("AGI", profile("AGI", tick_now), now=tick_now)
        self.assertFalse([params for endpoint, params in calls if endpoint == x.COUNTS])
        searches = [params for endpoint, params in calls if endpoint == x.SEARCH]
        self.assertEqual(len(searches), 3)
        self.assertTrue(all(p["max_results"] == 10 for p in searches))
        queries = " || ".join(p["query"] for p in searches)
        self.assertIn("from:realDonaldTrump", queries)
        self.assertIn("from:SECGov", queries)
        self.assertIn("earnings", queries)
        self.assertEqual(x.get_settings()["symbols"], [])
        with mock.patch.object(x.time, "time", return_value=tick_now):
            self.assertIn("AGI", x.public_status()["discovery"]["monitoring_symbols"])
            self.assertLessEqual(x.public_status()["budget"]["charged_upper_eur"], .28)

    def test_unverified_cashtags_cannot_poison_automatic_monitor_capacity(self):
        rows = [post(identity=str(800+n), symbol="FAKE"+str(n)) for n in range(12)]
        self.insert(rows[:10])
        self.insert(rows[10:]+[post()])
        with store.db() as con:
            self.assertEqual(x._refresh_monitoring(con, x._settings(con), NOW)["symbols"], [])
        self.assertFalse(x.record_candidate_validation("FAKE0", profile("FAKE0", isEtf="false"), now=NOW)["accepted"])
        self.assertFalse(x.record_candidate_validation("FAKE1", profile("FAKE1", isFund=True), now=NOW)["accepted"])
        self.assertFalse(x.record_candidate_validation("FAKE2", profile("FAKE2", NOW-2*x.DAY), now=NOW)["accepted"])
        self.assertTrue(x.record_candidate_validation("AGI", profile("AGI"), now=NOW)["monitoring"])
        self.assertEqual(x.public_status()["discovery"]["monitoring_symbols"], ["AGI"])
        self.assertFalse(x.record_candidate_validation("AGI", profile("AGI", isEtf=True), now=NOW)["monitoring"])
        self.assertEqual(x.public_status()["discovery"]["monitoring_symbols"], [])
