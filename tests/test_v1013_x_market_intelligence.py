"""Offline budget, coverage, provenance, privacy and fault-boundary tests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
import json
import unittest

from market_intelligence import service as x
from market_intelligence import store

NOW = 1789401600.0  # 2026-09-14 16:00 UTC


class Response:
    def __init__(self, body, status=200):
        self.content = json.dumps(body).encode()
        self.status_code = status
        self.headers = {}
    def json(self):
        return json.loads(self.content)
    def close(self):
        pass


class Session:
    def __init__(self, response=None):
        self.response = response
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.response is not None:
            return self.response
        params = kwargs["params"]
        if url.endswith("counts/recent"):
            start, end = x._parse_time(params["start_time"]), x._parse_time(params["end_time"])
            return Response({"data": [{"start": x.iso(s), "end": x.iso(s+x.DAY), "tweet_count": 0}
                             for s in range(int(start), int(end), x.DAY)]})
        return Response({"meta": {"result_count": 1}, "data": [{"id": "101", "author_id": "301",
            "created_at": x.iso(NOW-300), "text": "$NVDA $AMD earnings outlook and quarterly revenue",
            "entities": {"urls": [{"expanded_url": "https://investor.example/earnings"}]}}]})


class XTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.root = mock.patch.object(store, "ROOT", self.path)
        self.root.start()
        self.clock = mock.patch.object(x.time, "time", return_value=NOW)
        self.clock.start()
    def tearDown(self):
        self.clock.stop()
        self.root.stop()
        self.tmp.cleanup()
    def enable(self, **changes):
        return x.save_settings({"enabled": True, "pricing_acknowledged": True,
            "symbols": ["NVDA", "AMD"], **changes}, bearer_token="private-token-never-export")

    def test_public_status_without_configuration_never_creates_files(self):
        self.assertEqual(x.public_status()["state"], "DISABLED")
        self.assertFalse(x.get_settings()["token_configured"])
        self.assertEqual(list(self.path.iterdir()), [])

    def test_settings_reject_cost_escalation_and_query_injection(self):
        for changes in ({"monthly_budget_eur": 15.01}, {"monthly_budget_eur": float("nan")},
                        {"symbols": ["NVDA OR from:evil"]}, {"priority_accounts": ["a) OR (b"]},
                        {"posts_per_request": 100}, {"searches_per_day": 30}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                x.save_settings(changes)
        with self.assertRaises(ValueError):
            x.save_settings({"enabled": True})

    def test_restart_rejects_invalid_persisted_settings_before_reserving_or_http(self):
        self.enable()
        with store.db(readonly=True) as con:
            valid = json.loads(con.execute("SELECT payload FROM settings WHERE id=1").fetchone()[0])
        invalid = [{**valid, "monthly_budget_eur": 150}, {**valid, "enabled": "false"},
            {**valid, "monthly_budget_eur": float("nan")}, {**valid, "posts_per_request": 100},
            {**valid, "symbols": ["NVDA OR from:untrusted"]},
            {**valid, "foreign_secret": "private-token-never-export"},
            {**valid, "pricing_acknowledged_at": float("inf")}, [valid], "bad object"]
        for payload in invalid:
            with self.subTest(payload_type=type(payload).__name__):
                with store.db() as con:
                    con.execute("UPDATE settings SET payload=? WHERE id=1", (json.dumps(payload),))
                session = Session()
                self.assertFalse(x.tick(now=NOW, session=session))
                self.assertIsNone(x._reserve("search", "$NVDA", "NVDA", "restart", NOW))
                self.assertEqual(session.calls, [])
                status = x.public_status()
                self.assertEqual(status["state"], "CONFIGURATION_ERROR")
                self.assertFalse(status["settings"]["enabled"])
                self.assertEqual(status["settings"]["monthly_budget_eur"], 15)
                self.assertNotIn("foreign_secret", json.dumps(status))
                self.assertNotIn("private-token", json.dumps(status))
        with store.db() as con:
            con.execute("UPDATE settings SET payload='not-json' WHERE id=1")
        self.assertEqual(x.public_status()["state"], "CONFIGURATION_ERROR")

    def test_settings_and_receipt_exports_never_include_token(self):
        self.enable()
        x.tick(now=NOW, session=Session())
        for filename in ("market_intelligence_status.json", "market_intelligence_settings.json"):
            self.assertNotIn("private-token", (self.path/filename).read_text())
        self.assertNotIn("private-token", json.dumps(x.export_diagnostics()))
        self.assertEqual((self.path/"market_intelligence_credentials.json").stat().st_mode & 0o777, 0o600)
        before = (self.path/"market_intelligence.sqlite").stat().st_mtime_ns
        x.public_status()
        self.assertEqual(before, (self.path/"market_intelligence.sqlite").stat().st_mtime_ns)

    def test_one_shared_request_is_reserved_across_threads(self):
        # 10.3.0: Zaehlungsabrufe existieren nicht mehr; die atomare
        # Reservierung gilt fuer Suchabrufe.
        self.enable()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: x._reserve("search", "$NVDA", "ctx", "today", NOW), range(8)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(x.public_status()["budget"]["reserved_eur"], .065)

    def test_counts_requests_are_retired_with_the_baseline(self):
        self.enable()
        self.assertIsNone(x._reserve("counts", "$NVDA", "NVDA", "today", NOW))

    def test_budget_survives_reopen_and_unknown_outcome_is_not_refunded(self):
        self.enable(monthly_budget_eur=.14)
        for i in range(2):
            self.assertIsNotNone(x._reserve("search", "general "+str(i), "ctx", str(i), NOW))
        self.assertIsNone(x._reserve("search", "general 2", "ctx", "2", NOW))
        self.assertEqual(x.public_status()["budget"]["reserved_eur"], .13)
        x.save_settings({"enabled": False})
        x.save_settings({"enabled": True})
        self.assertIsNone(x._reserve("search", "general 3", "ctx", "3", NOW))

    def test_errors_pause_source_without_throwing_or_repeating(self):
        self.enable()
        session = Session(Response({"error": "private-token-never-export"}, 401))
        self.assertTrue(x.tick(now=NOW, session=session))
        self.assertFalse(x.tick(now=NOW+60, session=session))
        status = x.public_status()
        self.assertEqual(status["state"], "AUTH_ERROR")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(status["budget"]["charged_upper_eur"], .065)
        self.assertFalse(status["trade_effect"])
        self.assertNotIn("private-token", json.dumps(status))

    def test_rate_limit_cooldown_and_network_timeout_keep_reservation(self):
        self.enable()
        session = Session(Response({}, 429))
        self.assertTrue(x.tick(now=NOW, session=session))
        self.assertEqual(x.public_status()["state"], "RATE_LIMITED")
        self.assertFalse(x.tick(now=NOW+60, session=session))
        self.assertEqual(len(session.calls), 1)

    def test_network_exception_is_isolated_and_keeps_maximum_cost(self):
        self.enable()
        session = mock.Mock()
        session.get.side_effect = TimeoutError("private-token-never-export")
        self.assertTrue(x.tick(now=NOW, session=session))
        self.assertFalse(x.tick(now=NOW+60, session=session))
        status = x.public_status()
        self.assertEqual(status["state"], "NETWORK_ERROR")
        self.assertEqual(status["budget"]["charged_upper_eur"], .065)
        self.assertNotIn("private-token", json.dumps(status))
        self.assertEqual(session.get.call_count, 1)

    def test_malformed_count_response_does_not_become_zero_activity(self):
        self.enable()
        x.tick(now=NOW, session=Session(Response({"data": "unexpected"})))
        status = x.public_status()
        self.assertEqual(status["state"], "INVALID_RESPONSE")
        self.assertIsNone(status["attention"][0]["count"])
        self.assertEqual(status["attention"][0]["coverage_status"], "NICHT_ABGEFRAGT")

    def test_transport_is_fixed_get_no_expansion_redirect_or_user_lookup(self):
        # 10.3.0: Keine Zaehlungsabrufe mehr -- pro 12-Stunden-Slot genau
        # eine allgemeine Suche; wiederholte Ticks erzeugen keinen zweiten Call.
        self.enable()
        session = Session()
        for _ in range(5):
            x.tick(now=NOW, session=session)
        self.assertEqual(len(session.calls), 1)
        for url, kwargs in session.calls:
            self.assertEqual(url, "https://api.x.com"+x.SEARCH)
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["timeout"], (4, 10))
            self.assertNotIn("expansions", kwargs["params"])
        self.assertEqual(session.calls[-1][1]["params"]["max_results"], 10)

    def test_observed_zero_is_valid_missing_day_is_not_zero(self):
        # 10.3.0: tick() erzeugt keine Zaehlungsabrufe mehr. Die
        # Zaehlungs-Verarbeitung selbst behandelt fehlende Tage weiterhin als
        # NICHT_ABGEFRAGT und niemals als Null.
        self.enable()
        day = int(NOW//x.DAY)*x.DAY
        request = {"query_hash": "new", "context": "AMD", "id": "test"}
        status, _ = x._counts(request, {"data": [{"start": x.iso(day-x.DAY), "end": x.iso(day), "post_count": 12}]}, day-2*x.DAY, day, NOW)
        self.assertEqual(status, "TEILWEISE")
        with store.db(readonly=True) as con:
            missing = con.execute("SELECT count,coverage FROM attention WHERE query_hash='new' ORDER BY day").fetchone()
        self.assertEqual(tuple(missing), (None, "NICHT_ABGEFRAGT"))

    def test_paginated_counts_never_claim_complete(self):
        self.enable()
        day = int(NOW//x.DAY)*x.DAY
        body = {"data": [{"start": x.iso(day-x.DAY), "end": x.iso(day), "tweet_count": 10}], "meta": {"next_token": "more"}}
        coverage, _ = x._counts({"query_hash": "q", "context": "NVDA", "id": "test"}, body, day-x.DAY, day, NOW)
        self.assertEqual(coverage, "TEILWEISE")

    def test_query_hash_changes_do_not_reuse_control_baseline(self):
        self.enable()
        session = Session()
        for _ in range(3):
            x.tick(now=NOW, session=session)
        before = x.for_symbol("NVDA", now=NOW)["attention"]
        x.save_settings({"symbols": ["NVDA"]})
        after = x.for_symbol("NVDA", now=NOW)["attention"]
        self.assertEqual(before["query_hash"], after["query_hash"])
        self.assertNotEqual(before["market_control_query_hash"], after["market_control_query_hash"])
        self.assertIsNone(after["market_query_share"])

    def test_samples_feed_both_symbols_with_deduplicated_storage(self):
        self.enable()
        session = Session()
        for _ in range(4):
            x.tick(now=NOW, session=session)
        for symbol in ("NVDA", "AMD"):
            packet = x.for_symbol(symbol, now=NOW)
            self.assertEqual(packet["argument_clusters"][0]["event_type"], "COMPANY_EARNINGS")
            self.assertFalse(packet["trade_effect"])
        self.assertEqual(len(x.event_hints(now=NOW)), 1)
        # Same post returned under another query keeps one raw row and n:m links.
        request = {"context": "__MACRO__", "query_hash": "another"}
        body = Session().get("https://api.x.com"+x.SEARCH, params={}).json()
        received, processed, duplicates, _ = x._posts(request, body, NOW)
        self.assertEqual((received, processed, duplicates), (1, 1, 1))
        with store.db(readonly=True) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM posts").fetchone()[0], 1)
            self.assertGreater(con.execute("SELECT count(*) FROM post_context").fetchone()[0], 2)
        public = json.dumps(x.export_diagnostics())
        self.assertNotIn("earnings outlook and quarterly revenue", public)
        self.assertNotIn('"author_id"', public)

    def test_delete_post_cascades_contexts_and_blocks_reingestion(self):
        self.enable()
        body = Session().get("https://api.x.com"+x.SEARCH, params={}).json()
        request = {"context": "NVDA", "query_hash": "sample"}
        x._posts(request, body, NOW)
        self.assertEqual(x.delete_evidence(post_id="101")["deleted_posts"], 1)
        self.assertEqual(x.event_hints(now=NOW), [])
        self.assertEqual(x._posts(request, body, NOW)[1], 0)
        with store.db(readonly=True) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM post_context").fetchone()[0], 0)

    def test_delete_author_and_expiration_remove_raw_content(self):
        self.enable()
        body = Session().get("https://api.x.com"+x.SEARCH, params={}).json()
        x._posts({"context": "NVDA", "query_hash": "q"}, body, NOW)
        self.assertEqual(x.delete_evidence(author_id="301")["deleted_posts"], 1)
        body["data"][0].update(id="102", author_id="302")
        x._posts({"context": "NVDA", "query_hash": "q"}, body, NOW)
        x.save_settings({"enabled": False})
        x.tick(now=NOW+8*x.DAY, session=Session())
        with store.db(readonly=True) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM posts").fetchone()[0], 0)

    def test_unverified_harmless_posts_cannot_become_crisis_gate(self):
        self.enable()
        body = {"data": [{"id": "104", "created_at": x.iso(NOW-200), "text": "Movie about nuclear submarines and streaming price war"}]}
        x._posts({"context": "__MACRO__", "query_hash": "q"}, body, NOW)
        self.assertEqual(x.event_hints(now=NOW), [])
        self.assertFalse(x.public_status()["trade_effect"])

    def test_expired_price_acknowledgement_stops_all_paid_calls(self):
        self.enable()
        session = Session()
        x.tick(now=NOW+31*x.DAY, session=session)
        self.assertEqual(session.calls, [])
        with mock.patch.object(x.time, "time", return_value=NOW+31*x.DAY):
            self.assertEqual(x.public_status()["state"], "PRICING_EXPIRED")

    def test_future_price_acknowledgement_does_not_authorize_calls(self):
        self.enable()
        with store.db() as con:
            settings = json.loads(con.execute("SELECT payload FROM settings WHERE id=1").fetchone()[0])
            settings["pricing_acknowledged_at"] = NOW+3600
            con.execute("UPDATE settings SET payload=? WHERE id=1", (json.dumps(settings),))
        session = Session()
        self.assertFalse(x.tick(now=NOW, session=session))
        self.assertIsNone(x._reserve("counts", "$NVDA", "NVDA", "future", NOW))
        self.assertEqual(session.calls, [])
        self.assertEqual(x.public_status()["state"], "PRICING_EXPIRED")

    def test_general_search_cap_independent_of_query_changes(self):
        # 10.3.0: Zwei allgemeine (nicht kandidatengebundene) Suchen pro Tag.
        self.enable()
        for n in range(2):
            self.assertIsNotNone(x._reserve("search", "query"+str(n), "ctx", str(n), NOW))
        self.assertIsNone(x._reserve("search", "different", "ctx", "last", NOW))

    def test_existing_ai_packet_carries_only_derived_unconfirmed_topics(self):
        from pulsar.ai_packet import project, size
        packet = {"symbol": "NVDA", "sources": [{"id": "x-receipt", "provider": "X",
            "kind": "social_research_hint", "data": {"symbol": "NVDA", "text": "RAW CONTENT MUST NOT LEAVE",
                "argument_clusters": [{"event_type": "TARIFFS_TRADE", "title": "Zölle",
                    "status": "SOCIAL_ALERT", "posts": 3, "text": "RAW CONTENT MUST NOT LEAVE"}]}}]}
        view = project(packet, budget=4300)
        self.assertNotIn("RAW CONTENT", json.dumps(view))
        self.assertEqual(view["sources"][0]["data"]["role"], "UNCONFIRMED_RESEARCH_ONLY")
        self.assertFalse(view["sources"][0]["data"]["trade_effect"])
        self.assertLessEqual(size(view), 4300)

    def test_failed_error_persistence_logs_only_sanitized_message(self):
        with mock.patch.object(x, "tick", side_effect=RuntimeError("private-token-never-export")), \
                mock.patch.object(store, "db", side_effect=RuntimeError("private-token-never-export")), \
                mock.patch.object(x._stop, "is_set", side_effect=[False, True]), \
                mock.patch.object(x._stop, "wait"), \
                self.assertLogs(x.__name__, level="ERROR") as captured:
            x._run()
        self.assertIn("LOCAL_ERROR", " ".join(captured.output))
        self.assertNotIn("private-token", " ".join(captured.output))


if __name__ == "__main__":
    unittest.main()
