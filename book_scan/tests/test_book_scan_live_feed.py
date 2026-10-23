"""One live call against the real Goodreads shelf RSS feed.

Every other test in this suite fakes `fetch`, which proves the parsing but not the
CONTRACT: a renamed element, a reshaped cover-URL scheme, or a moved endpoint would
leave every faked test green. This test is the only thing in the suite that would
notice.

Skipped -- not failed -- under two conditions, both decided independently of the
subject: `BOOK_SCAN_OFFLINE=1` (no network on this host), and no `BOOK_SCAN_USER_ID`
in the environment (no shelf to test against). The user id is a personal identifier
that must never be written into a committed file (book_scan.md, "No message ever
names the user id or the key"), so it is read from the environment ONLY -- there is
no literal user id anywhere in this file, not even as a fallback default.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

OFFLINE = os.environ.get("BOOK_SCAN_OFFLINE") == "1"
_USER_ID = os.environ.get("BOOK_SCAN_USER_ID")


@unittest.skipIf(OFFLINE, "BOOK_SCAN_OFFLINE=1 - live feed skipped")
class LiveFeedTest(unittest.TestCase):
    """Exercise the real feed boundary once, so a moved/renamed field is caught."""

    def setUp(self) -> None:
        """Skip only for a genuinely absent user id, decided independently of the
        subject (`newest_books`) -- see module docstring.
        """
        if _USER_ID is None:
            self.skipTest("BOOK_SCAN_USER_ID not set - no shelf to test against")

    def test_the_real_feed_parses_into_a_well_shaped_record_or_none(self) -> None:
        assert _USER_ID is not None  # setUp already skipped otherwise
        url = book_scan.feed_url(
            _USER_ID, book_scan.DEFAULT_SHELF, book_scan.DEFAULT_WINDOW, None
        )
        body = feed_read.real_fetch(url, feed_read.FEED_TIMEOUT_SECONDS)
        self.assertTrue(body, "the shelf feed came back empty")
        items = feed_read.feed_items(body)
        records = book_scan.newest_books(items, 1)
        # Statement form, not `records[0] if records else None`, for the coverage
        # reason `book_scan._rating`'s docstring gives.
        record = None
        if records:
            record = records[0]
        # A record may legitimately be None -- an empty shelf is a real state, not a
        # broken contract. What this test guards is SHAPE: when a record does exist,
        # every field must still be the type book_scan.py promises, so a renamed or
        # reshaped upstream element fails this test loudly instead of blending into
        # "maybe the shelf is just empty".
        if record is not None:
            self.assertIsInstance(record["title"], str)
            self.assertTrue(record["title"])
            self.assertIsInstance(record["author"], str)
            self.assertTrue(record["author"])
            self.assertIsInstance(record["read"], str)
            self.assertTrue(record["read"])
            self.assertTrue(
                record["rating"] is None or isinstance(record["rating"], int)
            )
            self.assertTrue(record["cover"] is None or isinstance(record["cover"], str))
            # A renamed or dropped `book_published` upstream would land here: the
            # year is optional (None is a legitimate record), but when present it
            # must still be the bare-year STRING the feed carries, not an int and
            # not the empty string a blank element would otherwise leave behind.
            self.assertTrue(record["year"] is None or isinstance(record["year"], str))
            self.assertNotEqual(record["year"], "")


if __name__ == "__main__":
    unittest.main()
