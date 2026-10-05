"""Polite HTTP access to codeforces.com.

Codeforces answers 403 to a default User-Agent and starts throttling when hit
too fast, so every request here is throttled, retried with backoff and sent with
a browser-like UA (and optionally through a proxy).
"""

from __future__ import annotations

import random
import threading
import time
from typing import Self
from urllib.parse import urljoin

import httpx

from .config import Settings

RETRY_STATUS = frozenset({403, 408, 429, 500, 502, 503, 504})


class FetchError(RuntimeError):
    """Raised when a URL cannot be retrieved after all retries."""


class Fetcher:
    """Thread-safe, throttled HTTP client."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://codeforces.com/problemset",
            },
            timeout=settings.fetch_timeout,
            follow_redirects=True,
            proxy=settings.proxy or None,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self, min_interval: float = 0.0) -> None:
        """Keep at least `AI4CF_FETCH_DELAY` seconds between requests."""
        delay = max(self._settings.fetch_delay, min_interval)
        if delay <= 0:
            return
        with self._lock:
            wait = self._last_request + delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def get(
        self,
        url: str,
        *,
        retries: int | None = None,
        params: dict | None = None,
        min_interval: float = 0.0,
    ) -> httpx.Response:
        attempts = (self._settings.fetch_retries if retries is None else retries) + 1
        last_error = ""
        for attempt in range(attempts):
            self._throttle(min_interval)
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:  # network level failure
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 400:
                    return response
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in RETRY_STATUS:
                    break
            if attempt + 1 < attempts:
                # 403 is CF's throttling answer: back off harder than for a blip.
                base = max(self._settings.fetch_delay, 1.0) * (2**attempt)
                time.sleep(base + random.uniform(0, base / 2))
        raise FetchError(f"GET {url} failed after {attempts} attempts ({last_error})")

    def text(self, url: str) -> str:
        return self.get(url).text

    def content(self, url: str) -> tuple[bytes, str]:
        """Return `(body, final_url)` — the URL after redirects, for naming."""
        response = self.get(url)
        return response.content, str(response.url)

    def joins(self, base: str, href: str) -> str:
        return urljoin(base, href)
