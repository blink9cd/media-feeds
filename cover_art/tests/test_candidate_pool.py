"""The I/O half's candidate pool: the window each search asks for, and the dedupe.

`artwork_url` pools two searches before ranking (its docstring records why), keyed on
`collectionId` so the same record arriving from both is scored once. These pin the two
properties of that pool a fake fetcher can prove: the search asks for a window wide
enough to choose BETWEEN candidates, and a candidate with no usable id is kept rather
than dropped.
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

from cover_art import artwork_url  # noqa: E402 - import follows the sys.path bootstrap above
from cover_art import AlbumRef  # noqa: E402 - import follows the sys.path bootstrap above

REF = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2026, 8, 30))


def _reply(*results: dict[str, object]) -> bytes:
    """A search response body carrying exactly these results."""
    return json.dumps({"resultCount": len(results), "results": list(results)}).encode(
        "utf-8"
    )


class SearchWindowTest(unittest.TestCase):
    """What the request asks the store for."""

    def test_each_search_asks_for_twenty_five_results(self) -> None:
        # Was 1, which is the whole defect the matcher exists to fix: with a single
        # result there is nothing to choose BETWEEN. The limit is the one number
        # that makes verification possible, so it is pinned from the request the
        # fetcher actually receives, on both queries.
        seen: list[str] = []

        def record(url: str, _timeout: float) -> bytes:
            seen.append(url)
            return _reply()

        artwork_url(REF, fetch=record)
        self.assertEqual(len(seen), 2)
        for url in seen:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            self.assertEqual(query["limit"], ["25"], url)
            self.assertEqual(query["entity"], ["album"], url)


class DedupeTest(unittest.TestCase):
    """Candidates with no usable `collectionId` are kept, never deduped against."""

    def test_a_second_candidate_without_an_id_is_still_considered(self) -> None:
        # The dedupe treats a missing or unhashable id as NO identity, so two such
        # candidates must both reach the matcher: the second one here is the album
        # asked for, and a dedupe that keyed both on "no id" would drop it as a
        # repeat of the first and return no cover for an album the store listed.
        # Both shapes of "no usable id", because an unhashable one is folded to a
        # missing one: the first candidate has no `collectionId` at all, then one
        # whose id is a JSON array.
        no_id: dict[str, object] = {
            "artistName": "An Artist",
            "collectionName": "Some Other Record",
            "artworkUrl100": "https://example.com/other/100x100bb.jpg",
        }
        unhashable_id: dict[str, object] = {**no_id, "collectionId": [1, 2]}
        wanted: dict[str, object] = {
            "artistName": "An Artist",
            "collectionName": "A Record",
            "artworkUrl100": "https://example.com/wanted/100x100bb.jpg",
        }
        for other in (no_id, unhashable_id):
            with self.subTest(other_id=other.get("collectionId")):
                body = _reply(other, wanted)
                match = artwork_url(REF, fetch=lambda _u, _t: body)
                self.assertEqual(match.url, "https://example.com/wanted/400x400bb.webp")


if __name__ == "__main__":
    unittest.main()
