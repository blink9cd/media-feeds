"""What the tool says on stderr beyond the success line.

The notices that name a fallback, a fact that was asked for and not obtained, and a
folder the scan could not read. Each is one line, on stderr only: the exit code and
whatever reaches stdout are pinned unchanged beside every notice.

Every `main` call injects both boundary seams. A seam a case must not reach raises;
one that answers does so without a subprocess or a socket.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import (  # noqa: E402 - import follows the sys.path bootstrap above
    needs_toolbox,
    no_music,
    no_store,
    posix_filesystem_only,
)
from album_scan import main  # noqa: E402 - import follows the sys.path bootstrap above

# album_scan's bootstrap above is what puts its private src/ and cover_art on sys.path.
import notices  # noqa: E402 - import follows the sys.path bootstrap above
from cover_art import MISS_NO_ART, AlbumRef, ArtworkMatch  # noqa: E402 - follows the bootstrap above

# os.geteuid exists only off Windows, and the gate type-checks for win32 too, so the
# root check is a getattr rather than a bare attribute; root can list a mode-000
# directory, so the unreadable-folder case skips rather than fails under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


#: The phrase every fallback-notice assertion keys on; the wording test pins the rest.
_FALLBACK = "falling back to the folder scan"

#: The source that can fall back: the default, folder, has nothing to fall back from.
_AUTO = ["--source", "auto"]


def _music_absent(
    _args: list[str], _timeout: float
) -> subprocess.CompletedProcess[str]:
    """A Music.app that cannot be reached: osascript exits non-zero with nothing."""
    return subprocess.CompletedProcess(
        args=["osascript"], returncode=1, stdout="", stderr="not running"
    )


def _run(args: list[str], *, run: object, fetch: object) -> tuple[int, str, str]:
    """Drive `main`; return the exit code and what reached stdout and stderr."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args, fetch=fetch, run=run)  # type: ignore[arg-type]
    return code, stdout.getvalue(), stderr.getvalue()


class ControlCharacterEchoTest(unittest.TestCase):
    """A tag's control characters are escaped before any line is echoed.

    Every name this tool prints came out of a track tag, which is arbitrary text: the
    AppleScript concatenates it unescaped, and the row parser refuses only NUL and the
    row terminator. Measured: ESC, BEL, and the C1 block all reach the record intact.
    Printed to a terminal, ESC is not text but an instruction to it: a crafted tag
    can rewrite the window title, erase the lines above, or wrap the name in an OSC 8
    hyperlink pointing anywhere. The record is unaffected; the exposure is the
    terminal of whoever ran the refresh.
    """

    _ESC = "\x1b"

    def _music_returning(self, rows: str) -> object:
        """A Music.app that answers with exactly these rows."""

        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["osascript"], returncode=0, stdout=rows, stderr=""
            )

        return run

    @needs_toolbox
    def test_the_success_line_escapes_a_control_character_in_a_tag(self) -> None:
        rows = f"2000-06-01T10:00:00\tAn{self._ESC}Artist\tA Record\n"
        code, stdout, stderr = _run(
            ["--output", "-", "--source", "music", "--no-favorites", "--no-artwork"],
            run=self._music_returning(rows),
            fetch=no_store,
        )
        self.assertEqual(code, 0)
        self.assertIn("wrote 1 album(s)", stderr)
        self.assertNotIn(self._ESC, stderr)
        self.assertIn("\\x1b", stderr)
        # The record keeps the tag as it was: it is JSON a browser reads, and
        # escaping it there would change the data to protect a terminal.
        self.assertEqual(json.loads(stdout)[0]["artist"], f"An{self._ESC}Artist")

    @posix_filesystem_only
    def test_both_degradation_notices_escape_a_control_character(self) -> None:
        # Through the CLI, like every other case here: the folder scan reads an
        # artist and an album straight off directory names, and a control character
        # is legal in one, so no record has to be hand-built to reach the notices.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / f"An{self._ESC}Artist" / f"A{self._ESC}Record").mkdir(parents=True)
            code, _stdout, stderr = _run(
                ["--library", str(root), "--output", "-", "--source", "folder"],
                run=_music_absent,
                fetch=_empty_store,
            )
        self.assertEqual(code, 0)
        self.assertIn("counts unavailable", stderr)
        self.assertIn("cover art not resolved", stderr)
        self.assertNotIn(self._ESC, stderr)
        # Both names, in both notices, plus the confirmation line's pair.
        self.assertEqual(stderr.count("\\x1b"), 6)

    @posix_filesystem_only
    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 directory")
    def test_a_skipped_folder_name_is_escaped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            locked = root / f"An{self._ESC}Artist"
            locked.mkdir()
            os.chmod(locked, 0o000)
            (root / "Readable" / "Record").mkdir(parents=True)
            try:
                code, _stdout, stderr = _run(
                    [
                        "--library",
                        str(root),
                        "--output",
                        "-",
                        "--source",
                        "folder",
                        "--no-artwork",
                        "--no-favorites",
                    ],
                    run=no_music,
                    fetch=no_store,
                )
            finally:
                os.chmod(locked, 0o755)
        self.assertEqual(code, 0)
        self.assertIn("skipped unreadable folder", stderr)
        self.assertNotIn(self._ESC, stderr)
        self.assertIn("\\x1b", stderr)


@needs_toolbox
class FallbackNoticeTest(unittest.TestCase):
    """`--source auto`'s fallback is announced whenever it fires, whatever comes of it.

    A fallback that then finds nothing, or that finds --library is not a directory,
    still consulted Music.app first and got nothing: the operator must see that
    before the outcome, or an "empty library" is diagnosed where a Music.app that
    never answered is the real story.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_auto_says_it_fell_back_even_when_the_folder_scan_finds_nothing(
        self,
    ) -> None:
        code, stdout, stderr = _run(
            ["--library", str(self.root), "--output", "-", "--no-artwork", *_AUTO],
            run=_music_absent,
            fetch=no_store,
        )
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        lines = stderr.splitlines()
        self.assertIn(_FALLBACK, lines[0])
        self.assertEqual(
            lines[1:], [f"album_scan: no album found under {self.root.name}"]
        )

    def test_auto_says_it_fell_back_even_when_the_library_is_not_a_directory(
        self,
    ) -> None:
        code, stdout, stderr = _run(
            [
                "--library",
                str(self.root / "nope"),
                "--output",
                "-",
                "--no-artwork",
                *_AUTO,
            ],
            run=_music_absent,
            fetch=no_store,
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        lines = stderr.splitlines()
        self.assertIn(_FALLBACK, lines[0])
        self.assertEqual(
            lines[1:], ["album_scan: library root is not a directory: nope"]
        )

    @posix_filesystem_only
    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 directory")
    def test_auto_says_it_fell_back_even_when_the_library_is_unreadable(self) -> None:
        locked = self.root / "locked"
        locked.mkdir()
        self.addCleanup(os.chmod, locked, 0o755)
        os.chmod(locked, 0o000)
        code, stdout, stderr = _run(
            ["--library", str(locked), "--output", "-", "--no-artwork", *_AUTO],
            run=_music_absent,
            fetch=no_store,
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        lines = stderr.splitlines()
        self.assertIn(_FALLBACK, lines[0])
        self.assertEqual(
            lines[1:], ["album_scan: library root is not readable: locked"]
        )

    def test_the_notice_says_what_is_known_about_the_folder_scan(self) -> None:
        # What is known is that a folder's birth time is not a reliable "date added"
        # (album_scan.md, "Why the folder scan's date is a heuristic"). A stranger
        # reading "this library" would take it to mean their library.
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        _, _, stderr = _run(
            [
                "--library",
                str(self.root),
                "--output",
                "-",
                "--no-artwork",
                "--no-favorites",
                *_AUTO,
            ],
            run=_music_absent,
            fetch=no_store,
        )
        notice = next(line for line in stderr.splitlines() if _FALLBACK in line)
        self.assertIn("birth time", notice)
        self.assertIn("not a reliable", notice)
        self.assertNotIn("this library", notice)


def _failing_store(_url: str, _timeout: float) -> bytes:
    """A store that never answers: every request fails at the network layer."""
    raise OSError("unreachable")


def _empty_store(_url: str, _timeout: float) -> bytes:
    """An iTunes Search that answers, and has no candidate for this album."""
    return json.dumps({"results": []}).encode("utf-8")


class DegradationNoticeTest(unittest.TestCase):
    """A fact that was asked for and not obtained is said once, per album, on stderr.

    Null counts and a null cover are valid record states, so neither changes the
    exit code or the record; but a null that was asked for is a degraded refresh,
    and one that was declined (`--no-favorites`, `--no-artwork`) is not, so only the
    former is reported.
    """

    _COUNTS = (
        "album_scan: counts unavailable for An Artist - A Record (no audio file "
        "in its folder, or the folder could not be read)"
    )
    _COVER = (
        "album_scan: cover art not resolved for An Artist - A Record (the store "
        "answered and no album in it is this one)"
    )
    _COVER_FAILED = (
        "album_scan: cover art not resolved for An Artist - A Record (a store "
        "request failed, so the store may still carry it)"
    )

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / "An Artist" / "A Record").mkdir(parents=True)

    def _run(self, extra: list[str], *, run: object, fetch: object) -> tuple[str, str]:
        code, stdout, stderr = _run(
            [
                "--library",
                str(self.root),
                "--output",
                "-",
                "--source",
                "folder",
                *extra,
            ],
            run=run,
            fetch=fetch,
        )
        self.assertEqual(code, 0)
        return stdout, stderr

    def test_unavailable_counts_are_reported_per_album(self) -> None:
        stdout, stderr = self._run(["--no-artwork"], run=_music_absent, fetch=no_store)
        self.assertIn(self._COUNTS + "\n", stderr)
        self.assertIsNone(json.loads(stdout)[0]["favorited"])

    def test_the_folder_counts_notice_survives_no_favorites(self) -> None:
        # The folder source still reads the tracks and the year under --no-favorites,
        # so an album folder holding no audio file is still a fact asked for and lost.
        stdout, stderr = self._run(
            ["--no-artwork", "--no-favorites"], run=no_music, fetch=no_store
        )
        self.assertIn(self._COUNTS + "\n", stderr)
        self.assertIsNone(json.loads(stdout)[0]["tracks"])

    def test_no_favorites_suppresses_the_music_counts_notice(self) -> None:
        # Music.app's counts and year are one query, which --no-favorites skips, so
        # its null facts were declined, not lost.
        ref = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2001, 1, 1))
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            notices.report_degradation(
                ref,
                None,
                ArtworkMatch(url=None, year=None),
                source="music",
                no_favorites=True,
                no_artwork=True,
            )
        self.assertEqual(stderr.getvalue(), "")

    def test_an_unresolved_cover_is_reported_per_album(self) -> None:
        stdout, stderr = self._run(["--no-favorites"], run=no_music, fetch=_empty_store)
        self.assertIn(self._COVER + "\n", stderr)
        self.assertIsNone(json.loads(stdout)[0]["artwork"])

    def test_a_failed_cover_lookup_says_so_rather_than_no_match(self) -> None:
        # The two nulls used to share one line naming both causes, which is how a
        # search-index miss passed for a network blip in every refresh for weeks.
        stdout, stderr = self._run(
            ["--no-favorites"], run=no_music, fetch=_failing_store
        )
        self.assertIn(self._COVER_FAILED + "\n", stderr)
        self.assertNotIn(self._COVER + "\n", stderr)
        self.assertIsNone(json.loads(stdout)[0]["artwork"])

    def test_the_cover_notice_names_no_art_and_claims_no_cause_it_was_not_given(
        self,
    ) -> None:
        # Two nulls the CLI path cannot stage from a folder: the store's own match
        # with no usable image, and a null match built without a reason, which must
        # not be passed off as either cause.
        ref = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2001, 1, 1))
        expected = {
            MISS_NO_ART: "the store has this album but no usable cover image for it",
            None: "no verified store match, or the lookup failed",
        }
        for reason, text in expected.items():
            with self.subTest(reason=reason):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    notices.report_degradation(
                        ref,
                        None,
                        ArtworkMatch(url=None, year=None, reason=reason),
                        source="music",
                        no_favorites=True,
                        no_artwork=False,
                    )
                self.assertEqual(
                    stderr.getvalue(),
                    "album_scan: cover art not resolved for An Artist - A Record "
                    f"({text})\n",
                )

    def test_a_track_whose_tags_cannot_be_read_is_reported_by_name(self) -> None:
        album = self.root / "An Artist" / "A Record"
        (album / "01.mp3").write_bytes(b"ID3\x04\x00\x00\x7f\x7f\x7f\x7fgarbage")
        stdout, stderr = self._run(["--no-artwork"], run=no_music, fetch=no_store)
        self.assertIn(
            "album_scan: could not read the tags of 01.mp3; counted as an unrated "
            "track\n",
            stderr,
        )
        self.assertNotIn(str(self.root), stderr)
        self.assertEqual(json.loads(stdout)[0]["tracks"], 1)

    def test_tracks_with_no_popm_rating_are_reported_as_favorites_unknown(
        self,
    ) -> None:
        (self.root / "An Artist" / "A Record" / "01.m4a").write_bytes(b"")
        _, stderr = self._run(["--no-artwork"], run=no_music, fetch=no_store)
        self.assertIn(
            "album_scan: favorites unknown for An Artist - A Record (no track "
            "carries an ID3 POPM rating)\n",
            stderr,
        )
        _, declined = self._run(
            ["--no-artwork", "--no-favorites"], run=no_music, fetch=no_store
        )
        self.assertNotIn("favorites unknown", declined)

    def test_no_artwork_suppresses_the_cover_notice(self) -> None:
        _, stderr = self._run(
            ["--no-artwork", "--no-favorites"], run=no_music, fetch=no_store
        )
        self.assertNotIn("cover art not resolved", stderr)


class SkippedFolderNoticeTest(unittest.TestCase):
    """An artist folder the scan cannot list: one stderr line, by name, and on it goes."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    @posix_filesystem_only
    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 directory")
    def test_an_unreadable_artist_folder_is_reported_by_name_only(self) -> None:
        locked = self.root / "Locked"
        locked.mkdir()
        self.addCleanup(os.chmod, locked, 0o755)
        os.chmod(locked, 0o000)
        (self.root / "Readable" / "Record").mkdir(parents=True)
        code, stdout, stderr = _run(
            [
                "--library",
                str(self.root),
                "--output",
                "-",
                "--no-artwork",
                "--no-favorites",
                "--source",
                "folder",
            ],
            run=no_music,
            fetch=no_store,
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)[0]["artist"], "Readable")
        self.assertIn("album_scan: skipped unreadable folder Locked\n", stderr)
        self.assertNotIn(str(self.root), stderr)


if __name__ == "__main__":
    unittest.main()
