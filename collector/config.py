"""Collection settings: tracked shows, time zone and request pacing."""

import dataclasses
import datetime

# Timestamps and date folders use UTC+8 so days split at local midnight.
TZ = datetime.timezone(datetime.timedelta(hours=8))

REQUEST_INTERVAL_S = 0.8
MAX_ATTEMPTS = 3
TIMEOUT_S = 20

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)


@dataclasses.dataclass(frozen=True)
class Show:
    """A show listed on one site.

    Attributes:
        site: Site code, written to the `site` column.
        city: City code, written to the `city` column.
        show_id: The site's identifier for the show.
        city_code: City parameter the site requires, if any.
    """

    site: str
    city: str
    show_id: str
    city_code: str = ""


SHOWS = (
    Show("motianlun", "changsha", "69eb15cddabe840001c63c0e", "4301"),
    Show("motianlun", "guangzhou", "69eb16b469f432000151d062", "4401"),
    Show("piaoniu", "changsha", "774654"),
    Show("piaoniu", "guangzhou", "784190"),
)
