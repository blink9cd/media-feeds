"""One live call against Goodreads' real custom shelf widget.

Every other widget test parses the committed, invented fixture, which proves the
parser but not the contract: the widget is undocumented, so a renamed class, a
reshaped cover URL, or a moved endpoint would leave every offline test green. This
test is the only thing in the suite that would notice.

Skipped, not failed, under the live feed suite's two conditions, both decided
independently of the subject: `BOOK_SCAN_OFFLINE=1` (no network on this host), and
no `BOOK_SCAN_USER_ID` in the environment (no shelf to test against). The user id is
a personal identifier that must never be written into a committed file (book_scan.md,
"No message ever names the user id or the key"), so it is read from the environment
only, with no literal anywhere in this file. No assertion message carries a value
from the response either: the shelf is the account holder's.

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


@unittest.skipIf(OFFLINE, "BOOK_SCAN_OFFLINE=1 - live widget skipped")
class LiveWidgetTest(unittest.TestCase):
    """Exercise the real widget boundary once, so a reshaped widget is caught."""

    def setUp(self) -> None:
        """Skip only for a genuinely absent user id (see module docstring)."""
        if _USER_ID is None:
            self.skipTest("BOOK_SCAN_USER_ID not set - no shelf to test against")

    def test_the_real_widget_parses_into_well_shaped_books(self) -> None:
        assert _USER_ID is not None  # setUp already skipped otherwise
        body = feed_read.real_fetch(
            book_scan.widget_url(_USER_ID), feed_read.FEED_TIMEOUT_SECONDS
        )
        candidates = book_scan.widget_candidates(body)
        # Unlike the feed, an empty widget is a broken contract, not an empty shelf:
        # the tool exits 2 on it, so this test fails on it too.
        self.assertTrue(candidates, "the widget held no readable book")
        for record, book_id in candidates:
            self.assertTrue(record["title"], "a widget book had no title")
            self.assertTrue(record["author"], "a widget book had no author")
            # The Book Id comes from the cover's file name; losing it costs every
            # book its page cover and year, so a reshaped cover URL fails here.
            self.assertTrue(
                book_id.isascii() and book_id.isdigit(),
                "a widget book's cover named no Book Id",
            )


if __name__ == "__main__":
    unittest.main()
