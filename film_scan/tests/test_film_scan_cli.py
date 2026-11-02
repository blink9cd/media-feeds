"""The CLI's exit codes and output, driven end to end with an injected fetch.

Every test injects `fetch` or `--feed-file`, drives `main` through `_main` (whose
director and TMDB seams default to fakes, never `main`'s own defaults, the real
Wikidata client and the real HTTPS fetch, and which sets the TMDB token variable to a
placeholder), and points `--output` at a `tempfile.TemporaryDirectory()`, so no test
here touches the network. `tests/test_live_feed.py` is the one file in this suite that
reaches the real boundary, and only when explicitly asked to (see that file's module
docstring).
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

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above
import film_scan  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - reachable only via film_scan's bootstrap

_FIXTURE = (_TOOL_ROOT / "fixtures" / "diary.xml").read_bytes()

# A synthetic feed whose only item is a list, not a film: the "half a feed is not
# films" case `diary_entries` already filters out; the CLI-level case here is that it
# must exit 1, not crash, and its message must not mention the username.
_NO_FILMS_FEED = (
    b'<rss version="2.0" xmlns:letterboxd="https://letterboxd.com"><channel>'
    b"<item><title>A List, Not a Film</title>"
    b"<description>A list. It has no film elements at all.</description></item>"
    b"</channel></rss>"
)

# Deliberately not well-formed XML (an unterminated tag), so feed_read.feed_items
# raises ValueError: the "fetch returned garbage" case, distinct from _NO_FILMS_FEED
# above (well-formed XML with no films) and from an OSError (the fetch itself failed).
_MALFORMED_XML = b"<rss><channel><item>unterminated"

# A placeholder username chosen to look nothing like a real one and to be easy to spot
# if a message under test ever echoes it back; see film_scan.py's feed_url docstring
# on why the username is never written anywhere.
_A_USERNAME = "a-placeholder-username"


def _fetch_returning(body: bytes) -> feed_read.Fetcher:
    """A fake `fetch` that ignores its arguments and returns a fixed body."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        return body

    return fetch


def _explode_fetch(url: str, timeout: float) -> bytes:
    """A `fetch` fake that fails the test if it is ever called."""
    del url, timeout
    raise AssertionError("fetch must not be called when --feed-file is given")


def _unresolved_lookup(tmdb_id: str) -> str | None:
    """The director seam for a test that is not about the director: answers "none".

    What the real client answers for a film Wikidata does not carry, minus the
    requests. `main`'s own default is the real client, so every call here goes
    through `_main` below and never falls back to it; a test that is about the
    director injects a recording fake instead.
    """
    del tmdb_id
    return None


#: What `_tmdb_answering` names as every film's poster, and what the record carries.
_TMDB_POSTER = "https://image.tmdb.org/t/p/w500/a-placeholder-poster.jpg"


def _tmdb_answering(body: bytes) -> feed_read.HeaderFetcher:
    """A fake TMDB fetch that ignores its arguments and returns a fixed body."""

    def fetch(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
        del url, timeout, headers
        return body

    return fetch


_TMDB_WITH_POSTER = _tmdb_answering(b'{"poster_path": "/a-placeholder-poster.jpg"}')


def _explode_tmdb(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
    """A TMDB fetch fake that fails the test if it is ever called."""
    del url, timeout, headers
    raise AssertionError("the TMDB fetch must not be called on a --feed-file run")


def _main(
    argv: list[str],
    *,
    fetch: feed_read.Fetcher,
    lookup: film_scan.DirectorLookup = _unresolved_lookup,
    poster_fetch: feed_read.HeaderFetcher = _TMDB_WITH_POSTER,
) -> int:
    """`film_scan.main` with all three network seams injected and a placeholder token.

    The token variable is pinned here so a real one in the shell running the suite
    can change nothing; the TMDB tests proper are tests/test_film_scan_tmdb_poster.py.
    """
    with patch.dict(os.environ, {film_scan.TMDB_TOKEN_ENV: "a-placeholder-token"}):
        return film_scan.main(
            argv, fetch=fetch, lookup=lookup, poster_fetch=poster_fetch
        )


class FeedUrlTest(unittest.TestCase):
    """`feed_url` builds the one URL this tool ever fetches from."""

    def test_it_fills_in_the_template(self) -> None:
        self.assertEqual(
            film_scan.feed_url("example-viewer"),
            "https://letterboxd.com/example-viewer/rss/",
        )

    def test_it_percent_encodes_a_username_with_a_slash_and_a_question_mark(
        self,
    ) -> None:
        # A raw "/" would add a path segment; a raw "?" would start a query string;
        # either reshapes the URL entirely. Percent-encoding is what stops that.
        url = film_scan.feed_url("a/b?c")
        self.assertEqual(url, "https://letterboxd.com/a%2Fb%3Fc/rss/")
        self.assertNotIn("a/b?c", url)


class ParserShapeTest(unittest.TestCase):
    """CLI argument style: named options only, and a self-documenting --help."""

    def test_every_option_is_named_not_positional(self) -> None:
        parser = film_scan.build_parser()
        positional = [
            a for a in parser._actions if not a.option_strings and a.dest != "help"
        ]
        self.assertEqual(positional, [], "CLI arguments must be named options")

    def test_the_parser_leaves_a_missing_username_to_main(self) -> None:
        # Not `required=True`: the parser cannot see FILM_SCAN_USERNAME, so
        # the "neither source" refusal lives in `main` (tests/test_film_scan_identity.py)
        # and a bare parse must come back with None rather than exiting.
        self.assertIsNone(film_scan.build_parser().parse_args([]).username)

    def test_help_states_the_optional_defaults(self) -> None:
        text = film_scan.build_parser().format_help()
        self.assertIn("--output", text)
        self.assertIn("--feed-file", text)
        self.assertIn("default", text)

    def test_usage_shows_username_as_optional(self) -> None:
        # The usage line is the one place argparse states required-ness: a required
        # option renders bare, an optional one in brackets. Since FILM_SCAN_USERNAME can
        # stand in for it, the bracketed form is the truthful one.
        text = film_scan.build_parser().format_usage()
        self.assertIn("[--username USERNAME]", text)

    def test_help_states_prerequisites_and_run_order(self) -> None:
        # Whitespace collapsed first: argparse reflows the epilog, so a two-word
        # heading can land across a line break whenever the text before it grows.
        text = " ".join(film_scan.build_parser().format_help().split())
        self.assertIn("Prerequisites", text)
        self.assertIn("Run order", text)
        self.assertIn("Exit codes", text)

    def test_the_parser_uses_the_shared_formatter(self) -> None:
        # Pinned as a binding, not a rendering: the gate's interpreter already drops
        # the bogus "(default: None)" on a required option, so a --help assertion
        # would pass with the stock formatter back in place ("The version trap" in
        # help_formatter.md). The floor is 3.12, below that fix.
        formatter_class = film_scan.build_parser().formatter_class
        self.assertIs(formatter_class, help_formatter.UnsetHidingFormatter)

    def test_help_shows_no_unset_default(self) -> None:
        # An option left out holds None, which is no value a person could type, so
        # "(default: None)" would say nothing true; each such option's help says what
        # leaving it out means instead. This half renders on every interpreter.
        text = film_scan.build_parser().format_help()
        self.assertNotIn("(default: None)", text)

    def test_help_shows_no_false_default_for_a_flag(self) -> None:
        # A store_true flag's help already says what giving it does, so a
        # "(default: False)" adds nothing. Whitespace is collapsed first because
        # argparse may wrap the stamp across two lines.
        text = " ".join(film_scan.build_parser().format_help().split())
        self.assertNotIn("(default: False)", text)

    def test_no_director_is_falsy_when_omitted_and_true_when_given(self) -> None:
        parser = film_scan.build_parser()
        self.assertFalse(parser.parse_args(["--username", "x"]).no_director)
        self.assertIs(
            parser.parse_args(["--username", "x", "--no-director"]).no_director, True
        )


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_successful_scan_writes_the_record_and_confirms_the_film(self) -> None:
        out = self.root / "films.json"
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = _main(
                ["--username", _A_USERNAME, "--output", str(out)],
                fetch=_fetch_returning(_FIXTURE),
            )
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        self.assertIn("The Invented Voyage", buffer.getvalue())

    def test_the_written_record_has_exactly_the_filmrecord_keys(self) -> None:
        out = self.root / "films.json"
        _main(
            ["--username", _A_USERNAME, "--output", str(out)],
            fetch=_fetch_returning(_FIXTURE),
        )
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)
        self.assertEqual(
            set(records[0].keys()),
            {"title", "year", "rating", "watched", "rewatch", "poster", "director"},
        )
        self.assertEqual(records[0]["title"], "The Invented Voyage")
        # No filesystem path and no username anywhere in the written bytes.
        raw = out.read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn(_A_USERNAME, raw)

    def test_the_written_poster_is_the_one_tmdb_names(self) -> None:
        out = self.root / "films.json"
        _main(
            ["--username", _A_USERNAME, "--output", str(out)],
            fetch=_fetch_returning(_FIXTURE),
        )
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(records[0]["poster"], _TMDB_POSTER)

    def test_a_film_tmdb_has_no_poster_for_is_written_with_a_null_poster(self) -> None:
        # A missing poster degrades to a text-only card, not an error.
        out = self.root / "films.json"
        with redirect_stderr(io.StringIO()):
            code = _main(
                ["--username", _A_USERNAME, "--output", str(out)],
                fetch=_fetch_returning(_FIXTURE),
                poster_fetch=_tmdb_answering(b'{"poster_path": null}'),
            )
        self.assertEqual(code, 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertIsNone(records[0]["poster"])

    def test_the_record_is_written_atomically_no_temp_file_survives(self) -> None:
        out = self.root / "films.json"
        code = _main(
            ["--username", _A_USERNAME, "--output", str(out)],
            fetch=_fetch_returning(_FIXTURE),
        )
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        # A concurrent reader (the page, mid-refresh) must never observe a
        # half-written file: the temp file this write used is renamed away, not
        # left beside the real one.
        leftovers = list(self.root.glob("*.tmp"))
        self.assertEqual(leftovers, [], f"temp file(s) left behind: {leftovers}")

    def test_a_feed_with_only_lists_exits_1_and_never_names_the_username(self) -> None:
        out = self.root / "films.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = _main(
                ["--username", _A_USERNAME, "--output", str(out)],
                fetch=_fetch_returning(_NO_FILMS_FEED),
            )
        self.assertEqual(code, 1)
        self.assertFalse(out.exists(), "no film means no file, not an empty one")
        # The diagnostic is stderr's (tests/test_film_scan_failures.py pins the
        # stream); the username must appear on neither.
        self.assertIn("no film", stderr.getvalue().lower())
        self.assertNotIn(_A_USERNAME, stdout.getvalue() + stderr.getvalue())

    def test_a_fetch_raising_oserror_exits_2_and_leaves_the_old_record_untouched(
        self,
    ) -> None:
        out = self.root / "films.json"
        old_content = '[{"title": "Old Film", "watched": "2020-01-01"}]'
        out.write_text(old_content, encoding="utf-8", newline="\n")

        def explode(url: str, timeout: float) -> bytes:
            del url, timeout
            raise OSError("network unreachable")

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = _main(
                ["--username", _A_USERNAME, "--output", str(out)], fetch=explode
            )
        self.assertEqual(code, 2)
        # A failed refresh keeps the last good record: the pre-existing file must
        # be byte-for-byte unchanged, not merely still present.
        self.assertEqual(out.read_text(encoding="utf-8"), old_content)
        self.assertNotIn(_A_USERNAME, stdout.getvalue() + stderr.getvalue())

    def test_a_fetch_returning_malformed_xml_exits_2(self) -> None:
        out = self.root / "films.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = _main(
                ["--username", _A_USERNAME, "--output", str(out)],
                fetch=_fetch_returning(_MALFORMED_XML),
            )
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())
        self.assertNotIn(_A_USERNAME, stdout.getvalue() + stderr.getvalue())

    def test_feed_file_reads_a_local_file_and_never_calls_fetch(self) -> None:
        feed_path = self.root / "diary.xml"
        feed_path.write_bytes(_FIXTURE)
        out = self.root / "films.json"

        code = _main(
            [
                "--username",
                _A_USERNAME,
                "--output",
                str(out),
                "--feed-file",
                str(feed_path),
            ],
            fetch=_explode_fetch,
        )
        self.assertEqual(code, 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(records[0]["title"], "The Invented Voyage")

    def test_a_missing_feed_file_exits_2(self) -> None:
        out = self.root / "films.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = _main(
                [
                    "--username",
                    _A_USERNAME,
                    "--output",
                    str(out),
                    "--feed-file",
                    str(self.root / "does-not-exist.xml"),
                ],
                fetch=_explode_fetch,
            )
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())


class DirectorCliTest(unittest.TestCase):
    """The director seam as the CLI drives it."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, *extra: str, lookup: film_scan.DirectorLookup) -> dict[str, object]:
        """Run `main` and return the one record its default `--count` of 1 writes."""
        out = self.root / "films.json"
        with redirect_stdout(io.StringIO()):
            code = _main(
                ["--username", _A_USERNAME, "--output", str(out), *extra],
                fetch=_fetch_returning(_FIXTURE),
                lookup=lookup,
            )
        self.assertEqual(code, 0)
        loaded: list[dict[str, object]] = json.loads(out.read_text(encoding="utf-8"))
        return loaded[0]

    def test_the_written_record_carries_the_looked_up_director(self) -> None:
        calls: list[str] = []

        def lookup(tmdb_id: str) -> str | None:
            calls.append(tmdb_id)
            return "Ada Director"

        record = self._run(lookup=lookup)
        self.assertEqual(record["director"], "Ada Director")
        self.assertEqual(calls, ["1"])

    def test_no_director_skips_the_lookup_and_records_null(self) -> None:
        # The opt-out must not merely discard the answer, it must never ask; that
        # is what makes a fully offline refresh possible.
        def explode(_tmdb_id: str) -> str | None:
            raise AssertionError("--no-director must not reach the lookup")

        record = self._run("--no-director", lookup=explode)
        self.assertIsNone(record["director"])

    def test_the_help_documents_the_opt_out(self) -> None:
        text = film_scan.build_parser().format_help()
        self.assertIn("--no-director", text)


class SamplesPairTest(unittest.TestCase):
    """`samples/expected_films.json` must never drift from a fresh --feed-file run
    against the committed fixture.
    """

    def test_the_committed_sample_matches_a_fresh_run(self) -> None:
        samples_dir = _TOOL_ROOT / "samples"
        expected = json.loads(
            (samples_dir / "expected_films.json").read_text(encoding="utf-8")
        )
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "films.json"
            code = _main(
                [
                    "--username",
                    _A_USERNAME,
                    "--output",
                    str(out),
                    "--feed-file",
                    str(_TOOL_ROOT / "fixtures" / "diary.xml"),
                    # The sample's documented regeneration command must reach no
                    # network, and the feed is not the only boundary: without
                    # this the director lookup would fire at Wikidata, making the
                    # committed sample depend on a live third party.
                    "--no-director",
                ],
                fetch=_explode_fetch,
                # Nor TMDB: an offline run records no poster at all.
                poster_fetch=_explode_tmdb,
            )
            self.assertEqual(code, 0)
            fresh = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(fresh, expected)


if __name__ == "__main__":
    unittest.main()
