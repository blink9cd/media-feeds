"""The capped file view the tag reader hands mutagen: its cap, its refusal, its bound.

Each case opens a real temporary file, since the view bounds a read by what the file
holds past the current position, which it asks the file's own descriptor for. How the
tag reader meets it is tested in test_folder_facts_read_cap.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from mutagen import MutagenError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import album_scan  # noqa: E402, F401 - imported for its sys.path bootstrap

# album_scan's bootstrap above is what puts its private src/ on sys.path.
import capped_read  # noqa: E402 - import follows the sys.path bootstrap above

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _read_cap_files
from _read_cap_files import RecordingHandle  # noqa: E402 - follows the bootstrap

# The same registered profile tests/test_tag_fuzz.py loads, for the same reasons: a
# profile rather than an `@settings` decorator, derandomized for a reproducible run.
settings.register_profile(
    "deterministic",
    derandomize=True,
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("deterministic")

#: The content every hand-picked case reads: ten distinct bytes.
_CONTENT = bytes(range(10))
#: The cap every hand-picked case reads under, smaller than the file.
_CAP = 6


class _ReaderCase(unittest.TestCase):
    """A temporary file holding `_CONTENT`, opened under a recording handle."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "Invented Track.mp3"
        self.path.write_bytes(_CONTENT)

    def open_reader(
        self, cap: int = _CAP
    ) -> tuple[capped_read.CappedReader, RecordingHandle]:
        """A reader over the file at `cap`, and the recording handle beneath it."""
        handle = RecordingHandle(self.path.open("rb"))
        self.addCleanup(handle.__exit__, None, None, None)
        return capped_read.CappedReader(handle, cap, self.path.name), handle


class SizedReadTest(_ReaderCase):
    def test_a_read_within_the_cap_returns_its_bytes(self) -> None:
        reader, _ = self.open_reader()
        self.assertEqual(reader.read(4), _CONTENT[:4])
        self.assertEqual(reader.tell(), 4)
        self.assertFalse(reader.refused)

    def test_a_read_of_exactly_the_cap_is_allowed(self) -> None:
        reader, _ = self.open_reader()
        self.assertEqual(reader.read(_CAP), _CONTENT[:_CAP])
        self.assertFalse(reader.refused)

    def test_a_read_one_past_the_cap_is_refused_before_reading(self) -> None:
        reader, handle = self.open_reader()
        with self.assertRaises(capped_read.ReadOverCap):
            reader.read(_CAP + 1)
        self.assertTrue(reader.refused)
        self.assertEqual(handle.sizes, [])
        self.assertEqual(reader.tell(), 0)

    def test_the_refusal_is_neither_an_os_error_nor_a_mutagen_error(self) -> None:
        # mutagen converts an OSError into its own error type, which would hide it.
        self.assertFalse(issubclass(capped_read.ReadOverCap, OSError))
        self.assertFalse(issubclass(capped_read.ReadOverCap, MutagenError))

    def test_a_read_past_the_end_asks_the_file_only_for_what_it_holds(self) -> None:
        reader, handle = self.open_reader(cap=100)
        reader.seek(7)
        self.assertEqual(reader.read(50), _CONTENT[7:])
        self.assertEqual(handle.sizes, [3])

    def test_a_read_at_the_end_asks_for_nothing(self) -> None:
        reader, handle = self.open_reader()
        reader.seek(0, os.SEEK_END)
        self.assertEqual(reader.read(4), b"")
        self.assertEqual(handle.sizes, [0])


class ReadToTheEndTest(_ReaderCase):
    def test_the_rest_within_the_cap_is_read(self) -> None:
        for size in (-1, None):
            with self.subTest(size=size):
                reader, _ = self.open_reader()
                reader.seek(len(_CONTENT) - _CAP)
                self.assertEqual(reader.read(size), _CONTENT[-_CAP:])
                self.assertFalse(reader.refused)

    def test_the_rest_past_the_cap_is_refused_reading_one_past_it(self) -> None:
        reader, handle = self.open_reader()
        with self.assertRaises(capped_read.ReadOverCap):
            reader.read()
        self.assertTrue(reader.refused)
        self.assertEqual(handle.sizes, [_CAP + 1])


class PassThroughTest(_ReaderCase):
    def test_seek_and_tell_are_the_files_own(self) -> None:
        reader, _ = self.open_reader()
        self.assertEqual(reader.seek(-3, os.SEEK_END), len(_CONTENT) - 3)
        self.assertEqual(reader.tell(), len(_CONTENT) - 3)
        self.assertEqual(reader.seek(2), 2)

    def test_the_name_is_the_one_given(self) -> None:
        reader, _ = self.open_reader()
        self.assertEqual(reader.name, "Invented Track.mp3")


class ReaderPropertyTest(unittest.TestCase):
    """The view's contract over any file, position, size, and cap."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "Invented Track.mp3"

    @given(
        st.binary(max_size=256),
        st.integers(0, 300),
        st.one_of(st.none(), st.integers(-2, 600)),
        st.integers(0, 300),
    )
    def test_fuzz_read(
        self, content: bytes, start: int, size: int | None, cap: int
    ) -> None:
        self.path.write_bytes(content)
        with RecordingHandle(self.path.open("rb")) as handle:
            reader = capped_read.CappedReader(handle, cap, self.path.name)
            reader.seek(start)
            rest = content[start:]
            to_end = size is None or size < 0
            want = len(rest) if to_end else min(size or 0, len(rest))
            try:
                data = reader.read(size)
            except capped_read.ReadOverCap:
                self.assertTrue(reader.refused)
                self.assertTrue(len(rest) > cap if to_end else (size or 0) > cap)
            else:
                self.assertEqual(data, rest[:want])
                self.assertFalse(reader.refused)
            # Never asks the file for more than it holds past here, nor past the cap.
            for asked in handle.sizes:
                self.assertLessEqual(asked, min(len(rest), cap + 1))


if __name__ == "__main__":  # pragma: no cover - direct-run entry point
    unittest.main()
