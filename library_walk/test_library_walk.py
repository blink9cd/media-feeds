"""Tests for library_walk: the albums of a library are its Artist/Album folders.

Every test builds its own tree in a temporary directory, so nothing here reads a real
music library or depends on this machine having one.
"""

from __future__ import annotations

import unittest
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import library_walk


def _mode_000_locks_a_folder() -> bool:
    """True when ``chmod(0)`` stops this process listing a folder.

    Root can list a mode-000 folder, and on Windows ``chmod`` sets only the read-only
    attribute, which never stops a listing (toolkit 182, round six). Probed rather than
    inferred from the platform, so the real-permission tests run wherever they can.
    """
    with TemporaryDirectory() as tmp:
        folder = Path(tmp) / "probe"
        folder.mkdir()
        folder.chmod(0)
        try:
            list(folder.iterdir())
        except PermissionError:
            return True
        finally:
            folder.chmod(0o755)
    return False


_MODE_000_LOCKS = _mode_000_locks_a_folder()
_NO_LOCK = "a mode-000 folder can still be listed here (root, or Windows)"


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

    @unittest.skipUnless(_MODE_000_LOCKS, _NO_LOCK)
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

    @unittest.skipUnless(_MODE_000_LOCKS, _NO_LOCK)
    def test_an_unreadable_artist_folder_is_silent_without_a_reporter(self) -> None:
        locked = self._album("Locked")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        self.assertEqual(library_walk.album_dirs(self.root), [])

    def test_a_listing_refused_by_permission_is_reported_and_passed_over(self) -> None:
        # The portable twin of the mode-000 test: the refusal is raised directly, so the
        # skip path is proven on a host where chmod cannot lock a folder.
        self._album("Locked")
        self._album("Readable", "Record")
        real_iterdir = Path.iterdir

        def refuse_locked(path: Path) -> Iterator[Path]:
            if path.name == "Locked":
                raise PermissionError(13, "Permission denied", str(path))
            return real_iterdir(path)

        skipped: list[str] = []
        with mock.patch.object(Path, "iterdir", refuse_locked):
            found = library_walk.album_dirs(self.root, skipped.append)
        self.assertEqual(skipped, ["Locked"])
        self.assertEqual([path.name for path in found], ["Record"])

    def test_the_default_library_is_the_music_albums_folder_under_home(self) -> None:
        self.assertEqual(library_walk.DEFAULT_LIBRARY, "~/Music/Albums")


if __name__ == "__main__":
    unittest.main()
