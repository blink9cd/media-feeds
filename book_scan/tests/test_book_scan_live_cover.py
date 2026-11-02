"""One live call against a real public Goodreads book page, for the cover fallback.

Every other cover-lookup test fakes the page fetch, which proves the parsing but not
the contract: a page that drops its `og:image` tag, moves its cover off https, or
starts answering this project's honest User-Agent with a block page would leave every
faked test green. This test is the only thing in the suite that would notice.

Book id 1 is Goodreads' own public catalog entry, not anyone's account: the page is
the same for every visitor, so the test carries no personal identifier. Measured
2026-10-28: its page answered 200 with an `og:image` on Goodreads' image CDN.

Skipped, not failed, under `BOOK_SCAN_OFFLINE=1` (no network on this host), the
same switch the live shelf test honors.

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

#: A public catalog id, not an account (see the module docstring).
_PUBLIC_BOOK_ID = "1"


@unittest.skipIf(OFFLINE, "BOOK_SCAN_OFFLINE=1 - live book page skipped")
class LiveCoverTest(unittest.TestCase):
    """Exercise the real book page once, so a dropped or reshaped tag is caught.

    The contract is the tag on a served page, so the test asks two questions in turn.
    No page at all (an empty body, or one with no `<html>`) is Goodreads not
    answering this client, which the fallback already treats as no cover: a skip,
    with the reason, never a failure, so a throttle cannot turn the gate red.
    Measured 2026-10-28: after about ten requests in a few minutes the page answered
    200 with an empty body. A served page with no usable `og:image` is the contract
    broken, and fails.
    """

    def test_a_served_book_page_names_a_usable_cover(self) -> None:
        url = book_scan.book_page_url(_PUBLIC_BOOK_ID)
        assert url is not None  # a literal all-digit id
        body = feed_read.real_fetch(url, feed_read.FEED_TIMEOUT_SECONDS)
        if b"<html" not in body.lower():
            self.skipTest(
                f"Goodreads served no page ({len(body)} bytes) - not asked twice"
            )
        cover = book_scan.og_image_from_page(body)
        self.assertIsNotNone(cover, "a served book page gave no usable og:image")
        self.assertEqual(book_scan.usable_cover(str(cover)), cover)


if __name__ == "__main__":
    unittest.main()
