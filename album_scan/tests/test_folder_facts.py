"""The folder source's facts: tracks, year and favorites read from the album folder.

Every library here is built in a temporary folder, and every tag is written by mutagen
into an otherwise empty `.mp3`, which is all an ID3 reader needs: the frames sit in the
tag at the head of the file, and no audio frame is ever decoded. Nothing here touches
Music.app or the network; the folder source never does.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mutagen.id3 import ID3, POPM, TDRC

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import posix_filesystem_only  # noqa: E402 - import follows the sys.path bootstrap above
import album_scan  # noqa: E402, F401 - imported for its sys.path bootstrap

# album_scan's bootstrap above is what puts its private src/ on sys.path, the same way
# tests/test_discovery.py reaches folder_scan.
import folder_facts  # noqa: E402 - import follows the sys.path bootstrap above

# os.geteuid exists only off Windows, and the gate type-checks for win32 too, so the
# root check is a getattr rather than a bare attribute.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


def _write_mp3(
    path: Path,
    *,
    ratings: tuple[tuple[str, int], ...] = (),
    year: str | None = None,
    v2_version: int = 4,
) -> Path:
    """An empty `.mp3` carrying an ID3 tag with the given POPM frames and year."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = ID3()
    for email, rating in ratings:
        tags.add(POPM(email=email, rating=rating, count=0))
    if year is not None:
        tags.add(TDRC(encoding=3, text=year))
    tags.save(path, v2_version=v2_version)
    return path


class StarScaleTest(unittest.TestCase):
    """The rating byte to stars, at every one of the 256 bytes there are."""

    def test_each_threshold_starts_its_star(self) -> None:
        for byte, stars in ((0, 0), (1, 1), (64, 2), (128, 3), (196, 4), (255, 5)):
            with self.subTest(byte=byte):
                self.assertEqual(folder_facts.stars_for(byte), stars)

    def test_the_byte_below_each_threshold_is_the_star_below(self) -> None:
        for byte, stars in ((63, 1), (127, 2), (195, 3), (254, 4)):
            with self.subTest(byte=byte):
                self.assertEqual(folder_facts.stars_for(byte), stars)

    def test_the_scale_never_falls_and_stays_within_zero_to_five(self) -> None:
        # Exhaustive rather than sampled: the input space is 256 values.
        stars = [folder_facts.stars_for(byte) for byte in range(256)]
        self.assertEqual(stars, sorted(stars))
        self.assertEqual((min(stars), max(stars)), (0, 5))


class FolderFactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.album = Path(self._tmp.name) / "The Invented Quartet" / "Placeholder Album"
        self.album.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)
        self.unreadable: list[str] = []

    def _facts(
        self, *, min_stars: int = 1, favorites: bool = True
    ) -> folder_facts.AlbumFacts | None:
        return folder_facts.folder_facts(
            self.album,
            min_stars=min_stars,
            favorites=favorites,
            on_unreadable=self.unreadable.append,
        )

    def test_an_album_folder_with_no_audio_file_has_no_facts(self) -> None:
        (self.album / "cover.jpg").write_bytes(b"not audio")
        self.assertIsNone(self._facts())

    def test_tracks_counts_every_audio_file_on_every_disc(self) -> None:
        _write_mp3(self.album / "Disc 1" / "01.mp3")
        _write_mp3(self.album / "Disc 2" / "01.mp3")
        (self.album / "02.m4a").write_bytes(b"")
        (self.album / "03.FLAC").write_bytes(b"")
        (self.album / "notes.txt").write_bytes(b"")
        (self.album / ".hidden.mp3").write_bytes(b"")
        (self.album / ".sidecar").mkdir()
        (self.album / ".sidecar" / "04.mp3").write_bytes(b"")
        facts = self._facts()
        assert facts is not None
        self.assertEqual(facts.tracks, 4)

    def test_the_year_is_the_first_one_found_in_file_order(self) -> None:
        _write_mp3(self.album / "01.mp3")
        _write_mp3(self.album / "02.mp3", year="2001-05-03")
        _write_mp3(self.album / "03.mp3", year="1999")
        facts = self._facts()
        assert facts is not None
        self.assertEqual(facts.year, 2001)

    def test_a_version_2_3_year_frame_is_read_too(self) -> None:
        # An ID3v2.3 tag stores the year in TYER, not TDRC; mutagen upgrades it on
        # load, and this pins that the upgrade is what the reader sees.
        _write_mp3(self.album / "01.mp3", year="1987", v2_version=3)
        facts = self._facts()
        assert facts is not None
        self.assertEqual(facts.year, 1987)

    def test_a_year_outside_1000_to_2999_is_no_year(self) -> None:
        # A four-digit year mutagen parses from a corrupt or joke tag; no recording
        # is dated there, and the card would print it as the album's year.
        for year, expected in (
            ("0999", None),
            ("1000", 1000),
            ("2999", 2999),
            ("3000", None),
            ("9999", None),
        ):
            with self.subTest(year=year):
                _write_mp3(self.album / "01.mp3", year=year)
                facts = self._facts()
                assert facts is not None
                self.assertEqual(facts.year, expected)

    def test_a_year_tag_of_zero_is_no_year(self) -> None:
        _write_mp3(self.album / "01.mp3", year="0000")
        facts = self._facts()
        assert facts is not None
        self.assertIsNone(facts.year)

    def test_no_popm_frame_anywhere_means_favorites_unknown_not_zero(self) -> None:
        _write_mp3(self.album / "01.mp3", year="2001")
        _write_mp3(self.album / "02.mp3")
        facts = self._facts()
        assert facts is not None
        self.assertEqual(facts.tracks, 2)
        self.assertIsNone(facts.favorited)
        self.assertIsNone(facts.favorite_signal)

    def test_a_track_counts_by_its_highest_rating_across_every_popm_frame(self) -> None:
        _write_mp3(
            self.album / "01.mp3",
            ratings=(("a@example.com", 0), ("b@example.com", 196)),
        )
        _write_mp3(self.album / "02.mp3", ratings=(("a@example.com", 127),))
        _write_mp3(self.album / "03.mp3")
        facts = self._facts(min_stars=3)
        assert facts is not None
        self.assertEqual((facts.tracks, facts.favorited), (3, 1))
        self.assertEqual(facts.favorite_signal, "id3.popm>=3")

    def test_the_default_one_star_counts_any_rated_track(self) -> None:
        _write_mp3(self.album / "01.mp3", ratings=(("a@example.com", 1),))
        _write_mp3(self.album / "02.mp3", ratings=(("a@example.com", 0),))
        facts = self._facts()
        assert facts is not None
        self.assertEqual(facts.favorited, 1)
        self.assertEqual(facts.favorite_signal, "id3.popm>=1")

    def test_a_present_but_zero_rating_is_a_genuine_zero(self) -> None:
        # Byte 0 is "unrated", but the frame is there: some track on the album was
        # rated by something, so the count is a real zero rather than unknown.
        _write_mp3(self.album / "01.mp3", ratings=(("a@example.com", 0),))
        facts = self._facts()
        assert facts is not None
        self.assertEqual(facts.favorited, 0)
        self.assertEqual(facts.favorite_signal, "id3.popm>=1")

    def test_favorites_declined_reads_no_count_and_keeps_tracks_and_year(self) -> None:
        _write_mp3(
            self.album / "01.mp3", ratings=(("a@example.com", 255),), year="2001"
        )
        facts = self._facts(favorites=False)
        assert facts is not None
        self.assertEqual((facts.tracks, facts.year), (1, 2001))
        self.assertIsNone(facts.favorited)
        self.assertIsNone(facts.favorite_signal)

    def test_a_non_mp3_track_counts_and_contributes_no_rating(self) -> None:
        (self.album / "01.m4a").write_bytes(b"not an ID3 file")
        facts = self._facts()
        assert facts is not None
        self.assertEqual(facts.tracks, 1)
        self.assertIsNone(facts.favorited)
        self.assertEqual(self.unreadable, [])

    def test_an_mp3_with_no_tag_counts_and_is_not_reported(self) -> None:
        (self.album / "01.mp3").write_bytes(b"\xff\xfb\x90\x00" * 8)
        facts = self._facts()
        assert facts is not None
        self.assertEqual(facts.tracks, 1)
        self.assertEqual(self.unreadable, [])

    def test_a_corrupt_tag_counts_the_track_and_reports_it_by_name(self) -> None:
        (self.album / "01.mp3").write_bytes(b"ID3\x04\x00\x00\x7f\x7f\x7f\x7fgarbage")
        _write_mp3(self.album / "02.mp3", ratings=(("a@example.com", 255),))
        facts = self._facts()
        assert facts is not None
        self.assertEqual((facts.tracks, facts.favorited), (2, 1))
        self.assertEqual(self.unreadable, ["01.mp3"])

    @posix_filesystem_only
    @unittest.skipIf(_IS_ROOT, "root can read a mode-000 file")
    def test_an_mp3_that_cannot_be_opened_is_counted_and_reported(self) -> None:
        unreadable = _write_mp3(self.album / "01.mp3", ratings=(("a@example.com", 9),))
        self.addCleanup(os.chmod, unreadable, 0o644)
        os.chmod(unreadable, 0o000)
        facts = self._facts()
        assert facts is not None
        self.assertEqual(facts.tracks, 1)
        self.assertIsNone(facts.favorited)
        self.assertEqual(self.unreadable, ["01.mp3"])

    def test_a_folder_name_no_filesystem_call_accepts_has_no_facts(self) -> None:
        # A NUL cannot be in a real folder name; the walk refuses it with ValueError,
        # which must read as "no facts", never as a traceback out of the refresh.
        missing = self.album / "A\x00Record"
        self.assertIsNone(
            folder_facts.folder_facts(
                missing, min_stars=1, favorites=True, on_unreadable=print
            )
        )

    def test_a_missing_folder_has_no_facts(self) -> None:
        self.assertIsNone(
            folder_facts.folder_facts(
                self.album / "absent",
                min_stars=1,
                favorites=True,
                on_unreadable=print,
            )
        )


if __name__ == "__main__":
    unittest.main()
