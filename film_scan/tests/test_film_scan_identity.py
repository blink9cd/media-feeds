"""Where the username comes from: `--username`, else `FILM_SCAN_USERNAME`, else a usage error.

The fallback exists so the username, a personal identifier, never needs to appear in
a shell history or a scheduled command line; the environment name is the one the
live-boundary suite (`tests/test_live_feed.py`) already reads, so a host set up for that
suite is set up for a refresh too.

Every test injects `fetch`, drives `main` through `_main` (its director seam faked,
never the real client) and points `--output` at a temporary directory, so nothing here
touches the network.

The `test_film_scan_` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import film_scan  # noqa: E402 - import follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "diary.xml").read_bytes()

# Two placeholders chosen to look nothing like a real username and to be told apart in
# a captured URL: which one reached the fetch is the whole question in this file.
_OPTION_USERNAME = "a-placeholder-from-the-option"
_ENV_USERNAME = "a-placeholder-from-the-environment"

# The name is pinned as a literal here, not read back from the module: the live suite
# and the operator docs both spell it out, so a rename in film_scan.py alone must fail.
_ENV_NAME = "FILM_SCAN_USERNAME"


def _capturing_fetch(body: bytes) -> tuple[feed_read.Fetcher, list[str]]:
    """A fake `fetch` recording every URL it was handed; proves which username won."""
    urls: list[str] = []

    def fetch(url: str, timeout: float) -> bytes:
        del timeout
        urls.append(url)
        return body

    return fetch, urls


def _explode_fetch(url: str, timeout: float) -> bytes:
    """A `fetch` fake that fails the test if it is ever called."""
    del url, timeout
    raise AssertionError("fetch must not be called without a username to fetch for")


def _unresolved_lookup(tmdb_id: str) -> str | None:
    """The director seam for a test that is not about the director: answers "none".

    What the real client answers for a film Wikidata does not carry, minus the
    requests. `main`'s own default is the real client, so every call here goes
    through `_main` below and never falls back to it; a test that is about the
    director injects a recording fake instead.
    """
    del tmdb_id
    return None


class _RecordingTmdb:
    """A fake TMDB fetch that records each URL and answers a placeholder poster."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
        del timeout, headers
        self.urls.append(url)
        return b'{"poster_path": "/a-placeholder-poster.jpg"}'


def _main(
    argv: list[str],
    *,
    fetch: feed_read.Fetcher,
    lookup: film_scan.DirectorLookup = _unresolved_lookup,
    poster_fetch: feed_read.HeaderFetcher | None = None,
) -> int:
    """`film_scan.main` with all three network seams injected.

    The TMDB token variable is pinned to a placeholder, so a real one in the shell
    running the suite can change nothing.
    """
    tmdb = poster_fetch if poster_fetch is not None else _RecordingTmdb()
    with patch.dict(os.environ, {film_scan.TMDB_TOKEN_ENV: "a-placeholder-token"}):
        return film_scan.main(argv, fetch=fetch, lookup=lookup, poster_fetch=tmdb)


class UsernameResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.out = Path(self._tmp.name) / "films.json"
        self.addCleanup(self._tmp.cleanup)
        # The variable must never leak in from the real environment a test happens to
        # run in (an operator's shell with the identities file sourced), nor out to a
        # later test: each test that cares sets it explicitly via `patch.dict`.
        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        os.environ.pop(_ENV_NAME, None)
        self.addCleanup(env_patcher.stop)

    def test_the_env_name_is_the_one_the_live_suite_reads(self) -> None:
        self.assertEqual(film_scan.USERNAME_ENV, _ENV_NAME)

    def test_the_env_var_supplies_the_username_when_the_option_is_absent(self) -> None:
        fetch, urls = _capturing_fetch(_FIXTURE)
        tmdb = _RecordingTmdb()
        with (
            patch.dict(os.environ, {_ENV_NAME: _ENV_USERNAME}),
            redirect_stdout(io.StringIO()),
        ):
            code = _main(
                ["--output", str(self.out), "--no-director"],
                fetch=fetch,
                poster_fetch=tmdb,
            )
        self.assertEqual(code, 0)
        # The feed fetch is the only URL built from the username: the TMDB request
        # that follows it is keyed on the film's TMDB id, never the member.
        self.assertEqual(len(urls), 1)
        self.assertIn(_ENV_USERNAME, urls[0])
        self.assertTrue(tmdb.urls)
        self.assertEqual([url for url in tmdb.urls if _ENV_USERNAME in url], [])

    def test_the_explicit_option_wins_over_the_env_var(self) -> None:
        fetch, urls = _capturing_fetch(_FIXTURE)
        with (
            patch.dict(os.environ, {_ENV_NAME: _ENV_USERNAME}),
            redirect_stdout(io.StringIO()),
        ):
            code = _main(
                [
                    "--username",
                    _OPTION_USERNAME,
                    "--output",
                    str(self.out),
                    "--no-director",
                ],
                fetch=fetch,
            )
        self.assertEqual(code, 0)
        self.assertIn(_OPTION_USERNAME, urls[0])
        self.assertNotIn(_ENV_USERNAME, urls[0])

    def test_neither_source_is_a_usage_error_that_names_the_variable(self) -> None:
        # Exit 2 with the usage line, the same refusal `required=True` would give (a
        # missing identity is a usage error, not a feed error), and the message
        # says where else the value may come from.
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            _main(["--output", str(self.out)], fetch=_explode_fetch)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--username", stderr.getvalue())
        self.assertIn(_ENV_NAME, stderr.getvalue())
        self.assertFalse(self.out.exists())

    def test_an_empty_env_var_counts_as_unset(self) -> None:
        # `FILM_SCAN_USERNAME=` (sourced from a half-filled identities file) must not
        # become a fetch of `https://letterboxd.com//rss/`.
        with (
            patch.dict(os.environ, {_ENV_NAME: ""}),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            _main(["--output", str(self.out)], fetch=_explode_fetch)
        self.assertEqual(ctx.exception.code, 2)

    def test_a_feed_file_run_still_needs_a_username_from_one_source(self) -> None:
        # A username is still required from one source although this path never reads
        # it: the identity is validated before the feed branch, so an offline run is
        # not a way round it.
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            _main(
                [
                    "--output",
                    str(self.out),
                    "--feed-file",
                    str(_TOOL_ROOT / "fixtures" / "diary.xml"),
                ],
                fetch=_explode_fetch,
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_help_names_the_fallback_beside_the_option(self) -> None:
        # Sliced to just the --username entry, so the name being mentioned elsewhere in
        # the help (the epilog's prerequisites) cannot satisfy this on its own. The
        # slice starts after the options heading: the usage line above it also spells
        # "--username USERNAME", and a split from the top lands between two usage
        # tokens and tests nothing.
        text = film_scan.build_parser().format_help().split("options:", 1)[1]
        username_entry = text.split("--username USERNAME", 1)[1].split("--count", 1)[0]
        self.assertIn(_ENV_NAME, username_entry)


if __name__ == "__main__":
    unittest.main()
