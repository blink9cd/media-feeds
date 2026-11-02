"""Why a cover came back null: `ArtworkMatch.reason`, against a fake fetcher.

A null cover used to mean any of three things, and the one notice album_scan prints
for it named two of them at once, so an album the store's search never returned sat
null for weeks looking like a network blip. `artwork_url` still never raises;
it now says which null this is, as data. Every name here is an invented stand-in.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
import urllib.parse
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TOOL_ROOT)]

from cover_art import (  # noqa: E402 - import follows the sys.path bootstrap above
    MISS_LOOKUP_FAILED,
    MISS_NO_ART,
    MISS_NO_MATCH,
    AlbumRef,
    Fetcher,
    artwork_url,
)

REF = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2026, 8, 30))


def _reply(*results: object) -> bytes:
    """A response body carrying exactly these results."""
    return json.dumps({"resultCount": len(results), "results": list(results)}).encode(
        "utf-8"
    )


def _album(name: str, **overrides: object) -> dict[str, object]:
    """An album record by REF's artist."""
    record: dict[str, object] = {
        "artistId": 4242,
        "artistName": "An Artist",
        "collectionName": name,
        "collectionId": len(name),
        "releaseDate": "1994-01-01T00:00:00Z",
        "trackCount": 10,
        "artworkUrl100": "https://example.com/a/100x100bb.jpg",
    }
    record.update(overrides)
    return record


def _answering(body: bytes) -> Fetcher:
    """A fetcher that answers every request with `body`."""

    def fetch(_url: str, _timeout: float) -> bytes:
        return body

    return fetch


def _failing(_url: str, _timeout: float) -> bytes:
    raise OSError("unreachable")


class MissReasonTest(unittest.TestCase):
    """Each null cover names its cause; a found cover names none."""

    def test_a_found_cover_carries_no_reason(self) -> None:
        match = artwork_url(REF, fetch=lambda _u, _t: _reply(_album("A Record")))
        self.assertIsNotNone(match.url)
        self.assertIsNone(match.reason)

    def test_every_request_answered_and_none_matched_is_no_match(self) -> None:
        # The store answered every request, catalog included: the album is not there
        # under any name the matcher accepts.
        match = artwork_url(REF, fetch=lambda _u, _t: _reply(_album("Another Record")))
        self.assertEqual(match, (None, None, MISS_NO_MATCH))

    def test_an_empty_store_is_no_match(self) -> None:
        match = artwork_url(REF, fetch=lambda _u, _t: _reply())
        self.assertEqual(match.reason, MISS_NO_MATCH)

    def test_every_request_failing_is_lookup_failed(self) -> None:
        self.assertEqual(artwork_url(REF, fetch=_failing).reason, MISS_LOOKUP_FAILED)

    def test_one_failed_search_beside_a_miss_is_lookup_failed(self) -> None:
        # The request that failed may have been the one holding the album, so the
        # store cannot be said not to carry it.
        def half(url: str, timeout: float) -> bytes:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            if query.get("term") == ["An Artist"]:
                return _failing(url, timeout)
            return _reply()

        self.assertEqual(artwork_url(REF, fetch=half).reason, MISS_LOOKUP_FAILED)

    def test_a_failed_catalog_lookup_after_a_search_miss_is_lookup_failed(self) -> None:
        def store(url: str, timeout: float) -> bytes:
            if urllib.parse.urlsplit(url).path == "/lookup":
                return _failing(url, timeout)
            return _reply(_album("Another Record"))

        self.assertEqual(artwork_url(REF, fetch=store).reason, MISS_LOOKUP_FAILED)

    def test_a_reply_that_is_not_a_result_list_is_lookup_failed(self) -> None:
        # Malformed is not empty: a body with no usable `results` said nothing about
        # whether the album is there.
        for body in (b"not json", b'{"results": {}}', b'{"resultCount": 0}'):
            with self.subTest(body=body):
                match = artwork_url(REF, fetch=_answering(body))
                self.assertEqual(match.reason, MISS_LOOKUP_FAILED)

    def test_the_right_album_without_usable_art_is_no_art(self) -> None:
        # Found, so the year is kept; only the image is missing or refused.
        for art in ({"artworkUrl100": None}, {"artworkUrl100": "javascript:alert(1)"}):
            with self.subTest(art=art):
                match = artwork_url(
                    REF, fetch=_answering(_reply(_album("A Record", **art)))
                )
                self.assertEqual(match, (None, 1994, MISS_NO_ART))

    def test_the_reasons_are_distinct(self) -> None:
        self.assertEqual(len({MISS_NO_MATCH, MISS_LOOKUP_FAILED, MISS_NO_ART}), 3)


if __name__ == "__main__":
    unittest.main()
