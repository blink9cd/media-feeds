"""`--cover-report`: each recorded album's cover result, as a structured file.

`cover_art` names why a cover is null (`no-match`, `lookup-failed`, `no-art`), and the
notice says it on stderr for a person. A scheduled caller that counts persistent misses
needs the same answer without matching that text, so `--cover-report PATH` writes it as
JSON beside the record: one entry per album recorded, naming the album and its cover
result. It is written only when the record is, and a report that cannot be written is
said on stderr without failing the run, since the record itself was written.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import no_store  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import main  # noqa: E402 - import follows the sys.path bootstrap above
from cover_art import (  # noqa: E402 - import follows the sys.path bootstrap above
    MISS_LOOKUP_FAILED,
    MISS_NO_ART,
    MISS_NO_MATCH,
    AlbumRef,
    ArtworkMatch,
)
import cover_report  # noqa: E402 - import follows the sys.path bootstrap above

#: The `fetch` seam's shape: a URL and a timeout in, the reply's bytes out.
_Fetch = Callable[[str, float], bytes]

_ARTIST = "An Artist"
_ALBUM = "A Record"
#: A store reply that verifies as the album these tests build.
_FOUND_REPLY = {
    "results": [
        {
            "artistName": _ARTIST,
            "collectionName": _ALBUM,
            "collectionId": 1,
            "releaseDate": "1994-01-01T00:00:00Z",
            "trackCount": 10,
            "artworkUrl100": "https://example.com/a/100x100bb.jpg",
        }
    ]
}


def _music_says_nothing(
    _args: list[str], _timeout: float
) -> subprocess.CompletedProcess[str]:
    """A Music.app that answers with nothing; `--source folder` never asks it."""
    return subprocess.CompletedProcess(
        args=["osascript"], returncode=1, stdout="", stderr=""
    )


def _store(reply: Mapping[str, object]) -> _Fetch:
    """A fake `fetch` answering every request with `reply`."""

    def fetch(_url: str, _timeout: float) -> bytes:
        return json.dumps(reply).encode("utf-8")

    return fetch


def _failing_store(_url: str, _timeout: float) -> bytes:
    """A fake `fetch` whose every request fails."""
    raise OSError("the store did not answer")


class CoverStatusTest(unittest.TestCase):
    """`cover_status`: one word per cover result, the null causes passed through."""

    def test_a_found_cover_is_found(self) -> None:
        match = ArtworkMatch("https://example.com/a.webp", 1994)
        self.assertEqual(
            cover_report.cover_status(match, no_artwork=False), cover_report.FOUND
        )

    def test_each_null_cause_is_its_own_word(self) -> None:
        for reason in (MISS_NO_MATCH, MISS_LOOKUP_FAILED, MISS_NO_ART):
            with self.subTest(reason=reason):
                match = ArtworkMatch(None, None, reason)
                self.assertEqual(
                    cover_report.cover_status(match, no_artwork=False), reason
                )

    def test_a_declined_lookup_claims_no_cause(self) -> None:
        match = ArtworkMatch(None, None)
        self.assertEqual(
            cover_report.cover_status(match, no_artwork=True), cover_report.NOT_ASKED
        )

    def test_a_null_naming_no_reason_is_unknown(self) -> None:
        match = ArtworkMatch(None, None)
        self.assertEqual(
            cover_report.cover_status(match, no_artwork=False), cover_report.UNKNOWN
        )

    def test_an_entry_names_the_album_and_nothing_else(self) -> None:
        ref = AlbumRef(artist=_ARTIST, album=_ALBUM, added=dt.date(2001, 1, 1))
        self.assertEqual(
            cover_report.report_entry(ref, MISS_NO_MATCH),
            {"artist": _ARTIST, "album": _ALBUM, "cover": MISS_NO_MATCH},
        )


class CoverReportCliTest(unittest.TestCase):
    """`main` with `--cover-report`, over a one-album synthetic library."""

    def setUp(self) -> None:
        holder = TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.library = self.tmp / "library"
        (self.library / _ARTIST / _ALBUM).mkdir(parents=True)
        self.output = self.tmp / "album.json"
        self.report = self.tmp / "state" / "covers.json"

    def _main(self, *extra: str, fetch: _Fetch, library: Path | None = None) -> int:
        argv = [
            "--library",
            str(library or self.library),
            "--output",
            str(self.output),
            "--source",
            "folder",
            "--no-favorites",
            "--cover-report",
            str(self.report),
            *extra,
        ]
        self.stderr = io.StringIO()
        with redirect_stderr(self.stderr):
            return main(argv, fetch=fetch, run=_music_says_nothing)

    def _entries(self) -> list[dict[str, str]]:
        loaded: list[dict[str, str]] = json.loads(
            self.report.read_text(encoding="utf-8")
        )
        return loaded

    def test_a_found_cover_is_reported_found(self) -> None:
        self.assertEqual(self._main(fetch=_store(_FOUND_REPLY)), 0)
        self.assertEqual(
            self._entries(),
            [{"artist": _ARTIST, "album": _ALBUM, "cover": cover_report.FOUND}],
        )

    def test_a_real_miss_is_reported_no_match(self) -> None:
        self.assertEqual(self._main(fetch=_store({"results": []})), 0)
        self.assertEqual(self._entries()[0]["cover"], MISS_NO_MATCH)

    def test_a_failed_request_is_reported_lookup_failed(self) -> None:
        self.assertEqual(self._main(fetch=_failing_store), 0)
        self.assertEqual(self._entries()[0]["cover"], MISS_LOOKUP_FAILED)

    def test_no_artwork_is_reported_not_asked(self) -> None:
        self.assertEqual(self._main("--no-artwork", fetch=no_store), 0)
        self.assertEqual(self._entries()[0]["cover"], cover_report.NOT_ASKED)

    def test_no_album_writes_no_report(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(self._main(fetch=no_store, library=empty), 1)
        self.assertFalse(self.report.exists())

    def test_an_unwritable_report_is_said_and_the_run_still_succeeds(self) -> None:
        # The report's parent is a file, so the directory cannot be made under it.
        self.report = self.tmp / "album.json" / "covers.json"
        self.output = self.tmp / "out" / "album.json"
        (self.tmp / "album.json").write_text(
            "not a folder\n", encoding="utf-8", newline="\n"
        )
        self.assertEqual(self._main(fetch=_store(_FOUND_REPLY)), 0)
        self.assertTrue(self.output.exists())
        self.assertIn("album_scan: cannot write covers.json", self.stderr.getvalue())
        self.assertNotIn(str(self.tmp), self.stderr.getvalue())

    def test_without_the_option_no_report_is_written(self) -> None:
        self.stderr = io.StringIO()
        with redirect_stderr(self.stderr):
            code = main(
                [
                    "--library",
                    str(self.library),
                    "--output",
                    str(self.output),
                    "--source",
                    "folder",
                    "--no-favorites",
                    "--no-artwork",
                ],
                fetch=no_store,
                run=_music_says_nothing,
            )
        self.assertEqual(code, 0)
        self.assertFalse(self.report.exists())


if __name__ == "__main__":
    unittest.main()
