"""The cover fallback: a coverless book's cover, from its public Goodreads book page.

When a shelf item carries no usable cover (no large-image variant, a non-https one,
or Goodreads' "nophoto" placeholder), `main` asks the book's public page,
`/book/show/<book_id>`, and records the page's `og:image` instead. These tests drive
the page parser on fixture HTML, the URL rule on hostile ids, the lookup on every
failure a fetch can raise, and `main` with both the shelf fetch and the page fetch
injected. No test here reaches the network.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import email.message
import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURES = _TOOL_ROOT / "fixtures"
_PAGE = (_FIXTURES / "book_page.html").read_bytes()
_PAGE_NO_COVER = (_FIXTURES / "book_page_no_cover.html").read_bytes()
_PAGE_COVER = "https://images.example.invalid/books/1700000000i/7000004.jpg"

_PAGE_BASE = "https://www.goodreads.com/book/show/"

# The shape Goodreads gave a coverless book's image fields: a placeholder on its asset
# host, under a `nophoto` path segment. Invented file name, real path shape.
_NOPHOTO = "https://images.example.invalid/assets/nophoto/book/111x148-0000.png"

# Same reasoning as test_book_scan_cli.py's own _A_USER_ID: an obvious placeholder.
_A_USER_ID = "a-placeholder-user-id"

_NO_HEADERS = email.message.Message()


def _item(book_id: str, title: str, read_at: str, cover: str | None) -> bytes:
    """One shelf item, with a large-image variant only when `cover` is given."""
    large = b""
    if cover is not None:
        large = f"<book_large_image_url>{cover}</book_large_image_url>".encode()
    return (
        (
            f"<item><title>{title}</title><book_id>{book_id}</book_id>"
            f"<author_name>An Invented Author</author_name>"
            f"<user_read_at>{read_at}</user_read_at>"
        ).encode()
        + large
        + b"</item>"
    )


def _shelf(*items: bytes) -> bytes:
    """A shelf feed holding `items`, in the order given."""
    return b'<rss version="2.0"><channel>' + b"".join(items) + b"</channel></rss>"


def _page(meta: str) -> bytes:
    """A minimal page whose head holds `meta` verbatim."""
    return f"<html><head>{meta}</head><body></body></html>".encode()


class _Pages:
    """A page-fetch fake: `answers` maps a book id to a body or an exception."""

    def __init__(self, answers: dict[str, bytes | Exception]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.assertable_timeout = timeout
        self.calls.append(url)
        answer = self.answers[url.removeprefix(_PAGE_BASE)]
        if isinstance(answer, Exception):
            raise answer
        return answer


class OgImageParseTest(unittest.TestCase):
    """`og_image_from_page`: the og:image tag's https URL, or None."""

    def test_the_fixture_page_yields_its_og_image(self) -> None:
        self.assertEqual(book_scan.og_image_from_page(_PAGE), _PAGE_COVER)

    def test_a_page_with_no_og_image_yields_none(self) -> None:
        self.assertIsNone(book_scan.og_image_from_page(_PAGE_NO_COVER))

    def test_attribute_order_and_an_unclosed_tag_do_not_matter(self) -> None:
        page = _page(f'<meta content="{_PAGE_COVER}" property="og:image">')
        self.assertEqual(book_scan.og_image_from_page(page), _PAGE_COVER)

    def test_only_the_exact_property_matches(self) -> None:
        # og:image:width shares the prefix, and `name=` is not the attribute the
        # real page uses: neither is the cover tag.
        page = _page(
            f'<meta property="og:image:width" content="{_PAGE_COVER}"/>'
            f'<meta name="og:image" content="{_PAGE_COVER}"/>'
        )
        self.assertIsNone(book_scan.og_image_from_page(page))

    def test_the_first_og_image_wins(self) -> None:
        other = "https://images.example.invalid/books/other.jpg"
        page = _page(
            f'<meta property="og:image" content="{_PAGE_COVER}"/>'
            f'<meta property="og:image" content="{other}"/>'
        )
        self.assertEqual(book_scan.og_image_from_page(page), _PAGE_COVER)

    def test_a_non_https_cover_is_no_cover(self) -> None:
        for content in (
            "http://images.example.invalid/books/1.jpg",
            "javascript:alert(1)",
            "//images.example.invalid/books/1.jpg",
            "",
        ):
            with self.subTest(content=content):
                page = _page(f'<meta property="og:image" content="{content}"/>')
                self.assertIsNone(book_scan.og_image_from_page(page))

    def test_a_tag_with_no_content_is_no_cover(self) -> None:
        self.assertIsNone(
            book_scan.og_image_from_page(_page('<meta property="og:image">'))
        )

    def test_the_nophoto_placeholder_is_no_cover(self) -> None:
        page = _page(f'<meta property="og:image" content="{_NOPHOTO}"/>')
        self.assertIsNone(book_scan.og_image_from_page(page))

    def test_malformed_html_and_undecodable_bytes_do_not_raise(self) -> None:
        for body in (
            b"<html><head><meta property=",
            b"\xff\xfe<<<>>>\x00</meta",
            b"<!-- never closed <meta property='og:image' content='x'>",
            b"",
        ):
            with self.subTest(body=body):
                self.assertIsNone(book_scan.og_image_from_page(body))

    def test_an_entity_in_the_url_is_unescaped_once(self) -> None:
        page = _page(
            '<meta property="og:image" '
            'content="https://images.example.invalid/b.jpg?a=1&amp;b=2"/>'
        )
        self.assertEqual(
            book_scan.og_image_from_page(page),
            "https://images.example.invalid/b.jpg?a=1&b=2",
        )


class OgImagePropertyTest(unittest.TestCase):
    """The never-raise, https-or-None contract over arbitrary page bodies."""

    @given(st.binary(max_size=512))
    def test_fuzz_og_image_from_page(self, body: bytes) -> None:
        result = book_scan.og_image_from_page(body)
        if result is not None:
            self.assertEqual(feed_read.https_url_or_none(result), result)

    @given(st.text(max_size=200))
    def test_any_content_value_comes_back_https_or_none(self, content: str) -> None:
        escaped = content.replace("&", "&amp;").replace('"', "&quot;")
        result = book_scan.og_image_from_page(
            _page(f'<meta property="og:image" content="{escaped}"/>')
        )
        if result is not None:
            self.assertEqual(feed_read.https_url_or_none(result), result)


class UsableCoverTest(unittest.TestCase):
    """`usable_cover`: the https rule, plus the nophoto placeholder refused."""

    def test_an_https_cover_passes(self) -> None:
        self.assertEqual(book_scan.usable_cover(_PAGE_COVER), _PAGE_COVER)

    def test_the_placeholder_is_refused_but_a_lookalike_name_is_not(self) -> None:
        self.assertIsNone(book_scan.usable_cover(_NOPHOTO))
        lookalike = "https://images.example.invalid/books/nophotography.jpg"
        self.assertEqual(book_scan.usable_cover(lookalike), lookalike)

    def test_a_non_https_cover_is_refused(self) -> None:
        self.assertIsNone(book_scan.usable_cover("http://images.example.invalid/1.jpg"))


class BookPageUrlTest(unittest.TestCase):
    """`book_page_url`: only an all-ASCII-digit id reaches the path."""

    def test_a_digit_id_gives_the_public_page(self) -> None:
        self.assertEqual(book_scan.book_page_url("7000004"), _PAGE_BASE + "7000004")

    def test_anything_else_gives_none(self) -> None:
        for book_id in ("", " 1", "1a", "1/../2", "١٢", "1?x=2", "²"):
            with self.subTest(book_id=book_id):
                self.assertIsNone(book_scan.book_page_url(book_id))


class PageLookupTest(unittest.TestCase):
    """`goodreads_page_lookup`: one attempt, and every failure is "no page"."""

    def test_a_page_gives_its_cover_and_year(self) -> None:
        pages = _Pages({"7000004": _PAGE})
        self.assertEqual(
            book_scan.goodreads_page_lookup(pages)("7000004"),
            book_scan.BookPage(_PAGE_COVER, "1999"),
        )
        self.assertEqual(pages.calls, [_PAGE_BASE + "7000004"])
        self.assertEqual(pages.assertable_timeout, feed_read.FEED_TIMEOUT_SECONDS)

    def test_every_fetch_failure_is_no_page_after_one_attempt(self) -> None:
        for error in (
            OSError("connection refused"),
            ValueError("refused by the URL rule"),
            TimeoutError("timed out"),
        ):
            with self.subTest(error=error):
                pages = _Pages({"1": error})
                self.assertEqual(
                    book_scan.goodreads_page_lookup(pages)("1"),
                    book_scan.BookPage(None, None),
                )
                self.assertEqual(len(pages.calls), 1)

    def test_an_http_error_is_closed(self) -> None:
        for status in (403, 404, 503):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    _PAGE_BASE + "1", status, "refused", _NO_HEADERS, io.BytesIO(b"")
                )
                self.assertEqual(
                    book_scan.goodreads_page_lookup(_Pages({"1": error}))("1"),
                    book_scan.BookPage(None, None),
                )
                self.assertTrue(error.fp is None or error.fp.closed)

    def test_an_id_that_is_not_digits_makes_no_request(self) -> None:
        pages = _Pages({})
        self.assertEqual(
            book_scan.goodreads_page_lookup(pages)("1/../2"),
            book_scan.BookPage(None, None),
        )
        self.assertEqual(pages.calls, [])


class MainCoverFallbackTest(unittest.TestCase):
    """`main` asks the book page only for a recorded book with no usable cover."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output = Path(self._tmp.name) / "books.json"
        # This suite predates `--source`: a shell exporting the source settings must
        # not reroute its RSS runs to the library export.
        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop(book_scan.SOURCE_ENV, None)
        os.environ.pop(book_scan.CSV_DIR_ENV, None)

    def _run(
        self, shelf: bytes, pages: _Pages, *extra: str
    ) -> tuple[int, list[dict[str, object]]]:
        """Run `main` on `shelf`, with `pages` as the page fetch; the code and records."""

        def fetch(url: str, timeout: float) -> bytes:
            del url, timeout
            return shelf

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = book_scan.main(
                ["--user-id", _A_USER_ID, "--output", str(self.output), *extra],
                fetch=fetch,
                cover_fetch=pages,
            )
        records: list[dict[str, object]] = []
        if self.output.exists():
            records = json.loads(self.output.read_text(encoding="utf-8"))
        return code, records

    def test_a_book_with_a_cover_makes_no_lookup(self) -> None:
        shelf = _shelf(
            _item(
                "1",
                "Covered",
                "Sun, 30 Aug 2026 00:00:00 +0000",
                "https://images.example.invalid/c._SX318_.jpg",
            )
        )
        pages = _Pages({})
        code, records = self._run(shelf, pages)
        self.assertEqual(code, 0)
        self.assertEqual(pages.calls, [])
        self.assertEqual(
            records[0]["cover"], "https://images.example.invalid/c._SX318_.jpg"
        )

    def test_a_coverless_book_takes_its_page_cover(self) -> None:
        shelf = _shelf(
            _item("7000004", "Bare", "Sun, 30 Aug 2026 00:00:00 +0000", None)
        )
        pages = _Pages({"7000004": _PAGE})
        code, records = self._run(shelf, pages)
        self.assertEqual(code, 0)
        self.assertEqual(pages.calls, [_PAGE_BASE + "7000004"])
        self.assertEqual(records[0]["cover"], _PAGE_COVER)

    def test_the_nophoto_placeholder_counts_as_no_cover(self) -> None:
        # The regression: before the fallback, the placeholder passed the https rule
        # and was recorded as the book's cover.
        shelf = _shelf(
            _item("7000004", "Placeholder", "Sun, 30 Aug 2026 00:00:00 +0000", _NOPHOTO)
        )
        code, records = self._run(shelf, _Pages({"7000004": _PAGE_NO_COVER}))
        self.assertEqual(code, 0)
        self.assertIsNone(records[0]["cover"])

    def test_a_page_cover_goes_through_cover_at_width(self) -> None:
        sized = "https://images.example.invalid/books/7000004._SX98_.jpg"
        shelf = _shelf(
            _item("7000004", "Bare", "Sun, 30 Aug 2026 00:00:00 +0000", None)
        )
        pages = _Pages(
            {"7000004": _page(f'<meta property="og:image" content="{sized}"/>')}
        )
        _, records = self._run(shelf, pages, "--cover-width", "200")
        self.assertEqual(
            records[0]["cover"],
            "https://images.example.invalid/books/7000004._SX200_.jpg",
        )

    def test_a_failed_lookup_leaves_the_card_text_and_the_run_green(self) -> None:
        shelf = _shelf(_item("1", "Bare", "Sun, 30 Aug 2026 00:00:00 +0000", None))
        error = urllib.error.HTTPError(
            _PAGE_BASE + "1", 404, "gone", _NO_HEADERS, io.BytesIO(b"")
        )
        code, records = self._run(shelf, _Pages({"1": error}))
        self.assertEqual(code, 0)
        self.assertIsNone(records[0]["cover"])

    def test_lookups_are_bounded_by_count(self) -> None:
        days = ("30", "29", "28", "27", "26")
        shelf = _shelf(
            *(
                _item(str(n), f"Book {n}", f"Sun, {day} Aug 2026 00:00:00 +0000", None)
                for n, day in enumerate(days, start=1)
            )
        )
        pages = _Pages({str(n): _PAGE_NO_COVER for n in range(1, 6)})
        code, records = self._run(shelf, pages, "--count", "2")
        self.assertEqual(code, 0)
        self.assertEqual(len(records), 2)
        self.assertEqual(pages.calls, [_PAGE_BASE + "1", _PAGE_BASE + "2"])

    def test_a_book_with_no_book_id_makes_no_lookup(self) -> None:
        shelf = _shelf(
            b"<item><title>No Id</title>"
            b"<user_read_at>Sun, 30 Aug 2026 00:00:00 +0000</user_read_at></item>"
        )
        pages = _Pages({})
        code, records = self._run(shelf, pages)
        self.assertEqual(code, 0)
        self.assertEqual(pages.calls, [])
        self.assertIsNone(records[0]["cover"])

    def test_a_feed_file_run_makes_no_lookup(self) -> None:
        feed = Path(self._tmp.name) / "shelf.xml"
        feed.write_bytes(
            _shelf(_item("1", "Bare", "Sun, 30 Aug 2026 00:00:00 +0000", None))
        )
        pages = _Pages({})
        code, records = self._run(b"", pages, "--feed-file", str(feed))
        self.assertEqual(code, 0)
        self.assertEqual(pages.calls, [])
        self.assertIsNone(records[0]["cover"])

    def test_the_placeholder_is_never_recorded_as_a_cover(self) -> None:
        # The regression, through the API that predates the fallback: the
        # placeholder passed the https rule and was published as the book's cover,
        # a picture of "no cover" on a host the site's cover list does not name.
        items = feed_read.feed_items(
            _shelf(
                _item("1", "Placeholder", "Sun, 30 Aug 2026 00:00:00 +0000", _NOPHOTO)
            )
        )
        self.assertIsNone(book_scan.newest_books(items, 1)[0]["cover"])

    def test_newest_books_bare_makes_no_lookup(self) -> None:
        items = feed_read.feed_items(
            _shelf(_item("1", "Bare", "Sun, 30 Aug 2026 00:00:00 +0000", None))
        )
        self.assertIsNone(book_scan.newest_books(items, 1)[0]["cover"])


if __name__ == "__main__":
    unittest.main()
