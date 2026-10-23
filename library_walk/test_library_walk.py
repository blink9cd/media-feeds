"""Tests for library_walk: the albums of a library are its Artist/Album folders.

Every test builds its own tree in a temporary directory, so nothing here reads a real
music library or depends on this machine having one.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import library_walk

# os.geteuid exists only off Windows, and the gate type-checks for win32 too, so the
# root check is a getattr rather than a bare attribute; root can list a mode-000
# directory, so the unreadable-folder test skips rather than fails under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


class AlbumDirsTest(unittest.TestCase):
    """`album_dirs` lists exactly the non-hidden folders two levels down, sorted."""

    def setUp(self) -> None:
        """Give each test an empty library folder of its own."""
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def _album(self, *parts: str) -> Path:
        """Create the folder *parts* under the library and return it."""
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True)
        return path

    def _names(self) -> list[str]:
        """The walk's albums as ``Artist/Album`` strings."""
        return [
            path.relative_to(self.root).as_posix()
            for path in library_walk.album_dirs(self.root)
        ]

    def test_an_empty_library_has_no_albums(self) -> None:
        self.assertEqual(library_walk.album_dirs(self.root), [])

    def test_albums_are_listed_by_artist_then_album(self) -> None:
        self._album("B Artist", "Second")
        self._album("A Artist", "Zed")
        self._album("A Artist", "Alpha")
        self.assertEqual(
            self._names(), ["A Artist/Alpha", "A Artist/Zed", "B Artist/Second"]
        )

    def test_hidden_artist_and_album_folders_are_passed_over(self) -> None:
        self._album(".Trash", "Gone")
        self._album("Artist", ".album_lyrics")
        self._album("Artist", ".lyricsmith")
        self._album("Artist", "Kept")
        self.assertEqual(self._names(), ["Artist/Kept"])

    def test_files_at_the_wrong_depth_are_not_albums(self) -> None:
        (self.root / "stray.mp3").write_bytes(b"")
        artist = self.root / "Artist"
        artist.mkdir()
        (artist / "loose.mp3").write_bytes(b"")
        self._album("Artist", "Record")
        self.assertEqual(self._names(), ["Artist/Record"])

    def test_a_multi_disc_album_is_one_album_not_one_per_disc(self) -> None:
        self._album("Artist", "Double", "Disc 1")
        self._album("Artist", "Double", "Disc 2")
        self.assertEqual(self._names(), ["Artist/Double"])

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 directory")
    def test_an_unreadable_artist_folder_is_reported_by_name_and_passed_over(
        self,
    ) -> None:
        locked = self._album("Locked")
        self._album("Readable", "Record")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        skipped: list[str] = []
        found = library_walk.album_dirs(self.root, skipped.append)
        self.assertEqual(skipped, ["Locked"])
        self.assertEqual([path.name for path in found], ["Record"])

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 directory")
    def test_an_unreadable_artist_folder_is_silent_without_a_reporter(self) -> None:
        locked = self._album("Locked")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        self.assertEqual(library_walk.album_dirs(self.root), [])

    def test_the_default_library_is_the_music_albums_folder_under_home(self) -> None:
        self.assertEqual(library_walk.DEFAULT_LIBRARY, "~/Music/Albums")


if __name__ == "__main__":
    unittest.main()
