"""Parsing the Letterboxd diary feed -- every case measured on 2026-08-31."""

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
# tmdb:movieId (otherwise _director short-circuits before ever calling `lookup`,
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
    """HALF a real Letterboxd feed is not films (measured: 50 of 100 items)."""

    def test_list_items_are_excluded(self) -> None:
        # THE trap. A list item carries NO film elements, so a "first N items"
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


class TestPosterExtraction(unittest.TestCase):
    """The poster lives in the description HTML, not in an element of its own."""

    def test_the_first_img_src_is_returned(self) -> None:
        html = '<p><img src="https://example.invalid/a.jpg"/></p> <p>Watched.</p>'
        self.assertEqual(
            film_scan.poster_from_description(html), "https://example.invalid/a.jpg"
        )

    def test_review_paragraphs_after_the_poster_do_not_confuse_it(self) -> None:
        # Measured: 19 of 150 diary items carry extra <p>/<i> review paragraphs.
        html = (
            '<p><img src="https://example.invalid/a.jpg"/></p> <p>Watched.</p>'
            "<p>A review with <i>emphasis</i>.</p>"
        )
        self.assertEqual(
            film_scan.poster_from_description(html), "https://example.invalid/a.jpg"
        )

    def test_a_later_img_does_not_replace_the_poster(self) -> None:
        # The poster is the FIRST <img>. Nothing measured rules out a second one
        # further down the description, and a last-wins parser would record that
        # image as the film's poster instead.
        html = (
            '<p><img src="https://example.invalid/poster.jpg"/></p>'
            '<p>A review with <img src="https://example.invalid/still.jpg"/>.</p>'
        )
        self.assertEqual(
            film_scan.poster_from_description(html),
            "https://example.invalid/poster.jpg",
        )

    def test_a_description_with_no_img_is_none(self) -> None:
        # Measured: 1 item in 150 had no poster at all, so a missing poster degrading
        # to a text-only card is a REAL case, not a hypothetical.
        self.assertIsNone(film_scan.poster_from_description("<p>Watched.</p>"))

    def test_an_empty_description_is_none(self) -> None:
        self.assertIsNone(film_scan.poster_from_description(""))

    def test_a_first_img_that_is_not_https_is_no_poster(self) -> None:
        # The src is a third party's string, and the probe fetches what is
        # recorded: urlopen speaks file:, ftp: and data: as readily as https, so
        # each of these was fetched, and whatever it said was written into the
        # record. Only https with a host is a poster; the rest is the no-poster
        # case the card already degrades to text for.
        for src in (
            "http://example.invalid/a.jpg",
            "file:///srv/private-0-600-0-900-crop.jpg",
            "ftp://198.51.100.7/a.jpg",
            "data:image/png;base64,AAAA",
            "javascript:alert(1)",
        ):
            with self.subTest(src=src):
                self.assertIsNone(
                    film_scan.poster_from_description(f'<p><img src="{src}"/></p>')
                )


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
        self.assertEqual(
            record["poster"],
            "https://example.invalid/poster/voyage-0-600-0-900-crop.jpg",
        )

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
        # 0.0 must not render as five empty stars -- it must omit the rating segment
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

    def test_an_entry_with_no_poster_records_none(self) -> None:
        entries = film_scan.diary_entries(feed_read.feed_items(_FIXTURE))
        record = film_scan._record(entries[2])
        assert record is not None
        self.assertEqual(record["title"], "Posterless")
        self.assertIsNone(record["poster"])

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
    real state the record must survive -- not an error.
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
    """A director lookup that answers a fixed name and remembers what it was asked.

    The calls list is the point: the lookup crosses the network, so WHICH ids it is
    asked for -- and how many times -- is part of the contract, not an incidental.
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
        item itself -- see `DirectorLookup`/`_director`. So the spy below maps each
        id back to its film's title via the same fixture, rather than calling
        `.findtext` on its argument (which would raise: a string has no such
        method) -- that mapping is what lets the assertion below prove the lookups
        line up with the RETURNED records, not merely that there were two of them.
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
        # newest_films() bare and is written to make exactly ONE live call, to
        # Letterboxd. A network-resolving default would silently add Wikidata to it.
        records = film_scan.newest_films(self.items, 1)
        self.assertIsNone(records[0]["director"])

    def test_a_lookup_that_finds_nothing_records_no_director(self) -> None:
        lookup = _RecordingLookup(answer=None)
        records = film_scan.newest_films(self.items, 1, lookup=lookup)
        self.assertIsNone(records[0]["director"])
        self.assertEqual(lookup.calls, ["1"])

    def test_a_film_with_no_tmdb_id_is_never_looked_up(self) -> None:
        # No id means nothing to ask WITH, so the seam must not be called at all --
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


class TestPosterAtWidth(unittest.TestCase):
    """Re-requesting a poster at a smaller pre-generated size.

    MEASURED 2026-09-01 by fetching every candidate: Letterboxd serves a fixed set
    of PRE-GENERATED sizes and answers 403 Forbidden -- not 404, and never a live
    resize -- for anything else. An unlisted width would therefore fail at
    PAGE-VIEW time, which is why it has to be refused here instead.
    """

    def test_rewrites_the_dimension_suffix(self) -> None:
        self.assertEqual(
            film_scan.poster_at_width(
                "https://example.invalid/poster/voyage-0-600-0-900-crop.jpg", 400
            ),
            "https://example.invalid/poster/voyage-0-400-0-600-crop.jpg",
        )

    def test_the_other_path_shape_resizes_the_same_way(self) -> None:
        # Both /poster/ and /upload/ shapes occur in a real feed, so the rewrite
        # matches the DIMENSION suffix and never the path (poster_from_description's
        # own docstring forbids pattern-matching the path).
        self.assertEqual(
            film_scan.poster_at_width(
                "https://example.invalid/upload/unrated-0-600-0-900-crop.jpg", 300
            ),
            "https://example.invalid/upload/unrated-0-300-0-450-crop.jpg",
        )

    def test_the_height_comes_from_the_measured_table_not_a_ratio(self) -> None:
        # 125x187 is NOT 2:3 (that would be 187.5). Computing the height would emit
        # a URL that 403s, so the pairing has to be a measured table.
        self.assertEqual(
            film_scan.poster_at_width(
                "https://example.invalid/poster/a-0-600-0-900-crop.jpg", 125
            ),
            "https://example.invalid/poster/a-0-125-0-187-crop.jpg",
        )

    def test_the_table_is_the_measured_set(self) -> None:
        # Restated on purpose: the heights cannot be computed (the 125x187 test
        # above), so the 2026-09-01 measurement is the only oracle. A wrong entry
        # fails no refresh -- the poster probe falls back to the feed's own URL --
        # so it would surface only as a lost saving, or as a 403 at page-view time
        # on a --feed-file run. A change to Letterboxd's set means re-measuring and
        # editing both copies.
        self.assertEqual(
            film_scan.POSTER_SIZES,
            {
                70: 105,
                125: 187,
                150: 225,
                200: 300,
                230: 345,
                250: 375,
                300: 450,
                400: 600,
                460: 690,
                500: 750,
                600: 900,
                1000: 1500,
            },
        )

    def test_a_width_letterboxd_does_not_pre_generate_is_refused(self) -> None:
        # MEASURED: 345 and 568 both return 403 Forbidden from the real CDN.
        for width in (345, 568):
            with self.subTest(width=width):
                with self.assertRaises(ValueError):
                    film_scan.poster_at_width(
                        "https://example.invalid/poster/a-0-600-0-900-crop.jpg", width
                    )

    def test_a_url_carrying_no_dimension_suffix_is_returned_verbatim(self) -> None:
        # Nothing to rewrite is not an error: the ORIGINAL URL is still honest about
        # its own size, it simply misses the saving.
        url = "https://example.invalid/poster/no-dimensions-here.jpg"
        self.assertEqual(film_scan.poster_at_width(url, 400), url)


if __name__ == "__main__":
    unittest.main()
