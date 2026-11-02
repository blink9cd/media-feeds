"""Where the user id comes from: `--user-id`, else `BOOK_SCAN_USER_ID`, else a usage error.

The fallback gives the user id, a personal identifier, the treatment `--key` has
(see book_scan.md, "The key that isn't required"): it never needs to appear in a shell
history or a scheduled command line. The environment name is the one the live-boundary
suite (`tests/test_book_scan_live_feed.py`) reads, so a host set up for that suite is
set up for a refresh too.

Every test injects `fetch` and points `--output` at a temporary directory, so no unit
test reaches the network. The ``test_book_scan_`` prefix is load-bearing: one mypy
pass covers every tool folder in the project, so test basenames must be unique across
it.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
from _book_scan_offline import offline_main  # noqa: E402 - follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "shelf.xml").read_bytes()

# Two placeholders chosen to look nothing like a real user id and to be told apart in
# a captured URL: which one reached the fetch is the whole question in this file.
_OPTION_USER_ID = "a-placeholder-from-the-option"
_ENV_USER_ID = "a-placeholder-from-the-environment"

# The name is pinned as a literal here, not read back from the module: the live suite
# and the operator docs both spell it out, so a rename in book_scan.py alone must fail.
_ENV_NAME = "BOOK_SCAN_USER_ID"


def _capturing_fetch(body: bytes) -> tuple[feed_read.Fetcher, list[str]]:
    """A fake `fetch` recording every URL it was handed; proves which user id won."""
    urls: list[str] = []

    def fetch(url: str, timeout: float) -> bytes:
        del timeout
        urls.append(url)
        return body

    return fetch, urls


def _explode_fetch(url: str, timeout: float) -> bytes:
    """A `fetch` fake that fails the test if it is ever called."""
    del url, timeout
    raise AssertionError("fetch must not be called without a user id to fetch for")


class UserIdResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.out = Path(self._tmp.name) / "books.json"
        self.addCleanup(self._tmp.cleanup)
        # Neither variable may leak in from the real environment a test happens to run
        # in (an operator's shell with the identities file sourced), nor out to a later
        # test: each test that cares sets what it needs explicitly via `patch.dict`.
        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        os.environ.pop(_ENV_NAME, None)
        os.environ.pop("BOOK_SCAN_KEY", None)
        # The source settings too, so a shell exporting them cannot reroute an RSS run.
        os.environ.pop(book_scan.SOURCE_ENV, None)
        os.environ.pop(book_scan.CSV_DIR_ENV, None)
        self.addCleanup(env_patcher.stop)

    def test_the_env_name_is_the_one_the_live_suite_reads(self) -> None:
        self.assertEqual(book_scan.USER_ID_ENV, _ENV_NAME)

    def test_the_env_var_supplies_the_user_id_when_the_option_is_absent(self) -> None:
        fetch, urls = _capturing_fetch(_FIXTURE)
        with (
            patch.dict(os.environ, {_ENV_NAME: _ENV_USER_ID}),
            redirect_stdout(io.StringIO()),
        ):
            code = offline_main(["--output", str(self.out)], fetch=fetch)
        self.assertEqual(code, 0)
        self.assertEqual(len(urls), 1)
        self.assertIn(_ENV_USER_ID, urls[0])

    def test_the_explicit_option_wins_over_the_env_var(self) -> None:
        fetch, urls = _capturing_fetch(_FIXTURE)
        with (
            patch.dict(os.environ, {_ENV_NAME: _ENV_USER_ID}),
            redirect_stdout(io.StringIO()),
        ):
            code = offline_main(
                ["--user-id", _OPTION_USER_ID, "--output", str(self.out)], fetch=fetch
            )
        self.assertEqual(code, 0)
        self.assertIn(_OPTION_USER_ID, urls[0])
        self.assertNotIn(_ENV_USER_ID, urls[0])

    def test_neither_source_is_a_usage_error_that_names_the_variable(self) -> None:
        # Exit 2 with the usage line, as `required=True` would give (a missing
        # identity is a usage error, not a feed error), and the message says where
        # else the value may come from.
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            offline_main(["--output", str(self.out)], fetch=_explode_fetch)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--user-id", stderr.getvalue())
        self.assertIn(_ENV_NAME, stderr.getvalue())
        self.assertFalse(self.out.exists())

    def test_an_empty_env_var_counts_as_unset(self) -> None:
        # `BOOK_SCAN_USER_ID=` (sourced from a half-filled identities file) must not
        # become a fetch for a shelf with no id in its URL.
        with (
            patch.dict(os.environ, {_ENV_NAME: ""}),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            offline_main(["--output", str(self.out)], fetch=_explode_fetch)
        self.assertEqual(ctx.exception.code, 2)

    def test_a_feed_file_run_still_needs_a_user_id_from_one_source(self) -> None:
        # The documented contract (a user id is still required, but its value is never
        # read on this path) survives the fallback: the identity is validated before
        # the feed branch, from either source, so an offline run is not a way round it.
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            offline_main(
                [
                    "--output",
                    str(self.out),
                    "--feed-file",
                    str(_TOOL_ROOT / "fixtures" / "shelf.xml"),
                ],
                fetch=_explode_fetch,
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_help_names_the_fallback_beside_the_option(self) -> None:
        # Sliced to just the --user-id entry, so the name being mentioned elsewhere in
        # the help cannot satisfy this on its own. The slice starts after the options
        # heading: the usage line above it also spells "--user-id USER_ID", and a split
        # from the top lands between two usage tokens and tests nothing.
        text = book_scan.build_parser().format_help().split("options:", 1)[1]
        user_id_entry = text.split("--user-id USER_ID", 1)[1].split("--count", 1)[0]
        self.assertIn(_ENV_NAME, user_id_entry)


if __name__ == "__main__":
    unittest.main()
