"""The tag reader meeting compressed ID3 frames: a bomb, and a tag past the budget.

These prove a track whose frame inflates past the cap, or whose frames together inflate
past the per-tag budget, is refused without being held, reported as unreadable and
counted unrated; that frames which fail, at the checksum or mid-count, are charged
against the budget too; that each read starts a fresh budget; and that a small
compressed frame still decodes. The shim itself is tested in test_folder_facts_inflate.
"""

from __future__ import annotations

import sys
import unittest
import zlib
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from mutagen import MutagenError
from mutagen.id3 import ID3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import album_scan  # noqa: E402, F401 - imported for its sys.path bootstrap

# album_scan's bootstrap above is what puts its private src/ on sys.path.
import capped_zlib  # noqa: E402 - import follows the sys.path bootstrap above
import folder_facts  # noqa: E402 - import follows the sys.path bootstrap above

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # for _inflate_frames beside this file
from _inflate_frames import (  # noqa: E402 - follows the sys.path bootstrap
    BOMB_BYTES,
    CHECKSUM_BYTES,
    COMPRESSING_VERSIONS,
    FRAMES_IN_BUDGET,
    FRAMES_PAST_BUDGET,
    TINY_FILE_BYTES,
    UNDER_CAP_BYTES,
    TaggedFileCase,
    compressed_frame,
    plain_frame,
    popm_body,
    write_tagged,
)


class ReadTrackTagsInflateTest(TaggedFileCase):
    """A bomb frame as `read_track_tags` and `folder_facts` meet it."""

    def test_a_bomb_from_a_tiny_file_is_a_mutagen_error(self) -> None:
        # Regression: mutagen inflated a compressed frame unbounded, so a tag of a few
        # kilobytes held a frame of any size, and reading it could exhaust memory.
        for version in COMPRESSING_VERSIONS:
            with self.subTest(version=version):
                track = self.bomb_track(version, f"v2{version}.mp3")
                self.assertLess(track.stat().st_size, TINY_FILE_BYTES)
                with self.assertRaises(folder_facts.TagOverCap) as caught:
                    folder_facts.read_track_tags(track)
                self.assertIsInstance(caught.exception, MutagenError)

    def test_refusing_a_bomb_counts_at_most_a_chunk_past_the_cap(self) -> None:
        counts: list[int] = []
        real_count = capped_zlib.count_inflated

        def recording(data: bytes, limit: int) -> tuple[int, zlib.error | None]:
            result = real_count(data, limit)
            counts.append(result[0])
            return result

        track = self.bomb_track(4)
        with (
            mock.patch.object(capped_zlib, "count_inflated", recording),
            mock.patch.object(zlib, "decompress", wraps=zlib.decompress) as whole,
        ):
            with self.assertRaises(folder_facts.TagOverCap):
                folder_facts.read_track_tags(track)
        self.assertEqual(len(counts), 1)
        bound = folder_facts.MAX_FRAME_INFLATE_BYTES + capped_zlib.INFLATE_CHUNK_BYTES
        self.assertLessEqual(counts[0], bound)
        whole.assert_not_called()

    def test_a_bomb_track_is_reported_and_counted_unrated(self) -> None:
        for version in COMPRESSING_VERSIONS:
            with self.subTest(version=version):
                album = self.album / f"v2{version}"
                album.mkdir()
                self.bomb_track(version, f"v2{version}/Bomb.mp3")
                frames = plain_frame(b"POPM", popm_body(64), version)
                write_tagged(album / "Rated.mp3", frames, version)
                heard: list[str] = []
                facts = folder_facts.folder_facts(
                    album, min_stars=1, favorites=True, on_unreadable=heard.append
                )
                self.assertEqual(heard, ["Bomb.mp3"])
                assert facts is not None
                self.assertEqual((facts.tracks, facts.favorited), (2, 1))

    def test_a_refusal_mutagen_swallowed_still_refuses_the_tag(self) -> None:
        # Were a mutagen release to catch the refusal inside its frame loop, the tag
        # would load without the frame; the shim's count still tells the read it.
        def swallowing(track: Path) -> ID3:
            try:
                folder_facts.BOUNDED_ZLIB.decompress(zlib.compress(bytes(BOMB_BYTES)))
            except capped_zlib.InflateOverCap:
                pass
            return ID3()

        track = write_tagged(self.album / "Swallowed.mp3", b"", 4)
        with mock.patch.object(folder_facts, "ID3", swallowing):
            with self.assertRaises(folder_facts.TagOverCap):
                folder_facts.read_track_tags(track)

    def test_a_small_compressed_rating_and_year_still_decode(self) -> None:
        year_frames: dict[int, Callable[[], bytes]] = {
            3: lambda: compressed_frame(b"TYER", b"\x00" + b"2001", 3),
            4: lambda: compressed_frame(b"TDRC", b"\x03" + b"2001", 4),
        }
        for version in COMPRESSING_VERSIONS:
            with self.subTest(version=version):
                frames = compressed_frame(b"POPM", popm_body(196), version)
                frames += year_frames[version]()
                track = write_tagged(self.album / f"v2{version}.mp3", frames, version)
                self.assertEqual(
                    folder_facts.read_track_tags(track),
                    folder_facts.TrackTags(rating=196, year=2001),
                )


class ReadTrackTagsBudgetTest(TaggedFileCase):
    """A tag of many frames, each under the cap, as the reader meets it."""

    def assert_refused_within_budget(self, cut: int) -> None:
        """A past-budget tag is refused, inflating whole no more than the budget holds."""
        for version in COMPRESSING_VERSIONS:
            with self.subTest(version=version):
                track = self.budget_track(
                    version, FRAMES_PAST_BUDGET, f"v2{version}.mp3", cut=cut
                )
                self.assertLess(track.stat().st_size, TINY_FILE_BYTES)
                with mock.patch.object(
                    zlib, "decompress", wraps=zlib.decompress
                ) as whole:
                    with self.assertRaises(folder_facts.TagOverCap):
                        folder_facts.read_track_tags(track)
                self.assertLessEqual(whole.call_count, FRAMES_IN_BUDGET)

    def test_frames_together_past_the_budget_refuse_the_tag(self) -> None:
        # Regression: the cap bound one frame only, and mutagen keeps every private
        # frame of a distinct label, so a tag of tens of kilobytes held 120 MiB.
        self.assert_refused_within_budget(cut=0)

    def test_failed_frames_are_charged_against_the_budget(self) -> None:
        # Cut-short frames are counted whole, then fail, and mutagen drops each as junk
        # and reads on: uncharged, a tag of them inflated without end.
        self.assert_refused_within_budget(cut=CHECKSUM_BYTES)

    def test_frames_failing_mid_count_are_charged_against_the_budget(self) -> None:
        # Spoiled mid-body, each frame fails inside the count after inflating half of
        # itself, so only the count's error-path charge spends the budget. Each spends
        # about half a frame's worth, so the tag holds twice as many frames.
        counts: list[tuple[int, zlib.error | None]] = []
        real_count = capped_zlib.count_inflated

        def recording(data: bytes, limit: int) -> tuple[int, zlib.error | None]:
            result = real_count(data, limit)
            counts.append(result)
            return result

        for version in COMPRESSING_VERSIONS:
            with self.subTest(version=version):
                counts.clear()
                track = self.budget_track(
                    version, 2 * FRAMES_PAST_BUDGET, f"v2{version}.mp3", corrupt=True
                )
                with mock.patch.object(capped_zlib, "count_inflated", recording):
                    with self.assertRaises(folder_facts.TagOverCap):
                        folder_facts.read_track_tags(track)
                # The count that refuses may stop at the limit before the spoiled
                # block; every count before it failed, charged more than a chunk.
                failed = [charge for charge, error in counts if error is not None]
                self.assertGreaterEqual(len(failed), FRAMES_IN_BUDGET)
                self.assertGreater(failed[0], capped_zlib.INFLATE_CHUNK_BYTES)
                self.assertLess(failed[0], UNDER_CAP_BYTES)

    def test_a_track_past_the_budget_is_reported_and_counted_unrated(self) -> None:
        self.budget_track(4, FRAMES_PAST_BUDGET, "Heavy.mp3")
        rated = plain_frame(b"POPM", popm_body(64), 4)
        write_tagged(self.album / "Rated.mp3", rated, 4)
        heard: list[str] = []
        facts = folder_facts.folder_facts(
            self.album, min_stars=1, favorites=True, on_unreadable=heard.append
        )
        self.assertEqual(heard, ["Heavy.mp3"])
        assert facts is not None
        self.assertEqual((facts.tracks, facts.favorited), (2, 1))

    def test_each_track_starts_a_fresh_budget(self) -> None:
        # Two tags each within the budget, read one after the other: the second is
        # charged only its own frames, not the first track's as well.
        tracks = [
            self.budget_track(4, FRAMES_IN_BUDGET, f"Track {n}.mp3") for n in (1, 2)
        ]
        for track in tracks:
            with self.subTest(track=track.name):
                self.assertEqual(folder_facts.read_track_tags(track).rating, 255)


if __name__ == "__main__":  # pragma: no cover - direct-run entry point
    unittest.main()
