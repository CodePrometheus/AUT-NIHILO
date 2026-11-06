"""Paced JSON-over-HTTP client with retries."""

from collections.abc import Callable
import http.client
import json
import time
from typing import Any
import urllib.error
import urllib.request

from collector import config

Validator = Callable[[Any], None]


class FetchError(Exception):
    """A request failed or kept returning an unusable payload."""


class Client:
    """Sends JSON requests, at most one per interval, retrying failures.

    Attributes:
        requests: Number of HTTP requests sent so far.
    """

    def __init__(
        self,
        interval_s: float = config.REQUEST_INTERVAL_S,
        attempts: int = config.MAX_ATTEMPTS,
        timeout_s: float = config.TIMEOUT_S,
    ):
        self.requests = 0
        self._interval_s = interval_s
        self._attempts = attempts
        self._timeout_s = timeout_s
        self._last_sent = float("-inf")

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        validate: Validator | None = None,
    ) -> Any:
        """Sends a GET request and returns the decoded JSON body.

        Args:
            url: Absolute URL including the query string.
            headers: Request headers.
            validate: Raises FetchError for an unusable payload, which is then
                retried like a transport error.

        Returns:
            The decoded JSON payload.

        Raises:
            FetchError: Every attempt failed.
        """
        request = urllib.request.Request(url, headers=headers)
        return self._send(request, validate)

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str],
        validate: Validator | None = None,
    ) -> Any:
        """Sends `body` as a JSON POST and returns the decoded JSON body.

        Args:
            url: Absolute URL including the query string.
            body: Request body, encoded as JSON.
            headers: Request headers.
            validate: See `get_json`.

        Returns:
            The decoded JSON payload.

        Raises:
            FetchError: Every attempt failed.
        """
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={**headers, "Content-Type": "application/json"},
        )
        return self._send(request, validate)

    def _send(
        self, request: urllib.request.Request, validate: Validator | None
    ) -> Any:
        error: Exception | None = None
        for attempt in range(self._attempts):
            if attempt:
                time.sleep(2 * attempt)
            self._wait_turn()
            try:
                with urllib.request.urlopen(
                    request, timeout=self._timeout_s
                ) as response:
                    payload = json.loads(response.read())
                if validate:
                    validate(payload)
                return payload
            except urllib.error.HTTPError as e:
                error = e
                if e.code < 500 and e.code != 429:
                    break
            except (OSError, http.client.HTTPException, ValueError,
                    FetchError) as e:
                error = e
        endpoint = request.full_url.split("?", 1)[0]
        raise FetchError(f"{endpoint}: {error}") from error

    def _wait_turn(self) -> None:
        delay = self._last_sent + self._interval_s - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last_sent = time.monotonic()
        self.requests += 1
