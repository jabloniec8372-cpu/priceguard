"""Polite fetching: robots.txt, a pause between requests to one host, and
bounded retries on 429/5xx. The HTTP call is injected, so it is testable."""
from __future__ import annotations

import time
import urllib.request
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

UA = "PriceGuard/0.1 (price monitoring; contact: owner@example.invalid)"


class Blocked(Exception):
    """robots.txt does not allow this URL."""


class FetchError(Exception):
    pass


def default_get(url: str) -> tuple[int, str]:  # pragma: no cover - needs network
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
    except HTTPError as e:
        return e.code, ""


class Fetcher:
    def __init__(
        self,
        get: Callable[[str], tuple[int, str]] = default_get,
        delay: float = 2.0,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.get, self.delay, self.retries = get, delay, retries
        self.sleep, self.clock = sleep, clock
        self._robots: dict[str, RobotFileParser] = {}
        self._last: dict[str, float] = {}

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            status, text = self.get(origin + "/robots.txt")
            if status == 200:
                rp.parse(text.splitlines())
            elif status == 404:
                rp.parse([])  # no robots.txt: everything allowed
            else:  # cannot tell: be conservative
                rp.parse(["User-agent: *", "Disallow: /"])
            self._robots[origin] = rp
        return self._robots[origin].can_fetch(UA, url)

    def _pause(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self.delay - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def fetch(self, url: str) -> str:
        if not self._allowed(url):
            raise Blocked(url)
        host = urlsplit(url).netloc
        status = 0
        for attempt in range(self.retries + 1):
            self._pause(host)
            status, text = self.get(url)
            if status == 200:
                return text
            if status != 429 and status < 500:
                break
            self.sleep(self.delay * (2 ** attempt))
        raise FetchError(f"HTTP {status} for {url}")
