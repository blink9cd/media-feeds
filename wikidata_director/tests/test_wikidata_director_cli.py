"""Grouped suite: the CLI's exit codes, its streams, and its argument surface.

`fetch` is injected here just as it is in the parent suite, so nothing in this file
reaches the network either.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))

import wikidata_director  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - reachable only via wikidata_director's bootstrap


def _one_director_fetch(
    recorder: list[float] | None = None,
) -> wikidata_director.Fetcher:
    """A fetch answering the three requests for a single-director film.

    When `recorder` is given, every call's timeout is appended to it, which is how the
    `--timeout` pass-through is proven without a real socket.
    """
    pages: dict[str, object] = {
        "list=search": {"query": {"search": [{"title": "Q1"}]}},
        "wbgetclaims": {
            "claims": {"P57": [{"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}]}
        },
        "wbgetentities": {
            "entities": {"Q2": {"labels": {"en": {"value": "Bong Joon-ho"}}}}
        },
    }

    def fetch(url: str, timeout: float) -> bytes:
        if recorder is not None:
            recorder.append(timeout)
        for fragment, payload in pages.items():
            if fragment in url:
                return json.dumps(payload).encode("utf-8")
        raise AssertionError(f"unexpected url: {url}")

    return fetch


def _no_item_fetch(_url: str, _timeout: float) -> bytes:
    """A fetch whose search answers with no Wikidata item at all."""
    return b'{"query": {"search": []}}'


class CliTest(unittest.TestCase):
    def _run(
        self, argv: list[str], fetch: wikidata_director.Fetcher
    ) -> tuple[int, str, str]:
        """Run `main` with its streams captured; returns (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wikidata_director.main(argv, fetch=fetch)
        return code, out.getvalue(), err.getvalue()

    def test_a_resolved_director_is_printed_to_stdout_and_exits_zero(self) -> None:
        code, out, err = self._run(["--tmdb-id", "496243"], _one_director_fetch())
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "Bong Joon-ho")
        # The name alone on stdout, so the tool composes in a pipeline; anything
        # conversational belongs on stderr.
        self.assertEqual(err, "")

    def test_no_director_exits_one_and_says_so_on_stderr(self) -> None:
        code, out, err = self._run(["--tmdb-id", "999999999"], _no_item_fetch)
        self.assertEqual(code, wikidata_director.EXIT_NO_DIRECTOR)
        self.assertEqual(out, "", "stdout must stay empty so a pipeline reads nothing")
        self.assertIn("999999999", err)

    def test_the_exit_codes_are_the_literal_process_codes_the_doc_states(self) -> None:
        # Literals, not the module constants: a test comparing against EXIT_OK would
        # follow a changed constant and never notice the documented contract moved.
        self.assertEqual(wikidata_director.EXIT_OK, 0)
        self.assertEqual(wikidata_director.EXIT_NO_DIRECTOR, 1)
        resolved, _out, _err = self._run(["--tmdb-id", "496243"], _one_director_fetch())
        self.assertEqual(resolved, 0)
        unresolved, _out, _err = self._run(["--tmdb-id", "999999999"], _no_item_fetch)
        self.assertEqual(unresolved, 1)

    def test_the_tmdb_id_is_required(self) -> None:
        with (
            self.assertRaises(SystemExit) as raised,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            wikidata_director.build_parser().parse_args([])
        self.assertEqual(raised.exception.code, 2)

    def test_every_argument_is_a_named_option(self) -> None:
        # No positionals. Asserted structurally rather than by reading the usage
        # string, which would pass on a renamed positional.
        for action in wikidata_director.build_parser()._actions:
            with self.subTest(dest=action.dest):
                self.assertTrue(
                    all(opt.startswith("-") for opt in action.option_strings)
                    and action.option_strings,
                    f"{action.dest} is not a named option",
                )

    def test_the_timeout_reaches_every_request(self) -> None:
        recorder: list[float] = []
        code, _out, _err = self._run(
            ["--tmdb-id", "1", "--timeout", "2.5"], _one_director_fetch(recorder)
        )
        self.assertEqual(code, 0)
        self.assertEqual(recorder, [2.5, 2.5, 2.5])

    def test_the_default_timeout_is_the_modules_own_constant(self) -> None:
        # Derived, not restated: a literal here would drift from the module.
        parsed = wikidata_director.build_parser().parse_args(["--tmdb-id", "1"])
        self.assertEqual(parsed.timeout, wikidata_director.LOOKUP_TIMEOUT_SECONDS)

    def test_the_help_shows_defaults_and_states_the_prerequisites(self) -> None:
        text = wikidata_director.build_parser().format_help()
        self.assertIn(f"(default: {wikidata_director.LOOKUP_TIMEOUT_SECONDS})", text)
        self.assertIn("Prerequisites", text)
        self.assertIn("network", text)

    def test_the_parser_uses_the_shared_formatter(self) -> None:
        # Pinned as a binding, not a rendering: the gate's interpreter already drops
        # the bogus "(default: None)" on a required option, so a --help assertion
        # would pass with the stock formatter back in place ("The version trap" in
        # help_formatter.md). The floor is 3.12, below that fix.
        formatter_class = wikidata_director.build_parser().formatter_class
        self.assertIs(formatter_class, help_formatter.ReqAwareDefaultsFormatter)


if __name__ == "__main__":
    unittest.main()
