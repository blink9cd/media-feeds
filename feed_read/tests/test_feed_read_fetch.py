"""The fetch side that stays in feed_read: `real_fetch`'s wiring to `bounded_fetch`, and
the User-Agent it hands over, proven without a request being sent.

What the URL rule accepts, what a built request refuses, and how a body is bounded are
`bounded_fetch`'s, and its own suites prove them.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

#: One call `real_fetch` made to `fetch_url`: its URL, timeout, and headers.
_Call = tuple[str, float, dict[str, str]]


def _captured_fetch(url: str, timeout: float) -> tuple[bytes, list[_Call]]:
    """`real_fetch(url, timeout)`'s body, and every call it made to `fetch_url`."""
    calls: list[_Call] = []

    def fake_fetch_url(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
        calls.append((url, timeout, dict(headers)))
        return b"<rss/>"

    with mock.patch.object(feed_read.bounded_fetch, "fetch_url", fake_fetch_url):
        body = feed_read.real_fetch(url, timeout)
    return body, calls


def _captured_header_fetch(
    headers: Mapping[str, str],
) -> tuple[bytes, list[_Call]]:
    """`real_fetch_with_headers`' body for a fixed URL, and its calls to `fetch_url`."""
    calls: list[_Call] = []

    def fake_fetch_url(url: str, timeout: float, sent: Mapping[str, str]) -> bytes:
        calls.append((url, timeout, dict(sent)))
        return b"<rss/>"

    with mock.patch.object(feed_read.bounded_fetch, "fetch_url", fake_fetch_url):
        body = feed_read.real_fetch_with_headers(
            "https://api.example.invalid/x", 4.0, headers
        )
    return body, calls


class TestRealFetchWiring(unittest.TestCase):
    """`real_fetch` hands its URL, its timeout, and the User-Agent to `fetch_url`.

    Measured 2026-09-01 (see `_USER_AGENT` in feed_read.py): Letterboxd returns HTTP
    403 Forbidden for the urllib default User-Agent ("Python-urllib/3.14") and HTTP
    200 for a plain product identifier; Goodreads accepts either. The connection
    itself is `bounded_fetch`'s and excluded from coverage there, so the header is
    proven here, on what `real_fetch` hands to `fetch_url`: a dropped header or a
    reverted UA string fails a test instead of showing up as a live 403.
    """

    def test_real_fetch_sends_the_user_agent_through_fetch_url(self) -> None:
        body, calls = _captured_fetch("https://letterboxd.com/someone/rss/", 7.5)
        self.assertEqual(body, b"<rss/>")
        self.assertEqual(
            calls,
            [
                (
                    "https://letterboxd.com/someone/rss/",
                    7.5,
                    {"User-Agent": feed_read._USER_AGENT},
                )
            ],
        )

    def test_the_user_agent_is_not_the_urllib_default(self) -> None:
        # The assertion that matters: Letterboxd rejects this literal prefix,
        # not "non-browser-ness" in general; see _USER_AGENT.
        _, calls = _captured_fetch("https://letterboxd.com/someone/rss/", 7.5)
        [(_, _, headers)] = calls
        self.assertFalse(headers["User-Agent"].startswith("Python-urllib"))

    def test_a_header_taking_fetch_adds_the_callers_headers(self) -> None:
        # The TMDB poster lookup needs an Authorization header; it goes through the
        # same wiring, so the one User-Agent constant stays the only one.
        body, calls = _captured_header_fetch({"Accept": "application/json"})
        self.assertEqual(body, b"<rss/>")
        self.assertEqual(
            calls,
            [
                (
                    "https://api.example.invalid/x",
                    4.0,
                    {
                        "Accept": "application/json",
                        "User-Agent": feed_read._USER_AGENT,
                    },
                )
            ],
        )

    def test_a_callers_user_agent_cannot_replace_the_projects_own(self) -> None:
        # One User-Agent for every request this project sends: a caller's header set
        # adds to it and never overrides it.
        _, calls = _captured_header_fetch({"User-Agent": "something-else"})
        [(_, _, headers)] = calls
        self.assertEqual(headers, {"User-Agent": feed_read._USER_AGENT})

    def test_the_moved_names_are_bounded_fetchs_own(self) -> None:
        # Re-exports, not copies: a second copy of the URL rule or the body bound
        # would drift from the one every other caller uses.
        moved = feed_read.bounded_fetch
        self.assertIs(feed_read.https_url_or_none, moved.https_url_or_none)
        self.assertIs(feed_read.read_body, moved.read_body)
        self.assertIs(feed_read.ChunkReader, moved.ChunkReader)
        self.assertEqual(feed_read.FEED_TIMEOUT_SECONDS, moved.DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(feed_read.BODY_LIMIT_BYTES, moved.BODY_LIMIT_BYTES)
        self.assertEqual(feed_read.BODY_DEADLINE_SECONDS, moved.BODY_DEADLINE_SECONDS)
