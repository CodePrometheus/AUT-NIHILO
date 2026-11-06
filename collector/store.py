"""Writes run output: one price file per run and a daily fetch log."""

from collections.abc import Iterable
import csv
import dataclasses
import datetime
import os
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from collector import models

_TIMESTAMP = pa.timestamp("ms", tz="+08:00")

PRICE_SCHEMA = pa.schema([
    ("fetched_at", _TIMESTAMP),
    ("site", pa.string()),
    ("city", pa.string()),
    ("show_id", pa.string()),
    ("session_id", pa.string()),
    ("session_time", _TIMESTAMP),
    ("tier_id", pa.string()),
    ("tier_name", pa.string()),
    ("face_value", pa.float64()),
    ("source", pa.string()),
    ("buy_type", pa.string()),
    ("listing_id", pa.string()),
    ("price", pa.float64()),
    ("area", pa.string()),
    ("row", pa.int32()),
    ("stock", pa.int32()),
    ("qty_options", pa.string()),
])

RUN_FIELDS = (
    "fetched_at",
    "site",
    "city",
    "show_id",
    "session_id",
    "session_time",
    "status",
    "listings",
    "requests",
    "error",
)


def price_records(
    results: Iterable[models.SessionResult], fetched_at: datetime.datetime
) -> list[dict[str, Any]]:
    """Flattens the listings of `results` into price-table rows."""
    return [
        {
            "fetched_at": fetched_at,
            **_session_columns(result.session),
            **dataclasses.asdict(listing),
        }
        for result in results
        for listing in result.listings
    ]


def write_prices(
    results: Iterable[models.SessionResult],
    fetched_at: datetime.datetime,
    root: str,
) -> str | None:
    """Writes the run's listings to `prices/<date>/<time>.parquet`.

    Args:
        results: Session results of the run.
        fetched_at: Run start time; names the file.
        root: Data directory.

    Returns:
        Path of the written file, or None when the run found no listings.
    """
    records = price_records(results, fetched_at)
    if not records:
        return None
    path = os.path.join(
        root, "prices", f"{fetched_at:%Y-%m-%d}", f"{fetched_at:%H%M%S}.parquet"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    table = pa.Table.from_pylist(records, schema=PRICE_SCHEMA)
    pq.write_table(table, path, compression="zstd")
    return path


def append_runs(
    results: Iterable[models.SessionResult],
    fetched_at: datetime.datetime,
    root: str,
) -> str:
    """Appends one line per session to the fetch log `runs/<date>.csv`.

    Args:
        results: Session results of the run.
        fetched_at: Run start time.
        root: Data directory.

    Returns:
        Path of the log file.
    """
    path = os.path.join(root, "runs", f"{fetched_at:%Y-%m-%d}.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RUN_FIELDS)
        if is_new:
            writer.writeheader()
        for result in results:
            columns = _session_columns(result.session)
            start = columns["session_time"]
            writer.writerow({
                **columns,
                "fetched_at": fetched_at.isoformat(),
                "session_time": start.isoformat() if start else "",
                "status": result.status,
                "listings": len(result.listings),
                "requests": result.requests,
                "error": result.error,
            })
    return path


def _session_columns(session: models.Session) -> dict[str, Any]:
    return {
        "site": session.show.site,
        "city": session.show.city,
        "show_id": session.show.show_id,
        "session_id": session.session_id,
        "session_time": session.session_time,
    }
