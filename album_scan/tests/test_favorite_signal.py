"""`favorite_signal`: which favorites the record's count is, or null when none was read.

Three signals, one per way a count can be read: Music.app's own `favorited` flag
(`music.favorited`), Music.app's user rating at or above N stars (`music.rating>=N`),
and the highest ID3 POPM rating on each track at or above N stars (`id3.popm>=N`). A
record whose favorites were not read (declined, unanswerable, or carried by no tag)
says null, and so does its count.

The Music.app cases stand a fake toolbox in for the real one, shaped like its two
functions and patched in where `main` opens it, so these run with no toolbox installed
and no `osascript` at all. The folder cases write real ID3 tags into empty `.mp3` files.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
from unittest import mock

from mutagen.id3 import ID3, POPM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import no_music, no_store  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import AlbumRef, build_parser, main  # noqa: E402 - follows the bootstrap

# album_scan's bootstrap above is what puts its private src/ on sys.path.
import apple_source  # noqa: E402 - import follows the sys.path bootstrap above


class _ToolboxRef(NamedTuple):
    """The toolbox's own album reference, by shape: a different type from ours."""

    artist: str
    album: str
    added: dt.date


class _ToolboxFacts(NamedTuple):
    """The toolbox's own facts, by shape."""

    tracks: int
    favorited: int
    year: int | None


_REF = _ToolboxRef("The Invented Quartet", "Placeholder Album", dt.date(2026, 8, 30))


class _FakeToolbox:
    """The two toolbox calls, answering fixed values and recording each facts call."""

    def __init__(self, facts: _ToolboxFacts | None) -> None:
        self.facts = facts
        self.facts_calls: list[dict[str, object]] = []

    def newest_albums(
        self, count: int, *, run: apple_source.Runner
    ) -> list[_ToolboxRef]:
        del run  # unused: the fake answers without a subprocess
        return [_REF][:count]

    def album_facts(
        self,
        *,
        artist: str,
        album: str,
        run: apple_source.Runner,
        **options: int | None,
    ) -> _ToolboxFacts | None:
        del run  # unused: the fake answers without a subprocess
        self.facts_calls.append({"artist": artist, "album": album, **options})
        return self.facts

    def handle(self) -> apple_source.Toolbox:
        return apple_source.Toolbox(
            newest_albums=self.newest_albums,
            album_facts=self.album_facts,
            run=no_music,
            folder="invented-kit",
        )


class MusicFactsTest(unittest.TestCase):
    """The boundary conversion: the toolbox's facts into ours, with their signal."""

    def setUp(self) -> None:
        self.ref = AlbumRef(_REF.artist, _REF.album, _REF.added)

    def test_the_default_is_musics_favorited_flag(self) -> None:
        fake = _FakeToolbox(_ToolboxFacts(10, 2, 1994))
        facts = apple_source.music_facts(fake.handle(), self.ref, min_stars=None)
        assert facts is not None
        self.assertEqual((facts.tracks, facts.favorited, facts.year), (10, 2, 1994))
        self.assertEqual(facts.favorite_signal, "music.favorited")
        # No min_stars at all, rather than an explicit None: the call reads the same
        # to a toolbox that predates the rating mode.
        self.assertEqual(
            fake.facts_calls, [{"artist": _REF.artist, "album": _REF.album}]
        )

    def test_a_star_threshold_asks_for_the_rating_instead(self) -> None:
        fake = _FakeToolbox(_ToolboxFacts(10, 4, None))
        facts = apple_source.music_facts(fake.handle(), self.ref, min_stars=3)
        assert facts is not None
        self.assertEqual(facts.favorite_signal, "music.rating>=3")
        self.assertEqual(fake.facts_calls[0]["min_stars"], 3)

    def test_no_answer_is_no_facts_and_so_no_signal(self) -> None:
        fake = _FakeToolbox(None)
        self.assertIsNone(
            apple_source.music_facts(fake.handle(), self.ref, min_stars=2)
        )

    def test_the_toolboxs_refs_become_ours(self) -> None:
        refs = apple_source.music_refs(_FakeToolbox(None).handle(), 1)
        self.assertEqual(refs, [self.ref])
        self.assertIs(type(refs[0]), AlbumRef)


class FavoriteMinStarsOptionTest(unittest.TestCase):
    def test_it_takes_one_to_five_stars(self) -> None:
        self.assertIsNone(build_parser().parse_args([]).favorite_min_stars)
        self.assertEqual(
            build_parser().parse_args(["--favorite-min-stars", "5"]).favorite_min_stars,
            5,
        )
        for refused in ("0", "6", "two"):
            with self.subTest(value=refused):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        build_parser().parse_args(["--favorite-min-stars", refused])
                self.assertEqual(raised.exception.code, 2)


class CommandLineSignalTest(unittest.TestCase):
    """`favorite_signal` in the written record, end to end through `main`."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.library = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.album = self.library / _REF.artist / _REF.album
        self.album.mkdir(parents=True)

    def _rate(self, name: str, rating: int | None) -> None:
        """One empty `.mp3` in the album, with a POPM frame when `rating` is given."""
        path = self.album / name
        path.write_bytes(b"")
        tags = ID3()
        if rating is not None:
            tags.add(POPM(email="a@example.com", rating=rating, count=0))
        tags.save(path)

    def _record(
        self, extra: list[str], toolbox: _FakeToolbox | None = None
    ) -> dict[str, object]:
        stdout = io.StringIO()
        args = ["--library", str(self.library), "--no-artwork", *extra]
        opened = mock.patch(
            "album_scan.open_toolbox",
            return_value=None if toolbox is None else toolbox.handle(),
        )
        with opened, redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            code = main(args, fetch=no_store, run=no_music)
        self.assertEqual(code, 0)
        record: dict[str, object] = json.loads(stdout.getvalue())[0]
        return record

    def test_the_folder_default_is_popm_at_one_star(self) -> None:
        self._rate("01.mp3", 1)
        self._rate("02.mp3", None)
        record = self._record([])
        self.assertEqual((record["tracks"], record["favorited"]), (2, 1))
        self.assertEqual(record["favorite_signal"], "id3.popm>=1")

    def test_the_folder_threshold_reaches_the_signal(self) -> None:
        self._rate("01.mp3", 196)
        self._rate("02.mp3", 128)
        record = self._record(["--favorite-min-stars", "4"])
        self.assertEqual(record["favorited"], 1)
        self.assertEqual(record["favorite_signal"], "id3.popm>=4")

    def test_a_folder_with_no_popm_records_null_and_no_signal(self) -> None:
        self._rate("01.mp3", None)
        record = self._record([])
        self.assertIsNone(record["favorited"])
        self.assertIsNone(record["favorite_signal"])

    def test_music_records_its_favorited_flag(self) -> None:
        record = self._record(
            ["--source", "music"], _FakeToolbox(_ToolboxFacts(10, 2, 1994))
        )
        self.assertEqual(record["favorited"], 2)
        self.assertEqual(record["favorite_signal"], "music.favorited")

    def test_music_with_a_threshold_records_the_rating_signal(self) -> None:
        fake = _FakeToolbox(_ToolboxFacts(10, 3, 1994))
        record = self._record(["--source", "music", "--favorite-min-stars", "2"], fake)
        self.assertEqual(record["favorite_signal"], "music.rating>=2")
        self.assertEqual(fake.facts_calls[0]["min_stars"], 2)

    def test_music_that_cannot_answer_records_no_signal(self) -> None:
        record = self._record(["--source", "music"], _FakeToolbox(None))
        self.assertIsNone(record["favorited"])
        self.assertIsNone(record["favorite_signal"])

    def test_no_favorites_records_no_signal_from_either_source(self) -> None:
        self._rate("01.mp3", 255)
        for extra, toolbox in (
            (["--no-favorites"], None),
            (
                ["--no-favorites", "--source", "music"],
                _FakeToolbox(_ToolboxFacts(10, 2, 1994)),
            ),
        ):
            with self.subTest(extra=extra):
                record = self._record(extra, toolbox)
                self.assertIsNone(record["favorited"])
                self.assertIsNone(record["favorite_signal"])

    def test_auto_takes_the_signal_from_the_source_that_answered(self) -> None:
        self._rate("01.mp3", 255)
        answering = _FakeToolbox(_ToolboxFacts(10, 2, 1994))
        record = self._record(["--source", "auto"], answering)
        self.assertEqual(record["favorite_signal"], "music.favorited")

        # A Music.app with no recent album: auto falls back to the folder scan, and
        # the favorites come from the folder's tags along with it.
        silent = _FakeToolbox(_ToolboxFacts(10, 2, 1994))
        with mock.patch.object(silent, "newest_albums", return_value=[]):
            record = self._record(["--source", "auto"], silent)
        self.assertEqual(record["favorite_signal"], "id3.popm>=1")
        self.assertEqual(silent.facts_calls, [])


if __name__ == "__main__":
    unittest.main()
