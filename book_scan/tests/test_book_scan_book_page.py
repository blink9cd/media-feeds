"""One book-page fetch gives two fields: the cover, and the publication year.

A shelf widget's book carries no year and often no usable cover, and the card shows
both, so the public page `/book/show/<book_id>` is asked once for each. These tests
drive the page parser on invented pages, a stand-in for a stdlib parser that raises
mid-feed, and `fill_from_pages` with the kit's page fake. No test here reaches the
network.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
from _book_scan_sources_kit import (  # noqa: E402 - follows the sys.path bootstrap above
    PAGE,
    PAGE_COVER,
    Pages,
)

#: The private module `book_scan` re-exports from, reached through `sys.modules` so the
#: stand-in parser below can be patched in where `book_page_from_page` looks it up.
_PAGE_MODULE = "goodreads_cover"

#: The real parser class, taken before any test patches the module's name.
_REAL_PARSER: Any = sys.modules[_PAGE_MODULE]._BookPageParser


def _info(line: str) -> str:
    """`line` in the publication-info element, the shape a live page gives it."""
    return f'<p data-testid="publicationInfo">{line}</p>'


def _year_page(*lines: str) -> bytes:
    """A page with the fixture's cover whose body holds each of `lines` in `_info`."""
    return _body_page("".join(_info(line) for line in lines))


def _body_page(body: str) -> bytes:
    """A page with the fixture's cover whose body is `body`, verbatim."""
    return (
        f'<html><head><meta property="og:image" content="{PAGE_COVER}"/></head>'
        f"<body>{body}</body></html>"
    ).encode()


def _parser_raising_after_cover() -> Any:
    """A real page parser whose `feed` reads up to the body, then asserts.

    Stands in for an older stdlib `_markupbase`, which raises `AssertionError` on a
    malformed marked section part-way through a feed.
    """
    parser = _REAL_PARSER()
    real_feed = parser.feed

    def feed(data: str) -> None:
        """Feed the head only, then fail as the old stdlib did."""
        real_feed(data[: data.index("<body>")])
        raise AssertionError("unexpected call to parse_marked_section()")

    parser.feed = feed
    return parser


class BookPageParseTest(unittest.TestCase):
    """`book_page_from_page`: the cover and the publication year from one page."""

    def test_the_fixture_page_yields_its_cover_and_year(self) -> None:
        self.assertEqual(
            book_scan.book_page_from_page(PAGE),
            book_scan.BookPage(PAGE_COVER, "1999"),
        )

    def test_a_published_line_alone_gives_its_year(self) -> None:
        page = _year_page("Published June 2004 by An Invented Press")
        self.assertEqual(book_scan.book_page_from_page(page).year, "2004")

    def test_first_published_wins_over_published(self) -> None:
        page = _year_page(
            "Published June 2004 by An Invented Press",
            "First published September 16, 1987",
        )
        self.assertEqual(book_scan.book_page_from_page(page).year, "1987")

    def test_a_page_with_neither_line_has_no_year(self) -> None:
        self.assertEqual(
            book_scan.book_page_from_page(_year_page("A Fictional Book")),
            book_scan.BookPage(PAGE_COVER, None),
        )

    def test_a_line_is_matched_only_at_the_start_of_its_text(self) -> None:
        for line in (
            "Reprinted. First published 1999",
            "Published 19999",
            "Editions: Published 2004",
        ):
            with self.subTest(line=line):
                self.assertIsNone(book_scan.book_page_from_page(_year_page(line)).year)

    def test_surrounding_whitespace_does_not_hide_the_line(self) -> None:
        page = _year_page("\n   First published 1999\n")
        self.assertEqual(book_scan.book_page_from_page(page).year, "1999")

    def test_a_parser_failure_keeps_what_was_read_before_it(self) -> None:
        page = _year_page("First published 1999")
        with patch(f"{_PAGE_MODULE}._BookPageParser", _parser_raising_after_cover):
            got = book_scan.book_page_from_page(page)
        self.assertEqual(got, book_scan.BookPage(PAGE_COVER, None))

    def test_malformed_marked_section_does_not_raise(self) -> None:
        # Raises inside the stdlib on interpreters without the backported fix; on
        # this one it may not, and either way the call must return.
        self.assertEqual(
            book_scan.book_page_from_page(b"<![x["), book_scan.BookPage(None, None)
        )

    @given(st.binary(max_size=512))
    def test_fuzz_book_page_from_page(self, body: bytes) -> None:
        year = book_scan.book_page_from_page(body).year
        if year is not None:
            self.assertRegex(year, r"\A[0-9]{4}\Z")


class BookPageYearScopeTest(unittest.TestCase):
    """The year is read inside the publication-info element only, first match wins."""

    def _year(self, body: str) -> str | None:
        """The year `book_page_from_page` reads from a page whose body is `body`."""
        return book_scan.book_page_from_page(_body_page(body)).year

    def test_a_description_above_the_element_does_not_win(self) -> None:
        body = "<p>First published in 1959 as a short story</p>" + _info(
            "First published March 1, 1966"
        )
        self.assertEqual(self._year(body), "1966")

    def test_a_review_paragraph_alone_gives_no_year(self) -> None:
        self.assertIsNone(self._year("<p>Published in 2004, this sequel</p>"))

    def test_a_script_body_gives_no_year(self) -> None:
        self.assertIsNone(self._year("<script>Published 2004</script>"))

    def test_text_after_the_element_closes_is_not_read(self) -> None:
        body = _info("Published 2004") + "<p>First published 1990</p>"
        self.assertEqual(self._year(body), "2004")

    def test_a_word_that_is_not_a_month_gives_no_year(self) -> None:
        for line in (
            "First published in 1959",
            "Published as 2004",
            "Published Jun 2004",
        ):
            with self.subTest(line=line):
                self.assertIsNone(self._year(_info(line)))

    def test_the_first_line_of_each_kind_wins(self) -> None:
        body = "".join(
            _info(line)
            for line in (
                "Published May 2004",
                "Published 2010",
                "First published 1987",
                "First published 1990",
            )
        )
        self.assertEqual(self._year(body), "1987")
        self.assertEqual(
            self._year(_info("Published 2004") + _info("Published 2010")), "2004"
        )


class FillFromPagesTest(unittest.TestCase):
    """`fill_from_pages`: a coverless record gains the page's cover, and a year if bare."""

    @staticmethod
    def _record(year: str | None, cover: str | None) -> book_scan.BookRecord:
        """A record with `year` and `cover`, its other fields invented."""
        return book_scan.BookRecord(
            title="A Fictional Book",
            author="An Invented Author",
            year=year,
            rating=None,
            read=None,
            cover=cover,
        )

    def test_a_record_with_a_year_keeps_it_while_gaining_a_cover(self) -> None:
        pages = Pages()
        got = book_scan.fill_from_pages(
            [(self._record("2011", None), "7000004")],
            book_scan.goodreads_page_lookup(pages),
        )
        self.assertEqual((got[0]["year"], got[0]["cover"]), ("2011", PAGE_COVER))

    def test_a_record_with_no_year_takes_the_page_year(self) -> None:
        pages = Pages()
        got = book_scan.fill_from_pages(
            [(self._record(None, None), "7000004")],
            book_scan.goodreads_page_lookup(pages),
        )
        self.assertEqual((got[0]["year"], got[0]["cover"]), ("1999", PAGE_COVER))

    def test_a_record_with_a_cover_asks_no_page(self) -> None:
        pages = Pages()
        got = book_scan.fill_from_pages(
            [(self._record(None, PAGE_COVER), "7000004")],
            book_scan.goodreads_page_lookup(pages),
        )
        self.assertEqual((pages.ids, got[0]["year"]), ([], None))

    def test_the_default_lookup_asks_nothing(self) -> None:
        self.assertEqual(book_scan.no_page("7000004"), book_scan.BookPage(None, None))


if __name__ == "__main__":
    unittest.main()
