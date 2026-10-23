"""Parsing the Goodreads shelf feed: the shapes measured on a real feed on 2026-08-31.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "shelf.xml").read_bytes()


class TestNewestBook(unittest.TestCase):
    """The record src/data/books.json holds."""

    def setUp(self) -> None:
        # Only the count-based tests below use `self.items`; the rest parse the
        # fixture, or an inline item, themselves.
        self.items = feed_read.feed_items(_FIXTURE)

    def test_the_newest_READ_book_wins_not_the_newest_ADDED(self) -> None:
        # THE Goodreads finding, measured 2026-08-31 on a real feed: the shelf RSS is
        # ordered by user_date_added, NOT user_read_at. There the top 3 coincided but
        # the 4th and 5th BY READ DATE sat at feed positions 5 and 6, so taking
        # items[0] is WRONG even where it happens to look right. This fixture's feed
        # order and read order disagree AT THE TOP: feed position 0 ("An Early
        # Addition") was read in March, six months before the true winner ("The Later
        # Read") at feed position 2 -- so a feed-order implementation fails here, not
        # on some deeper item a shallow test could miss.
        items = feed_read.feed_items(_FIXTURE)
        self.assertEqual(items[0].findtext("title"), "An Early Addition")
        records = book_scan.newest_books(items, 1)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["title"], "The Later Read")
        self.assertEqual(record["author"], "Dominic Ashworth")
        self.assertEqual(record["read"], "2026-08-01")
        # Positive assertion, not merely "not None": with every rating assertion
        # in this suite an assertIsNone (unrated / missing-tag), a mutation making
        # _rating always return None would pass the whole suite untouched. "The Later Read" is rated 5.
        self.assertEqual(record["rating"], 5)
        # Pinned on the FIXTURE, not only on the synthetic items below: this item
        # carries `book_published` alongside every sibling element a real shelf item
        # has, so it proves the year survives the full-item path, not just a
        # three-element stub.
        self.assertEqual(record["year"], "2023")

    def test_a_single_digit_day_read_date_parses(self) -> None:
        # 'Sat, 1 Aug 2026 ...' -- 4 of the 49 items on the real feed measured were
        # this shape. A strptime %d accepts an unpadded day too, so this pins the
        # feed's real shape, not a parser choice (feed_read.rfc822_date's docstring
        # gives the reason for parsedate_to_datetime). The raw string is pinned as
        # well, so a fixture edit "normalizing" it to a two-digit day fails here.
        items = feed_read.feed_items(_FIXTURE)
        winner = next(i for i in items if i.findtext("title") == "The Later Read")
        self.assertEqual(
            winner.findtext("user_read_at"), "Sat, 1 Aug 2026 00:00:00 +0000"
        )
        record = book_scan._record(winner)
        assert record is not None
        self.assertEqual(record["read"], "2026-08-01")

    def test_a_blank_read_date_falls_back_to_the_added_date(self) -> None:
        # None was blank on the real feed; a book marked read with no date would
        # produce one.
        # "Blank Read Date Ballad" carries an empty user_read_at, so the record must
        # fall back to its user_date_added (Mon, 1 Jun 2026) rather than being
        # dropped as undateable.
        items = feed_read.feed_items(_FIXTURE)
        item = next(i for i in items if i.findtext("title") == "Blank Read Date Ballad")
        self.assertEqual(item.findtext("user_read_at"), "")
        record = book_scan._record(item)
        assert record is not None
        self.assertEqual(record["read"], "2026-06-01")

    def test_the_LARGE_image_variant_is_chosen(self) -> None:
        # book_image_url and book_small_image_url are BOTH _SX50_ -- 50 pixels wide,
        # unusable on a card; only book_large_image_url (_SX318_) is big enough.
        # "The Later Read" carries all four size variants (see the fixture), so this
        # proves the LARGE one specifically was chosen over the other three, not
        # merely that some cover was found.
        records = book_scan.newest_books(feed_read.feed_items(_FIXTURE), 1)
        record = records[0]
        self.assertIn("_SX318_", record["cover"] or "")
        self.assertNotIn("_SX50_", record["cover"] or "")

    def test_a_book_with_no_large_image_records_none(self) -> None:
        # "No Cover Cottage" carries book_image_url/small/medium but no
        # book_large_image_url element at all -- the cover must degrade to None
        # rather than silently falling back to one of the unusable smaller variants.
        items = feed_read.feed_items(_FIXTURE)
        item = next(i for i in items if i.findtext("title") == "No Cover Cottage")
        self.assertIsNone(item.findtext("book_large_image_url"))
        record = book_scan._record(item)
        assert record is not None
        self.assertIsNone(record["cover"])

    def test_a_large_image_that_is_not_https_records_no_cover(self) -> None:
        # The URL is a third party's string, written into a record the page loads
        # an image from. Only https with a host is a cover; anything else is the
        # no-cover case the card already degrades to text for.
        item = ET.fromstring(
            "<item><title>T</title><author_name>A</author_name>"
            "<user_read_at>Mon, 25 Aug 2026 10:00:00 -0700</user_read_at>"
            "<book_large_image_url>http://images.example.invalid/books/a._SX318_.jpg"
            "</book_large_image_url></item>"
        )
        record = book_scan._record(item)
        assert record is not None
        self.assertIsNone(record["cover"])

    def test_an_unrated_book_records_a_none_rating(self) -> None:
        # user_rating is "0" for an unrated book, which must become None, not 0
        # stars. "Unrated Wanderings" carries the literal "0" Goodreads writes.
        items = feed_read.feed_items(_FIXTURE)
        item = next(i for i in items if i.findtext("title") == "Unrated Wanderings")
        self.assertEqual(item.findtext("user_rating"), "0")
        record = book_scan._record(item)
        assert record is not None
        self.assertIsNone(record["rating"])

    def test_the_publication_year_is_read_from_the_feed(self) -> None:
        # `book_published` is a BARE year in this feed (e.g. "2018"), not a date --
        # so it is carried through as the string it already is, matching
        # FilmRecord.year's existing `str | None` shape rather than inventing a
        # second convention (an int) for the same concept across two sibling tools.
        item = ET.fromstring(
            "<item><title>T</title><author_name>A</author_name>"
            "<user_read_at>Mon, 25 Aug 2026 10:00:00 -0700</user_read_at>"
            "<book_published>2018</book_published></item>"
        )
        record = book_scan._record(item)
        assert record is not None
        self.assertEqual(record["year"], "2018")

    def test_a_missing_publication_year_is_none_not_empty_string(self) -> None:
        # The card omits the line on None; an empty string would render a blank row.
        # This is the whole reason `_year` is a statement-form if/else rather than a
        # bare `.strip()`: "" and None are NOT interchangeable downstream.
        item = ET.fromstring(
            "<item><title>T</title><author_name>A</author_name>"
            "<user_read_at>Mon, 25 Aug 2026 10:00:00 -0700</user_read_at></item>"
        )
        record = book_scan._record(item)
        assert record is not None
        self.assertIsNone(record["year"])

    def test_an_empty_publication_year_element_is_none_not_empty_string(self) -> None:
        # The tag PRESENT but empty is a distinct input from the tag absent:
        # findtext returns "" for the first and None for the second, and both must
        # land on None. Without this case the empty-string arm is only ever reached
        # from the absent-tag side, so a `_year` that returned "" for a present-but-
        # blank element would go unnoticed.
        item = ET.fromstring(
            "<item><title>T</title><author_name>A</author_name>"
            "<user_read_at>Mon, 25 Aug 2026 10:00:00 -0700</user_read_at>"
            "<book_published>  </book_published></item>"
        )
        record = book_scan._record(item)
        assert record is not None
        self.assertIsNone(record["year"])

    def test_a_feed_that_parses_to_no_items_is_an_empty_list(self) -> None:
        empty = b'<rss version="2.0"><channel><title>x</title></channel></rss>'
        self.assertEqual(book_scan.newest_books(feed_read.feed_items(empty), 3), [])

    def test_a_missing_user_rating_element_records_a_none_rating(self) -> None:
        # Vendor-drift insurance, not a live case: all 49 items on the real feed
        # measured carried a user_rating tag (Goodreads writes "0" for unrated, never
        # omits it). _rating's int() call raises ValueError when the tag is entirely
        # absent (findtext returns None, so int("") is attempted) -- that except
        # branch has no case anywhere else in this fixture, since every item above
        # supplies a plain-digit user_rating (including the literal "0" case).
        item = ET.fromstring(
            "<item><title>No Rating Tag</title>"
            "<user_read_at>Mon, 1 Jun 2026 00:00:00 +0000</user_read_at></item>"
        )
        record = book_scan._record(item)
        assert record is not None
        self.assertIsNone(record["rating"])

    def test_an_item_with_no_parseable_date_at_all_is_skipped(self) -> None:
        # Vendor-drift insurance: a reshaped feed must degrade to "no card", never a
        # traceback. Neither user_read_at nor user_date_added is present, so
        # _read_date returns None and the item is dropped rather than crashing or
        # silently ranking as though read on some default date.
        item = ET.fromstring("<item><title>Undated</title></item>")
        self.assertIsNone(book_scan._record(item))

    def test_a_feed_where_no_item_has_a_usable_date_is_an_empty_list(self) -> None:
        broken = (
            b'<rss version="2.0"><channel>'
            b"<item><title>Undated</title></item>"
            b"</channel></rss>"
        )
        self.assertEqual(book_scan.newest_books(feed_read.feed_items(broken), 3), [])

    def test_a_count_of_three_returns_three_records_newest_read_first(self) -> None:
        records = book_scan.newest_books(self.items, 3)
        self.assertEqual(len(records), 3)
        reads = [r["read"] for r in records]
        self.assertEqual(reads, sorted(reads, reverse=True))

    def test_asking_for_more_than_the_shelf_holds_returns_what_exists(self) -> None:
        # Not an error and not padded: a short shelf is the site's 1-or-2 case.
        records = book_scan.newest_books(self.items, 99)
        self.assertEqual(len(records), len(self.items))

    def test_a_feed_with_no_items_is_an_empty_list_not_none(self) -> None:
        self.assertEqual(book_scan.newest_books([], 3), [])


class TestCoverAtWidth(unittest.TestCase):
    """Re-requesting a Goodreads cover at another width.

    MEASURED 2026-09-01 against a real cover: `_SX<n>_` scales by width and is
    honored DOWNWARD only. Above the book's own master image Goodreads silently
    returns the master with a 200 -- `_SX400_`, `_SX636_`, `_SX800_` and no token at
    all all returned the identical 348x475 image -- so a larger number buys nothing
    and quietly lies about the size it names. Each book's master differs, so no
    allowlist can catch that here; `book_scan.md` records the ceiling instead.
    """

    def test_rewrites_the_sizing_token(self) -> None:
        self.assertEqual(
            book_scan.cover_at_width(
                "https://images.example.invalid/books/a._SX318_.jpg", 250
            ),
            "https://images.example.invalid/books/a._SX250_.jpg",
        )

    def test_a_url_carrying_no_sizing_token_is_returned_verbatim(self) -> None:
        # Goodreads serves the master with no token at all; there is nothing to
        # rewrite, and the URL is still honest about its own size.
        url = "https://images.example.invalid/books/a.jpg"
        self.assertEqual(book_scan.cover_at_width(url, 250), url)

    def test_a_non_positive_width_is_refused(self) -> None:
        for width in (0, -1):
            with self.subTest(width=width):
                with self.assertRaises(ValueError):
                    book_scan.cover_at_width(
                        "https://images.example.invalid/books/a._SX318_.jpg", width
                    )

    def test_a_width_of_one_is_the_smallest_accepted(self) -> None:
        # The boundary of the refusal above: one is not refused, and is written
        # into the token exactly. Pins the `< 1` from drifting to `<= 1`.
        self.assertEqual(
            book_scan.cover_at_width(
                "https://images.example.invalid/books/a._SX318_.jpg", 1
            ),
            "https://images.example.invalid/books/a._SX1_.jpg",
        )


if __name__ == "__main__":
    unittest.main()
