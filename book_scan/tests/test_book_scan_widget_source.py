"""`--source widget`: a run that reads Goodreads' custom shelf widget alone.

The widget fetch is a fake answering the committed fixture (every book in it
invented), and the page fetch is the kit's `Pages` fake, recording each Book Id asked
and answering the fixture book page, whose publication line gives the year 1999. No
test here reaches the network.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import email.message
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
import feed_read  # noqa: E402 - same reason
from _book_scan_sources_kit import (  # noqa: E402 - follows the sys.path bootstrap above
    A_USER_ID,
    FIXTURES,
    PAGE_COVER,
    Pages,
    SourceCase,
    fetch_raising,
    fetch_returning,
)

# Pinned as a literal here, not read back from the module: the feeds env spells it
# out, so a rename in the code alone must fail.
_SOURCE_ENV = "BOOK_SCAN_SOURCE"

_WIDGET_FILE = FIXTURES / "widget_response.txt"
_WIDGET = _WIDGET_FILE.read_bytes()

#: The fixture's books in widget order, and the Book Id each cover names.
_TITLES = ["The Lantern Keeper", "Rook's Gambit", "Café Nights"]
_IDS = ["90001", "90002", "90003"]

#: The year the fixture book page's publication line states.
_PAGE_YEAR = "1999"

_NO_HEADERS = email.message.Message()


class _RecordingFetch:
    """A widget fetch fake recording each (url, timeout) asked; answers the fixture."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.calls.append((url, timeout))
        return _WIDGET


def _http_error(status: int) -> urllib.error.HTTPError:
    """An HTTP refusal with `status`, as urllib raises it."""
    return urllib.error.HTTPError(
        "https://example.invalid/", status, "refused", _NO_HEADERS, None
    )


class WidgetRunTest(SourceCase):
    """A widget run writes the widget's first books, in the widget's own order."""

    def _widget(self, *argv: str, pages: Pages | None = None) -> tuple[int, str, str]:
        """A `--source widget` run whose widget fetch answers the fixture."""
        return self.run_main(
            "--source",
            "widget",
            "--user-id",
            A_USER_ID,
            *argv,
            fetch=fetch_returning(_WIDGET),
            cover_fetch=pages if pages is not None else Pages(),
        )

    def test_it_writes_the_first_books_in_widget_order(self) -> None:
        pages = Pages()
        code, out, err = self._widget("--count", "3", pages=pages)
        self.assertEqual((code, err), (0, ""))
        written = self.written()
        self.assertEqual([record["title"] for record in written], _TITLES)
        self.assertEqual([record["read"] for record in written], [None] * 3)
        self.assertEqual([record["year"] for record in written], [_PAGE_YEAR] * 3)
        self.assertEqual([record["cover"] for record in written], [PAGE_COVER] * 3)
        self.assertEqual(out, "book_scan: wrote 3 book(s), newest The Lantern Keeper\n")

    def test_it_fetches_the_widget_url_once_with_the_feed_timeout(self) -> None:
        fetch = _RecordingFetch()
        code, _, _ = self.run_main(
            "--source",
            "widget",
            "--user-id",
            A_USER_ID,
            "--no-cover-lookup",
            fetch=fetch,
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            fetch.calls,
            [(book_scan.widget_url(A_USER_ID), feed_read.FEED_TIMEOUT_SECONDS)],
        )
        self.assertIn("/review/custom_widget/", fetch.calls[0][0])

    def test_only_the_chosen_books_ask_their_page(self) -> None:
        pages = Pages()
        code, _, _ = self._widget("--count", "2", pages=pages)
        self.assertEqual((code, pages.ids), (0, _IDS[:2]))

    def test_the_read_date_is_written_as_null(self) -> None:
        code, _, _ = self._widget()
        self.assertEqual(code, 0)
        raw = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertIn("read", raw[0])
        self.assertIsNone(raw[0]["read"])

    def test_the_variable_selects_it_and_no_folder_is_needed(self) -> None:
        os.environ[_SOURCE_ENV] = "widget"
        code, _, _ = self.run_main(
            "--user-id",
            A_USER_ID,
            "--no-cover-lookup",
            fetch=fetch_returning(_WIDGET),
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.written()[0]["title"], _TITLES[0])

    def test_a_folder_in_the_environment_is_ignored(self) -> None:
        os.environ["BOOK_SCAN_CSV_DIR"] = str(self.root / "no-such-folder")
        code, _, err = self._widget("--no-cover-lookup")
        self.assertEqual((code, err), (0, ""))

    def test_it_needs_a_user_id(self) -> None:
        stderr = self.usage_error("--source", "widget")
        self.assertIn("--user-id is required", stderr)

    def test_a_feed_file_run_asks_no_fetch_and_no_page(self) -> None:
        # The kit's default fakes fail the test if either is ever called.
        code, _, _ = self.run_main(
            "--source",
            "widget",
            "--user-id",
            A_USER_ID,
            "--feed-file",
            str(_WIDGET_FILE),
            "--count",
            "2",
        )
        self.assertEqual(code, 0)
        written = self.written()
        self.assertEqual([record["title"] for record in written], _TITLES[:2])
        self.assertEqual([record["cover"] for record in written], [None, None])

    def test_the_committed_sample_matches_a_fresh_run(self) -> None:
        # samples/README.md's widget command, byte for byte.
        code, _, _ = self.run_main(
            "--source",
            "widget",
            "--user-id",
            "example-reader",
            "--feed-file",
            str(_WIDGET_FILE),
            "--count",
            "3",
        )
        self.assertEqual(code, 0)
        expected = _TOOL_ROOT / "samples" / "expected_books_from_widget.json"
        self.assertEqual(self.out.read_bytes(), expected.read_bytes())

    def test_a_later_dated_record_does_not_stop_a_widget_write(self) -> None:
        # The never-go-backward guard is `--source both` only.
        kept = [
            {
                "title": "Kept",
                "author": "A",
                "year": None,
                "rating": None,
                "read": "2099-01-01",
                "cover": None,
            }
        ]
        self.out.write_text(json.dumps(kept), encoding="utf-8", newline="\n")
        code, _, _ = self._widget("--no-cover-lookup")
        self.assertEqual(code, 0)
        self.assertEqual(self.written()[0]["title"], _TITLES[0])


class WidgetFailureTest(SourceCase):
    """Each widget failure: its exit code, one stderr line, the record untouched."""

    def setUp(self) -> None:
        super().setUp()
        self.out.write_text("[]", encoding="utf-8", newline="\n")

    def _fail(self, fetch_error: Exception | None = None) -> tuple[int, str, str]:
        """A widget run whose fetch raises `fetch_error`, or answers an empty widget."""
        fetch = (
            fetch_returning(b"var widget_code = '<div>nothing here</div>'")
            if fetch_error is None
            else fetch_raising(fetch_error)
        )
        return self.run_main("--source", "widget", "--user-id", A_USER_ID, fetch=fetch)

    def _assert_one_line(self, err: str) -> None:
        """One stderr line, naming the shelf widget and never the user id."""
        self.assertEqual(err.count("\n"), 1)
        self.assertIn("the shelf widget", err)
        self.assertNotIn(A_USER_ID, err)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "[]")

    def test_a_gone_widget_exits_3(self) -> None:
        for status in (404, 410):
            with self.subTest(status=status):
                code, out, err = self._fail(_http_error(status))
                self.assertEqual((code, out), (3, ""))
                self._assert_one_line(err)

    def test_a_timeout_exits_2(self) -> None:
        code, out, err = self._fail(TimeoutError("timed out"))
        self.assertEqual((code, out), (2, ""))
        self._assert_one_line(err)

    def test_another_refusal_exits_2(self) -> None:
        code, _, err = self._fail(_http_error(503))
        self.assertEqual(code, 2)
        self._assert_one_line(err)

    def test_an_unreadable_widget_file_exits_2(self) -> None:
        code, _, err = self.run_main(
            "--source",
            "widget",
            "--user-id",
            A_USER_ID,
            "--feed-file",
            str(self.root / "no-such-file.txt"),
        )
        self.assertEqual(code, 2)
        self._assert_one_line(err)

    def test_a_body_with_no_book_exits_2_not_1(self) -> None:
        code, out, err = self._fail()
        self.assertEqual((code, out), (2, ""))
        self._assert_one_line(err)
        self.assertIn("markup may have changed", err)


if __name__ == "__main__":
    unittest.main()
