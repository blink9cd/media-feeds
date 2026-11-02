"""`feed_gone`: which fetch failures are the server's final answer that a feed is gone.

Regression: a dead Goodreads shelf feed's 404 was retried for about 15 minutes a run.
"""

from __future__ import annotations

import email.message
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

_NO_HEADERS = email.message.Message()


def _http_error(status: int) -> urllib.error.HTTPError:
    """A synthetic HTTP error with `status` and no body."""
    return urllib.error.HTTPError(
        "https://example.invalid/", status, "x", _NO_HEADERS, None
    )


class FeedGoneTests(unittest.TestCase):
    def test_not_found_and_gone_are_final(self) -> None:
        for status in (404, 410):
            with self.subTest(status=status):
                self.assertTrue(feed_read.feed_gone(_http_error(status)))

    def test_other_statuses_may_pass_on_a_retry(self) -> None:
        for status in (400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                self.assertFalse(feed_read.feed_gone(_http_error(status)))

    def test_a_failure_with_no_status_is_not_final(self) -> None:
        self.assertFalse(feed_read.feed_gone(OSError("network unreachable")))
        self.assertFalse(feed_read.feed_gone(ValueError("not XML")))


if __name__ == "__main__":
    unittest.main()
