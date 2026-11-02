"""Newest-album discovery over a synthetic Artist/Album tree.

Every test builds its own tree under tmp_path, so nothing here reads the real
music library or depends on this machine having one.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import posix_filesystem_only  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import AlbumRef  # noqa: E402 - import follows the sys.path bootstrap above

# folder_scan is album_scan's private src/ module. Importing album_scan above ran
# its sys.path bootstrap as a side effect, which is what puts src/ on sys.path, the
# same way tests/test_live_music.py reaches recently_added. Imported directly (not
# through album_scan's re-export) because test_each_album_is_stat_ed_once below
# monkeypatches folder_scan._birth_time, a private name album_scan has no reason to
# re-export.
import folder_scan  # noqa: E402 - import follows the sys.path bootstrap above


# os.geteuid exists only off Windows, and the gate type-checks for win32 too, so the
# root check is a getattr rather than a bare attribute; root can list a mode-000
# directory, so the unreadable-folder tests skip rather than fail under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


def make_album(root: Path, artist: str, album: str, *, added: float) -> Path:
    """Create root/artist/album and force its birth-time-adjacent stamps to `added`."""
    path = root / artist / album
    path.mkdir(parents=True)
    os.utime(path, (added, added))
    return path


class NewestAlbumTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_an_empty_library_has_no_newest_album(self) -> None:
        self.assertEqual(folder_scan.newest_albums(self.root, 1), [])

    def test_a_missing_library_root_is_an_operator_error(self) -> None:
        with self.assertRaises(NotADirectoryError):
            folder_scan.newest_albums(self.root / "does-not-exist", 1)

    def test_the_missing_library_root_message_omits_the_absolute_path(self) -> None:
        # Nothing this tool writes, an error message included, may carry a
        # filesystem path. src/folder_scan.py deliberately raises with
        # `library_root.name`, never the full `library_root`; this pins that choice so
        # a refactor to an f-string embedding the whole Path would fail here instead
        # of leaking an absolute machine path silently.
        missing = self.root / "does-not-exist"
        with self.assertRaises(NotADirectoryError) as caught:
            folder_scan.newest_albums(missing, 1)
        self.assertNotIn(str(missing), str(caught.exception))

    @posix_filesystem_only
    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 directory")
    def test_an_unreadable_library_root_is_named_without_its_path(self) -> None:
        # A root that exists but cannot be listed is the same operator error as a
        # missing one, and the PermissionError the listing raises carries the whole
        # path in its own text, so it is re-raised naming the folder alone, as the
        # missing-root case above does.
        locked = self.root / "locked"
        locked.mkdir()
        self.addCleanup(os.chmod, locked, 0o755)
        os.chmod(locked, 0o000)
        with self.assertRaises(PermissionError) as caught:
            folder_scan.newest_albums(locked, 1)
        self.assertEqual(str(caught.exception), "library root is not readable: locked")

    def test_the_only_album_is_the_newest(self) -> None:
        make_album(self.root, "Artist One", "First Record", added=1_000_000)
        found = folder_scan.newest_albums(self.root, 1)
        self.assertEqual(len(found), 1)
        self.assertIsInstance(found[0], AlbumRef)
        self.assertEqual(found[0].artist, "Artist One")
        self.assertEqual(found[0].album, "First Record")

    def test_the_most_recently_added_album_wins(self) -> None:
        make_album(self.root, "Older", "Older Record", added=1_000_000)
        make_album(self.root, "Newer", "Newer Record", added=2_000_000)
        found = folder_scan.newest_albums(self.root, 1)
        self.assertEqual(found[0].album, "Newer Record")

    def test_a_loose_file_at_artist_level_is_not_an_album(self) -> None:
        (self.root / "stray.mp3").write_bytes(b"")
        self.assertEqual(folder_scan.newest_albums(self.root, 1), [])

    def test_a_loose_file_at_album_level_is_not_an_album(self) -> None:
        # "AAA_stray.mp3" sorts before "Real Record", so the loop must reject the
        # stray file and continue on to the real album directory that follows it,
        # the only way to exercise the loop-continues branch after a rejected entry.
        make_album(self.root, "Artist", "Real Record", added=1_000_000)
        (self.root / "Artist" / "AAA_stray.mp3").write_bytes(b"")
        found = folder_scan.newest_albums(self.root, 1)
        self.assertEqual(found[0].album, "Real Record")

    def test_an_artist_folder_with_no_albums_is_skipped(self) -> None:
        (self.root / "Empty Artist").mkdir()
        make_album(self.root, "Real Artist", "Real Record", added=1_000_000)
        found = folder_scan.newest_albums(self.root, 1)
        self.assertEqual(found[0].artist, "Real Artist")

    def test_dot_directories_are_ignored(self) -> None:
        make_album(self.root, ".hidden", "Not A Record", added=9_000_000)
        make_album(self.root, "Artist", "Record", added=1_000_000)
        found = folder_scan.newest_albums(self.root, 1)
        self.assertEqual(found[0].artist, "Artist")

    def test_a_tie_resolves_deterministically_by_path(self) -> None:
        make_album(self.root, "B Artist", "Record", added=5_000_000)
        make_album(self.root, "A Artist", "Record", added=5_000_000)
        first = folder_scan.newest_albums(self.root, 1)
        second = folder_scan.newest_albums(self.root, 1)
        self.assertEqual(first, second)

    def _lock_artist_folder(self, name: str) -> Path:
        """An artist folder the scan cannot list, restored to listable on cleanup."""
        locked = self.root / name
        locked.mkdir()
        self.addCleanup(os.chmod, locked, 0o755)
        os.chmod(locked, 0o000)
        return locked

    @posix_filesystem_only
    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 directory")
    def test_an_unreadable_artist_folder_is_skipped_and_the_scan_goes_on(self) -> None:
        # "Locked" sorts before "Readable", so the scan has to pass over the folder it
        # cannot list and carry on to the album behind it: one unreadable folder
        # costs the scan one entry, not the whole refresh.
        self._lock_artist_folder("Locked")
        make_album(self.root, "Readable", "Record", added=1_000_000)
        found = folder_scan.newest_albums(self.root, 1)
        self.assertEqual(found[0].artist, "Readable")

    @posix_filesystem_only
    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 directory")
    def test_an_unreadable_artist_folder_is_reported_by_name_to_on_skip(self) -> None:
        # By name, never a path: whatever the caller prints from this must obey the
        # same rule the missing-root message does.
        self._lock_artist_folder("Locked")
        make_album(self.root, "Readable", "Record", added=1_000_000)
        skipped: list[str] = []
        folder_scan.newest_albums(self.root, 1, on_skip=skipped.append)
        self.assertEqual(skipped, ["Locked"])

    def test_two_albums_added_the_same_day_rank_by_time_of_day(self) -> None:
        """Music.app ranks by its full "date added"; the folder scan must not lose
        that precision to a calendar date. "A Artist" sorts first by name, so a
        ranking on the date alone (a tie, which the stable sort resolves in
        `library_walk.album_dirs`' name order) would pick it: the wrong answer, since
        "B Artist" was added an hour later the same day.
        """
        noon = dt.datetime(2026, 1, 15, 12, 0)
        born = {"A Artist": noon, "B Artist": noon + dt.timedelta(hours=1)}
        for artist, added in born.items():
            make_album(self.root, artist, "Record", added=added.timestamp())
        # The two times go in through `_birth_time`, the stamp the ranking reads,
        # rather than through `make_album`'s `os.utime` alone: on Windows that
        # stamp is the folder's creation time, which `os.utime` cannot set, and two
        # folders made back to back there can share one creation time to the
        # clock's resolution, which is the very tie this case must not have.
        with mock.patch.object(
            folder_scan, "_birth_time", lambda path: born[path.parent.name]
        ):
            found = folder_scan.newest_albums(self.root, 1)
        self.assertEqual(found[0].artist, "B Artist")

    def test_added_is_a_date_not_a_timestamp(self) -> None:
        make_album(self.root, "Artist", "Record", added=1_000_000)
        found = folder_scan.newest_albums(self.root, 1)
        self.assertIsInstance(found[0].added, dt.date)

    def test_a_count_of_two_returns_the_two_newest_folders(self) -> None:
        make_album(self.root, "Oldest Artist", "Oldest", added=1_000_000)
        make_album(self.root, "Middle Artist", "Middle", added=2_000_000)
        make_album(self.root, "Newest Artist", "Newest", added=3_000_000)
        refs = folder_scan.newest_albums(self.root, 2)
        self.assertEqual([r.album for r in refs], ["Newest", "Middle"])

    def test_asking_past_the_end_returns_every_album(self) -> None:
        make_album(self.root, "Oldest Artist", "Oldest", added=1_000_000)
        make_album(self.root, "Middle Artist", "Middle", added=2_000_000)
        make_album(self.root, "Newest Artist", "Newest", added=3_000_000)
        refs = folder_scan.newest_albums(self.root, 99)
        self.assertEqual(len(refs), 3)

    def test_an_empty_library_is_an_empty_list(self) -> None:
        self.assertEqual(folder_scan.newest_albums(self.root, 3), [])

    def test_each_album_is_stat_ed_once(self) -> None:
        """Guards the decorate-sort-slice: each folder is stat-ed exactly once. A
        `max()` keyed on `_birth_time` stats the winner twice, which at N results
        would be N extra stat calls.
        """
        make_album(self.root, "Oldest Artist", "Oldest", added=1_000_000)
        make_album(self.root, "Middle Artist", "Middle", added=2_000_000)
        make_album(self.root, "Newest Artist", "Newest", added=3_000_000)
        calls: list[Path] = []
        original = folder_scan._birth_time

        # A `def`, not a `lambda p: calls.append(p) or original(p)` one-liner: mypy's
        # func-returns-value check flags `list.append(...) or ...` because `append`
        # is declared to return `None` and using that call's value is almost always a
        # mistake.
        def fake_birth_time(path: Path) -> dt.datetime:
            calls.append(path)
            return original(path)

        folder_scan._birth_time = fake_birth_time  # type: ignore[assignment]
        try:
            folder_scan.newest_albums(self.root, 3)
        finally:
            folder_scan._birth_time = original  # type: ignore[assignment]
        self.assertEqual(len(calls), len(set(calls)))


if __name__ == "__main__":
    unittest.main()
