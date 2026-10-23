"""The Music.app arms of `main`, driven through a FAKE apple-music-toolbox on disk.

The suites marked `needs_toolbox` drive the real toolbox's own parsing of a faked
`osascript` reply, so they skip wherever the toolbox is not installed -- a standalone
clone of this project among them. The arms they alone would reach still have to run
there: Music.app reporting no album at all, and Music.app's facts reaching the artwork
matcher as its tiebreakers. These cases write a stand-in toolbox into a temporary
folder instead, two real modules answering from constants, and point `--toolbox` at
it, so the whole locate-import-call path runs for real with no toolbox installed.

The stand-in's modules take the real ones' import names, so each case lifts any
already-imported `recently_added` / `album_stats` out of `sys.modules` and puts them
back afterwards, and restores `sys.path`: a later suite driving the real toolbox must
never be handed a stand-in. Nothing here spawns `osascript` or reaches the network.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import no_music, no_store  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import main  # noqa: E402 - import follows the sys.path bootstrap above

# album_scan's bootstrap above is what puts its private src/ on sys.path.
import apple_source  # noqa: E402 - import follows the sys.path bootstrap above

#: The toolbox's two module names, which the stand-in takes too.
_TOOLBOX_MODULES = ("recently_added", "album_stats")

#: Writable by this user alone, on every stand-in path: `untrusted_reason` refuses a toolbox any
#: other account could write to, whatever the umask the suite runs under.
_FOLDER_MODE = 0o755
_MODULE_MODE = 0o644

#: The stand-in `recently_added`: the toolbox's call shape, answering from `NEWEST`.
_RECENTLY_ADDED = '''"""A stand-in recently_added: answers from NEWEST, never Music.app."""
import datetime as dt
from typing import NamedTuple


class AlbumRef(NamedTuple):
    artist: str
    album: str
    added: dt.date


def newest_albums_from_music(count, *, run):
    return [
        AlbumRef(artist, album, dt.date.fromisoformat(added))
        for artist, album, added in NEWEST
    ][:count]
'''

#: The stand-in `album_stats`: the toolbox's call shape, answering from `FACTS`.
_ALBUM_STATS = '''"""A stand-in album_stats: answers from FACTS, never Music.app."""
from typing import NamedTuple


class AlbumFacts(NamedTuple):
    tracks: int
    favorited: int
    year: object


def album_facts(*, artist, album, run, min_stars=None):
    return AlbumFacts(*FACTS)
'''

#: The `fetch` seam's shape: (url, timeout_seconds) -> response body.
Fetch = Callable[[str, float], bytes]

#: One album, by the names the store result below carries for its artist.
_NEWEST = [("An Artist", "A Record", "2026-08-20")]

#: A store result whose NAME is not the album's: only Music.app's year AND track count
#: agreeing with it can admit it (`pick_album`'s second door).
_RENAMED_RESULT = {
    "artistName": "An Artist",
    "collectionName": "Another Title",
    "collectionId": 1,
    "releaseDate": "1994-01-01T00:00:00Z",
    "trackCount": 10,
    "artworkUrl100": "https://example.com/a/100x100bb.jpg",
}


def _holds_toolbox_module(entry: str) -> bool:
    """Whether the `sys.path` entry holds a module by one of the toolbox's names."""
    return any((Path(entry) / f"{name}.py").is_file() for name in _TOOLBOX_MODULES)


def _write_module(path: Path, source: str) -> None:
    """Write one stand-in module, mode set explicitly so the trust check passes."""
    path.parent.mkdir(parents=True)
    path.parent.chmod(_FOLDER_MODE)
    path.write_bytes(source.encode("utf-8"))
    path.chmod(_MODULE_MODE)


def write_fake_toolbox(
    folder: Path, *, newest: list[tuple[str, str, str]], facts: tuple[int, int, int]
) -> Path:
    """A stand-in toolbox at `folder`: Music.app's newest albums and one album's facts."""
    folder.mkdir()
    folder.chmod(_FOLDER_MODE)
    _write_module(
        folder / "recently_added" / "recently_added.py",
        f"{_RECENTLY_ADDED}\nNEWEST = {newest!r}\n",
    )
    _write_module(
        folder / "album_stats" / "album_stats.py",
        f"{_ALBUM_STATS}\nFACTS = {facts!r}\n",
    )
    return folder


class _FakeToolboxCase(unittest.TestCase):
    """A temporary folder per case, with the toolbox's import names set aside."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.library = self.root / "library"
        self.library.mkdir()
        saved_path = list(sys.path)
        self.addCleanup(sys.path.__setitem__, slice(None), saved_path)
        # A real toolbox's folders an earlier suite put on sys.path would be found
        # ahead of the stand-in's, which `import_toolbox` APPENDS: set them aside too.
        sys.path[:] = [
            entry for entry in saved_path if not _holds_toolbox_module(entry)
        ]
        saved: dict[str, ModuleType] = {}
        for name in _TOOLBOX_MODULES:
            module = sys.modules.pop(name, None)
            if module is not None:
                saved[name] = module
        self.addCleanup(self._restore_modules, saved)

    @staticmethod
    def _restore_modules(saved: dict[str, ModuleType]) -> None:
        """Drop the stand-ins this case imported and put back whatever was there."""
        for name in _TOOLBOX_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)

    def _main(
        self, kit: Path, *extra: str, fetch: Fetch = no_store
    ) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "--library",
                    str(self.library),
                    "--source",
                    "music",
                    "--toolbox",
                    str(kit),
                    *extra,
                ],
                fetch=fetch,
                run=no_music,
            )
        return code, stdout.getvalue(), stderr.getvalue()


class NoMusicAlbumTest(_FakeToolboxCase):
    def test_music_reporting_no_album_exits_1_naming_music_not_the_library(
        self,
    ) -> None:
        kit = write_fake_toolbox(self.root / "kit", newest=[], facts=(0, 0, 0))
        out = self.root / "album.json"
        code, stdout, stderr = self._main(kit, "--output", str(out), "--no-artwork")
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("album_scan: Music.app reported no recently added album", stderr)
        # --source music never consults the library, so naming it would misdiagnose.
        self.assertNotIn(self.library.name, stderr)
        self.assertFalse(out.exists())


class FactsAsTiebreakersTest(_FakeToolboxCase):
    """Music.app's year and track count are what admit a store result by another name."""

    def _artwork(self, facts: tuple[int, int, int]) -> object:
        kit = write_fake_toolbox(self.root / "kit", newest=_NEWEST, facts=facts)
        out = self.root / "album.json"

        def fetch(url: str, timeout: float) -> bytes:
            del url, timeout  # unused: one answer for every search
            return json.dumps({"results": [_RENAMED_RESULT]}).encode("utf-8")

        code, _, _ = self._main(kit, "--output", str(out), fetch=fetch)
        self.assertEqual(code, 0)
        record: dict[str, object] = json.loads(out.read_text(encoding="utf-8"))[0]
        return record["artwork"]

    def test_agreeing_facts_admit_a_store_result_by_another_name(self) -> None:
        artwork = self._artwork((10, 2, 1994))
        self.assertIsInstance(artwork, str)
        self.assertTrue(str(artwork).startswith("https://example.com/a/"))

    def test_disagreeing_facts_leave_that_result_refused(self) -> None:
        # The control: the same result, with a year Music.app does not report, shows
        # the admission above came from the facts and not from the name.
        self.assertIsNone(self._artwork((10, 2, 1969)))


class ImportToolboxTest(_FakeToolboxCase):
    def test_importing_the_same_toolbox_twice_appends_each_folder_once(self) -> None:
        kit = write_fake_toolbox(self.root / "kit", newest=_NEWEST, facts=(1, 0, 1))
        first = apple_source.import_toolbox(kit)
        second = apple_source.import_toolbox(kit)
        self.assertIs(first.album_stats, second.album_stats)
        for name in _TOOLBOX_MODULES:
            with self.subTest(folder=name):
                self.assertEqual(sys.path.count(str(kit / name)), 1)


if __name__ == "__main__":
    unittest.main()
