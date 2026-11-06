"""Listings from the second site: events, ticket tiers, offers, seat maps."""

from collections.abc import Callable
import datetime
from typing import Any
import urllib.parse

from collector import client
from collector import config
from collector import models

BASE_URL = "https://m.piaoniu.com/api"

_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "application/json",
    "Referer": "https://m.piaoniu.com/",
}
_ANY_ROW = 999  # The site's placeholder for "row not specified".
_BUY_TYPES = (1, 2)  # The site's purchase modes: direct and proxy.


def list_sessions(
    api: client.Client, show: config.Show
) -> list[models.Session]:
    """Returns every session of `show`, including ones already started."""
    payload = api.get_json(
        _url("/v5/activities/getAllEvents", activityId=show.show_id),
        _HEADERS,
        _validate_events,
    )
    return [
        models.Session(
            show=show,
            session_id=str(event["id"]),
            session_time=datetime.datetime.fromtimestamp(
                event["start"] / 1000, config.TZ
            ),
        )
        for event in payload["data"].get("events") or []
    ]


def fetch_listings(
    api: client.Client, session: models.Session
) -> list[models.Listing]:
    """Returns the regular offers of every tier plus the seat-map offers.

    The two feeds never share an offer, and the site's own lowest prices only
    cover the regular one, so both are needed for a complete picture.

    Args:
        api: HTTP client.
        session: Session to fetch.

    Returns:
        All listings of the session.

    Raises:
        client.FetchError: A request kept failing.
    """
    tiers = api.get_json(
        _url(
            "/v4/ticketCategories/",
            eventId=session.session_id,
            supportSpeedPackBuy="true",
        ),
        _HEADERS,
        _expect(list),
    )
    offers = {
        (buy_type, offer["id"]): offer_listing(tier, buy_type, offer)
        for tier in tiers
        for buy_mode in _BUY_TYPES
        for buy_type, offer in _fetch_offers(api, tier["id"], buy_mode)
    }
    listings = list(offers.values())
    seat_map = api.get_json(
        _url("/v4/seatMap", activityEventId=session.session_id),
        _HEADERS,
        _expect(dict),
    )
    tiers_by_id = {tier["id"]: tier for tier in tiers}
    seats = seat_map.get("tickets") or []
    listings.extend(seat_listing(seat, tiers_by_id) for seat in seats)
    return listings


def offer_listing(
    tier: dict[str, Any], buy_type: str | None, offer: dict[str, Any]
) -> models.Listing:
    """Converts a regular offer into a listing.

    Args:
        tier: The ticket tier the offer belongs to.
        buy_type: Purchase mode of the offer group.
        offer: One ticket group of the offer feed.

    Returns:
        The listing.
    """
    return models.Listing(
        tier_id=models.to_str(tier.get("id")),
        tier_name=models.to_str(tier.get("specification")),
        face_value=models.to_float(
            offer.get("originPrice") or tier.get("originPrice")
        ),
        source="tickets",
        buy_type=models.to_str(buy_type),
        listing_id=str(offer["id"]),
        price=models.to_float(offer.get("salePrice")),
        area=models.to_str(offer.get("areaName")),
        row=_row(offer.get("rowIndex")),
        stock=models.to_int(offer.get("count")),
        qty_options=None,
    )


def seat_listing(
    seat: dict[str, Any], tiers_by_id: dict[Any, dict[str, Any]]
) -> models.Listing:
    """Converts a seat-map offer into a listing.

    Args:
        seat: One entry of the seat-map ticket list.
        tiers_by_id: Ticket tiers of the session keyed by id.

    Returns:
        The listing.
    """
    tier = tiers_by_id.get(seat.get("ticketCategoryId"), {})
    qtys = seat.get("availableNumbers") or []
    return models.Listing(
        tier_id=models.to_str(seat.get("ticketCategoryId")),
        tier_name=models.to_str(tier.get("specification")),
        face_value=models.to_float(
            seat.get("originPrice") or tier.get("originPrice")
        ),
        source="seat_map",
        buy_type=None,
        listing_id=str(seat["id"]),
        price=models.to_float(seat.get("salePrice")),
        area=models.to_str(seat.get("areaName")),
        row=_row(seat.get("rowIndex")),
        stock=models.to_int(seat.get("sellingAmount")),
        qty_options=",".join(str(q) for q in qtys) or None,
    )


def _fetch_offers(
    api: client.Client, tier_id: Any, buy_mode: int
) -> list[tuple[str | None, dict[str, Any]]]:
    payload = api.post_json(
        _url("/v8/tickets"),
        {
            "ticketCategoryId": tier_id,
            "buyType": buy_mode,
            "supportSpeedPackBuy": True,
        },
        _HEADERS,
        _expect(dict),
    )
    return [
        (group.get("type"), item["ticketGroupInfoDetail"]["ticketGroup"])
        for group in payload.get("data") or []
        for item in group.get("ticketGroupInfoList") or []
    ]


def _row(value: Any) -> int | None:
    row = models.to_int(value)
    return None if row == _ANY_ROW else row


def _url(path: str, **params: Any) -> str:
    query = urllib.parse.urlencode(params)
    return f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"


def _expect(kind: type) -> Callable[[Any], None]:
    def validate(payload: Any) -> None:
        if not isinstance(payload, kind):
            raise client.FetchError(
                f"expected {kind.__name__}: {payload!r:.120}"
            )

    return validate


def _validate_events(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise client.FetchError(f"unexpected payload: {payload!r:.120}")
    if not isinstance(payload.get("data"), dict):
        raise client.FetchError("payload has no data")
