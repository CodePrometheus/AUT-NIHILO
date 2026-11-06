"""Listings from the first site: sessions, seat plans and tickets."""

from collections.abc import Iterator
import datetime
import time
from typing import Any
import urllib.parse

from collector import client
from collector import config
from collector import models

BASE_URL = "https://m.motianlun.cn"

_APP_PARAMS = {"src": "m_web", "ver": "9.9.9"}
_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "application/json",
    "src": "m_web",
    "source": "m_web",
    "ver": "9.9.9",
    "Referer": BASE_URL + "/",
}
_PAGE_SIZE = 20  # Page size the site's own ticket list uses.


def list_sessions(
    api: client.Client, show: config.Show
) -> list[models.Session]:
    """Returns every session of `show`, including ones already started.

    Each session carries the site's per-order ticket limit, which bounds the
    order sizes queried later.
    """
    data = _get(
        api,
        f"/showapi/pub/v3/show/{show.show_id}/sessionone",
        {"locationCityOID": show.city_code, "orderDecision": "RANDOM"},
    )
    return [
        models.Session(
            show=show,
            session_id=str(item["sessionId"]),
            session_time=_parse_time(item["sessionShowTime"]),
            max_qty=int(item["limitation"]),
        )
        for item in data
    ]


def fetch_listings(
    api: client.Client, session: models.Session
) -> list[models.Listing]:
    """Returns every offer of `session`.

    A ticket is only returned for the order sizes it can be sold in, so every
    size up to the session limit is queried and the results are merged.

    Args:
        api: HTTP client.
        session: Session to fetch.

    Returns:
        One listing per distinct ticket.

    Raises:
        client.FetchError: A request kept failing.
    """
    tiers = _fetch_tiers(api, session)
    offers: dict[str, tuple[dict[str, Any], set[int]]] = {}
    for qty in range(1, session.max_qty + 1):
        for ticket in _fetch_tickets(api, session, qty):
            offers.setdefault(ticket["ticketId"], (ticket, set()))[1].add(qty)
    return [to_listing(ticket, qtys, tiers) for ticket, qtys in offers.values()]


def to_listing(
    ticket: dict[str, Any],
    qtys: set[int],
    tiers: dict[str, dict[str, Any]],
) -> models.Listing:
    """Converts a ticket payload into a listing.

    Args:
        ticket: One entry of the ticket feed.
        qtys: Order sizes the ticket was returned for.
        tiers: Seat plans of the session keyed by id. Some tickets belong to
            plans the site does not list; their tier fields stay empty except
            the id.

    Returns:
        The listing.
    """
    tier = tiers.get(ticket.get("seatPlanId"), {})
    return models.Listing(
        tier_id=models.to_str(ticket.get("seatPlanId")),
        tier_name=models.to_str(
            tier.get("seatPlanName") or tier.get("colorAreaName")
        ),
        face_value=models.to_float(tier.get("originalPrice")),
        source="tickets",
        buy_type=None,
        listing_id=str(ticket["ticketId"]),
        price=models.to_float(ticket.get("price")),
        area=models.to_str(ticket.get("ticketTitle") or ticket.get("zoneName")),
        row=None,
        stock=models.to_int(ticket.get("leftStocks")),
        qty_options=",".join(str(q) for q in sorted(qtys)) or None,
    )


def _fetch_tiers(
    api: client.Client, session: models.Session
) -> dict[str, dict[str, Any]]:
    data = _post(
        api,
        "/showapi/pub/show_sessions/v2/find_seat_plans",
        {
            "showSessionId": session.session_id,
            "locationCityOID": session.show.city_code,
        },
    )
    plans = data.get("seatPlanList") if isinstance(data, dict) else data
    return {plan["seatPlanOID"]: plan for plan in plans or []}


def _fetch_tickets(
    api: client.Client, session: models.Session, qty: int
) -> Iterator[dict[str, Any]]:
    seen: set[str] = set()
    while True:
        data = _post(
            api,
            "/showapi/pub/show_session/v2/find_tickets",
            {
                "showSessionId": session.session_id,
                "locationCityOID": session.show.city_code,
                "ticketNumber": qty,
                "offset": len(seen),
                "length": _PAGE_SIZE,
            },
        )
        page = [
            ticket
            for ticket in data.get("sessionTicketList") or []
            if ticket["ticketId"] not in seen
        ]
        seen.update(ticket["ticketId"] for ticket in page)
        yield from page
        # Stop on the reported total, or when a page brings nothing new.
        if not page or len(seen) >= (models.to_int(data.get("total")) or 0):
            return


def validate(payload: Any) -> None:
    """Raises FetchError unless `payload` is a successful response."""
    if (
        not isinstance(payload, dict)
        or payload.get("statusCode") != 200
        or payload.get("data") is None
    ):
        status = (
            payload.get("statusCode")
            if isinstance(payload, dict)
            else type(payload).__name__
        )
        raise client.FetchError(f"statusCode={status}")


def _app_params() -> dict[str, str]:
    return {**_APP_PARAMS, "time": str(time.time_ns() // 1_000_000)}


def _get(api: client.Client, path: str, params: dict[str, Any]) -> Any:
    query = urllib.parse.urlencode({**_app_params(), **params})
    payload = api.get_json(f"{BASE_URL}{path}?{query}", _HEADERS, validate)
    return payload["data"]


def _post(api: client.Client, path: str, body: dict[str, Any]) -> Any:
    params = _app_params()
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    payload = api.post_json(url, {**params, **body}, _HEADERS, validate)
    return payload["data"]


def _parse_time(text: str) -> datetime.datetime:
    parsed = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=config.TZ)
