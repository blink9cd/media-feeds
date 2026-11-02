"""Goodreads' custom shelf widget: the URL, the JavaScript string, and the book parse.

The widget is the third source `book_scan` reads. These tests drive the parser on the
committed fixture, whose every book is invented and reproduces only the widget's
measured structure, on variants of it built in memory, and on arbitrary bytes and
arbitrary widget strings (the property test). No test here reaches the network or
reads a file outside this tool's fixtures.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "widget_response.txt").read_bytes()

#: The private module `book_scan` re-exports from, reached through `sys.modules` so the
#: stand-in parser below can be patched in where `widget_candidates` looks it up.
_WIDGET_MODULE = "goodreads_widget"

#: The real parser class, taken before any test patches the module's name.
_REAL_PARSER: Any = sys.modules[_WIDGET_MODULE]._WidgetParser

#: The class opening each book block; the stand-in parser stops at its second one.
_BOOK_BLOCK = "gr_custom_each_container_"


def _widget(html: str) -> bytes:
    """A widget body whose `widget_code` string holds `html`, escaped as measured."""
    escaped = html.replace("'", "\\'").replace('"', '\\"').replace("</", "<\\/")
    return ("var widget_code = '" + escaped.replace("\n", "\\n") + "'").encode()


class WidgetParseTest(unittest.TestCase):
    """The fixture's five books, in widget order, with the fields the widget carries."""

    def test_reads_every_book_in_widget_order(self) -> None:
        got = book_scan.widget_candidates(_FIXTURE)
        self.assertEqual(
            [record["title"] for record, _ in got],
            [
                "The Lantern Keeper",
                "Rook's Gambit",
                "Café Nights",
                "Salt & Cedar",
                "Untitled Draft",
            ],
        )

    def test_reads_every_author(self) -> None:
        got = [record["author"] for record, _ in book_scan.widget_candidates(_FIXTURE)]
        self.assertEqual(
            got,
            [
                "Ada Quill",
                "Bram Holloway",
                "Inès Marlow",
                "Owen Fairweather",
                "Mina Torres",
            ],
        )

    def test_maps_rating_words_and_absence(self) -> None:
        got = [record["rating"] for record, _ in book_scan.widget_candidates(_FIXTURE)]
        self.assertEqual(got, [5, 4, 2, None, 1])

    def test_takes_book_id_from_cover_file_name(self) -> None:
        got = [book_id for _, book_id in book_scan.widget_candidates(_FIXTURE)]
        self.assertEqual(got, ["90001", "90002", "90003", "90004", ""])

    def test_leaves_year_read_and_cover_empty(self) -> None:
        for record, _ in book_scan.widget_candidates(_FIXTURE):
            self.assertEqual(
                (record["year"], record["read"], record["cover"]), (None, None, None)
            )

    def test_body_without_widget_code_reads_no_book(self) -> None:
        self.assertEqual(
            book_scan.widget_candidates(b"<html>Page not found</html>"), []
        )

    def test_book_missing_title_or_author_is_dropped(self) -> None:
        body = _FIXTURE.replace(b"Ada Quill", b"")
        self.assertEqual(len(book_scan.widget_candidates(body)), 4)

    def test_url_quotes_the_user_id(self) -> None:
        self.assertEqual(
            book_scan.widget_url("12?x=1"),
            "https://www.goodreads.com/review/custom_widget/12%3Fx%3D1",
        )


class WidgetHtmlTest(unittest.TestCase):
    """The JavaScript string's unescaping, before any HTML is read."""

    def test_unescapes_each_measured_escape(self) -> None:
        body = b"var widget_code = 'a\\'b\\\"c<\\/i>\\nd'"
        self.assertEqual(book_scan.widget_html(body), "a'b\"c</i>\nd")

    def test_absent_string_is_none(self) -> None:
        self.assertIsNone(book_scan.widget_html(b"var other = 'x'"))


class WidgetMarkupTest(unittest.TestCase):
    """Markup outside the measured shape: nothing raises, and no stray field is read."""

    def test_stray_closing_div_before_any_div_reads_no_book(self) -> None:
        self.assertEqual(book_scan.widget_candidates(_widget("</div></div>")), [])

    def test_fields_outside_a_book_block_are_ignored(self) -> None:
        html = (
            '<div class="gr_custom_title_"><a href="/">Loose Title</a></div>'
            '<div class="gr_custom_author_"><a href="/">Loose Author</a></div>'
        )
        self.assertEqual(book_scan.widget_candidates(_widget(html)), [])

    def test_rating_span_without_title_reads_no_rating(self) -> None:
        html = (
            '<div class="gr_custom_each_container_">'
            '<div class="gr_custom_rating_"><span>it was amazing</span></div>'
            '<div class="gr_custom_title_"><a href="/">A Title</a></div>'
            '<div class="gr_custom_author_"><a href="/">An Author</a></div>'
            "</div>"
        )
        (record, book_id), *rest = book_scan.widget_candidates(_widget(html))
        self.assertEqual((record["rating"], book_id, rest), (None, "", []))

    def test_first_rating_span_with_a_title_wins(self) -> None:
        html = (
            '<div class="gr_custom_each_container_">'
            '<div class="gr_custom_rating_">'
            '<span title="liked it"></span><span title="it was ok"></span></div>'
            '<div class="gr_custom_title_"><a href="/">A Title</a></div>'
            '<div class="gr_custom_author_"><a href="/">An Author</a></div>'
            "</div>"
        )
        got = [
            record["rating"] for record, _ in book_scan.widget_candidates(_widget(html))
        ]
        self.assertEqual(got, [3])


def _parser_raising_at_second_book() -> Any:
    """A real widget parser whose `feed` reads up to the second book, then asserts.

    Stands in for an older stdlib `_markupbase`, which raises `AssertionError` on a
    malformed marked section part-way through a feed.
    """
    parser = _REAL_PARSER()
    real_feed = parser.feed

    def feed(data: str) -> None:
        """Feed the first book block only, then fail as the old stdlib did."""
        first = data.index(_BOOK_BLOCK)
        real_feed(data[: data.index(_BOOK_BLOCK, first + 1)])
        raise AssertionError("unexpected call to parse_marked_section()")

    parser.feed = feed
    return parser


class WidgetParserFailureTest(unittest.TestCase):
    """A stdlib parser that raises mid-feed costs the rest of the body, not the run."""

    def test_book_parsed_before_the_failure_survives(self) -> None:
        with mock.patch(
            f"{_WIDGET_MODULE}._WidgetParser", _parser_raising_at_second_book
        ):
            got = book_scan.widget_candidates(_FIXTURE)
        self.assertEqual(
            [(record["title"], book_id) for record, book_id in got],
            [("The Lantern Keeper", "90001")],
        )

    def test_malformed_marked_section_does_not_raise(self) -> None:
        # Raises inside the stdlib on interpreters without the backported fix; on
        # this one it may not, and either way the call must return.
        self.assertEqual(book_scan.widget_candidates(_widget("<![x[")), [])


class WidgetPropertyTest(unittest.TestCase):
    """The parser over arbitrary bytes and arbitrary widget strings: never raises."""

    @settings(deadline=None)
    @given(
        st.one_of(
            st.binary(max_size=400),
            st.text(max_size=300).map(
                lambda s: ("var widget_code = '" + s + "'").encode("utf-8")
            ),
            # Markup-heavy strings, so declarations and marked sections are reached.
            st.text(alphabet="<>![]/-? adiv=\"'", max_size=300).map(_widget),
        )
    )
    def test_fuzz_widget_candidates(self, body: bytes) -> None:
        for record, book_id in book_scan.widget_candidates(body):
            self.assertTrue(record["title"] and record["author"])
            self.assertTrue(book_id == "" or book_id.isascii() and book_id.isdigit())


if __name__ == "__main__":
    unittest.main()
