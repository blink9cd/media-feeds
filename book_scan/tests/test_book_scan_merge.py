"""`--source both`: the shelf feed and the export merged, and never going backward.

As `book_scan.md` states under "Merge, never go backward": both
sources are read, each once; books are de-duplicated by Book Id, the feed's record
preferred; and a record already newer than anything either source gave is kept. One
source failing is survivable while the other gives a book. Every fetch is a fake: no
test here reaches the network.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above
from _book_scan_sources_kit import (  # noqa: E402 - follows the sys.path bootstrap above
    A_USER_ID,
    PAGE_COVER,
    SHELF_FEED,
    Pages,
    SourceCase,
    fetch_raising,
    fetch_returning,
    gone_error,
)

_FEED = SHELF_FEED.read_bytes()
_EMPTY_FEED = b'<rss version="2.0"><channel></channel></rss>'
_FEED_COVER = "https://images.example.invalid/books/later-read._SX318_.jpg"


def _record(title: str, read: str) -> book_scan.BookRecord:
    """A record with only a title and a read date of interest."""
    return book_scan.BookRecord(
        title=title, author="An Author", year=None, rating=None, read=read, cover=None
    )


class MergeRunTest(SourceCase):
    """Both sources answering."""

    def _both(
        self,
        *argv: str,
        fetch: feed_read.Fetcher | None = None,
        pages: Pages | None = None,
    ) -> tuple[int, str, str]:
        """A merged run on the test's export folder, the shelf fetch answering the fixture."""
        return self.run_main(
            "--source",
            "both",
            "--user-id",
            A_USER_ID,
            "--csv-dir",
            str(self.exports),
            *argv,
            fetch=fetch or fetch_returning(_FEED),
            cover_fetch=pages or Pages(),
        )

    def test_the_newest_books_of_both_sources_are_written_once_each(self) -> None:
        code, out, err = self._both("--count", "4")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            [record["title"] for record in self.written()],
            [
                "A Lantern for Wren",
                "The Later Read",
                "Harbors, Bridges and Tolls",
                "No Cover Cottage",
            ],
        )
        self.assertIn("newest A Lantern for Wren (2026-09-20)", out)

    def test_a_book_in_both_keeps_the_feed_record_and_asks_no_page(self) -> None:
        pages = Pages()
        code, _, _ = self._both("--count", "3", pages=pages)
        self.assertEqual(code, 0)
        later = self.written()[1]
        self.assertEqual(
            (later["title"], later["cover"]), ("The Later Read", _FEED_COVER)
        )
        self.assertEqual(pages.ids, ["8100001", "8100002"])
        self.assertEqual(self.written()[0]["cover"], PAGE_COVER)

    def test_no_cover_lookup_and_feed_file_each_ask_no_page(self) -> None:
        for extra in (("--no-cover-lookup",), ("--feed-file", str(SHELF_FEED))):
            with self.subTest(extra=extra):
                pages = Pages()
                code, _, _ = self._both(*extra, pages=pages)
                self.assertEqual((code, pages.ids), (0, []))

    def test_both_answering_with_no_book_exits_1(self) -> None:
        (self.exports / "goodreads_library_export.csv").write_text(
            (_TOOL_ROOT / "fixtures" / "library_export.csv")
            .read_text(encoding="utf-8")
            .split("\n")[0]
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        code, _, err = self._both(fetch=fetch_returning(_EMPTY_FEED))
        self.assertEqual(code, 1)
        self.assertEqual(
            err, "book_scan: no book found in the shelf feed or the library export\n"
        )


class OneSourceFailingTest(SourceCase):
    """One source failing does not fail the run while the other gives a book."""

    def _both(
        self, fetch: feed_read.Fetcher, csv_dir: Path | None = None
    ) -> tuple[int, str, str]:
        """A merged run with `fetch` for the shelf and `csv_dir` (else the test's) for the export."""
        return self.run_main(
            "--source",
            "both",
            "--user-id",
            A_USER_ID,
            "--csv-dir",
            str(csv_dir or self.exports),
            "--no-cover-lookup",
            fetch=fetch,
        )

    def test_a_failed_feed_leaves_the_export(self) -> None:
        for error in (OSError("timed out"), gone_error()):
            with self.subTest(error=type(error).__name__):
                code, _, err = self._both(fetch_raising(error))
                self.assertEqual(code, 0)
                self.assertIn("book_scan: could not read the shelf feed:", err)
                self.assertIn("book_scan: recording from the library export alone", err)
                self.assertEqual(self.written()[0]["title"], "A Lantern for Wren")

    def test_a_failed_export_leaves_the_feed(self) -> None:
        code, _, err = self._both(fetch_returning(_FEED), self.root / "missing")
        self.assertEqual(code, 0)
        self.assertIn("book_scan: could not read the library export:", err)
        self.assertIn("book_scan: recording from the shelf feed alone", err)
        self.assertEqual(self.written()[0]["title"], "The Later Read")

    def test_with_no_book_the_feed_failure_code_wins(self) -> None:
        missing = self.root / "missing"
        cases = ((gone_error(), 3), (OSError("timed out"), 2))
        for error, expected in cases:
            with self.subTest(code=expected):
                code, _, _ = self._both(fetch_raising(error), missing)
                self.assertEqual(code, expected)

    def test_a_gone_feed_beside_an_empty_export_stays_3(self) -> None:
        empty = self.root / "header-only"
        empty.mkdir()
        header = (
            (_TOOL_ROOT / "fixtures" / "library_export.csv")
            .read_text(encoding="utf-8")
            .split("\n")[0]
        )
        (empty / "export.csv").write_text(header + "\n", encoding="utf-8", newline="\n")
        code, _, _ = self._both(fetch_raising(gone_error()), empty)
        self.assertEqual(code, 3)

    def test_an_empty_feed_beside_a_failed_export_is_the_export_code(self) -> None:
        code, _, _ = self._both(fetch_returning(_EMPTY_FEED), self.root / "missing")
        self.assertEqual(code, 2)


class NeverBackwardTest(SourceCase):
    """A record already newer than both sources is kept, under `both` only."""

    def _keep(self, records: object) -> str:
        """Write `records` as the existing output; its text."""
        text = json.dumps(records)
        self.out.write_text(text, encoding="utf-8", newline="\n")
        return text

    def _run(self, source: str = "both") -> tuple[int, str, str]:
        """A run of `source` whose newest book is the export's 2026-09-20."""
        return self.run_main(
            "--source",
            source,
            "--user-id",
            A_USER_ID,
            "--csv-dir",
            str(self.exports),
            fetch=fetch_returning(_FEED),
            cover_fetch=Pages(),
        )

    def test_a_newer_record_is_kept_and_no_page_is_asked(self) -> None:
        kept = self._keep(
            [_record("Kept", "2026-10-01"), _record("Older", "2026-01-01")]
        )
        pages = Pages()
        code, out, err = self.run_main(
            "--source",
            "both",
            "--user-id",
            A_USER_ID,
            "--csv-dir",
            str(self.exports),
            fetch=fetch_returning(_FEED),
            cover_fetch=pages,
        )
        self.assertEqual((code, err, pages.ids), (0, "", []))
        self.assertEqual(
            out,
            "book_scan: kept the record; its newest book (2026-10-01) is newer than "
            "anything the sources gave\n",
        )
        self.assertEqual(self.out.read_text(encoding="utf-8"), kept)

    def test_a_record_as_new_or_older_is_replaced(self) -> None:
        for read in ("2026-09-20", "2026-09-19"):
            with self.subTest(read=read):
                self._keep([_record("Kept", read)])
                code, _, _ = self._run()
                self.assertEqual(code, 0)
                self.assertEqual(self.written()[0]["title"], "A Lantern for Wren")

    def test_the_guard_is_only_for_both(self) -> None:
        for source in ("csv", "rss"):
            with self.subTest(source=source):
                self._keep([_record("Kept", "2027-01-01")])
                code, _, _ = self._run(source)
                self.assertEqual(code, 0)
                self.assertNotEqual(self.written()[0]["title"], "Kept")

    def test_an_absent_or_unusable_record_guards_nothing(self) -> None:
        cases: list[object] = [
            {"read": "2027-01-01"},
            ["2027-01-01", {"read": 20270101}, {"read": "next year"}, {"title": "x"}],
            [],
        ]
        for records in cases:
            with self.subTest(records=records):
                self._keep(records)
                code, _, _ = self._run()
                self.assertEqual(
                    (code, self.written()[0]["title"]), (0, "A Lantern for Wren")
                )
        self.out.write_text("not json", encoding="utf-8", newline="\n")
        self.assertEqual(self._run()[0], 0)
        self.out.unlink()
        self.assertEqual(self._run()[0], 0)


class NewestKeptReadTest(SourceCase):
    """`newest_kept_read` on its own."""

    def test_the_latest_iso_read_among_the_objects(self) -> None:
        self.out.write_text(
            json.dumps([{"read": "2026-01-02"}, {"read": "2026-03-04"}, {"read": "x"}]),
            encoding="utf-8",
            newline="\n",
        )
        self.assertEqual(book_scan.newest_kept_read(self.out), "2026-03-04")

    def test_a_null_read_is_skipped(self) -> None:
        # A widget record's read is null: it neither counts as a date nor
        # stops the guard reading a dated neighbor.
        self.out.write_text(
            json.dumps([{"read": None}, {"read": "2026-03-04"}]),
            encoding="utf-8",
            newline="\n",
        )
        self.assertEqual(book_scan.newest_kept_read(self.out), "2026-03-04")

    def test_a_folder_in_its_place_is_no_record(self) -> None:
        self.assertIsNone(book_scan.newest_kept_read(self.root))


class MergeCandidatesTest(unittest.TestCase):
    """`merge_candidates` and `pick_newest` on hand-built candidates."""

    def test_the_preferred_record_wins_and_an_id_less_book_is_always_kept(self) -> None:
        feed = [(_record("Feed copy", "2026-01-01"), "1")]
        export = [
            (_record("Export copy", "2026-01-01"), "1"),
            (_record("No id", "2026-01-02"), ""),
            (_record("Also no id", "2026-01-03"), ""),
            (_record("First", "2026-01-04"), "2"),
            (_record("Repeat", "2026-01-05"), "2"),
        ]
        merged = book_scan.merge_candidates(feed, export)
        self.assertEqual(
            [record["title"] for record, _ in merged],
            ["Feed copy", "No id", "Also no id", "First"],
        )

    def test_a_tie_keeps_source_order(self) -> None:
        first = (_record("First", "2026-01-01"), "1")
        second = (_record("Second", "2026-01-01"), "2")
        chosen = book_scan.pick_newest([first, second], 2)
        self.assertEqual([record["title"] for record, _ in chosen], ["First", "Second"])


if __name__ == "__main__":
    unittest.main()
