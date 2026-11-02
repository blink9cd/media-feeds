"""The poster as the CLI records it: from TMDB, by the token in the environment.

Letterboxd's image host refuses scripted requests (HTTP 403, Cloudflare `error code:
1010`), so a record's poster no longer comes from the feed's description: `main` asks
TMDB's movie-details API for each recorded film's poster, with the token read from
`FILM_SCAN_TMDB_TOKEN`, and records `poster: null` for any film it could not get one
for. These tests drive `main` with the feed fetch, the director lookup, and the TMDB
fetch all injected, and set or clear the token variable themselves, so the outcome
never depends on the shell the suite runs in.

The `test_film_scan_` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import io
import json
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

_FIXTURE_PATH = _TOOL_ROOT / "fixtures" / "diary.xml"
_FIXTURE = _FIXTURE_PATH.read_bytes()

# The name is pinned as a literal here, not read back from the module: the feeds env
# file and the operator docs spell it out, so a rename in film_scan.py alone must fail.
_TOKEN_ENV = "FILM_SCAN_TMDB_TOKEN"

#: An invented token, easy to spot if any output under test ever echoed it.
_TOKEN = "tok-SENTINEL-NEVER-PRINTED"

# Same reasoning as test_film_scan_cli.py's own _A_USERNAME: an obvious placeholder.
_A_USERNAME = "a-placeholder-username"

_API = "https://api.themoviedb.org/3/movie/"
_IMAGES = "https://image.tmdb.org/t/p/w500"


def _feed(url: str, timeout: float) -> bytes:
    """The feed fetch: always the committed fixture."""
    del url, timeout
    return _FIXTURE


def _no_director(tmdb_id: str) -> str | None:
    """The director seam, answering "none" without a request."""
    del tmdb_id
    return None


class _Tmdb:
    """A TMDB fetch fake: a poster per film id, a failure for the ids in `failing`.

    The fixture's films carry TMDB ids 1, 3 and 4 (newest first). Each answer names
    `/poster-<id>.jpg`; an id in `failing` raises an error whose message carries the
    token, so a test can prove no failure text ever reaches the output.
    """

    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = failing
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
        del timeout
        self.calls.append((url, dict(headers)))
        tmdb_id = url.removeprefix(_API)
        if tmdb_id in self.failing:
            raise OSError(f"refused, and the request held {headers['Authorization']}")
        return json.dumps({"poster_path": f"/poster-{tmdb_id}.jpg"}).encode()


def _explode_tmdb(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
    """A TMDB fetch fake that fails the test if it is ever called."""
    del url, timeout, headers
    raise AssertionError("the TMDB fetch must not be called on this path")


class TmdbPosterCliTest(unittest.TestCase):
    """What `main` records, and reports, for each poster outcome."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.out = Path(self._tmp.name) / "films.json"
        self.addCleanup(self._tmp.cleanup)
        env_patcher = patch.dict(os.environ, {_TOKEN_ENV: _TOKEN})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def _run(
        self, *extra: str, tmdb: film_scan.feed_read.HeaderFetcher
    ) -> tuple[int, str, str, list[dict[str, object]]]:
        """Run `main` at `--count 3`: (code, stdout, stderr, the written records)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = film_scan.main(
                [
                    "--username",
                    _A_USERNAME,
                    "--count",
                    "3",
                    "--output",
                    str(self.out),
                    *extra,
                ],
                fetch=_feed,
                lookup=_no_director,
                poster_fetch=tmdb,
            )
        records: list[dict[str, object]] = []
        if self.out.exists():
            records = json.loads(self.out.read_text(encoding="utf-8"))
        return code, stdout.getvalue(), stderr.getvalue(), records

    def test_the_env_name_is_the_documented_one(self) -> None:
        self.assertEqual(film_scan.TMDB_TOKEN_ENV, _TOKEN_ENV)

    def test_each_recorded_film_gets_its_tmdb_poster(self) -> None:
        tmdb = _Tmdb()
        code, _out, err, records = self._run(tmdb=tmdb)
        self.assertEqual(code, 0)
        self.assertEqual(
            [record["poster"] for record in records],
            [
                f"{_IMAGES}/poster-1.jpg",
                f"{_IMAGES}/poster-3.jpg",
                f"{_IMAGES}/poster-4.jpg",
            ],
        )
        self.assertEqual(err, "")

    def test_the_token_travels_only_as_a_bearer_header(self) -> None:
        tmdb = _Tmdb()
        self._run(tmdb=tmdb)
        self.assertEqual(
            [url for url, _ in tmdb.calls], [f"{_API}1", f"{_API}3", f"{_API}4"]
        )
        for url, headers in tmdb.calls:
            self.assertEqual(headers["Authorization"], f"Bearer {_TOKEN}")
            self.assertNotIn(_TOKEN, url)

    def test_only_the_recorded_films_are_looked_up(self) -> None:
        # The default --count of 1 records one film, so exactly one TMDB request.
        tmdb = _Tmdb()
        with redirect_stdout(io.StringIO()):
            code = film_scan.main(
                ["--username", _A_USERNAME, "--output", str(self.out)],
                fetch=_feed,
                lookup=_no_director,
                poster_fetch=tmdb,
            )
        self.assertEqual(code, 0)
        self.assertEqual([url for url, _ in tmdb.calls], [f"{_API}1"])

    def test_a_failed_lookup_leaves_only_that_film_without_a_poster(self) -> None:
        code, out, err, records = self._run(tmdb=_Tmdb(failing=("3",)))
        self.assertEqual(code, 0)
        self.assertEqual(
            [record["poster"] for record in records],
            [f"{_IMAGES}/poster-1.jpg", None, f"{_IMAGES}/poster-4.jpg"],
        )
        self.assertIn("wrote 3 film(s)", out)
        # One line, a count, and no title.
        self.assertEqual(err, "film_scan: 1 of 3 film(s) left without a poster\n")

    def test_the_token_never_reaches_any_output_even_from_a_failure(self) -> None:
        code, out, err, _records = self._run(tmdb=_Tmdb(failing=("1", "3", "4")))
        self.assertEqual(code, 0)
        self.assertNotIn(_TOKEN, out + err)
        self.assertNotIn(_TOKEN, self.out.read_text(encoding="utf-8"))
        self.assertIn("3 of 3 film(s) left without a poster", err)

    def test_the_feeds_own_poster_is_never_recorded(self) -> None:
        # The fixture's description still carries Letterboxd's <img>, as a real feed
        # does. A TMDB answer with no poster must give null, never that address,
        # which the deploy cannot fetch.
        def no_poster(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
            del url, timeout, headers
            return b'{"poster_path": null}'

        code, _out, _err, records = self._run(tmdb=no_poster)
        self.assertEqual(code, 0)
        self.assertEqual([record["poster"] for record in records], [None, None, None])
        self.assertNotIn("example.invalid", self.out.read_text(encoding="utf-8"))

    def test_no_token_records_no_posters_and_names_the_variable_once(self) -> None:
        for unset in ({}, {_TOKEN_ENV: ""}):
            with self.subTest(unset=unset), patch.dict(os.environ, unset):
                if not unset:
                    os.environ.pop(_TOKEN_ENV)
                code, out, err, records = self._run(tmdb=_explode_tmdb)
                self.assertEqual(code, 0)
                self.assertIn("wrote 3 film(s)", out)
                self.assertEqual([record["poster"] for record in records], [None] * 3)
                self.assertEqual(err.count(_TOKEN_ENV), 1)
                self.assertIn(f"{_TOKEN_ENV} is unset", err)
                self.assertIn("3 of 3 film(s) left without a poster", err)

    def test_a_feed_file_run_makes_no_tmdb_request(self) -> None:
        # The offline path stays offline even with a token set: how samples/ is
        # regenerated, with a fetch that fails the test if it is ever called.
        code, _out, err, records = self._run(
            "--feed-file", str(_FIXTURE_PATH), tmdb=_explode_tmdb
        )
        self.assertEqual(code, 0)
        self.assertEqual([record["poster"] for record in records], [None] * 3)
        self.assertNotIn(_TOKEN_ENV, err)
        self.assertIn("3 of 3 film(s) left without a poster", err)

    def test_the_letterboxd_poster_width_option_is_gone(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            film_scan.build_parser().parse_args(["--poster-width", "400"])
        self.assertEqual(caught.exception.code, 2)

    def test_mains_default_poster_fetch_is_the_header_taking_real_fetch(self) -> None:
        defaults = film_scan.main.__kwdefaults__
        assert defaults is not None
        self.assertIs(
            defaults["poster_fetch"], film_scan.feed_read.real_fetch_with_headers
        )


if __name__ == "__main__":
    unittest.main()
