"""Only an https URL with a host is ever recorded as a cover.

The store's `artworkUrl100` is remote input that ends up in an `<img src>` on a page,
and the lookup used to record any string it found there. The predicate under test is
the rule that now stands between the two; the end-to-end cases prove `artwork_url`
actually consults it, since a predicate nobody calls passes every test of its own.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
from collections.abc import Callable
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TOOL_ROOT)]

from cover_art import (  # noqa: E402 - import follows the sys.path bootstrap above
    _is_recordable_url,
    artwork_url,
)
from cover_art import AlbumRef  # noqa: E402 - import follows the sys.path bootstrap above

REF = AlbumRef(artist="Artist A", album="Record A", added=dt.date(2026, 8, 30))

#: The release year the fixture below carries, so the "year survives" assertions can
#: name the value rather than merely asserting non-None.
_FIXTURE_YEAR = 1994

_GOOD_THUMB = "https://example.com/a/100x100bb.jpg"
_GOOD_UPSIZED = "https://example.com/a/400x400bb.webp"

#: Every class of value that must never reach the page, each with the reason it is
#: refused. The path segment matches the rewrite pattern on purpose, so a refusal is
#: never an accident of the rewrite leaving the string alone.
_HOSTILE_URLS = {
    "javascript scheme": "javascript:alert(1)",
    "data scheme": "data:image/png;base64,AAAA",
    "file scheme": "file:///etc/hosts",
    "plain http": "http://example.com/a/100x100bb.jpg",
    "scheme-relative": "//example.com/a/100x100bb.jpg",
    "no host": "https:///nohost/100x100bb.jpg",
    "empty": "",
    "scheme only": "https://",
    "userinfo": "https://user:pass@example.com/a/100x100bb.jpg",
    "username only": "https://user@example.com/a/100x100bb.jpg",
    "carriage return": "https://example.com/a/100x100bb.jpg\r\nX-Injected: yes",
    "line feed": "https://example.com/a\n/100x100bb.jpg",
    "tab": "https://example.com/a\t/100x100bb.jpg",
    "space": "https://example.com/a b/100x100bb.jpg",
    "ideographic space": "https://example.com/a　b/100x100bb.jpg",
    "nul": "https://example.com/a\x00/100x100bb.jpg",
    "escape": "https://example.com/a\x1b[31m/100x100bb.jpg",
    "zero-width space": "https://example.com/a​/100x100bb.jpg",
    "unbalanced ipv6 bracket": "https://[::1/100x100bb.jpg",
}


def _hit(thumb: object) -> bytes:
    """A search reply holding one candidate that verifies as REF, with `thumb` as its art."""
    result = {
        "artistName": "Artist A",
        "collectionName": "Record A",
        "collectionId": 1,
        "releaseDate": f"{_FIXTURE_YEAR}-01-01T00:00:00Z",
        "trackCount": 10,
        "artworkUrl100": thumb,
    }
    return json.dumps({"resultCount": 1, "results": [result]}).encode("utf-8")


def _fetch_of(thumb: object) -> Callable[[str, float], bytes]:
    """A fetcher answering every query with the one hit that carries `thumb`."""

    def fetch(_url: str, _timeout: float) -> bytes:
        return _hit(thumb)

    return fetch


class IsRecordableUrlTest(unittest.TestCase):
    """The predicate on its own: what it admits and every class it refuses."""

    def test_an_https_url_with_a_host_is_recordable(self) -> None:
        self.assertTrue(_is_recordable_url(_GOOD_THUMB))
        self.assertTrue(_is_recordable_url(_GOOD_UPSIZED))

    def test_the_scheme_and_host_are_read_case_insensitively(self) -> None:
        # A URL is the same URL however its scheme and host are capitalized, and the
        # parser folds both, so refusing this would refuse a valid cover.
        self.assertTrue(_is_recordable_url("HTTPS://Example.COM/a/100x100bb.jpg"))

    def test_a_port_and_a_query_do_not_disqualify_a_url(self) -> None:
        self.assertTrue(
            _is_recordable_url("https://example.com:8443/a/100x100bb.jpg?v=2")
        )

    def test_every_hostile_class_is_refused(self) -> None:
        for reason, url in _HOSTILE_URLS.items():
            with self.subTest(reason=reason):
                self.assertFalse(_is_recordable_url(url))


class ArtworkUrlRecordsOnlyRecordableUrlsTest(unittest.TestCase):
    """`artwork_url` consults the predicate, and keeps the year when it refuses."""

    def test_a_normal_thumbnail_is_still_rewritten_and_recorded(self) -> None:
        match = artwork_url(REF, fetch=_fetch_of(_GOOD_THUMB))
        self.assertEqual(match.url, _GOOD_UPSIZED)
        self.assertEqual(match.year, _FIXTURE_YEAR)

    def test_a_hostile_thumbnail_yields_no_url_but_keeps_the_year(self) -> None:
        # The right album, with art that cannot be used: the same arm as a missing
        # `artworkUrl100`, so the store's year still reaches a library with none.
        for reason, url in _HOSTILE_URLS.items():
            with self.subTest(reason=reason):
                match = artwork_url(REF, fetch=_fetch_of(url))
                self.assertIsNone(match.url)
                self.assertEqual(match.year, _FIXTURE_YEAR)

    def test_the_check_runs_on_the_rewritten_url_not_the_thumbnail(self) -> None:
        # The rewrite splices the caller's `image_format` into the final segment, so
        # the string actually recorded can carry a character the thumbnail never had.
        # Checking the thumbnail first would pass this; checking the result refuses it.
        match = artwork_url(REF, fetch=_fetch_of(_GOOD_THUMB), image_format="webp\r")
        self.assertIsNone(match.url)
        self.assertEqual(match.year, _FIXTURE_YEAR)


if __name__ == "__main__":
    unittest.main()
