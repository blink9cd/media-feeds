"""`--count`'s CLI surface: what it accepts, what it refuses, and how many it writes.

The `test_film_scan_` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above
import film_scan  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "diary.xml").read_bytes()

# Same reasoning as test_film_scan_cli.py's own _A_USERNAME: an obvious placeholder,
# chosen to look nothing like a real Letterboxd username and easy to spot if a
# message under test ever echoed it back.
_A_USERNAME = "a-placeholder-username"


def _fetch_returning(body: bytes) -> feed_read.Fetcher:
    """A fake `fetch` that ignores its arguments and returns a fixed body."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        return body

    return fetch


class CountOptionTest(unittest.TestCase):
    """--count's surface: what it accepts, and what it refuses.

    Every run passes `--no-director`: with `--count` above 1 these tests would
    otherwise drive the real `directors_for` lookup (the default `lookup=`), and no
    unit test reaches the network. The count/lookup-cost interaction itself is
    covered directly in `tests/test_parse.py`'s `TestDirector`
    (`test_only_the_sliced_films_are_looked_up`,
    `test_a_short_diary_costs_one_lookup_not_three`), so nothing here needs to
    re-inject a lookup fake just to prove the CLI surface.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, extra_args: list[str]) -> int:
        """Invoke `main` with a fixed --username and a faked feed fetch.

        A --count value argparse rejects raises `SystemExit` rather than
        returning -- the same path the real `if __name__ == "__main__":` entry
        point relies on -- so this normalizes both outcomes into the plain int
        every other CLI test in this suite already compares against.
        """
        try:
            return film_scan.main(
                ["--username", _A_USERNAME, "--no-director", *extra_args],
                fetch=_fetch_returning(_FIXTURE),
            )
        except SystemExit as exc:
            code = exc.code
            # argparse's own usage-error exit always passes an int status
            # (ArgumentParser.exit(2, message)); this narrows the type for
            # mypy rather than guessing at a str/None case that can't occur on
            # this path.
            assert isinstance(code, int)
            return code

    def test_a_count_below_one_is_a_usage_error_and_writes_nothing(self) -> None:
        for bad in ("0", "-1"):
            with self.subTest(count=bad):
                out = self.root / "films.json"
                code = self._run(["--count", bad, "--output", str(out)])
                self.assertEqual(code, 2)
                self.assertFalse(out.exists())

    def test_a_non_integer_count_is_the_same_usage_error(self) -> None:
        out = self.root / "films.json"
        self.assertEqual(self._run(["--count", "two", "--output", str(out)]), 2)
        self.assertFalse(out.exists())

    def test_the_record_file_is_a_json_array_even_at_the_default_count(self) -> None:
        out = self.root / "films.json"
        self.assertEqual(self._run(["--output", str(out)]), 0)
        self.assertIsInstance(json.loads(out.read_text(encoding="utf-8")), list)

    def test_a_valid_explicit_count_selects_that_many_films(self) -> None:
        # Exercises `positive_int`'s SUCCESS path end to end through the parser:
        # every case above only ever reaches its ArgumentTypeError branch, so
        # this is what proves a well-formed --count value actually reaches
        # `main` and `newest_films`, not just that a bad one is refused. The
        # fixture holds exactly 3 diary entries (1 list item is excluded), so
        # --count 3 is the largest value this fixture can satisfy in full.
        out = self.root / "films.json"
        self.assertEqual(self._run(["--count", "3", "--output", str(out)]), 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 3)

    def test_a_count_of_one_selects_exactly_one_film_from_a_longer_diary(self) -> None:
        # `positive_int`'s boundary, end to end. The fixture holds 3 entries, so a
        # `--count 1` that was refused (exit 2) or that fell through as None (every
        # entry written) both fail HERE -- the --count 3 case above cannot tell
        # either apart from a correct run.
        out = self.root / "films.json"
        self.assertEqual(self._run(["--count", "1", "--output", str(out)]), 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
