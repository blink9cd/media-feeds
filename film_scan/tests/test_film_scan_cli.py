"""The CLI's exit codes and output, driven end to end with an injected fetch.

Every test injects `fetch` or `--feed-file`, drives `main` through `_main` (whose
director seam defaults to a fake, never `main`'s own default, the real Wikidata
client), and points `--output` at a `tempfile.TemporaryDirectory()` -- no test here
touches the network. `tests/test_live_feed.py` is the one file in this suite that
reaches the real boundary, and only when explicitly asked to (see that file's module
docstring).
"""

from __future__ import annotations

import io
import json
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above
import film_scan  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - reachable only via film_scan's bootstrap

_FIXTURE = (_TOOL_ROOT / "fixtures" / "diary.xml").read_bytes()

# A synthetic feed whose only item is a list, not a film -- the "half a feed is not
# films" case `diary_entries` already filters out; the CLI-level case here is that it
# must exit 1, not crash, and its message must not mention the username.
_NO_FILMS_FEED = (
    b'<rss version="2.0" xmlns:letterboxd="https://letterboxd.com"><channel>'
    b"<item><title>A List, Not a Film</title>"
    b"<description>A list. It has no film elements at all.</description></item>"
    b"</channel></rss>"
)

# Deliberately not well-formed XML (an unterminated tag), so feed_read.feed_items
# raises ValueError -- the "fetch returned garbage" case, distinct from _NO_FILMS_FEED
# above (well-formed XML with no films) and from an OSError (the fetch itself failed).
_MALFORMED_XML = b"<rss><channel><item>unterminated"

# A placeholder username chosen to look nothing like a real one and to be easy to spot
# if a message under test ever echoes it back -- see film_scan.py's feed_url docstring
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
    requests. `main`'s own default IS the real client, so every call here goes
    through `_main` below and never falls back to it; a test that is about the
    director injects a recording fake instead.
    """
    del tmdb_id
    return None


def _main(
    argv: list[str],
    *,
    fetch: feed_read.Fetcher,
    lookup: film_scan.DirectorLookup = _unresolved_lookup,
) -> int:
    """`film_scan.main` with both network seams injected."""
    return film_scan.main(argv, fetch=fetch, lookup=lookup)


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
        # A raw "/" would add a path segment; a raw "?" would start a query string --
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
        self.assertIs(formatter_class, help_formatter.ReqAwareDefaultsFormatter)


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

    def test_the_written_poster_is_resized_to_the_default_width(self) -> None:
        # The fixture's poster is the 600x900 the feed hands out; the record must
        # carry the smaller PRE-GENERATED size the site actually renders.
        out = self.root / "films.json"
        _main(
            ["--username", _A_USERNAME, "--output", str(out)],
            fetch=_fetch_returning(_FIXTURE),
        )
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("-0-400-0-600-crop", records[0]["poster"])
        self.assertNotIn("-0-600-0-900-crop", records[0]["poster"])

    def test_poster_width_chooses_another_pre_generated_size(self) -> None:
        out = self.root / "films.json"
        _main(
            [
                "--username",
                _A_USERNAME,
                "--output",
                str(out),
                "--poster-width",
                "300",
            ],
            fetch=_fetch_returning(_FIXTURE),
        )
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("-0-300-0-450-crop", records[0]["poster"])

    def test_a_film_with_no_poster_is_written_with_a_null_poster(self) -> None:
        # The resize step must be SKIPPED, not applied to None. 1 diary item in 150
        # (measured) carries no <img> at all, so this is a real feed shape, and a
        # missing poster degrades to a text-only card, not an error.
        out = self.root / "films.json"
        code = _main(
            ["--username", _A_USERNAME, "--output", str(out)],
            fetch=_fetch_returning(re.sub(rb"<img[^>]*>", b"", _FIXTURE)),
        )
        self.assertEqual(code, 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertIsNone(records[0]["poster"])

    def test_a_width_letterboxd_does_not_serve_is_refused_by_the_parser(self) -> None:
        # argparse `choices` refuses it at PARSE time -- offline, before any fetch --
        # so a width that would 403 at page-view time can never reach the record.
        with self.assertRaises(SystemExit) as caught:
            with redirect_stderr(io.StringIO()):
                film_scan.build_parser().parse_args(
                    ["--username", _A_USERNAME, "--poster-width", "345"]
                )
        self.assertEqual(caught.exception.code, 2)

    def test_the_record_is_written_atomically_no_temp_file_survives(self) -> None:
        out = self.root / "films.json"
        code = _main(
            ["--username", _A_USERNAME, "--output", str(out)],
            fetch=_fetch_returning(_FIXTURE),
        )
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        # A concurrent reader (the page, mid-refresh) must never observe a
        # half-written file -- the temp file this write used is renamed away, not
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
        # A failed refresh keeps the last good record -- the pre-existing file must
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
        """Run `main` and return the ONE record its default `--count` of 1 writes."""
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
        # The opt-out must not merely discard the answer, it must never ASK -- that
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
                    # The sample's documented regeneration command must reach NO
                    # network, and the feed is not the only boundary: without
                    # this the director lookup would fire at Wikidata, making the
                    # committed sample depend on a live third party.
                    "--no-director",
                ],
                fetch=_explode_fetch,
            )
            self.assertEqual(code, 0)
            fresh = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(fresh, expected)


if __name__ == "__main__":
    unittest.main()
