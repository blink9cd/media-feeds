"""`--count`'s CLI surface: what it accepts, what it refuses, and how many it writes.

`--cover-width` and `--window` share `--count`'s floor of one, so their surface lives
here too.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "shelf.xml").read_bytes()

# Same reasoning as test_book_scan_cli.py's own _A_USER_ID: an obvious placeholder,
# chosen to look nothing like a real Goodreads user id and easy to spot if a message
# under test ever echoed it back.
_A_USER_ID = "a-placeholder-user-id"


def _fetch_returning(body: bytes) -> feed_read.Fetcher:
    """A fake `fetch` that ignores its arguments and returns a fixed body."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        return body

    return fetch


def _capturing_fetch(body: bytes) -> tuple[feed_read.Fetcher, list[str]]:
    """A fake `fetch` that records every URL it was called with and returns `body`."""
    urls: list[str] = []

    def fetch(url: str, timeout: float) -> bytes:
        del timeout
        urls.append(url)
        return body

    return fetch, urls


def _explode_fetch(url: str, timeout: float) -> bytes:
    """A `fetch` fake that fails the test if it is ever called."""
    del url, timeout
    raise AssertionError("fetch must not be called for an option argparse refuses")


def _exit_code(extra_args: list[str], fetch: feed_read.Fetcher) -> int:
    """`main`'s exit code for a fixed --user-id, whether returned or raised.

    An option value argparse rejects raises `SystemExit` rather than returning --
    the same path the real `if __name__ == "__main__":` entry point relies on --
    so this normalizes both outcomes into the plain int every other CLI test in
    this suite already compares against. Both streams are discarded: a usage
    error prints to stderr, and nothing here reads either.
    """
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return book_scan.main(["--user-id", _A_USER_ID, *extra_args], fetch=fetch)
    except SystemExit as exc:
        code = exc.code
        # argparse's own usage-error exit always passes an int status
        # (ArgumentParser.exit(2, message)); this narrows the type for
        # mypy rather than guessing at a str/None case that can't occur on
        # this path.
        assert isinstance(code, int)
        return code


class CountOptionTest(unittest.TestCase):
    """--count's surface: what it accepts, and what it refuses."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, extra_args: list[str]) -> int:
        """Invoke `main` with a fixed --user-id and a faked feed fetch."""
        return _exit_code(extra_args, _fetch_returning(_FIXTURE))

    def test_a_count_below_one_is_a_usage_error_and_writes_nothing(self) -> None:
        for bad in ("0", "-1"):
            with self.subTest(count=bad):
                out = self.root / "books.json"
                code = self._run(["--count", bad, "--output", str(out)])
                self.assertEqual(code, 2)
                self.assertFalse(out.exists())

    def test_a_non_integer_count_is_the_same_usage_error(self) -> None:
        out = self.root / "books.json"
        self.assertEqual(self._run(["--count", "two", "--output", str(out)]), 2)
        self.assertFalse(out.exists())

    def test_the_record_file_is_a_json_array_even_at_the_default_count(self) -> None:
        out = self.root / "books.json"
        self.assertEqual(self._run(["--output", str(out)]), 0)
        self.assertIsInstance(json.loads(out.read_text(encoding="utf-8")), list)

    def test_a_valid_explicit_count_selects_that_many_books(self) -> None:
        # Exercises `positive_int`'s SUCCESS path end to end through the parser:
        # every case above only ever reaches its ArgumentTypeError branch, so
        # this is what proves a well-formed --count value actually reaches
        # `main` and `newest_books`, not just that a bad one is refused.
        out = self.root / "books.json"
        self.assertEqual(self._run(["--count", "3", "--output", str(out)]), 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 3)

    def test_a_count_of_one_selects_exactly_one_book_from_a_longer_shelf(self) -> None:
        # `positive_int`'s boundary, end to end. The fixture holds more than one
        # book, so a `--count 1` that was refused (exit 2) or that fell through as
        # None (every book written) both fail HERE -- the --count 3 case above
        # cannot tell either apart from a correct run.
        out = self.root / "books.json"
        self.assertEqual(self._run(["--count", "1", "--output", str(out)]), 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)


class PositiveOptionsTest(unittest.TestCase):
    """`--cover-width` and `--window` share --count's floor of one.

    Refused by argparse, at exit 2, BEFORE any fetch: otherwise a zero cover width
    crashes inside `cover_at_width` after the feed has already been fetched, and a
    zero window reaches the vendor as `per_page=0`.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.out = Path(self._tmp.name) / "books.json"
        self.addCleanup(self._tmp.cleanup)

    def test_a_cover_width_below_one_is_a_usage_error_before_any_fetch(self) -> None:
        for bad in ("0", "-1"):
            with self.subTest(cover_width=bad):
                args = ["--cover-width", bad, "--output", str(self.out)]
                self.assertEqual(_exit_code(args, _explode_fetch), 2)
                self.assertFalse(self.out.exists())

    def test_a_window_below_one_is_a_usage_error_before_any_fetch(self) -> None:
        for bad in ("0", "-1"):
            with self.subTest(window=bad):
                args = ["--window", bad, "--output", str(self.out)]
                self.assertEqual(_exit_code(args, _explode_fetch), 2)
                self.assertFalse(self.out.exists())

    def test_a_cover_width_of_one_is_accepted(self) -> None:
        # The boundary: one is the smallest width the floor admits, and it must
        # reach the record as the token it names.
        args = ["--cover-width", "1", "--output", str(self.out)]
        self.assertEqual(_exit_code(args, _fetch_returning(_FIXTURE)), 0)
        records = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertIn("._SX1_.", records[0]["cover"])

    def test_a_window_of_one_is_accepted_and_reaches_the_url(self) -> None:
        fetch, urls = _capturing_fetch(_FIXTURE)
        args = ["--window", "1", "--output", str(self.out)]
        self.assertEqual(_exit_code(args, fetch), 0)
        self.assertIn("per_page=1", urls[0])


if __name__ == "__main__":
    unittest.main()
