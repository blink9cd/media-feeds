"""The tag reader meeting a tag that declares more than the file holds.

These prove a tiny file declaring a huge tag, or a huge v2.3 extended header, is
refused as `TagOverCap` without the declared size ever being asked of the file,
reported with its cause named and counted unrated; that the cap's boundary is exact;
that a real tag with a large cover still reads; that a refusal mutagen swallowed or
wrapped, or followed with a `ValueError`, still refuses; that an extended header
claiming more than its tag is an unreadable tag rather than a crash; and that the
notice naming the size cap is drawn by an inflate refusal as well as a read one. The
capped view itself is tested in test_capped_read.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mutagen import MutagenError
from mutagen.id3 import APIC, ID3, POPM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from album_scan import main  # noqa: E402 - import follows the sys.path bootstrap above

# album_scan's bootstrap above is what puts its private src/ on sys.path.
import capped_read  # noqa: E402 - import follows the sys.path bootstrap above
import folder_facts  # noqa: E402 - import follows the sys.path bootstrap above

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # for the helpers beside this file
from _inflate_frames import (  # noqa: E402 - follows the bootstrap
    FRAMES_PAST_BUDGET,
    TaggedFileCase,
    synchsafe,
    write_tagged,
)
from _read_cap_files import (  # noqa: E402 - follows the bootstrap
    DECLARED_MAX,
    EXTENDED_HUGE,
    TINY_FILE_BYTES,
    RecordingHandle,
    extended_header_past_tag,
    huge_declared_tag,
    huge_extended_header,
    padded_tag,
    rated_frame,
)
from _seams import no_music, no_store  # noqa: E402 - follows the bootstrap

#: A cover several MiB large, as a real embedded front cover can be, under the cap.
_LARGE_COVER_BYTES = 6 * 1024 * 1024
#: The body length of the boundary tag: past the 131 bytes the ID3v1 probe reads.
_BOUNDARY_BODY = 4096


def _record_reads(track: Path) -> RecordingHandle:
    """The file at `track`, opened under a handle recording each read size."""
    return RecordingHandle(track.open("rb"))


class _Heard:
    """Both reporters `folder_facts` takes, each recording the names it hears."""

    def __init__(self) -> None:
        self.unreadable: list[str] = []
        self.over_cap: list[str] = []

    def facts(self, album: Path) -> folder_facts.AlbumFacts | None:
        """`album`'s facts, reported through both reporters."""
        return folder_facts.folder_facts(
            album,
            min_stars=1,
            favorites=True,
            on_unreadable=self.unreadable.append,
            on_over_cap=self.over_cap.append,
        )


class HugeDeclaredSizeTest(TaggedFileCase):
    """A tiny file declaring a huge tag, as `read_track_tags` and `folder_facts` meet it."""

    def huge_tracks(self) -> dict[str, Path]:
        """One tiny file per huge claim: the tag's own size, and the extended header's."""
        return {
            "tag size": huge_declared_tag(self.album / "Huge.mp3"),
            "extended header": huge_extended_header(self.album / "Extended.mp3"),
        }

    def test_a_huge_declared_size_is_refused_as_over_the_cap(self) -> None:
        # Regression: mutagen read a tag's declared size whole, so a file of about 100
        # bytes declaring 256 MiB made the reader allocate 256 MiB.
        for claim, track in self.huge_tracks().items():
            with self.subTest(claim=claim):
                self.assertLess(track.stat().st_size, TINY_FILE_BYTES)
                with self.assertRaises(folder_facts.TagOverCap) as caught:
                    folder_facts.read_track_tags(track)
                self.assertIsInstance(caught.exception, MutagenError)

    def test_the_file_is_never_asked_for_the_declared_size(self) -> None:
        cap = folder_facts.MAX_TAG_READ_BYTES
        for claim, track in self.huge_tracks().items():
            with self.subTest(claim=claim):
                handles: list[RecordingHandle] = []

                def opening(path: Path) -> RecordingHandle:
                    handles.append(_record_reads(path))
                    return handles[-1]

                with mock.patch.object(folder_facts, "_open_track", opening):
                    with self.assertRaises(folder_facts.TagOverCap):
                        folder_facts.read_track_tags(track)
                sizes = handles[0].sizes
                self.assertTrue(sizes)
                self.assertLessEqual(max(sizes), cap)
                self.assertNotIn(DECLARED_MAX, sizes)
                self.assertNotIn(EXTENDED_HUGE, sizes)

    def test_a_declared_size_under_the_cap_asks_only_what_the_file_holds(self) -> None:
        # A short file declaring a size the cap allows: the read is bounded by the
        # file, not by the claim, so the claim is never allocated either.
        declared = folder_facts.MAX_TAG_READ_BYTES // 2
        track = self.album / "Short.mp3"
        track.write_bytes(b"ID3\x04\x00\x00" + synchsafe(declared) + rated_frame(4))
        handles: list[RecordingHandle] = []

        def opening(path: Path) -> RecordingHandle:
            handles.append(_record_reads(path))
            return handles[-1]

        with mock.patch.object(folder_facts, "_open_track", opening):
            with self.assertRaises(MutagenError):
                folder_facts.read_track_tags(track)
        self.assertLessEqual(max(handles[0].sizes), track.stat().st_size)

    def test_a_huge_track_is_reported_by_cause_and_counted_unrated(self) -> None:
        self.huge_tracks()
        write_tagged(self.album / "Rated.mp3", rated_frame(4, 64), 4)
        heard = _Heard()
        facts = heard.facts(self.album)
        self.assertEqual(sorted(heard.over_cap), ["Extended.mp3", "Huge.mp3"])
        self.assertEqual(heard.unreadable, [])
        assert facts is not None
        self.assertEqual((facts.tracks, facts.favorited), (3, 1))

    def test_with_no_over_cap_reporter_the_generic_one_hears_it(self) -> None:
        huge_declared_tag(self.album / "Huge.mp3")
        heard: list[str] = []
        facts = folder_facts.folder_facts(
            self.album, min_stars=1, favorites=True, on_unreadable=heard.append
        )
        self.assertEqual(heard, ["Huge.mp3"])
        assert facts is not None
        self.assertEqual((facts.tracks, facts.favorited), (1, None))


class ReadCapBoundaryTest(TaggedFileCase):
    """The cap against a tag of a known length, and a real tag with a large cover."""

    def test_a_cap_one_short_of_the_tag_refuses_it(self) -> None:
        track = padded_tag(self.album / "Edge.mp3", _BOUNDARY_BODY)
        with mock.patch.object(folder_facts, "MAX_TAG_READ_BYTES", _BOUNDARY_BODY - 1):
            with self.assertRaises(folder_facts.TagOverCap):
                folder_facts.read_track_tags(track)

    def test_a_cap_of_exactly_the_tag_reads_it(self) -> None:
        track = padded_tag(self.album / "Edge.mp3", _BOUNDARY_BODY)
        with mock.patch.object(folder_facts, "MAX_TAG_READ_BYTES", _BOUNDARY_BODY):
            self.assertEqual(folder_facts.read_track_tags(track).rating, 255)

    def test_a_tag_with_a_cover_of_several_mib_still_reads(self) -> None:
        track = self.album / "Covered.mp3"
        track.write_bytes(b"")
        tags = ID3()
        tags.add(
            APIC(encoding=3, mime="image/jpeg", type=3, data=bytes(_LARGE_COVER_BYTES))
        )
        tags.add(POPM(email="player@example.com", rating=196, count=0))
        tags.save(track, v2_version=4)
        self.assertGreater(track.stat().st_size, _LARGE_COVER_BYTES)
        self.assertEqual(folder_facts.read_track_tags(track).rating, 196)


class RefusalBackstopTest(TaggedFileCase):
    """A refusal that does not reach the reader as itself still refuses the tag."""

    def over_cap_read(self, reader: capped_read.CappedReader) -> None:
        """Ask `reader` for one byte past the cap, and swallow the refusal."""
        try:
            reader.read(folder_facts.MAX_TAG_READ_BYTES + 1)
        except capped_read.ReadOverCap:
            pass

    def test_a_refusal_mutagen_swallowed_still_refuses_the_tag(self) -> None:
        # Were a mutagen release to catch the refusal, the tag would load partial.
        def swallowing(reader: capped_read.CappedReader) -> ID3:
            self.over_cap_read(reader)
            return ID3()

        track = padded_tag(self.album / "Swallowed.mp3", _BOUNDARY_BODY)
        with mock.patch.object(folder_facts, "ID3", swallowing):
            with self.assertRaises(folder_facts.TagOverCap):
                folder_facts.read_track_tags(track)

    def test_a_refusal_mutagen_wrapped_still_reads_as_over_the_cap(self) -> None:
        def wrapping(reader: capped_read.CappedReader) -> ID3:
            self.over_cap_read(reader)
            raise MutagenError("a refusal wrapped in mutagen's own error")

        track = padded_tag(self.album / "Wrapped.mp3", _BOUNDARY_BODY)
        with mock.patch.object(folder_facts, "ID3", wrapping):
            with self.assertRaises(folder_facts.TagOverCap):
                folder_facts.read_track_tags(track)

    def test_a_value_error_after_a_refusal_still_reads_as_over_the_cap(self) -> None:
        # Regression: only mutagen's own errors were checked for an earlier refusal, so
        # a ValueError raised once the reader had refused read as a malformed tag.
        def refusing_then_failing(reader: capped_read.CappedReader) -> ID3:
            self.over_cap_read(reader)
            raise ValueError("a refusal followed by a failure of another type")

        track = padded_tag(self.album / "Failing.mp3", _BOUNDARY_BODY)
        with mock.patch.object(folder_facts, "ID3", refusing_then_failing):
            with self.assertRaises(folder_facts.TagOverCap):
                folder_facts.read_track_tags(track)


class ExtendedHeaderPastTagTest(TaggedFileCase):
    """An extended header claiming more than its tag: unreadable, never a crash."""

    def test_it_is_an_unreadable_tag_not_a_value_error(self) -> None:
        # Regression: mutagen asked for the tag's remainder at a negative size, and the
        # ValueError that raised escaped the reader and its wrapper, ending the refresh.
        for version in (3, 4):
            with self.subTest(version=version):
                track = extended_header_past_tag(
                    self.album / f"v2{version}.mp3", version
                )
                with self.assertRaises(folder_facts.TagMalformed) as caught:
                    folder_facts.read_track_tags(track)
                self.assertIsInstance(caught.exception, MutagenError)

    def test_it_is_reported_as_unreadable_and_counted_unrated(self) -> None:
        extended_header_past_tag(self.album / "Bad.mp3", 3)
        heard = _Heard()
        facts = heard.facts(self.album)
        self.assertEqual((heard.unreadable, heard.over_cap), (["Bad.mp3"], []))
        assert facts is not None
        self.assertEqual((facts.tracks, facts.favorited), (1, None))


class OverCapNoticeTest(TaggedFileCase):
    """The line a refresh prints for a track over the cap names that cause."""

    def refresh(self) -> tuple[int, str, str]:
        """Run a folder-source refresh of this album's library: exit code and streams."""
        root = str(self.album.parent.parent)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "--library",
                    root,
                    "--output",
                    "-",
                    "--source",
                    "folder",
                    "--no-artwork",
                ],
                fetch=no_store,
                run=no_music,
            )
        self.assertNotIn(root, stderr.getvalue())
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_named_over_cap(self, stderr: str, name: str) -> None:
        """`stderr` holds the unreadable-tag line naming the size cap for `name`."""
        self.assertIn(
            f"album_scan: could not read the tags of {name} (the tag is over the size "
            "cap); counted as an unrated track\n",
            stderr,
        )

    def test_the_notice_names_the_size_cap(self) -> None:
        huge_declared_tag(self.album / "01.mp3")
        code, stdout, stderr = self.refresh()
        self.assertEqual(code, 0)
        self.assert_named_over_cap(stderr, "01.mp3")
        self.assertEqual(json.loads(stdout)[0]["tracks"], 1)

    def test_an_inflate_refusal_draws_the_same_named_notice(self) -> None:
        # A frame past the inflate cap, and frames together past the budget, are each
        # over a size cap as well, so each draws the line naming it, not the generic one.
        self.bomb_track(4, "01.mp3")
        self.budget_track(4, FRAMES_PAST_BUDGET, "02.mp3")
        code, stdout, stderr = self.refresh()
        self.assertEqual(code, 0)
        self.assert_named_over_cap(stderr, "01.mp3")
        self.assert_named_over_cap(stderr, "02.mp3")
        self.assertNotIn("could not read the tags of 01.mp3;", stderr)
        self.assertNotIn("could not read the tags of 02.mp3;", stderr)
        record = json.loads(stdout)[0]
        self.assertEqual((record["tracks"], record["favorited"]), (2, None))


if __name__ == "__main__":  # pragma: no cover - direct-run entry point
    unittest.main()
