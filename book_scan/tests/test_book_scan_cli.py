"""The CLI's exit codes and output, driven end to end with an injected fetch.

Every test injects `fetch` or `--feed-file` and points `--output` at a
`tempfile.TemporaryDirectory()`, so no unit test reaches the network.
`tests/test_book_scan_live_feed.py` is the one file in this suite that reaches the
real boundary, and only when explicitly asked to (see that file's module docstring).

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import io
import json
import re
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
import help_formatter  # noqa: E402 - reachable only via book_scan's bootstrap

_FIXTURE = (_TOOL_ROOT / "fixtures" / "shelf.xml").read_bytes()

# A synthetic feed whose one item has no usable date at all. Well-formed XML, distinct
# from _MALFORMED_XML below (which is not even parseable), so this exercises exit 1
# while that exercises exit 2.
_NO_BOOK_FEED = (
    b'<rss version="2.0"><channel><item><title>Undated</title></item></channel></rss>'
)

# Deliberately not well-formed XML (an unterminated tag), so feed_read.feed_items
# raises ValueError: the "fetch returned garbage" case, distinct from _NO_BOOK_FEED
# above (well-formed XML with no dateable book) and from an OSError (the fetch itself
# failed).
_MALFORMED_XML = b"<rss><channel><item>unterminated"

# A placeholder Goodreads user id chosen to look nothing like a real one and to be
# easy to spot if a message under test ever echoes it back; see book_scan.md, "No
# message ever names the user id or the key".
_A_USER_ID = "a-placeholder-user-id"

# A placeholder API key, same reasoning as _A_USER_ID above: --key is a runtime
# argument that must never appear in a message either.
_A_KEY = "a-placeholder-api-key"


def _fetch_returning(body: bytes) -> feed_read.Fetcher:
    """A fake `fetch` that ignores its arguments and returns a fixed body."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        return body

    return fetch


def _capturing_fetch(body: bytes) -> tuple[feed_read.Fetcher, list[str]]:
    """A fake `fetch` that records every URL it was called with and returns `body`.

    The URL is what actually reached the network seam, the only way to prove `--key`
    and `--window` reached the built URL rather than merely that `feed_url` computes
    them correctly in isolation (`FeedUrlTest` below already covers that).
    """
    urls: list[str] = []

    def fetch(url: str, timeout: float) -> bytes:
        del timeout
        urls.append(url)
        return body

    return fetch, urls


def _explode_fetch(url: str, timeout: float) -> bytes:
    """A `fetch` fake that fails the test if it is ever called."""
    del url, timeout
    raise AssertionError("fetch must not be called when --feed-file is given")


class FeedUrlTest(unittest.TestCase):
    """`feed_url` builds the one URL this tool ever fetches from."""

    def test_it_fills_in_the_template_with_no_key(self) -> None:
        url = book_scan.feed_url("42", "read", 50, None)
        self.assertEqual(
            url,
            "https://www.goodreads.com/review/list_rss/42?shelf=read&per_page=50",
        )

    def test_key_is_omitted_entirely_when_absent_not_sent_empty(self) -> None:
        # Verified 2026-08-31: a deliberately bogus key returned byte-identical
        # output to no key at all, so `--key` is optional and, when absent, `key`
        # must not appear in the URL at all, not even as an empty `key=`, which would
        # misrepresent that measured behavior. This is the test that stops it
        # silently becoming required again.
        url = book_scan.feed_url("42", "read", 50, None)
        self.assertNotIn("key=", url)
        self.assertNotIn("key", url.split("?", 1)[1])

    def test_key_appears_in_the_url_when_given(self) -> None:
        url = book_scan.feed_url("42", "read", 50, _A_KEY)
        self.assertIn(f"key={_A_KEY}", url)

    def test_window_reaches_the_url_as_per_page(self) -> None:
        # A window wider than the card count is what makes the date-read re-sort
        # correct, so assert the built URL actually carries the value given, not a
        # hardcoded 50.
        url = book_scan.feed_url("42", "read", 99, None)
        self.assertIn("per_page=99", url)

    def test_shelf_reaches_the_url(self) -> None:
        url = book_scan.feed_url("42", "to-read", 50, None)
        self.assertIn("shelf=to-read", url)

    def test_it_percent_encodes_a_user_id_with_a_slash_and_a_question_mark(
        self,
    ) -> None:
        # A raw "/" would add a path segment; a raw "?" would start a query string;
        # either reshapes the URL entirely. Percent-encoding is what stops that.
        url = book_scan.feed_url("a/b?c", "read", 50, None)
        self.assertEqual(
            url,
            "https://www.goodreads.com/review/list_rss/a%2Fb%3Fc?shelf=read&per_page=50",
        )
        self.assertNotIn("a/b?c", url)


class ParserShapeTest(unittest.TestCase):
    """CLI argument style: named options only, and a self-documenting --help."""

    def test_every_option_is_named_not_positional(self) -> None:
        parser = book_scan.build_parser()
        positional = [
            a for a in parser._actions if not a.option_strings and a.dest != "help"
        ]
        self.assertEqual(positional, [], "CLI arguments must be named options")

    def test_the_parser_leaves_a_missing_user_id_to_main(self) -> None:
        # Not `required=True`: the parser cannot see BOOK_SCAN_USER_ID, so
        # the "neither source" refusal lives in `main` (tests/test_book_scan_identity.py)
        # and a bare parse must come back with None rather than exiting.
        self.assertIsNone(book_scan.build_parser().parse_args([]).user_id)

    def test_help_states_the_optional_defaults(self) -> None:
        text = book_scan.build_parser().format_help()
        for option in ("--shelf", "--window", "--key", "--output", "--feed-file"):
            self.assertIn(option, text)
        self.assertIn("default", text)

    def test_help_shows_the_shelf_and_window_defaults_live_not_hardcoded(self) -> None:
        # The defaults formatter renders these from the live default=, so this
        # also proves DEFAULT_SHELF/DEFAULT_WINDOW actually reached the parser.
        text = book_scan.build_parser().format_help()
        self.assertIn(book_scan.DEFAULT_SHELF, text)
        self.assertIn(str(book_scan.DEFAULT_WINDOW), text)

    def test_usage_shows_user_id_as_optional(self) -> None:
        # The usage line is the one place argparse states required-ness: a required
        # option renders bare, an optional one in brackets. Since BOOK_SCAN_USER_ID can
        # stand in for it, the bracketed form is the truthful one.
        text = book_scan.build_parser().format_usage()
        self.assertIn("[--user-id USER_ID]", text)

    def test_help_states_prerequisites_and_run_order(self) -> None:
        # Whitespace collapsed first: argparse wraps to the terminal width, so an
        # unnormalized match fails at whichever widths split a phrase across lines.
        text = " ".join(book_scan.build_parser().format_help().split())
        self.assertIn("Prerequisites", text)
        self.assertIn("Run order", text)
        self.assertIn("Exit codes", text)

    def test_help_lists_take_a_serial_comma(self) -> None:
        text = " ".join(book_scan.build_parser().format_help().split())
        for phrase in ("rss, or both,", "unreadable, or malformed"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_the_parser_uses_the_shared_formatter(self) -> None:
        # Pinned as a binding, not a rendering: the gate's interpreter already drops
        # the bogus "(default: None)" on a required option, so a --help assertion
        # would pass with the stock formatter back in place ("The version trap" in
        # help_formatter.md). The floor is 3.12, below that fix.
        formatter_class = book_scan.build_parser().formatter_class
        self.assertIs(formatter_class, help_formatter.UnsetHidingFormatter)

    def test_help_shows_no_unset_default(self) -> None:
        # An option left out holds None, which is no value a person could type, so
        # "(default: None)" would say nothing true; each such option's help says what
        # leaving it out means instead. This half renders on every interpreter.
        text = book_scan.build_parser().format_help()
        self.assertNotIn("(default: None)", text)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # BOOK_SCAN_KEY must never leak in from the real environment a test happens
        # to run in, and must never leak out to a later test either: every test
        # that cares about it sets/clears it explicitly via `patch.dict` instead.
        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        os.environ.pop("BOOK_SCAN_KEY", None)
        # The source settings too, so a shell exporting them cannot reroute an RSS run.
        os.environ.pop(book_scan.SOURCE_ENV, None)
        os.environ.pop(book_scan.CSV_DIR_ENV, None)
        self.addCleanup(env_patcher.stop)

    def test_cover_width_rewrites_the_recorded_sizing_token(self) -> None:
        out = self.root / "books.json"
        offline_main(
            ["--user-id", _A_USER_ID, "--output", str(out), "--cover-width", "250"],
            fetch=_fetch_returning(_FIXTURE),
        )
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("._SX250_.", records[0]["cover"])

    def test_a_book_with_no_cover_is_written_with_a_null_cover(self) -> None:
        # The resize step must be skipped, not applied to None. A shelf item with no
        # book_large_image_url degrades to cover: None rather than falling back to an
        # unusable thumbnail (see book_scan.md, "Why book_image_url is not the cover
        # URL"), and that record is correctly shaped, not broken.
        out = self.root / "books.json"
        code = offline_main(
            ["--user-id", _A_USER_ID, "--output", str(out)],
            fetch=_fetch_returning(
                re.sub(
                    rb"<book_large_image_url>[^<]*</book_large_image_url>",
                    b"",
                    _FIXTURE,
                )
            ),
        )
        self.assertEqual(code, 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertIsNone(records[0]["cover"])

    def test_a_cover_that_is_not_https_is_written_as_null(self) -> None:
        # The feed's large-image URL is a third party's string and the record is
        # what the page loads an image from: an http: URL is written as no cover,
        # and the resize step is skipped for it as for any other None.
        out = self.root / "books.json"
        code = offline_main(
            ["--user-id", _A_USER_ID, "--output", str(out)],
            fetch=_fetch_returning(
                _FIXTURE.replace(
                    b"<book_large_image_url>https://", b"<book_large_image_url>http://"
                )
            ),
        )
        self.assertEqual(code, 0)
        raw = out.read_text(encoding="utf-8")
        self.assertIn('"cover": null', raw)
        self.assertIsNone(json.loads(raw)[0]["cover"])

    def test_the_default_cover_width_leaves_the_feed_token_alone(self) -> None:
        # Deliberate: measured, this cover has no saving to give (see
        # _DEFAULT_COVER_WIDTH). The default must therefore be a no-op on the record,
        # so adding the option cannot quietly change what is committed.
        out = self.root / "books.json"
        offline_main(
            ["--user-id", _A_USER_ID, "--output", str(out)],
            fetch=_fetch_returning(_FIXTURE),
        )
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("._SX318_.", records[0]["cover"])

    def test_a_successful_scan_writes_the_record_and_confirms_the_book(self) -> None:
        out = self.root / "books.json"
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = offline_main(
                ["--user-id", _A_USER_ID, "--output", str(out)],
                fetch=_fetch_returning(_FIXTURE),
            )
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        self.assertIn("The Later Read", buffer.getvalue())

    def test_the_written_record_has_exactly_the_bookrecord_keys(self) -> None:
        out = self.root / "books.json"
        offline_main(
            ["--user-id", _A_USER_ID, "--output", str(out)],
            fetch=_fetch_returning(_FIXTURE),
        )
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)
        self.assertEqual(
            set(records[0].keys()),
            {"title", "author", "year", "rating", "read", "cover"},
        )
        self.assertEqual(records[0]["title"], "The Later Read")
        # No filesystem path and no user id anywhere in the written bytes.
        raw = out.read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn(_A_USER_ID, raw)

    def test_the_record_is_written_atomically_no_temp_file_survives(self) -> None:
        out = self.root / "books.json"
        code = offline_main(
            ["--user-id", _A_USER_ID, "--output", str(out)],
            fetch=_fetch_returning(_FIXTURE),
        )
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        # The record is never truncated in place: a concurrent reader (the page,
        # mid-refresh) would see a half-written file. The temp file this write used
        # is renamed away, not left beside the real one.
        leftovers = list(self.root.glob("*.tmp"))
        self.assertEqual(leftovers, [], f"temp file(s) left behind: {leftovers}")

    def test_a_feed_with_no_dateable_book_exits_1_and_never_names_the_user_id(
        self,
    ) -> None:
        out = self.root / "books.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = offline_main(
                ["--user-id", _A_USER_ID, "--output", str(out)],
                fetch=_fetch_returning(_NO_BOOK_FEED),
            )
        self.assertEqual(code, 1)
        self.assertFalse(out.exists(), "no book means no file, not an empty one")
        # The diagnostic is stderr's (tests/test_book_scan_failures.py pins the
        # stream); the user id must appear on neither.
        self.assertIn("no book", stderr.getvalue().lower())
        self.assertNotIn(_A_USER_ID, stdout.getvalue() + stderr.getvalue())

    def test_a_fetch_raising_oserror_exits_2_and_leaves_the_old_record_untouched(
        self,
    ) -> None:
        out = self.root / "books.json"
        old_content = '[{"title": "Old Book", "read": "2020-01-01"}]'
        out.write_text(old_content, encoding="utf-8", newline="\n")

        def explode(url: str, timeout: float) -> bytes:
            del url, timeout
            raise OSError("network unreachable")

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = offline_main(
                ["--user-id", _A_USER_ID, "--output", str(out)], fetch=explode
            )
        self.assertEqual(code, 2)
        # A failed refresh keeps the last good record: byte-for-byte unchanged, not
        # merely still present.
        self.assertEqual(out.read_text(encoding="utf-8"), old_content)
        self.assertNotIn(_A_USER_ID, stdout.getvalue() + stderr.getvalue())

    def test_a_fetch_returning_malformed_xml_exits_2(self) -> None:
        out = self.root / "books.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = offline_main(
                ["--user-id", _A_USER_ID, "--output", str(out)],
                fetch=_fetch_returning(_MALFORMED_XML),
            )
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())
        self.assertNotIn(_A_USER_ID, stdout.getvalue() + stderr.getvalue())

    def test_feed_file_reads_a_local_file_and_never_calls_fetch(self) -> None:
        feed_path = self.root / "shelf.xml"
        feed_path.write_bytes(_FIXTURE)
        out = self.root / "books.json"

        code = offline_main(
            [
                "--user-id",
                _A_USER_ID,
                "--output",
                str(out),
                "--feed-file",
                str(feed_path),
            ],
            fetch=_explode_fetch,
        )
        self.assertEqual(code, 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(records[0]["title"], "The Later Read")

    def test_a_missing_feed_file_exits_2(self) -> None:
        out = self.root / "books.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = offline_main(
                [
                    "--user-id",
                    _A_USER_ID,
                    "--output",
                    str(out),
                    "--feed-file",
                    str(self.root / "does-not-exist.xml"),
                ],
                fetch=_explode_fetch,
            )
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())

    def test_key_absent_omits_key_from_the_built_url(self) -> None:
        # BOOK_SCAN_KEY is unset (setUp's cleared it) and --key is not given, so the
        # URL that actually reaches `fetch` must carry no key at all.
        out = self.root / "books.json"
        fetch, urls = _capturing_fetch(_FIXTURE)
        code = offline_main(
            ["--user-id", _A_USER_ID, "--output", str(out)], fetch=fetch
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(urls), 1)
        self.assertNotIn("key=", urls[0])

    def test_explicit_key_option_reaches_the_built_url(self) -> None:
        out = self.root / "books.json"
        fetch, urls = _capturing_fetch(_FIXTURE)
        code = offline_main(
            ["--user-id", _A_USER_ID, "--key", _A_KEY, "--output", str(out)],
            fetch=fetch,
        )
        self.assertEqual(code, 0)
        self.assertIn(f"key={_A_KEY}", urls[0])

    def test_book_scan_key_env_var_supplies_the_key_when_option_absent(self) -> None:
        # BOOK_SCAN_KEY exists precisely so the key never needs to appear in a shell
        # history or a scheduled command line; this is what proves the fallback.
        out = self.root / "books.json"
        fetch, urls = _capturing_fetch(_FIXTURE)
        with patch.dict(os.environ, {"BOOK_SCAN_KEY": _A_KEY}):
            code = offline_main(
                ["--user-id", _A_USER_ID, "--output", str(out)], fetch=fetch
            )
        self.assertEqual(code, 0)
        self.assertIn(f"key={_A_KEY}", urls[0])

    def test_explicit_key_option_wins_over_the_env_var(self) -> None:
        out = self.root / "books.json"
        fetch, urls = _capturing_fetch(_FIXTURE)
        with patch.dict(os.environ, {"BOOK_SCAN_KEY": "env-key-must-lose"}):
            code = offline_main(
                ["--user-id", _A_USER_ID, "--key", _A_KEY, "--output", str(out)],
                fetch=fetch,
            )
        self.assertEqual(code, 0)
        self.assertIn(f"key={_A_KEY}", urls[0])
        self.assertNotIn("env-key-must-lose", urls[0])

    def test_no_message_ever_echoes_the_key(self) -> None:
        out = self.root / "books.json"
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = offline_main(
                ["--user-id", _A_USER_ID, "--key", _A_KEY, "--output", str(out)],
                fetch=_fetch_returning(_FIXTURE),
            )
        self.assertEqual(code, 0)
        self.assertNotIn(_A_KEY, buffer.getvalue())


class SamplesPairTest(unittest.TestCase):
    """`samples/expected_books.json` must never drift from a fresh --feed-file run
    against the committed fixture.
    """

    def test_the_committed_sample_matches_a_fresh_run(self) -> None:
        samples_dir = _TOOL_ROOT / "samples"
        expected = json.loads(
            (samples_dir / "expected_books.json").read_text(encoding="utf-8")
        )
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "books.json"
            code = offline_main(
                [
                    "--user-id",
                    _A_USER_ID,
                    "--output",
                    str(out),
                    "--feed-file",
                    str(_TOOL_ROOT / "fixtures" / "shelf.xml"),
                ],
                fetch=_explode_fetch,
            )
            self.assertEqual(code, 0)
            fresh = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(fresh, expected)


if __name__ == "__main__":
    unittest.main()
