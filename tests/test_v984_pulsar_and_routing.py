from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
import time

import pytest

from broker.base import BrokerFehler
from pulsar import control as c, research as r
from okx_entry_routing import compare_routes, initial_route


def plan():
    return {"symbol": "PEP", "broker": "etoro", "instrument_id": "123", "settlement": "REAL",
            "leverage": 1, "currency": "USD", "account": "acct", "environment": "DEMO",
            "rules": dict(c.RULES), "evidence_hash": "evidence", "assessment_id": "assessment",
            "cost_budget": 1., "estimated_costs": .95, "max_entry_price": 100.3, "capital_reserved": 201.6,
            "quantity": 2., "price": 100., "stop": 90., "take": 160., "equity": 10000.}


def ready(now=None):
    now = time.time() if now is None else now
    session = c.start_session(now)
    c.set_mode("FREIGABE")
    c.heartbeat(session, now)
    return now


def approved(now=None):
    now = ready(now)
    item = c.nominate(plan(), chat="1", user="2", now=now)
    c.bind_message(item["id"], 99)
    second = c.callback(item["id"], item["nonce"], "confirm", chat="1", user="2", message_id=99, now=now)
    assert second["status"] == "CONFIRMING"
    result = c.callback(item["id"], second["nonce"], "confirm", chat="1", user="2", message_id=99, now=now)
    assert result["status"] == "APPROVED"
    return item, now


def claim(item, now, **extra):
    args = dict(decision_id=123, account="acct", environment="DEMO", price=100.,
                quantity=2., stop=90., take=160., equity=10000., cash=1000.,
                evidence_hash="evidence", estimated_fees=.95, now=now)
    args.update(extra)
    return c.claim(item["id"], **args)


def test_pulsar_two_steps_and_atomic_single_submit():
    item, now = approved()
    def attempt():
        try:
            claim(item, now)
            return True
        except c.Blocked:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [False, True]
    c.record_execution(item["id"], "UNKNOWN", {"reference_id": "persisted-before-post"}, now=now)
    with pytest.raises(c.Blocked):
        claim(item, now)
    c.start_session(now)
    assert c.for_symbol("PEP")["status"] == "UNKNOWN"


@pytest.mark.parametrize("changed", [{"chat": "3"}, {"user": "3"}, {"message_id": 98}])
def test_pulsar_foreign_callbacks_cannot_approve(changed):
    now = ready()
    item = c.nominate(plan(), chat="1", user="2", now=now)
    c.bind_message(item["id"], 99)
    args = dict(chat="1", user="2", message_id=99, now=now)
    args.update(changed)
    with pytest.raises(c.Blocked):
        c.callback(item["id"], item["nonce"], "confirm", **args)
    assert c.for_symbol("PEP")["status"] == "WAITING_APPROVAL"


def test_pulsar_rejection_consumes_day_across_accounts_and_modes():
    # 10.3.0: Eine Ablehnung verbraucht den Kalendertag (Europe/Berlin),
    # nicht mehr die Woche. Am naechsten Tag geht eine neue Nominierung.
    now = ready()
    item = c.nominate(plan(), chat="1", user="2", now=now)
    c.bind_message(item["id"], 99)
    c.callback(item["id"], item["nonce"], "reject", chat="1", user="2", message_id=99, now=now)
    c.set_mode("AUS"); c.set_mode("FREIGABE")
    with pytest.raises(c.Blocked, match="Heute"):
        c.nominate({**plan(), "account": "other", "environment": "LIVE"}, chat="1", user="2", now=now)
    tomorrow = ready(now+86400)
    item2 = c.nominate({**plan(), "account": "other", "environment": "LIVE"},
                       chat="1", user="2", now=tomorrow)
    assert item2["id"]


@pytest.mark.parametrize("changed", [{"environment": "LIVE"}, {"account": "other"},
    {"quantity": 3.}, {"stop": 89.}, {"price": 101.}, {"evidence_hash": "changed"}, {"cash": 100.}])
def test_pulsar_changed_plan_cannot_submit(changed):
    item, now = approved()
    with pytest.raises(c.Blocked):
        claim(item, now, **changed)


def test_pulsar_mode_off_revokes_permission_but_keeps_exposure():
    item, now = approved()
    claim(item, now)
    c.set_mode("AUS")
    assert c.for_symbol("PEP")["status"] == "SUBMITTING"
    assert c.settings()["mode"] == "AUS"


def test_pulsar_old_session_invalidates_buttons():
    item, now = approved()
    c.start_session(now)
    with pytest.raises(c.Blocked):
        claim(item, now)


def test_social_aggregates_do_not_invent_authors_or_zero_baseline_growth():
    data = {"results": [{"ticker": "ABC", "mentions": 500, "mentions_24h_ago": 0},
                         {"ticker": "<script>", "mentions": 99999},
                         {"ticker": "BAD", "mentions": float("nan")}]}
    rows = r.normalise("apewisdom", data)
    assert len(rows) == 1
    assert rows[0]["unique_authors"] is None
    assert rows[0]["growth_ratio"] is None and rows[0]["newly_observed"]


def test_pulsar_budget_reservations_survive_unknown_cost_and_week_change():
    # Weekly cap, not only a daily reset.
    from datetime import datetime, timezone
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc).timestamp()
    r.reserve("ai", 1., now=now)
    r.reserve("ai", 1., now=now+86400)
    with pytest.raises(c.Blocked):
        r.reserve("ai", .01, now=now+2*86400)


class Broker:
    demo = True
    allowed_quotes = ("EUR", "USDC")
    def __init__(self, *, eur_cost=.002, usdc_cost=.001, missing_fee=False, eur_depth=True):
        self.eur_cost, self.usdc_cost = eur_cost, usdc_cost
        self.eur_depth, self.missing_fee = eur_depth, missing_fee
        self.metas = {iid: NS(inst_id=iid, ist_live=True, base_ccy="DOGE", quote_ccy=quote,
                    min_size=1, trade_quote_ccy_list=(lane,)) for iid, quote, lane in
                    [("DOGE-EUR", "EUR", "EUR"), ("DOGE-USD", "USD", "USDC")]}
        self.client = NS(hat_zugangsdaten=True, instruments=lambda: self.metas,
            balances=lambda: {"EUR": {"cash": 1000}, "USDC": {"cash": 1000}},
            trade_fee=lambda **kw: {"taker": .001, "taker_known": not self.missing_fee})
    def quote_conversion_rate(self, a, b):
        return {("EUR", "EUR"): 1., ("USD", "EUR"): .9, ("USD", "USDC"): .998}.get((a, b))
    def execution_quote(self, instrument, quantity, *, side):
        eur = instrument.currency == "EUR"
        if eur and not self.eur_depth:
            raise BrokerFehler("Tiefe fehlt")
        spread = self.eur_cost if eur else self.usdc_cost
        price = 1. if eur else 1/.9
        px = price * (1 + spread/2 if side == "buy" else 1 - spread/2)
        return {"vwap": px, "best": px, "slippage_pct": 0., "timestamp_ms": int(time.time()*1000)}


def test_eur_preferred_despite_small_usdc_saving():
    b = Broker()
    result = compare_routes(b, "DOGE", 10, NS())
    assert result["trade_quote_ccy"] == "EUR"
    assert initial_route(b, "DOGE", "DOGE-USD")["inst_id"] == "DOGE-EUR"


def test_usdc_only_on_meaningful_actual_cost_advantage_or_unsuitable_eur():
    result = compare_routes(Broker(eur_cost=.004), "DOGE", 10, NS())
    assert result["inst_id"] == "DOGE-USD" and result["trade_quote_ccy"] == "USDC"
    assert result["quote_rate_to_settlement"] == .998
    result = compare_routes(Broker(eur_depth=False), "DOGE", 10, NS())
    assert result["trade_quote_ccy"] == "USDC" and result["excluded"]


def test_routing_no_fee_evidence_and_no_fx_parity_assumption():
    with pytest.raises(BrokerFehler):
        compare_routes(Broker(missing_fee=True), "DOGE", 10, NS())
    b = Broker(eur_depth=False)
    b.quote_conversion_rate = lambda a, b: None
    with pytest.raises(BrokerFehler):
        compare_routes(b, "DOGE", 10, NS())


def test_usdc_optout_respected_and_existing_usd_not_relabelled():
    b = Broker(eur_depth=False)
    b.allowed_quotes = ("EUR",)
    with pytest.raises(BrokerFehler):
        compare_routes(b, "DOGE", 10, NS())
    assert b.metas["DOGE-USD"].quote_ccy == "USD"
