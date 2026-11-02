"""Every failure `main` reports: which stream carries it, and what it leaves behind.

The success summary is this tool's stdout. A diagnostic printed there too is read by a
pipeline as if it were output, which is why each failure line belongs on stderr.
Every test injects `fetch` and writes under a `tempfile.TemporaryDirectory()`; no unit
test reaches the network.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import email.message
import io
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

from _book_scan_offline import offline_main  # noqa: E402 - follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "shelf.xml").read_bytes()

# A feed whose one item has no usable date at all: well-formed, and holding nothing
# this tool can record: the exit-1 case, as test_book_scan_cli.py's own fixture has it.
_NO_BOOK_FEED = (
    b'<rss version="2.0"><channel><item><title>Undated</title></item></channel></rss>'
)

# A shelf whose book title carries a C1 control character. C1, not ESC, because
# Measured 2026-09-22: expat refuses a raw C0 character and refuses `&#27;` as well,
# while XML 1.0 admits U+0080-U+009F unescaped, so this is the class that can
# genuinely reach the summary line from a vendor's feed. U+009B is the control
# sequence introducer.
_C1_TITLE_FEED = (
    b'<rss version="2.0"><channel><item>'
    b"<title>A Book&#155;0m</title>"
    b"<user_read_at>Sun, 30 Aug 2026 09:00:00 +0000</user_read_at>"
    b"</item></channel></rss>"
)

# Same reasoning as test_book_scan_cli.py's own _A_USER_ID: an obvious placeholder.
_A_USER_ID = "a-placeholder-user-id"

#: An empty header set for the synthetic HTTPError below. `HTTPError` is typed as
#: taking an `email.message.Message`, and a bare `{}` would need a type: ignore.
_NO_HEADERS = email.message.Message()


def _fetch_returning(body: bytes) -> feed_read.Fetcher:
    """A fake `fetch` that ignores its arguments and returns a fixed body."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        return body

    return fetch


def _fetch_raising(error: Exception) -> feed_read.Fetcher:
    """A fake `fetch` that raises `error` instead of answering."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        raise error

    return fetch


class FailureStreamTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.out = Path(self._tmp.name) / "books.json"
        self.addCleanup(self._tmp.cleanup)

    def _run(self, fetch: feed_read.Fetcher) -> tuple[int, str, str]:
        """Run `main` with the given fetch seam: (code, stdout, stderr)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = offline_main(
                ["--user-id", _A_USER_ID, "--output", str(self.out)], fetch=fetch
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_the_success_summary_is_on_stdout_and_stderr_stays_empty(self) -> None:
        code, stdout, stderr = self._run(_fetch_returning(_FIXTURE))
        self.assertEqual(code, 0)
        self.assertIn("wrote 1 book(s)", stdout)
        self.assertEqual(stderr, "")

    def test_a_control_character_in_a_title_is_escaped_before_it_is_echoed(
        self,
    ) -> None:
        # The summary echoes a vendor's title straight to a terminal. A control
        # character there is not text: it is an instruction to the terminal, and a
        # crafted title could rewrite the window title or wrap the line in an OSC 8
        # hyperlink pointing anywhere. The record itself is unaffected (it is JSON,
        # read by a browser), so this is only about what the operator's terminal
        # receives.
        code, stdout, stderr = self._run(_fetch_returning(_C1_TITLE_FEED))
        self.assertEqual(code, 0)
        self.assertIn("wrote 1 book(s)", stdout)
        self.assertNotIn("\u009b", stdout)
        self.assertIn("\\x9b", stdout)
        self.assertEqual(stderr, "")

    def test_an_unreadable_feed_is_reported_on_stderr_not_stdout(self) -> None:
        code, stdout, stderr = self._run(_fetch_raising(OSError("network unreachable")))
        self.assertEqual(code, 2)
        self.assertIn("could not read the shelf feed", stderr)
        self.assertIn("network unreachable", stderr)
        self.assertEqual(stdout, "")

    def test_a_feed_with_no_book_is_reported_on_stderr_not_stdout(self) -> None:
        code, stdout, stderr = self._run(_fetch_returning(_NO_BOOK_FEED))
        self.assertEqual(code, 1)
        self.assertIn("no book found", stderr)
        self.assertEqual(stdout, "")

    def test_a_failed_write_is_reported_on_stderr_not_stdout(self) -> None:
        self.out.mkdir()
        code, stdout, stderr = self._run(_fetch_returning(_FIXTURE))
        self.assertEqual(code, 2)
        self.assertIn("could not write the record file", stderr)
        self.assertEqual(stdout, "")

    def test_a_refused_feed_response_is_closed_rather_than_leaked(self) -> None:
        # urlopen hands the error page over inside the exception, so an HTTPError
        # owns an open body. Discarding it unclosed leaks the connection and
        # surfaces later as a ResourceWarning raised nowhere near its cause.
        # Asserted on the stream directly rather than by catching that warning,
        # which would depend on when the collector happens to run.
        error = urllib.error.HTTPError(
            "https://example.invalid/", 503, "unavailable", _NO_HEADERS, None
        )
        code, _stdout, stderr = self._run(_fetch_raising(error))
        self.assertTrue(error.fp.closed, "the HTTP error response was left open")
        self.assertEqual(code, 2)
        self.assertIn("503", stderr)

    def test_a_feed_the_server_calls_gone_exits_3_with_its_response_closed(
        self,
    ) -> None:
        # 404 and 410 are the server's final answer, so the exit says so and the
        # scheduled refresh skips its retries (the Goodreads
        # shelf RSS answered 404 on every attempt).
        for status in (404, 410):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://example.invalid/", status, "gone", _NO_HEADERS, None
                )
                code, stdout, stderr = self._run(_fetch_raising(error))
                self.assertEqual(code, 3)
                self.assertTrue(
                    error.fp.closed, "the HTTP error response was left open"
                )
                self.assertIn("could not read the shelf feed", stderr)
                self.assertIn(str(status), stderr)
                self.assertEqual(stdout, "")

    def test_a_refusal_that_may_pass_stays_a_retryable_exit_2(self) -> None:
        # A 403 can be a block that lifts, a 429 a rate limit: worth the retries.
        for status in (403, 429):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://example.invalid/", status, "refused", _NO_HEADERS, None
                )
                code, _stdout, _stderr = self._run(_fetch_raising(error))
                self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
