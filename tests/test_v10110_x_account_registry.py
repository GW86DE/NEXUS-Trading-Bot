"""10.1.10: beobachtungsbasierte X-Quellen-Registry (Zielbild Abschnitt 7, v1).

Vorschlaege entstehen nur aus lokal gespeicherten Posts; Aktivierung bleibt
eine Nutzerentscheidung mit Identitaetsvermerk. Kein Test erzeugt eine
Netzanfrage.
"""
import json
from pathlib import Path
import tempfile
import time
import unittest

from market_intelligence import account_registry, store
from market_intelligence.source_registry import search_plan

NOW = 1_790_000_000.0
DAY = 86400


def insert_post(con, identity, author, created, *, topic="SANCTIONS", spam=0, urls=()):
    con.execute("INSERT OR REPLACE INTO posts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (str(identity), str(author), created, created, "t" + str(identity),
                 "hash" + str(identity), topic, spam, json.dumps(list(urls)), "[]"))


class AccountRegistryProposals(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_root = store.ROOT
        store.ROOT = Path(self._tmp.name)

    def tearDown(self):
        store.ROOT = self._old_root
        self._tmp.cleanup()

    def test_proposal_needs_relevance_on_multiple_days(self):
        with store.db() as con:
            for i in range(3):  # Autor 1: 3 relevante Posts an 2 Tagen
                insert_post(con, 100 + i, "1001", NOW - (i % 2) * DAY - i)
            for i in range(2):  # Autor 2: nur 2 relevante Posts
                insert_post(con, 200 + i, "1002", NOW - i * DAY)
            for i in range(5):  # Autor 3: 5 Posts, aber nur ein Tag
                insert_post(con, 300 + i, "1003", NOW - i * 60)
            for i in range(4):  # Autor 4: Spam zaehlt nicht
                insert_post(con, 400 + i, "1004", NOW - (i % 3) * DAY, spam=1)
            for i in range(4):  # Autor 5: Thema OTHER ohne Primaerlink zaehlt nicht
                insert_post(con, 500 + i, "1005", NOW - (i % 3) * DAY, topic="OTHER")
            for i in range(3):  # Autor 6: OTHER, aber SEC-Link = relevant
                insert_post(con, 600 + i, "1006", NOW - (i % 2) * DAY,
                            topic="OTHER", urls=["www.sec.gov"])
            account_registry.refresh(con, NOW)
            states = {r["account_id"]: r["state"] for r in store.value(con, "account_registry", [])}
        self.assertEqual(states.get("1001"), "PROPOSED")
        self.assertEqual(states.get("1006"), "PROPOSED")
        for author in ("1002", "1003", "1004", "1005"):
            self.assertNotIn(author, states, author)

    def test_tombstoned_author_is_never_proposed(self):
        with store.db() as con:
            for i in range(3):
                insert_post(con, 700 + i, "1007", NOW - (i % 2) * DAY)
            con.execute("INSERT INTO tombstones VALUES('author',?)",
                        (account_registry._digest("1007"),))
            account_registry.refresh(con, NOW)
            rows = store.value(con, "account_registry", [])
        self.assertEqual(rows, [])

    def test_proposal_expires_without_new_relevance(self):
        with store.db() as con:
            store.put(con, "account_registry", [{
                "account_id": "1008", "state": "PROPOSED", "proposed_at": NOW - 20 * DAY,
                "last_relevant_at": NOW - 15 * DAY, "false_signal_count": 0}])
            account_registry.refresh(con, NOW)
            rows = store.value(con, "account_registry", [])
        self.assertEqual(rows[0]["state"], "EXPIRED")

    def test_activation_requires_identity_note_and_respects_cap(self):
        with store.db() as con:
            rows = [{"account_id": str(2000 + i), "state": "ACTIVE", "activated_at": NOW,
                     "last_relevant_at": NOW, "expires_at": NOW + DAY, "false_signal_count": 0}
                    for i in range(account_registry.MAX_DYNAMIC_ACTIVE)]
            rows.append({"account_id": "2100", "state": "PROPOSED", "proposed_at": NOW,
                         "last_relevant_at": NOW, "false_signal_count": 0})
            store.put(con, "account_registry", rows)
        with self.assertRaises(ValueError):  # Vermerk zu kurz
            account_registry.activate("2100", actor="webui:test", identity_note="kurz")
        with self.assertRaises(ValueError):  # Kapazitaet voll
            account_registry.activate("2100", actor="webui:test",
                                      identity_note="Offizielles Konto der Beispielbehoerde")
        account_registry.remove("2000", actor="webui:test")
        row = account_registry.activate("2100", actor="webui:test",
                                        identity_note="Offizielles Konto der Beispielbehoerde")
        self.assertEqual(row["state"], "ACTIVE")
        self.assertFalse(row["trade_effect"])
        with store.db() as con:
            self.assertIn("2100", account_registry.active_ids(con, NOW))

    def test_removed_account_is_not_proposed_again(self):
        with store.db() as con:
            store.put(con, "account_registry", [{
                "account_id": "1009", "state": "REMOVED", "removed_at": NOW,
                "false_signal_count": 0}])
            for i in range(4):
                insert_post(con, 900 + i, "1009", NOW - (i % 2) * DAY)
            account_registry.refresh(con, NOW)
            rows = {r["account_id"]: r for r in store.value(con, "account_registry", [])}
        self.assertEqual(rows["1009"]["state"], "REMOVED")

    def test_snapshot_declares_no_trade_effect(self):
        with store.db() as con:
            snapshot = account_registry.snapshot(con, NOW)
        self.assertFalse(snapshot["trade_effect"])
        self.assertEqual(snapshot["limits"]["active"], account_registry.MAX_DYNAMIC_ACTIVE)


class DynamicAccountsInSearchPlan(unittest.TestCase):
    def test_financial_slot_includes_active_ids_only(self):
        query, context = search_plan(1, (), ("2001", "2002", "nicht-numerisch"))
        self.assertEqual(context, "__FINANCIAL_SOURCES__")
        self.assertIn("from:2001", query)
        self.assertIn("from:2002", query)
        self.assertNotIn("nicht-numerisch", query)
        policy_query, _ = search_plan(0, (), ("2001",))
        self.assertNotIn("from:2001", policy_query)
        discovery_query, discovery_context = search_plan(2, (), ("2001",))
        self.assertEqual(discovery_context, "__STOCK_DISCOVERY__")
        self.assertNotIn("from:2001", discovery_query)

    def test_dynamic_ids_are_capped(self):
        query, _ = search_plan(1, (), tuple(str(3000 + i) for i in range(9)))
        self.assertEqual(sum("from:30" in part for part in query.split(" OR ")), 5)


if __name__ == "__main__":
    unittest.main()
