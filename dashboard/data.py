"""Aggregates collected listings into the time series the dashboard plots."""

import dataclasses
import datetime
import os

import duckdb

from collector import config

# Rounds are summarised per bucket so that a single odd round does not spike
# the chart.
BUCKET_MINUTES = 30

Series = tuple[list[datetime.datetime], list[float]]


@dataclasses.dataclass(frozen=True)
class DashboardData:
    """Everything the dashboard draws.

    Attributes:
        start: First collection time in the data.
        end: Last collection time in the data.
        sessions: Session labels ("MM-DD") in start order.
        listings: Listing count per (site, session).
        best: Lowest site median per session and time bucket.
        best_sites: Sites matching each session's last value in best.
        tiers: Lowest price per (site, session, face value).
    """

    start: datetime.datetime
    end: datetime.datetime
    sessions: list[str]
    listings: dict[tuple[str, str], Series]
    best: dict[str, Series]
    best_sites: dict[str, tuple[str, ...]]
    tiers: dict[tuple[str, str, int], Series]


def load(root: str, city: str) -> DashboardData:
    """Reads the price files under `root` and aggregates them for `city`.

    Args:
        root: Data directory holding `prices/<date>/*.parquet`.
        city: City whose sessions are shown.

    Returns:
        The aggregated series.

    Raises:
        ValueError: There is no data for `city`.
    """
    con = duckdb.connect()
    files = os.path.join(root, "prices", "*", "*.parquet")
    con.execute(
        "create view p as select *, strftime(session_time, '%m-%d') as sess "
        f"from read_parquet({_literal(files)}) where city = {_literal(city)}"
    )
    (start, end, rows), = con.sql(
        "select epoch(min(fetched_at)), epoch(max(fetched_at)), count(*) from p"
    ).fetchall()
    if not rows:
        raise ValueError(f"No listings for {city}.")
    sessions = [s for (s,) in con.sql(
        "select sess from p group by sess order by min(session_time)"
    ).fetchall()]
    best, best_sites = _best_prices(_series(con, "site, sess", "min(price)"))
    return DashboardData(
        start=_local(start),
        end=_local(end),
        sessions=sessions,
        listings=_series(con, "site, sess", "count(*)"),
        best=best,
        best_sites=best_sites,
        tiers={
            (site, session, int(face)): value
            for (site, session, face), value in _series(
                con,
                "site, sess, face_value",
                "min(price)",
                "face_value is not null",
            ).items()
        },
    )


def _series(con, keys, metric, where="true"):
    """Returns {key tuple: series} of `metric` per round, bucketed."""
    rows = con.sql(f"""
        with per_round as (
          select {keys}, fetched_at, {metric} as v
          from p where {where} group by all)
        select {keys},
               epoch(time_bucket(interval {BUCKET_MINUTES} minutes,
                                 fetched_at)),
               median(v)
        from per_round group by all order by all""").fetchall()
    out: dict[tuple, Series] = {}
    width = len(keys.split(","))
    for row in rows:
        times, values = out.setdefault(tuple(row[:width]), ([], []))
        times.append(_local(row[width]))
        values.append(float(row[width + 1]))
    return out


def _best_prices(site_lowest):
    by_session: dict[str, dict[datetime.datetime, dict[str, float]]] = {}
    for (site, session), (times, prices) in site_lowest.items():
        buckets = by_session.setdefault(session, {})
        for time, price in zip(times, prices):
            buckets.setdefault(time, {})[site] = price

    best = {}
    best_sites = {}
    for session, buckets in by_session.items():
        times = sorted(buckets)
        prices = [min(buckets[time].values()) for time in times]
        best[session] = (times, prices)
        best_sites[session] = tuple(
            site for site, price in buckets[times[-1]].items()
            if price == prices[-1]
        )
    return best, best_sites


def _literal(text: str) -> str:
    """Quotes `text` as a SQL string literal."""
    escaped = text.replace("'", "''")
    return f"'{escaped}'"


def _local(epoch_s: float) -> datetime.datetime:
    moment = datetime.datetime.fromtimestamp(epoch_s, config.TZ)
    return moment.replace(tzinfo=None)
