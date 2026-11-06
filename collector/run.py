"""Runs one collection pass over every tracked show.

Usage: python -m collector.run
"""

import datetime
import logging
import os
import sys

from collector import client
from collector import config
from collector import models
from collector import motianlun
from collector import piaoniu
from collector import store

SITES = {"motianlun": motianlun, "piaoniu": piaoniu}

# Exit codes; the workflow stops scheduling runs on EXIT_FINISHED.
EXIT_OK = 0
EXIT_ALL_FAILED = 1
EXIT_FINISHED = 3  # Every tracked session has started.

# Unexpected payload shapes fail only the affected session, not the run.
_SESSION_ERRORS = (client.FetchError, KeyError, TypeError, ValueError)


def collect_show(
    api: client.Client, show: config.Show, now: datetime.datetime
) -> list[models.SessionResult]:
    """Fetches every session of `show` that has not started yet.

    Args:
        api: HTTP client.
        show: Show to collect.
        now: Run start time; sessions starting at or before it are skipped.

    Returns:
        One result per upcoming session, or a single failed result with an
        empty session id when the session list could not be fetched.
    """
    site = SITES[show.site]
    start = api.requests
    try:
        sessions = site.list_sessions(api, show)
    except _SESSION_ERRORS as e:
        return [
            models.SessionResult(
                models.Session(show, "", None),
                requests=api.requests - start,
                error=_describe(e),
            )
        ]
    results = []
    for session in sessions:
        if session.session_time is not None and session.session_time <= now:
            continue
        start = api.requests
        try:
            listings = tuple(site.fetch_listings(api, session))
        except _SESSION_ERRORS as e:
            results.append(
                models.SessionResult(
                    session, requests=api.requests - start, error=_describe(e)
                )
            )
        else:
            results.append(
                models.SessionResult(session, listings, api.requests - start)
            )
    return results


def main() -> int:
    """Collects all shows and writes the run's files.

    Returns:
        EXIT_FINISHED when no session is left to collect, EXIT_ALL_FAILED
        when every session failed, otherwise EXIT_OK.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    now = datetime.datetime.now(config.TZ).replace(microsecond=0)
    api = client.Client()
    results = [r for show in config.SHOWS for r in collect_show(api, show, now)]
    if not results:
        logging.info("No upcoming sessions.")
        return EXIT_FINISHED
    root = os.environ.get("DATA_DIR", "data")
    prices = store.write_prices(results, now, root)
    log = store.append_runs(results, now, root)
    failed = [r for r in results if r.error]
    logging.info(
        "%s: %d listings, %d sessions, %d failed, %d requests",
        now.isoformat(),
        sum(len(r.listings) for r in results),
        len(results),
        len(failed),
        api.requests,
    )
    logging.info("Wrote %s and %s.", prices or "no price file", log)
    for r in failed:
        logging.warning(
            "Failed %s %s %s: %s",
            r.session.show.site,
            r.session.show.city,
            r.session.session_id or "(session list)",
            r.error,
        )
    return EXIT_ALL_FAILED if len(failed) == len(results) else EXIT_OK


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


if __name__ == "__main__":
    sys.exit(main())
