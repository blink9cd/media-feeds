"""`--count`'s CLI surface on the folder path: what it accepts, refuses, and writes.

Every test that calls `main` passes `--source folder --no-favorites --no-artwork`, so
nothing below reaches the `run` seam or the network, and both seams are injected
with fakes that raise, so dropping either flag fails the test rather than spawning a
real `osascript`. This file is scoped to the folder-scan path's own `--count` surface
(below-one, non-integer, the default, an explicit value at, below, and past the
library's size). The Music.app path takes a `count` above one too,
because its per-track AppleScript rows are collapsed to per-album ones before ranking
(`newest_albums_from_music`, in `recently_added.py`), but the CLI wiring that
threads `--count` through to it is exercised in `test_source_selection.py`, which owns
every other `--source music` case and injects the `run` seam this file never reaches.

Named `test_album_scan_count.py` (not `test_count.py`): one mypy pass across every
tool folder at once exits 2 on duplicate module basenames, so every test module here
carries a basename unique across the project.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import no_music, no_store  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import build_parser, main  # noqa: E402 - import follows the sys.path bootstrap above


def _make_album(root: Path, artist: str, album: str, *, added: float) -> Path:
    """Create root/artist/album and force its birth-time-adjacent stamps to `added`.

    A private copy of `tests/test_discovery.py`'s own `make_album`, not a shared
    import: each grouped test file in this suite keeps its own small fixtures rather
    than reaching across files for one five-line helper.
    """
    path = root / artist / album
    path.mkdir(parents=True)
    os.utime(path, (added, added))
    return path


class CountOptionTest(unittest.TestCase):
    """--count's surface: what it accepts, what it refuses, and how many it writes."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.out = self.root / "album.json"
        self.addCleanup(self._tmp.cleanup)

    def _run(self, extra_args: list[str]) -> int:
        """Invoke `main` against `self.root`, fully offline.

        A --count value argparse rejects raises `SystemExit` rather than returning
        (the same path the real `if __name__ == "__main__":` entry point relies on),
        so this normalizes both outcomes into the plain int every other assertion
        below compares against.
        """
        try:
            with redirect_stderr(io.StringIO()):
                return main(
                    [
                        "--library",
                        str(self.root),
                        "--output",
                        str(self.out),
                        "--no-artwork",
                        "--no-favorites",
                        "--source",
                        "folder",
                        *extra_args,
                    ],
                    fetch=no_store,
                    run=no_music,
                )
        except SystemExit as exc:
            code = exc.code
            # argparse's own usage-error exit always passes an int status
            # (ArgumentParser.exit(2, message)); this narrows the type for mypy
            # rather than guessing at a str/None case that can't occur on this path.
            assert isinstance(code, int)
            return code

    def test_a_count_below_one_is_a_usage_error_and_writes_nothing(self) -> None:
        for bad in ("0", "-1"):
            with self.subTest(count=bad):
                code = self._run(["--count", bad])
                self.assertEqual(code, 2)
                self.assertFalse(self.out.exists())

    def test_a_non_integer_count_is_the_same_usage_error(self) -> None:
        self.assertEqual(self._run(["--count", "two"]), 2)
        self.assertFalse(self.out.exists())

    def test_the_record_file_is_a_json_array_even_at_the_default_count(self) -> None:
        _make_album(self.root, "An Artist", "A Record", added=1_000_000)
        self.assertEqual(self._run([]), 0)
        self.assertIsInstance(json.loads(self.out.read_text(encoding="utf-8")), list)

    def test_a_valid_explicit_count_selects_that_many_albums_newest_first(self) -> None:
        # Exercises `positive_int`'s success path end to end through the parser: the
        # cases above are either refused or never call it (argparse does not apply
        # `type` to a non-string default), so this is what proves a well-formed
        # --count value actually reaches `main` and `newest_albums`, as well as that a
        # bad one is refused.
        _make_album(self.root, "Oldest Artist", "Oldest", added=1_000_000)
        _make_album(self.root, "Middle Artist", "Middle", added=2_000_000)
        _make_album(self.root, "Newest Artist", "Newest", added=3_000_000)
        self.assertEqual(self._run(["--count", "3"]), 0)
        records = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual([r["album"] for r in records], ["Newest", "Middle", "Oldest"])

    def test_an_explicit_count_of_one_is_accepted_and_writes_one_record(self) -> None:
        # The floor is "at least one", so one itself must pass: a floor written as
        # "more than one" refuses exactly this value, and no other test passes it
        # explicitly (the default is a non-string argparse never runs `type` on).
        _make_album(self.root, "Older Artist", "Older", added=1_000_000)
        _make_album(self.root, "Newer Artist", "Newer", added=2_000_000)
        self.assertEqual(self._run(["--count", "1"]), 0)
        records = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual([r["album"] for r in records], ["Newer"])

    def test_a_count_below_the_library_size_writes_only_that_many(self) -> None:
        # A parsed count that reached `newest_albums` as None would slice nothing
        # off and return every album; only a count smaller than the library can tell
        # that apart from the value getting through.
        _make_album(self.root, "Oldest Artist", "Oldest", added=1_000_000)
        _make_album(self.root, "Middle Artist", "Middle", added=2_000_000)
        _make_album(self.root, "Newest Artist", "Newest", added=3_000_000)
        self.assertEqual(self._run(["--count", "2"]), 0)
        records = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual([r["album"] for r in records], ["Newest", "Middle"])

    def test_asking_for_more_than_the_library_holds_returns_what_exists(self) -> None:
        _make_album(self.root, "An Artist", "A Record", added=1_000_000)
        self.assertEqual(self._run(["--count", "99"]), 0)
        records = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)

    def test_count_defaults_to_one(self) -> None:
        self.assertEqual(build_parser().parse_args([]).count, 1)


if __name__ == "__main__":
    unittest.main()
