"""The kept cover: a failed page lookup does not erase what the record already holds.

A coverless book asks its public page on every run, and the page is throttled: on a
day it is not served, the run used to write `cover: None` and `year: None` over a
record that held both the day before, so a published card lost its picture until the
next good fetch. These tests hold that the record already at `--output` is the
fallback for a lookup that found nothing, that a page's own answer still wins, and
that nothing unusable is carried forward. No test here reaches the network.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURES = _TOOL_ROOT / "fixtures"
_PAGE = (_FIXTURES / "book_page.html").read_bytes()

# A served page that states a year and names no cover: the two fields fail apart.
_PAGE_YEAR = "1950"
_PAGE_YEAR_ONLY = (
    '<html><body><p data-testid="publicationInfo">'
    f"First published January 1, {_PAGE_YEAR}</p></body></html>"
).encode()

_TITLE = "An Invented Title"
_AUTHOR = "An Invented Author"
_KEPT_COVER = "https://images.example.invalid/books/1600000000i/42._SX318_.jpg"
_KEPT_YEAR = "1975"

# Same reasoning as test_book_scan_cli.py's own _A_USER_ID: an obvious placeholder.
_A_USER_ID = "a-placeholder-user-id"

_SHELF = (
    b'<rss version="2.0"><channel><item>'
    + f"<title>{_TITLE}</title><book_id>42</book_id>".encode()
    + f"<author_name>{_AUTHOR}</author_name>".encode()
    + b"<user_read_at>Sun, 30 Aug 2026 00:00:00 +0000</user_read_at>"
    + b"</item></channel></rss>"
)


def _kept(**changes: object) -> dict[str, object]:
    """The record a previous run wrote for the shelf's one book, with `changes`."""
    record: dict[str, object] = {
        "title": _TITLE,
        "author": _AUTHOR,
        "year": _KEPT_YEAR,
        "rating": None,
        "read": "2026-08-30",
        "cover": _KEPT_COVER,
    }
    record.update(changes)
    return record


def _refused(url: str, timeout: float) -> bytes:
    """A page fetch that fails the way a throttled or unreachable host does."""
    del url, timeout
    raise OSError("no page is served")


class KeptPagesTest(unittest.TestCase):
    """`kept_pages`: each recorded book's usable cover and year, by title and author."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output = Path(self._tmp.name) / "books.json"

    def _write(self, content: object) -> None:
        self.output.write_text(json.dumps(content), encoding="utf-8", newline="\n")

    def test_a_recorded_book_gives_its_cover_and_year(self) -> None:
        self._write([_kept()])
        self.assertEqual(
            book_scan.kept_pages(self.output),
            {(_TITLE, _AUTHOR): book_scan.BookPage(_KEPT_COVER, _KEPT_YEAR)},
        )

    def test_an_absent_or_damaged_file_gives_nothing(self) -> None:
        self.assertEqual(book_scan.kept_pages(self.output), {})
        self.output.write_text("{not json", encoding="utf-8", newline="\n")
        self.assertEqual(book_scan.kept_pages(self.output), {})
        self._write({"title": _TITLE})
        self.assertEqual(book_scan.kept_pages(self.output), {})
        # Nested past the interpreter's limit: the decoder raises RecursionError.
        self.output.write_text("[" * 200_000, encoding="utf-8", newline="\n")
        self.assertEqual(book_scan.kept_pages(self.output), {})

    def test_an_entry_that_names_no_book_is_skipped(self) -> None:
        self._write(["text", _kept(title=None), _kept(author=7), _kept()])
        self.assertEqual(list(book_scan.kept_pages(self.output)), [(_TITLE, _AUTHOR)])

    def test_an_unusable_cover_or_year_is_not_kept(self) -> None:
        for changes in (
            {"cover": "http://images.example.invalid/books/42.jpg"},
            {"cover": "https://images.example.invalid/assets/nophoto/book/1.png"},
            {"cover": None},
            {"cover": 7},
        ):
            with self.subTest(changes=changes):
                self._write([_kept(**changes)])
                page = book_scan.kept_pages(self.output)[(_TITLE, _AUTHOR)]
                self.assertEqual(page, book_scan.BookPage(None, _KEPT_YEAR))
        for year in (None, 1975, "75", "19755", "in 1975"):
            with self.subTest(year=year):
                self._write([_kept(year=year)])
                page = book_scan.kept_pages(self.output)[(_TITLE, _AUTHOR)]
                self.assertEqual(page, book_scan.BookPage(_KEPT_COVER, None))

    def test_the_first_entry_for_a_book_wins(self) -> None:
        self._write([_kept(), _kept(year="1999")])
        page = book_scan.kept_pages(self.output)[(_TITLE, _AUTHOR)]
        self.assertEqual(page.year, _KEPT_YEAR)


class MainKeptCoverTest(unittest.TestCase):
    """`main` falls back to the record at `--output` when a page gives nothing."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output = Path(self._tmp.name) / "books.json"
        # A shell exporting the source settings must not reroute these RSS runs.
        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop(book_scan.SOURCE_ENV, None)
        os.environ.pop(book_scan.CSV_DIR_ENV, None)

    def _run(
        self,
        page: bytes | None,
        *kept: dict[str, object],
        extra: tuple[str, ...] = (),
    ) -> dict[str, object]:
        """Run `main` over `kept` as the existing record; the one record written.

        `page` is the body the book's page answers with, or None for a failed fetch.
        `extra` is appended to the command line.
        """
        if kept:
            self.output.write_text(
                json.dumps(list(kept)), encoding="utf-8", newline="\n"
            )

        def cover_fetch(url: str, timeout: float) -> bytes:
            if page is None:
                return _refused(url, timeout)
            return page

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = book_scan.main(
                ["--user-id", _A_USER_ID, "--output", str(self.output), *extra],
                fetch=lambda url, timeout: _SHELF,
                cover_fetch=cover_fetch,
            )
        self.assertEqual(code, 0)
        records: list[dict[str, object]] = json.loads(
            self.output.read_text(encoding="utf-8")
        )
        self.assertEqual(len(records), 1)
        return records[0]

    def test_a_failed_lookup_keeps_the_recorded_cover_and_year(self) -> None:
        # The regression: the flapping card. The day before, the page was served and
        # the record holds its cover and year; today the page is not served.
        record = self._run(None, _kept())
        self.assertEqual(record["cover"], _KEPT_COVER)
        self.assertEqual(record["year"], _KEPT_YEAR)

    def test_a_kept_cover_is_re_pointed_at_the_asked_width(self) -> None:
        # The kept URL carries the last run's width; this run's width replaces it.
        record = self._run(None, _kept(), extra=("--cover-width", "200"))
        self.assertEqual(record["cover"], _KEPT_COVER.replace("_SX318_", "_SX200_"))

    def test_a_run_that_asks_no_page_reads_no_record(self) -> None:
        # Offline output is a function of the input alone: the samples depend on it.
        feed_file = Path(self._tmp.name) / "shelf.xml"
        feed_file.write_bytes(_SHELF)
        for extra in (("--feed-file", str(feed_file)), ("--no-cover-lookup",)):
            with self.subTest(extra=extra):
                record = self._run(_PAGE, _kept(), extra=extra)
                self.assertIsNone(record["cover"])
                self.assertIsNone(record["year"])

    def test_a_served_page_wins_over_the_record(self) -> None:
        record = self._run(_PAGE, _kept())
        self.assertNotEqual(record["cover"], _KEPT_COVER)
        self.assertIsNotNone(record["cover"])
        self.assertEqual(record["year"], book_scan.book_page_from_page(_PAGE).year)

    def test_a_page_with_a_year_and_no_cover_keeps_only_the_cover(self) -> None:
        record = self._run(_PAGE_YEAR_ONLY, _kept())
        self.assertEqual(record["cover"], _KEPT_COVER)
        self.assertEqual(record["year"], _PAGE_YEAR)

    def test_another_books_record_is_not_carried(self) -> None:
        record = self._run(None, _kept(title="Another Invented Title"))
        self.assertIsNone(record["cover"])
        self.assertIsNone(record["year"])

    def test_no_record_and_no_page_is_still_no_cover(self) -> None:
        record = self._run(None)
        self.assertIsNone(record["cover"])
        self.assertIsNone(record["year"])


if __name__ == "__main__":
    unittest.main()
