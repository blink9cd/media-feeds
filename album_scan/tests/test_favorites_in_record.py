"""The favorited-track counts, from the toolbox's `album_stats` into the emitted record.

Drives a FAKE `run` throughout -- nothing here spawns a real `osascript` process or
touches Music.app. Every CLI test below uses `--source music`, the source whose counts
come from `album_stats`, so the fake answers two scripts off one seam: the date-added
lookup naming one album, and that album's counts query (`_seams.music_answering`, which
tells them apart by the script). They drive the real toolbox's parsing, so they skip
where no toolbox is installed beside this project. The folder source's counts are
tests/test_folder_facts.py's.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import music_answering, needs_toolbox, no_store  # noqa: E402 - import follows the sys.path bootstrap above

# Both come from album_scan, which re-exports them, NOT from their own modules: this
# tool's bootstrap is what puts those folders on sys.path, and ruff's isort would sort a
# bare import above the line that makes it importable. Same reason
# test_record_contract.py gives.
from album_scan import (  # noqa: E402 - import follows the sys.path bootstrap above
    AlbumFacts,
    AlbumRef,
    Runner,
    album_record,
    main,
)

REF = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2026, 8, 30))

#: Music.app's date-added reply naming one album: the one every CLI case below records.
_NEWEST = "2026-08-20T10:00:00\tAn Artist\tA Record\n"


class RecordCarriesCountsTest(unittest.TestCase):
    """`album_record`'s counts arm, tested directly and without a subprocess."""

    def test_supplied_counts_reach_the_record(self) -> None:
        record = album_record(
            REF,
            None,
            facts=AlbumFacts(
                tracks=10, favorited=2, year=1994, favorite_signal="music.favorited"
            ),
        )
        self.assertEqual(record["tracks"], 10)
        self.assertEqual(record["favorited"], 2)

    def test_absent_counts_serialize_as_null_not_zero(self) -> None:
        # `None` means "could not tell"; 0 means "told, and none are favorited". A
        # consumer renders those differently -- "no signal" versus "nothing favorited
        # yet" -- so collapsing them here would destroy a distinction downstream.
        record = album_record(REF, None)
        self.assertIsNone(record["tracks"])
        self.assertIsNone(record["favorited"])
        self.assertIn('"favorited": null', json.dumps(record, indent=2))

    def test_zero_favorited_is_recorded_as_zero(self) -> None:
        record = album_record(
            REF,
            None,
            facts=AlbumFacts(
                tracks=12, favorited=0, year=2001, favorite_signal="music.favorited"
            ),
        )
        self.assertEqual(record["favorited"], 0)
        self.assertEqual(record["tracks"], 12)


class RecordCarriesYearTest(unittest.TestCase):
    """The album year, and which source wins when both have one.

    Both sources report an INTEGER and the record publishes TEXT -- so these
    assertions are quoted. The conversion happens once, at the record boundary,
    because `facts.year` is still compared numerically by the artwork matcher.
    """

    def test_the_music_year_reaches_the_record(self) -> None:
        record = album_record(
            REF,
            None,
            facts=AlbumFacts(
                tracks=10, favorited=2, year=1994, favorite_signal="music.favorited"
            ),
        )
        self.assertEqual(record["year"], "1994")

    def test_the_music_year_wins_over_the_store_fallback(self) -> None:
        # The fallback fills an ABSENCE; it never corrects a tag. Music's own year is
        # the album as this library holds it, and the store's is a different edition's.
        record = album_record(
            REF,
            None,
            facts=AlbumFacts(
                tracks=1, favorited=0, year=1994, favorite_signal="music.favorited"
            ),
            fallback_year=2015,
        )
        self.assertEqual(record["year"], "1994")

    def test_the_store_fallback_fills_an_absent_music_year(self) -> None:
        record = album_record(
            REF,
            None,
            facts=AlbumFacts(
                tracks=1, favorited=0, year=None, favorite_signal="music.favorited"
            ),
            fallback_year=2015,
        )
        self.assertEqual(record["year"], "2015")

    def test_no_year_from_either_source_records_null(self) -> None:
        self.assertIsNone(album_record(REF, None)["year"])


@needs_toolbox
class CommandLineCountsTest(unittest.TestCase):
    """The CLI's counts arm, end to end through `main` and out to the written file."""

    def _run_main(self, extra: list[str], run: Runner) -> dict[str, object]:
        """Drive `main` with `--source music`; return the written record.

        The written file is always a JSON array, even at the default --count of one,
        so this returns element [0] rather than the raw parse.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "album.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "--library",
                        str(root),
                        "--output",
                        str(out),
                        "--no-artwork",
                        "--source",
                        "music",
                        *extra,
                    ],
                    fetch=no_store,
                    run=run,
                )
            self.assertEqual(code, 0)
            written: list[dict[str, object]] = json.loads(
                out.read_text(encoding="utf-8")
            )
            return written[0]

    def test_counts_from_music_reach_the_written_record(self) -> None:
        record = self._run_main([], music_answering(_NEWEST, "10\t2\t1994"))
        self.assertEqual(record["tracks"], 10)
        self.assertEqual(record["favorited"], 2)
        self.assertEqual(record["favorite_signal"], "music.favorited")

    def test_no_favorites_records_nulls_without_asking_music(self) -> None:
        asked: list[str] = []
        answering = music_answering(_NEWEST, "10\t2\t1994")

        def run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            asked.append(args[-1])
            return answering(args, timeout)

        record = self._run_main(["--no-favorites"], run)
        self.assertIsNone(record["tracks"])
        self.assertIsNone(record["favorited"])
        # The flag must SKIP the query, not run it and discard the answer: its whole
        # point is staying off Music.app, mirroring --no-artwork staying off the
        # network. The date-added query is the only one asked.
        self.assertEqual(len(asked), 1)
        self.assertIn("date added", asked[0])

    def test_an_unreachable_music_still_writes_a_valid_record(self) -> None:
        # A denied automation permission, or Music.app going away between the two
        # queries, must degrade to a record with null counts -- never to a failed
        # refresh, and never to a card that cannot be built.
        answering = music_answering(_NEWEST, "")

        def run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            if "date added" not in args[-1]:
                raise OSError("osascript not found")
            return answering(args, timeout)

        record = self._run_main([], run)
        self.assertIsNone(record["favorited"])
        self.assertIsNone(record["favorite_signal"])
        self.assertEqual(record["artist"], "An Artist")

    def test_a_name_the_seam_refuses_still_writes_a_valid_record(self) -> None:
        """A NUL in an album name, end to end through `main`.

        `subprocess.Popen` refuses an argv element holding a NUL with ValueError, not
        OSError, and the fake below does exactly that and nothing else. The name
        reaches the counts query by patching the Music.app lookup to return it:
        Music.app's rows are filtered before they get this far, so the patch stands
        in for ANY caller that hands `album_facts` such a name. Before the seam's
        contract covered ValueError, this killed the whole refresh with a traceback;
        it must degrade to null counts, exactly as an unreachable Music.app does.
        """

        def popen_like(
            args: list[str], _timeout: float
        ) -> subprocess.CompletedProcess[str]:
            if any("\x00" in argument for argument in args):
                raise ValueError("embedded null byte")
            return subprocess.CompletedProcess(
                args=["osascript"], returncode=0, stdout="10\t2\t1994", stderr=""
            )

        nul_ref = AlbumRef(
            artist="An Artist", album="A\x00Record", added=dt.date(2026, 8, 30)
        )
        # `album_lookups`, not `album_scan`: the lookup is CALLED from there, and a
        # `from apple_source import music_refs` binds the name in the calling module.
        # Patching it anywhere else leaves this call reaching the real toolbox query,
        # with the test still green on every other assertion.
        with mock.patch("album_lookups.music_refs", return_value=[nul_ref]):
            record = self._run_main([], popen_like)
        self.assertEqual(record["album"], "A\x00Record")
        self.assertIsNone(record["tracks"])
        self.assertIsNone(record["favorited"])

    def test_an_album_music_does_not_know_writes_null_counts(self) -> None:
        record = self._run_main([], music_answering(_NEWEST, "0\t0\t0"))
        self.assertIsNone(record["tracks"])
        self.assertIsNone(record["favorited"])


if __name__ == "__main__":
    unittest.main()
