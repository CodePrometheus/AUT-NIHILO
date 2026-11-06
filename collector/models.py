"""Records produced by a collection run."""

import dataclasses
import datetime
from typing import Any

from collector import config


@dataclasses.dataclass(frozen=True)
class Session:
    """One performance of a show.

    Attributes:
        show: The show this session belongs to.
        session_id: The site's identifier for the session.
        session_time: Scheduled start, or None when unknown.
        max_qty: Largest number of tickets one order may contain.
    """

    show: config.Show
    session_id: str
    session_time: datetime.datetime | None
    max_qty: int = 1


@dataclasses.dataclass(frozen=True)
class Listing:
    """One ticket offer as seen in a single run.

    Attributes:
        tier_id: The site's identifier for the price tier.
        tier_name: Tier label shown by the site.
        face_value: Printed ticket price of the tier.
        source: Listing feed the offer came from.
        buy_type: The site's purchase mode, when it reports one.
        listing_id: The site's identifier for the offer.
        price: Asking price per ticket.
        area: Seat location text shown by the site.
        row: Row number, when the offer names one.
        stock: Tickets left in the offer.
        qty_options: Comma-separated order sizes the offer can be bought in.
    """

    tier_id: str | None
    tier_name: str | None
    face_value: float | None
    source: str
    buy_type: str | None
    listing_id: str
    price: float | None
    area: str | None
    row: int | None
    stock: int | None
    qty_options: str | None


@dataclasses.dataclass(frozen=True)
class SessionResult:
    """Outcome of fetching one session in one run.

    Attributes:
        session: The session that was fetched.
        listings: Offers found; empty when the fetch failed.
        requests: HTTP requests spent on this session.
        error: Failure description, empty on success.
    """

    session: Session
    listings: tuple[Listing, ...] = ()
    requests: int = 0
    error: str = ""

    @property
    def status(self) -> str:
        """Returns "ok" or "failed"."""
        return "failed" if self.error else "ok"


def to_float(value: Any) -> float | None:
    """Returns `value` as a float, or None when it is not numeric."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    """Returns `value` as an int, or None when it is not numeric."""
    number = to_float(value)
    return None if number is None else int(number)


def to_str(value: Any) -> str | None:
    """Returns `value` as a string, or None when it is empty."""
    return None if value is None or value == "" else str(value)
