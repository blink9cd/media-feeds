"""The catalog fallback: when neither search returns the album, ask the artist's catalog.

The store's search index can leave out an album it carries: both searches come back
with the artist's other albums, so `pick_album` has nothing to choose and the cover is
null forever, while `/lookup?id=<artistId>&entity=album` lists the album. These pin the
fallback against a fake fetcher that routes by endpoint: it runs only on a miss, only
for an `artistId` a same-artist candidate supplied, at most twice, and its catalog is
ranked by the same matcher, so the library's own year and track count pick the
original over a later remaster. Every name here is an invented stand-in.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
import urllib.parse
import zlib
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TOOL_ROOT)]

from cover_art import artwork_url  # noqa: E402 - import follows the sys.path bootstrap above
from cover_art import AlbumRef  # noqa: E402 - import follows the sys.path bootstrap above

REF = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2026, 8, 30))
ARTIST_ID = 4242
ORIGINAL_ART = "https://example.com/original/100x100bb.jpg"


def _reply(*results: dict[str, object]) -> bytes:
    """A search or lookup response body carrying exactly these results."""
    return json.dumps({"resultCount": len(results), "results": list(results)}).encode(
        "utf-8"
    )


def _album(name: str, **overrides: object) -> dict[str, object]:
    """An album record by REF's artist, as either endpoint returns one."""
    record: dict[str, object] = {
        "wrapperType": "collection",
        "artistId": ARTIST_ID,
        "artistName": "An Artist",
        "collectionName": name,
        "collectionId": zlib.crc32(name.encode("utf-8")),
        "releaseDate": "2001-01-01T00:00:00Z",
        "trackCount": 12,
        "artworkUrl100": f"https://example.com/{len(name)}/100x100bb.jpg",
    }
    record.update(overrides)
    return record


# What both searches return in the reproduced case: the artist's other albums only.
OTHER_ALBUMS = (_album("A Later Record"), _album("Another Record"))

# The catalog as `/lookup` returns it: the artist row first, then the wanted album
# and a later remaster that shares its name, the remaster listed first, so only the
# year and track count can put the original ahead of it.
CATALOG = (
    {"wrapperType": "artist", "artistId": ARTIST_ID, "artistName": "An Artist"},
    _album(
        "A Record (Remastered)",
        releaseDate="2019-05-01T00:00:00Z",
        trackCount=14,
        artworkUrl100="https://example.com/remaster/100x100bb.jpg",
    ),
    _album(
        "A Record",
        releaseDate="1994-03-01T00:00:00Z",
        trackCount=10,
        artworkUrl100=ORIGINAL_ART,
    ),
    *OTHER_ALBUMS,
)


class _Store:
    """A fake fetcher answering searches and lookups from fixed replies, recording URLs."""

    def __init__(
        self,
        search: bytes,
        catalogs: dict[str, bytes] | None = None,
    ) -> None:
        self.search = search
        self.catalogs = catalogs or {}
        self.urls: list[str] = []

    def __call__(self, url: str, _timeout: float) -> bytes:
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        if parts.path == "/search":
            return self.search
        query = urllib.parse.parse_qs(parts.query)
        return self.catalogs.get(query["id"][0], _reply())

    def lookups(self) -> list[dict[str, list[str]]]:
        """The query of every `/lookup` request, in the order they were made."""
        return [
            urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            for url in self.urls
            if urllib.parse.urlsplit(url).path == "/lookup"
        ]


class CatalogFallbackTest(unittest.TestCase):
    """A search miss falls back to the artist's catalog, ranked by the same matcher."""

    def test_an_album_absent_from_both_searches_is_found_in_the_catalog(self) -> None:
        # The reproduced defect: both searches return only the artist's other albums,
        # so before the fallback this cover was null in every refresh.
        store = _Store(_reply(*OTHER_ALBUMS), {str(ARTIST_ID): _reply(*CATALOG)})
        match = artwork_url(REF, fetch=store, year=1994, tracks=10)
        self.assertEqual(match.url, "https://example.com/original/400x400bb.webp")
        self.assertEqual(match.year, 1994)

    def test_the_lookup_asks_for_the_artists_whole_album_catalog(self) -> None:
        store = _Store(_reply(*OTHER_ALBUMS), {str(ARTIST_ID): _reply(*CATALOG)})
        artwork_url(REF, fetch=store, year=1994, tracks=10)
        self.assertEqual(
            store.lookups(),
            [{"id": [str(ARTIST_ID)], "entity": ["album"], "limit": ["200"]}],
        )

    def test_a_search_that_already_matched_sends_no_lookup(self) -> None:
        store = _Store(_reply(_album("A Record"), *OTHER_ALBUMS))
        self.assertIsNotNone(artwork_url(REF, fetch=store).url)
        self.assertEqual(store.lookups(), [])

    def test_no_lookup_without_a_same_artist_candidate(self) -> None:
        # Another artist's id would fetch another artist's catalog: nothing in it can
        # be REF's album, so the request would be spent for nothing.
        stranger = _album("A Record", artistName="Someone Else", artistId=7)
        store = _Store(_reply(stranger))
        self.assertIsNone(artwork_url(REF, fetch=store).url)
        self.assertEqual(store.lookups(), [])

    def test_an_artist_id_that_is_not_an_integer_is_never_sent(self) -> None:
        # Remote input destined for a URL: a string, a float, a JSON array, or a JSON
        # `true` (a Python bool, which is an int) is no artist id, so none is looked up.
        for bogus in ("4242", 4242.0, [4242], True, 0, -4242):
            with self.subTest(artist_id=bogus):
                store = _Store(_reply(_album("A Later Record", artistId=bogus)))
                self.assertIsNone(artwork_url(REF, fetch=store).url)
                self.assertEqual(store.lookups(), [])

    def test_a_second_artist_id_is_tried_when_the_first_catalog_lacks_the_album(
        self,
    ) -> None:
        # Two artists can share a name; the first id's catalog is the wrong one here.
        namesake = _album("A Namesake Record", artistId=1)
        store = _Store(
            _reply(namesake, *OTHER_ALBUMS),
            {"1": _reply(namesake), str(ARTIST_ID): _reply(*CATALOG)},
        )
        match = artwork_url(REF, fetch=store, year=1994, tracks=10)
        self.assertEqual(match.url, "https://example.com/original/400x400bb.webp")
        self.assertEqual([q["id"] for q in store.lookups()], [["1"], [str(ARTIST_ID)]])

    def test_at_most_two_catalogs_are_fetched(self) -> None:
        # The fallback's whole cost is bounded: a pool naming many ids under the
        # artist's name spends two extra requests, never one per id.
        pool = [_album(f"Record {n}", artistId=n) for n in range(1, 6)]
        store = _Store(_reply(*pool))
        self.assertIsNone(artwork_url(REF, fetch=store).url)
        self.assertEqual([q["id"] for q in store.lookups()], [["1"], ["2"]])

    def test_an_over_long_artist_name_lends_no_id(self) -> None:
        # The name strip is quadratic in length (`artwork_match.split_name`), so a
        # name past the cap is never normalized, on the network's side and on the
        # library's, where a folder name is the input.
        long_name = "x" * 600
        cases = (
            (REF, _album("A Later Record", artistName=long_name)),
            (REF._replace(artist=long_name), _album("A Later Record")),
        )
        for ref, candidate in cases:
            with self.subTest(wanted=len(ref.artist)):
                store = _Store(_reply(candidate))
                self.assertIsNone(artwork_url(ref, fetch=store).url)
                self.assertEqual(store.lookups(), [])

    def test_a_failed_lookup_yields_no_url_rather_than_raising(self) -> None:
        def store(url: str, _timeout: float) -> bytes:
            if urllib.parse.urlsplit(url).path == "/lookup":
                raise OSError("unreachable")
            return _reply(*OTHER_ALBUMS)

        self.assertIsNone(artwork_url(REF, fetch=store).url)


if __name__ == "__main__":
    unittest.main()
