"""Tests for payload parsing, run orchestration and output files."""

import csv
import datetime
import os
import tempfile
from typing import Any
import unittest
from unittest import mock
import urllib.parse

import pyarrow.parquet as pq

from collector import client
from collector import config
from collector import models
from collector import motianlun
from collector import piaoniu
from collector import run
from collector import store

_NOW = datetime.datetime(2026, 11, 6, 15, 0, tzinfo=config.TZ)
_LATER = datetime.datetime(2026, 11, 27, 19, 0, tzinfo=config.TZ)
_MTL_SHOW = config.Show("motianlun", "guangzhou", "show-1", "4401")
_PN_SHOW = config.Show("piaoniu", "guangzhou", "784190")
_MTL_PLANS = "/showapi/pub/show_sessions/v2/find_seat_plans"
_MTL_TICKETS = "/showapi/pub/show_session/v2/find_tickets"


class FakeApi:
    """Stands in for `client.Client`, answering requests by URL path."""

    def __init__(self, routes):
        self.requests = 0
        self.calls: list[tuple[str, dict[str, str], Any]] = []
        self._routes = routes

    def get_json(self, url, headers, validate=None):
        del headers  # Unused.
        return self._answer(url, None, validate)

    def post_json(self, url, body, headers, validate=None):
        del headers  # Unused.
        return self._answer(url, body, validate)

    def _answer(self, url, body, validate):
        self.requests += 1
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append((parts.path, query, body))
        payload = self._routes[parts.path](query, body)
        if validate:
            validate(payload)
        return payload


def _mtl_ok(data):
    return {"statusCode": 200, "data": data}


def _ticket(ticket_id, plan_id, price):
    return {
        "ticketId": ticket_id,
        "seatPlanId": plan_id,
        "price": price,
        "ticketTitle": f"zone {ticket_id}",
        "leftStocks": 2,
    }


class MotianlunTest(unittest.TestCase):

    def test_lists_sessions_with_site_order_limit(self):
        api = FakeApi({
            "/showapi/pub/v3/show/show-1/sessionone": lambda q, b: _mtl_ok([{
                "sessionId": "s1",
                "sessionShowTime": "2026-11-27 19:00:00",
                "limitation": 8,
            }]),
        })
        (session,) = motianlun.list_sessions(api, _MTL_SHOW)
        self.assertEqual(session.session_id, "s1")
        self.assertEqual(session.session_time, _LATER)
        self.assertEqual(session.max_qty, 8)

    def test_session_without_order_limit_is_an_error(self):
        api = FakeApi({
            "/showapi/pub/v3/show/show-1/sessionone": lambda q, b: _mtl_ok([{
                "sessionId": "s1",
                "sessionShowTime": "2026-11-27 19:00:00",
            }]),
        })
        with self.assertRaises(KeyError):
            motianlun.list_sessions(api, _MTL_SHOW)

    def test_merges_tickets_across_order_sizes(self):
        by_qty = {
            1: [_ticket("a", "p1", 980), _ticket("b", "hidden", 7223)],
            2: [_ticket("b", "hidden", 7223), _ticket("c", "p1", 1200)],
        }

        def tickets(query, body):
            del query  # Unused.
            page = by_qty.get(body["ticketNumber"], [])
            return _mtl_ok({"total": len(page), "sessionTicketList": page})

        api = FakeApi({
            _MTL_PLANS: lambda q, b: _mtl_ok({"seatPlanList": [
                {"seatPlanOID": "p1", "originalPrice": 480},
            ]}),
            _MTL_TICKETS: tickets,
        })
        session = models.Session(_MTL_SHOW, "s1", _LATER, max_qty=3)

        listings = {
            x.listing_id: x for x in motianlun.fetch_listings(api, session)
        }

        self.assertEqual(sorted(listings), ["a", "b", "c"])
        self.assertEqual(listings["b"].qty_options, "1,2")
        self.assertEqual(listings["a"].face_value, 480.0)
        self.assertEqual(listings["b"].tier_id, "hidden")
        self.assertIsNone(listings["b"].face_value)

    def test_pages_through_tickets(self):
        all_tickets = [_ticket(str(i), "p1", 100 + i) for i in range(5)]

        def tickets(query, body):
            del query  # Unused.
            start = body["offset"]
            page = all_tickets[start:start + body["length"]]
            if body["ticketNumber"] > 1:
                page = []
            return _mtl_ok({"total": 5, "sessionTicketList": page})

        api = FakeApi({
            _MTL_PLANS: lambda q, b: _mtl_ok({"seatPlanList": []}),
            _MTL_TICKETS: tickets,
        })
        session = models.Session(_MTL_SHOW, "s1", _LATER, max_qty=1)

        with mock.patch.object(motianlun, "_PAGE_SIZE", 2):
            listings = motianlun.fetch_listings(api, session)

        self.assertEqual(len(listings), 5)

    def test_stops_when_server_ignores_offset(self):
        page = [_ticket("a", "p1", 100), _ticket("b", "p1", 200)]
        api = FakeApi({
            _MTL_PLANS: lambda q, b: _mtl_ok({"seatPlanList": []}),
            _MTL_TICKETS: lambda q, b: _mtl_ok(
                {"total": 10, "sessionTicketList": page}
            ),
        })
        session = models.Session(_MTL_SHOW, "s1", _LATER, max_qty=1)

        listings = motianlun.fetch_listings(api, session)

        ticket_calls = [c for c in api.calls if c[0] == _MTL_TICKETS]
        self.assertEqual(len(listings), 2)
        self.assertEqual(len(ticket_calls), 2)

    def test_rejects_rate_limited_payload(self):
        with self.assertRaises(client.FetchError):
            motianlun.validate({"statusCode": 556, "comments": "busy"})


def _offer_group(buy_type, offer_id, price):
    return {
        "type": buy_type,
        "ticketGroupInfoList": [{"ticketGroupInfoDetail": {"ticketGroup": {
            "id": offer_id,
            "salePrice": price,
            "areaName": "any",
            "rowIndex": 999,
            "count": 2,
        }}}],
    }


class PiaoniuTest(unittest.TestCase):

    def test_combines_offer_feed_and_seat_map(self):
        tier = {"id": 11, "specification": "980 stand", "originPrice": 980.0}
        direct = _offer_group("DIRECT_BUY", 501, 3023.0)
        offers_by_mode = {
            # The direct-buy group is repeated to check de-duplication.
            1: {"data": [direct]},
            2: {"data": [direct, _offer_group("PROXY_BUY", 502, 3100.0)]},
        }
        api = FakeApi({
            "/api/v4/ticketCategories/": lambda q, b: [tier],
            "/api/v8/tickets": lambda q, b: offers_by_mode[b["buyType"]],
            "/api/v4/seatMap": lambda q, b: {"tickets": [{
                "id": 601,
                "ticketCategoryId": 11,
                "salePrice": 3456.0,
                "originPrice": 980.0,
                "areaName": "P1 row 25",
                "rowIndex": 25,
                "availableNumbers": [1, 2],
                "sellingAmount": 10,
            }]},
        })
        session = models.Session(_PN_SHOW, "14953266", _LATER)

        offer, proxy, seat = piaoniu.fetch_listings(api, session)

        self.assertEqual(
            (offer.source, offer.buy_type, offer.row, offer.stock),
            ("tickets", "DIRECT_BUY", None, 2),
        )
        self.assertEqual(offer.face_value, 980.0)
        self.assertEqual((proxy.buy_type, proxy.price), ("PROXY_BUY", 3100.0))
        tier_query = next(q for path, q, _ in api.calls if path.endswith("/"))
        self.assertNotIn("buyType", tier_query)
        self.assertEqual(
            (seat.source, seat.row, seat.stock, seat.qty_options),
            ("seat_map", 25, 10, "1,2"),
        )
        self.assertEqual(seat.tier_name, "980 stand")

    def test_lists_sessions_from_events(self):
        start_ms = int(_LATER.timestamp() * 1000)
        api = FakeApi({
            "/api/v5/activities/getAllEvents": lambda q, b: {
                "code": 200,
                "data": {"events": [{"id": 14953266, "start": start_ms}]},
            },
        })
        (session,) = piaoniu.list_sessions(api, _PN_SHOW)
        self.assertEqual(session.session_id, "14953266")
        self.assertEqual(session.session_time, _LATER)


class FakeSite:
    """Site module double with fixed sessions and per-session outcomes."""

    def __init__(self, sessions, outcomes):
        self._sessions = sessions
        self._outcomes = outcomes

    def list_sessions(self, api, show):
        del api, show  # Unused.
        return self._sessions

    def fetch_listings(self, api, session):
        api.requests += 1
        outcome = self._outcomes[session.session_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _listing(listing_id):
    return models.Listing(
        tier_id="t", tier_name=None, face_value=480.0, source="tickets",
        buy_type=None, listing_id=listing_id, price=2500.0, area=None,
        row=None, stock=1, qty_options="1",
    )


class CollectShowTest(unittest.TestCase):

    def test_skips_started_sessions_and_isolates_failures(self):
        earlier = _NOW - datetime.timedelta(hours=1)
        sessions = [
            models.Session(_MTL_SHOW, "past", earlier),
            models.Session(_MTL_SHOW, "good", _LATER),
            models.Session(_MTL_SHOW, "bad", _LATER),
        ]
        site = FakeSite(sessions, {
            "good": [_listing("x")],
            "bad": KeyError("ticketId"),
        })
        api = FakeApi({})

        with mock.patch.dict(run.SITES, {"motianlun": site}):
            results = run.collect_show(api, _MTL_SHOW, _NOW)

        ids = [r.session.session_id for r in results]
        self.assertEqual(ids, ["good", "bad"])
        good, bad = results
        self.assertEqual((good.status, len(good.listings)), ("ok", 1))
        self.assertEqual(bad.status, "failed")
        self.assertIn("KeyError", bad.error)

    def test_records_session_list_failure(self):
        site = mock.Mock()
        site.list_sessions.side_effect = client.FetchError("timed out")

        with mock.patch.dict(run.SITES, {"motianlun": site}):
            (result,) = run.collect_show(FakeApi({}), _MTL_SHOW, _NOW)

        self.assertEqual(result.session.session_id, "")
        self.assertEqual(result.status, "failed")


class StoreTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        session = models.Session(_MTL_SHOW, "s1", _LATER)
        self.results = [
            models.SessionResult(session, (_listing("x"), _listing("y")), 3),
            models.SessionResult(session, (), 1, "FetchError: busy"),
        ]

    def test_writes_typed_price_file(self):
        path = store.write_prices(self.results, _NOW, self.root)

        self.assertEqual(
            path,
            os.path.join(self.root, "prices", "2026-11-06", "150000.parquet"),
        )
        table = pq.read_table(path)
        self.assertEqual(table.schema, store.PRICE_SCHEMA)
        self.assertEqual(table.num_rows, 2)
        self.assertEqual(table.column("fetched_at")[0].as_py(), _NOW)

    def test_skips_price_file_without_listings(self):
        empty = [models.SessionResult(self.results[0].session)]
        self.assertIsNone(store.write_prices(empty, _NOW, self.root))

    def test_appends_fetch_log_with_single_header(self):
        store.append_runs(self.results, _NOW, self.root)
        path = store.append_runs(self.results, _NOW, self.root)

        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(rows[1]["error"], "FetchError: busy")
        self.assertEqual(rows[0]["session_time"], _LATER.isoformat())


if __name__ == "__main__":
    unittest.main()
