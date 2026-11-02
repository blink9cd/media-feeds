"""Parsing the Letterboxd diary feed, every case measured on 2026-08-31."""

from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above
import film_scan  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "diary.xml").read_bytes()

# A single-entry diary feed for the "short diary" cost case: it must carry a
# tmdb:movieId (otherwise _by_tmdb_id short-circuits before ever calling `lookup`,
# and the test would pass without exercising the cost it claims to guard).
_ONE_FILM_FEED = (
    b'<rss version="2.0" xmlns:letterboxd="https://letterboxd.com" '
    b'xmlns:tmdb="https://themoviedb.org"><channel>'
    b"<item><letterboxd:filmTitle>Solo Watch</letterboxd:filmTitle>"
    b"<letterboxd:watchedDate>2026-08-30</letterboxd:watchedDate>"
    b"<tmdb:movieId>9</tmdb:movieId>"
    b"<description>x</description></item>"
    b"</channel></rss>"
)


class TestDiaryEntries(unittest.TestCase):
    """Half a real Letterboxd feed is not films (measured: 50 of 100 items)."""

    def test_list_items_are_excluded(self) -> None:
        # The trap. A list item carries no film elements, so a "first N items"
        # adapter would render "A Made-Up List of Films" as a film. A feed with no
        # public lists is 100% diary entries, so this filter would look like dead
        # code if it were only tested against such a feed.
        items = feed_read.feed_items(_FIXTURE)
        self.assertEqual(len(items), 4)
        entries = film_scan.diary_entries(items)
        self.assertEqual(len(entries), 3)
        titles = [e.findtext(film_scan.LETTERBOXD_NS + "filmTitle") for e in entries]
        self.assertNotIn(None, titles)
        self.assertNotIn("A Made-Up List of Films", titles)


class TestNewestFilms(unittest.TestCase):
    """The records the committed films.json holds."""

    def setUp(self) -> None:
        # Read by the count-based tests below; the others parse the feed they need
        # inline.
        self.items = feed_read.feed_items(_FIXTURE)

    def test_it_takes_the_newest_diary_entry(self) -> None:
        records = film_scan.newest_films(feed_read.feed_items(_FIXTURE), 1)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["title"], "The Invented Voyage")
        self.assertEqual(record["year"], "2024")
        self.assertEqual(record["rating"], 4.0)
        self.assertEqual(record["watched"], "2026-08-30")
        self.assertFalse(record["rewatch"])
        # No poster lookup was injected, so none: see TestPoster.
        self.assertIsNone(record["poster"])

    def test_an_unrated_entry_records_a_none_rating(self) -> None:
        # Measured: 14 of 50 diary items were logged without a rating.
        items = feed_read.feed_items(_FIXTURE)
        entries = film_scan.diary_entries(items)
        record = film_scan._record(entries[1])
        assert record is not None
        self.assertEqual(record["title"], "Unrated Fiction")
        self.assertIsNone(record["rating"])
        self.assertTrue(record["rewatch"])

    def test_a_zero_rating_records_a_none_rating(self) -> None:
        # Nothing about the Letterboxd feed rules out a literal 0.0 memberRating the
        # way Goodreads writes a literal "0" user_rating for an unrated book, and a
        # 0.0 must not render as five empty stars; it must omit the rating segment
        # like every other unrated record. Vendor-drift insurance, not a case in the
        # fixture (every rated entry there is > 0), which is why this constructs the
        # element directly rather than reading it out of diary.xml.
        item = ET.fromstring(
            '<item xmlns:letterboxd="https://letterboxd.com">'
            "<letterboxd:memberRating>0.0</letterboxd:memberRating>"
            "</item>"
        )
        self.assertIsNone(film_scan._rating(item))

    def test_a_non_finite_rating_records_a_none_rating(self) -> None:
        # `float()` accepts these spellings, and json.dumps would write them as the
        # bare tokens NaN and Infinity, which are not JSON: the page's JSON.parse
        # would then fail on the whole record file and every film card would
        # disappear. A value that is not a number is not a rating.
        for raw in ("nan", "inf", "-inf"):
            with self.subTest(raw=raw):
                item = ET.fromstring(
                    '<item xmlns:letterboxd="https://letterboxd.com">'
                    f"<letterboxd:memberRating>{raw}</letterboxd:memberRating>"
                    "</item>"
                )
                self.assertIsNone(film_scan._rating(item))

    def test_a_feed_with_no_diary_entries_is_an_empty_list(self) -> None:
        empty = b'<rss version="2.0"><channel><title>x</title></channel></rss>'
        self.assertEqual(film_scan.newest_films(feed_read.feed_items(empty), 1), [])

    def test_an_entry_missing_its_watched_date_is_skipped(self) -> None:
        # Vendor drift insurance: a reshaped feed must degrade to "no card", never a
        # traceback: one adapter failing leaves the others' data in place.
        broken = (
            b'<rss version="2.0" xmlns:letterboxd="https://letterboxd.com"><channel>'
            b"<item><letterboxd:filmTitle>X</letterboxd:filmTitle>"
            b"<link>https://example.invalid/x</link></item>"
            b"</channel></rss>"
        )
        self.assertEqual(film_scan.newest_films(feed_read.feed_items(broken), 1), [])

    def test_entries_are_ranked_by_watched_date_not_feed_order(self) -> None:
        # Letterboxd happens to serve newest-first, but that is the vendor's choice,
        # not a contract. Rank explicitly, so a reordered feed cannot silently
        # promote an old film to the card.
        out_of_order = (
            b'<rss version="2.0" xmlns:letterboxd="https://letterboxd.com"><channel>'
            b"<item><letterboxd:filmTitle>Older</letterboxd:filmTitle>"
            b"<letterboxd:watchedDate>2020-01-01</letterboxd:watchedDate>"
            b"<link>https://example.invalid/old</link>"
            b"<description>x</description></item>"
            b"<item><letterboxd:filmTitle>Newer</letterboxd:filmTitle>"
            b"<letterboxd:watchedDate>2026-01-01</letterboxd:watchedDate>"
            b"<link>https://example.invalid/new</link>"
            b"<description>x</description></item>"
            b"</channel></rss>"
        )
        records = film_scan.newest_films(feed_read.feed_items(out_of_order), 1)
        self.assertEqual(records[0]["title"], "Newer")

    def test_a_count_of_two_returns_two_records_newest_watched_first(self) -> None:
        records = film_scan.newest_films(self.items, 2)
        self.assertEqual(len(records), 2)
        watched = [r["watched"] for r in records]
        self.assertEqual(watched, sorted(watched, reverse=True))

    def test_asking_for_more_than_the_diary_holds_returns_what_exists(self) -> None:
        # Not an error and not padded: a short diary is the site's 1-or-2 case.
        entries = film_scan.diary_entries(self.items)
        records = film_scan.newest_films(self.items, 99)
        self.assertEqual(len(records), len(entries))


class TestTmdbId(unittest.TestCase):
    """The TMDB id is what the director lookup is keyed on, so its absence is a
    real state the record must survive, not an error.
    """

    def test_the_id_is_read_from_the_tmdb_namespace(self) -> None:
        entries = film_scan.diary_entries(feed_read.feed_items(_FIXTURE))
        self.assertEqual(film_scan._tmdb_id(entries[0]), "1")

    def test_an_item_without_a_tmdb_id_has_none(self) -> None:
        # Letterboxd omits tmdb:movieId for a film it has no TMDB match for. The
        # fixture's three entries all carry one, so the absent case is built here
        # rather than by weakening the fixture every other test depends on.
        item = ET.fromstring(
            '<item xmlns:letterboxd="https://letterboxd.com">'
            "<letterboxd:filmTitle>No Id</letterboxd:filmTitle>"
            "</item>"
        )
        self.assertIsNone(film_scan._tmdb_id(item))

    def test_a_blank_tmdb_id_is_no_id(self) -> None:
        item = ET.fromstring(
            '<item xmlns:tmdb="https://themoviedb.org">'
            "<tmdb:movieId>   </tmdb:movieId>"
            "</item>"
        )
        self.assertIsNone(film_scan._tmdb_id(item))


class _RecordingLookup:
    """A director or poster lookup that answers a fixed name and remembers what it was asked.

    The calls list is the point: the lookup crosses the network, so which ids it is
    asked for, and how many times, is part of the contract, not an incidental.
    """

    def __init__(self, answer: str | None = "Ada Director") -> None:
        self.answer = answer
        self.calls: list[str] = []

    def __call__(self, tmdb_id: str) -> str | None:
        self.calls.append(tmdb_id)
        return self.answer


class TestDirector(unittest.TestCase):
    """The director rides on the record, resolved through an injected seam."""

    def setUp(self) -> None:
        self.items = feed_read.feed_items(_FIXTURE)
        self.one_film_items = feed_read.feed_items(_ONE_FILM_FEED)

    def test_the_newest_film_carries_its_director(self) -> None:
        lookup = _RecordingLookup()
        records = film_scan.newest_films(self.items, 1, lookup=lookup)
        self.assertEqual(records[0]["director"], "Ada Director")

    def test_only_the_sliced_films_are_looked_up(self) -> None:
        """The ~47 entries that did not make the cut must cost nothing.

        This is why ranking happens before the lookup rather than inside _record:
        each lookup is about three Wikidata calls.

        `lookup` is called with the film's TMDB id (a plain string), not the diary
        item itself; see `DirectorLookup`/`_by_tmdb_id`. So the spy below maps each
        id back to its film's title via the same fixture, rather than calling
        `.findtext` on its argument (which would raise: a string has no such
        method). That mapping is what lets the assertion below prove the lookups
        line up with the returned records, not merely that there were two of them.
        """
        entries = film_scan.diary_entries(self.items)
        title_by_tmdb_id = {
            film_scan._tmdb_id(entry): (
                entry.findtext(film_scan.LETTERBOXD_NS + "filmTitle") or ""
            ).strip()
            for entry in entries
        }
        seen: list[str] = []

        def spy(tmdb_id: str) -> str:
            seen.append(title_by_tmdb_id[tmdb_id])
            return "A Director"

        records = film_scan.newest_films(self.items, 2, lookup=spy)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen, [r["title"] for r in records])

    def test_a_short_diary_costs_one_lookup_not_three(self) -> None:
        # A `def`, not a `lambda i: seen.append(1) or "D"` one-liner:
        # mypy's func-returns-value check flags `list.append(...) or ...` because
        # `append` only ever returns None, so that idiom cannot pass a clean
        # `mypy` gate. Same behavior, typecheck-clean.
        seen: list[int] = []

        def lookup(_tmdb_id: str) -> str:
            seen.append(1)
            return "D"

        film_scan.newest_films(self.one_film_items, 3, lookup=lookup)
        self.assertEqual(len(seen), 1)

    def test_no_lookup_happens_by_default(self) -> None:
        # The default must make no network call at all: tests/test_live_feed.py calls
        # newest_films() bare and is written to make exactly one live call, to
        # Letterboxd. A network-resolving default would silently add Wikidata to it.
        records = film_scan.newest_films(self.items, 1)
        self.assertIsNone(records[0]["director"])

    def test_a_lookup_that_finds_nothing_records_no_director(self) -> None:
        lookup = _RecordingLookup(answer=None)
        records = film_scan.newest_films(self.items, 1, lookup=lookup)
        self.assertIsNone(records[0]["director"])
        self.assertEqual(lookup.calls, ["1"])

    def test_a_film_with_no_tmdb_id_is_never_looked_up(self) -> None:
        # No id means nothing to ask with, so the seam must not be called at all:
        # a lookup on an empty string would be a wasted round trip whose answer
        # could only ever be None.
        feed = (
            '<rss xmlns:letterboxd="https://letterboxd.com"><channel><item>'
            "<letterboxd:filmTitle>No Id</letterboxd:filmTitle>"
            "<letterboxd:watchedDate>2026-08-30</letterboxd:watchedDate>"
            "</item></channel></rss>"
        ).encode("utf-8")
        lookup = _RecordingLookup()
        records = film_scan.newest_films(feed_read.feed_items(feed), 1, lookup=lookup)
        self.assertIsNone(records[0]["director"])
        self.assertEqual(lookup.calls, [])


class TestPoster(unittest.TestCase):
    """The poster rides on the record, resolved from TMDB through an injected seam.

    The description's own <img> is never read: its host refuses scripted requests, so
    a record carrying it would fail the deploy that fetches covers.
    """

    def setUp(self) -> None:
        self.items = feed_read.feed_items(_FIXTURE)

    def test_the_newest_film_carries_the_looked_up_poster(self) -> None:
        lookup = _RecordingLookup(answer="https://image.tmdb.org/t/p/w500/a.jpg")
        records = film_scan.newest_films(self.items, 1, poster_lookup=lookup)
        self.assertEqual(records[0]["poster"], "https://image.tmdb.org/t/p/w500/a.jpg")
        self.assertEqual(lookup.calls, ["1"])

    def test_only_the_sliced_films_are_looked_up_newest_first(self) -> None:
        lookup = _RecordingLookup(answer=None)
        film_scan.newest_films(self.items, 2, poster_lookup=lookup)
        self.assertEqual(lookup.calls, ["1", "3"])

    def test_no_poster_lookup_happens_by_default(self) -> None:
        # The same inert default as the director's, for tests/test_live_feed.py's
        # one-live-call reason; the description's <img> is not a fallback.
        records = film_scan.newest_films(self.items, 3)
        self.assertEqual([record["poster"] for record in records], [None] * 3)

    def test_a_record_never_takes_the_poster_from_the_description(self) -> None:
        entries = film_scan.diary_entries(self.items)
        record = film_scan._record(entries[0])
        assert record is not None
        self.assertEqual(record["title"], "The Invented Voyage")
        self.assertIsNone(record["poster"])

    def test_a_film_with_no_tmdb_id_is_never_looked_up(self) -> None:
        feed = (
            '<rss xmlns:letterboxd="https://letterboxd.com"><channel><item>'
            "<letterboxd:filmTitle>No Id</letterboxd:filmTitle>"
            "<letterboxd:watchedDate>2026-08-30</letterboxd:watchedDate>"
            '<description><![CDATA[<img src="https://example.invalid/p.jpg"/>]]>'
            "</description></item></channel></rss>"
        ).encode("utf-8")
        lookup = _RecordingLookup()
        records = film_scan.newest_films(
            feed_read.feed_items(feed), 1, poster_lookup=lookup
        )
        self.assertIsNone(records[0]["poster"])
        self.assertEqual(lookup.calls, [])

    def test_the_two_lookups_are_independent(self) -> None:
        director = _RecordingLookup(answer="Ada Director")
        poster = _RecordingLookup(answer=None)
        records = film_scan.newest_films(
            self.items, 1, lookup=director, poster_lookup=poster
        )
        self.assertEqual(records[0]["director"], "Ada Director")
        self.assertIsNone(records[0]["poster"])
        self.assertEqual((director.calls, poster.calls), (["1"], ["1"]))


if __name__ == "__main__":
    unittest.main()
