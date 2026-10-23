"""One live call against the real Letterboxd diary RSS feed.

Every other test in this suite fakes `fetch`, which proves the parsing but not the
CONTRACT: a renamed element, a reshaped `description`, or a moved endpoint would leave
every faked test green. This test is the only thing in the suite that would notice.

Skipped -- not failed -- under two conditions, both decided independently of the
subject: `FILM_SCAN_OFFLINE=1` (no network on this host, e.g. a sandboxed run that
denies the real host outright), and no `FILM_SCAN_USERNAME` in the environment (no
diary to test against). The username is a personal identifier that must never be
written into a committed file (film_scan.py's `feed_url` docstring), so it is read
from the environment ONLY -- there is no literal username anywhere in this file, not
even as a fallback default.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above
import film_scan  # noqa: E402 - import follows the sys.path bootstrap above

OFFLINE = os.environ.get("FILM_SCAN_OFFLINE") == "1"
_USERNAME = os.environ.get("FILM_SCAN_USERNAME")


@unittest.skipIf(OFFLINE, "FILM_SCAN_OFFLINE=1 - live feed skipped")
class LiveFeedTest(unittest.TestCase):
    """Exercise the real feed boundary once, so a moved/renamed field is caught."""

    def setUp(self) -> None:
        """Skip only for a genuinely absent username, decided independently of the
        subject (`newest_films`) -- see module docstring.
        """
        if _USERNAME is None:
            self.skipTest("FILM_SCAN_USERNAME not set - no diary to test against")

    def test_the_real_feed_parses_into_a_well_shaped_record_or_none(self) -> None:
        assert _USERNAME is not None  # setUp already skipped otherwise
        body = feed_read.real_fetch(
            film_scan.feed_url(_USERNAME), feed_read.FEED_TIMEOUT_SECONDS
        )
        self.assertTrue(body, "the diary feed came back empty")
        items = feed_read.feed_items(body)
        records = film_scan.newest_films(items, 1)
        # The list may legitimately be empty -- an empty diary is a real state, not a
        # broken contract. What this test guards is SHAPE: when a record does exist,
        # every field must still be the type film_scan.py promises, so a renamed or
        # reshaped upstream element fails this test loudly instead of blending into
        # "maybe the diary is just empty".
        if records:
            record = records[0]
            self.assertIsInstance(record["title"], str)
            self.assertTrue(record["title"])
            self.assertIsInstance(record["watched"], str)
            self.assertTrue(record["watched"])
            self.assertIsInstance(record["rewatch"], bool)
            self.assertTrue(record["year"] is None or isinstance(record["year"], str))
            self.assertTrue(
                record["rating"] is None or isinstance(record["rating"], float)
            )
            self.assertTrue(
                record["poster"] is None or isinstance(record["poster"], str)
            )
            # newest_films() was called WITHOUT a lookup, so this is the record's
            # shape, not the director's value -- resolving one here would add a second
            # live boundary to a test whose whole point is to exercise the Letterboxd
            # one.
            self.assertIsNone(record["director"])

    def test_the_real_feed_still_carries_the_tmdb_id(self) -> None:
        """The join key the director lookup depends on, checked at its own source.

        This is the only test anywhere that would notice Letterboxd dropping or
        renaming `tmdb:movieId`. Nothing would break loudly if it vanished -- every
        record would simply record a null director forever, at a truthful 100%
        coverage and with every faked test still green.
        """
        assert _USERNAME is not None  # setUp already skipped otherwise
        body = feed_read.real_fetch(
            film_scan.feed_url(_USERNAME), feed_read.FEED_TIMEOUT_SECONDS
        )
        entries = film_scan.diary_entries(feed_read.feed_items(body))
        if not entries:
            self.skipTest("the diary is empty - no entry to carry a TMDB id")
        found = [film_scan._tmdb_id(entry) for entry in entries]
        self.assertTrue(
            any(value is not None for value in found),
            "no diary entry carried a tmdb:movieId - the feed contract moved",
        )


if __name__ == "__main__":
    unittest.main()
