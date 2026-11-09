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
        lowest: Lowest price per (site, session).
        listings: Listing count per (site, session).
        best: Lowest price per session across both sites.
        tier_session: Session whose tiers are broken down.
        tiers: Lowest price per (site, face value) for `tier_session`.
    """

    start: datetime.datetime
    end: datetime.datetime
    sessions: list[str]
    lowest: dict[tuple[str, str], Series]
    listings: dict[tuple[str, str], Series]
    best: dict[str, Series]
    tier_session: str
    tiers: dict[tuple[str, int], Series]


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
    lowest = _series(con, "site, sess", "min(price)")
    best = {
        key[0]: value
        for key, value in _series(con, "sess", "min(price)").items()
    }
    tier_session = max(best, key=lambda s: _rise(best[s]))
    return DashboardData(
        start=_local(start),
        end=_local(end),
        sessions=sessions,
        lowest=lowest,
        listings=_series(con, "site, sess", "count(*)"),
        best=best,
        tier_session=tier_session,
        tiers={
            (site, int(face)): value
            for (site, face), value in _series(
                con,
                "site, face_value",
                "min(price)",
                f"sess = {_literal(tier_session)} and face_value is not null",
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


def _rise(series: Series) -> float:
    values = series[1]
    return (values[-1] - values[0]) / values[0]


def _literal(text: str) -> str:
    """Quotes `text` as a SQL string literal."""
    escaped = text.replace("'", "''")
    return f"'{escaped}'"


def _local(epoch_s: float) -> datetime.datetime:
    moment = datetime.datetime.fromtimestamp(epoch_s, config.TZ)
    return moment.replace(tzinfo=None)
