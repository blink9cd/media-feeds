"""One live call against a real public Goodreads book page, for the cover fallback.

Every other cover-lookup test fakes the page fetch, which proves the parsing but not
the contract: a page that drops its `og:image` tag, moves its cover off https, or
starts answering this project's honest User-Agent with a block page would leave every
faked test green. This test is the only thing in the suite that would notice.

Book id 1 is Goodreads' own public catalog entry, not anyone's account: the page is
the same for every visitor, so the test carries no personal identifier. Measured
2026-10-28: its page answered 200 with an `og:image` on Goodreads' image CDN.

Skipped, not failed, under `BOOK_SCAN_OFFLINE=1` (no network on this host), the
same switch the live shelf test honors. The offline `FetchServedPageTest` beside it
runs either way: it proves, with fake fetches, which statuses skip and which fail.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import email.message
import os
import sys
import unittest
import urllib.error
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

OFFLINE = os.environ.get("BOOK_SCAN_OFFLINE") == "1"

#: A public catalog id, not an account (see the module docstring).
_PUBLIC_BOOK_ID = "1"

#: A documentation-domain URL for the offline fetch tests; nothing is ever sent to it.
_PAGE_URL = "https://example.invalid/book/show/1"

#: "Too many requests": the server is throttling this client, not refusing the page.
_THROTTLED = 429
#: The first server-error status: from here up the server could not serve right now.
_SERVER_ERROR_FLOOR = 500


def fetch_served_page(url: str, fetch: feed_read.Fetcher) -> bytes:
    """Fetch `url`, skipping the test when the server says it cannot serve now.

    A throttle (429) or a server error (5xx) is Goodreads not answering, like the empty
    body the live test already skips, so it raises `unittest.SkipTest` naming the
    status. Any other HTTP error, a 404 above all, is the page gone, and propagates.
    """
    try:
        return fetch(url, feed_read.FEED_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        if error.code != _THROTTLED and error.code < _SERVER_ERROR_FLOOR:
            raise
        # An HTTPError is also the response, holding its body open; the skip never
        # reads it, so close it here rather than leave it to the garbage collector.
        error.close()
        raise unittest.SkipTest(
            f"Goodreads could not serve the page (HTTP {error.code}) - not asked twice"
        ) from error


@unittest.skipIf(OFFLINE, "BOOK_SCAN_OFFLINE=1 - live book page skipped")
class LiveCoverTest(unittest.TestCase):
    """Exercise the real book page once, so a dropped or reshaped tag is caught.

    The contract is the tag on a served page, so the test asks two questions in turn.
    No page at all (an empty body, one with no `<html>`, or a 429 or 5xx status) is
    Goodreads not answering this client, which the fallback already treats as no
    cover: a skip, with the reason, never a failure, so a throttle or an outage
    cannot turn the gate red. A 503 did exactly that on 2026-11-06.
    Measured 2026-10-28: after about ten requests in a few minutes the page answered
    200 with an empty body. A served page with no usable `og:image` is the contract
    broken, and fails.
    """

    def test_a_served_book_page_names_a_usable_cover(self) -> None:
        url = book_scan.book_page_url(_PUBLIC_BOOK_ID)
        assert url is not None  # a literal all-digit id
        body = fetch_served_page(url, feed_read.real_fetch)
        if b"<html" not in body.lower():
            self.skipTest(
                f"Goodreads served no page ({len(body)} bytes) - not asked twice"
            )
        cover = book_scan.og_image_from_page(body)
        self.assertIsNotNone(cover, "a served book page gave no usable og:image")
        self.assertEqual(book_scan.usable_cover(str(cover)), cover)


def _status_fetcher(code: int) -> feed_read.Fetcher:
    """A page fetch that answers with HTTP status `code`, the way urllib raises one."""

    def fetch(url: str, timeout: float) -> bytes:
        del timeout
        raise urllib.error.HTTPError(url, code, "status", email.message.Message(), None)

    return fetch


class FetchServedPageTest(unittest.TestCase):
    """The live test's fetch skips on a server that cannot serve now, and only then.

    Offline on purpose: every fetch here is a fake, so these run under
    `BOOK_SCAN_OFFLINE=1` too, and prove the skip without waiting for an outage.
    """

    def test_a_served_page_is_returned(self) -> None:
        def fetch(url: str, timeout: float) -> bytes:
            del url, timeout
            return b"<html></html>"

        self.assertEqual(fetch_served_page(_PAGE_URL, fetch), b"<html></html>")

    def test_a_server_error_or_throttle_skips_naming_the_status(self) -> None:
        for code in (429, 500, 503):
            with self.subTest(code=code):
                with self.assertRaises(unittest.SkipTest) as caught:
                    fetch_served_page(_PAGE_URL, _status_fetcher(code))
                self.assertIn(f"HTTP {code}", str(caught.exception))

    def test_any_other_status_still_fails(self) -> None:
        for code in (403, 404, 499):
            with (
                self.subTest(code=code),
                self.assertRaises(urllib.error.HTTPError) as caught,
            ):
                fetch_served_page(_PAGE_URL, _status_fetcher(code))
            caught.exception.close()  # the error is a response too: close its body


if __name__ == "__main__":
    unittest.main()
