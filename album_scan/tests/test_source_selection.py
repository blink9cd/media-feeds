"""`--source {auto,music,folder}` dispatch: which source answers, and when.

One cohesive group -- which source answers, and what each one does with --library --
with its own helpers, which share nothing with test_album_scan_cli.py's. They fake
both boundary seams, so nothing here reaches Music.app or the store.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import needs_toolbox, no_store  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import (  # noqa: E402 - import follows the sys.path bootstrap above
    build_parser,
    main,
)


def _music_result(returncode: int, stdout: str) -> subprocess.CompletedProcess[str]:
    """A fake `osascript` result, built without spawning a real process."""
    return subprocess.CompletedProcess(
        args=["osascript"], returncode=returncode, stdout=stdout, stderr=""
    )


def _explode_run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
    """A `run` fake that fails the test if the seam is invoked at all.

    `--source folder` reads every fact from the library itself -- the album list from
    folder birth times, the counts, year and favorites from the files' own tags -- so
    no Music.app query may run under it, whether or not `--no-favorites` is passed.
    """
    raise AssertionError("run must not be called with --source folder")


# -- --source {folder,music,auto}: the folder scan is the default, Music.app an
# explicit choice (music), and Music.app with the folder scan as its fallback a third
# (auto). Every case that calls main injects a fake `run` and a `fetch` that raises, so
# nothing below spawns a real osascript process, touches Music.app or reaches the
# network; the Music.app cases drive the real toolbox's parsing, so they skip where no
# toolbox is installed beside this project. --


class SourceSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_source_defaults_to_folder(self) -> None:
        # The source that needs nothing installed beyond this project: a run naming
        # no source reads no other program, and a caller wanting Music.app says so.
        self.assertEqual(build_parser().parse_args([]).source, "folder")

    def test_source_rejects_an_invalid_choice(self) -> None:
        # Captured, not left to print into every suite run: the usage error is the
        # assertion's subject, so it is read back here rather than shown.
        buffer = io.StringIO()
        with self.assertRaises(SystemExit) as caught:
            with redirect_stderr(buffer):
                build_parser().parse_args(["--source", "bogus"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("argument --source: invalid choice: 'bogus'", buffer.getvalue())

    @needs_toolbox
    def test_auto_prefers_music_and_never_touches_an_invalid_library(self) -> None:
        # --library points at a directory that does not exist. If auto fell through
        # to the folder scan anyway, this would exit 2 (NotADirectoryError) instead
        # of 0 -- so a passing exit 0 here proves the folder scan was never attempted.
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return _music_result(
                0, "2001-03-15T10:00:00\tThe Invented Quartet\tPlaceholder Album\n"
            )

        out = self.root / "album.json"
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(
                [
                    "--library",
                    str(self.root / "does-not-exist"),
                    "--output",
                    str(out),
                    "--no-artwork",
                    "--source",
                    "auto",
                ],
                fetch=no_store,
                run=run,
            )
        self.assertEqual(code, 0)
        # Always a JSON array, even at the default --count of one.
        record = json.loads(out.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["artist"], "The Invented Quartet")
        # The success line always names which source answered, and which toolbox
        # folder was imported, so a wrong pick is visible; a genuine Music.app hit
        # prints no fallback notice at all.
        self.assertIn("(source: music, toolbox: ", buffer.getvalue())
        self.assertNotIn("falling back", buffer.getvalue())

    @needs_toolbox
    def test_auto_falls_back_to_folder_when_music_has_nothing(self) -> None:
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return _music_result(1, "")  # Music.app not running, say

        (self.root / "Folder Artist" / "Folder Record").mkdir(parents=True)
        out = self.root / "album.json"
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    str(out),
                    "--no-artwork",
                    "--source",
                    "auto",
                ],
                fetch=no_store,
                run=run,
            )
        self.assertEqual(code, 0)
        record = json.loads(out.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["artist"], "Folder Artist")
        # A Music.app-to-folder fallback under --source auto must be LOUD, not
        # silent -- the folder heuristic mis-ranks a real library (album_scan.md,
        # "Why the folder scan's date is a heuristic"), so the operator needs to see
        # the fallback fired, not just infer it from the record's own contents.
        output = buffer.getvalue()
        self.assertIn("falling back", output)
        # The toolbox was still imported, so it is still named.
        self.assertIn("(source: folder, toolbox: ", output)

    def test_source_folder_reports_folder_as_the_answering_source(self) -> None:
        (self.root / "Folder Artist" / "Folder Record").mkdir(parents=True)
        out = self.root / "album.json"
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    str(out),
                    "--no-artwork",
                    "--no-favorites",
                    "--source",
                    "folder",
                ],
                fetch=no_store,
                run=_explode_run,
            )
        self.assertEqual(code, 0)
        # No toolbox is opened for --source folder, so none is named.
        self.assertIn("(source: folder)\n", buffer.getvalue())
        # --source folder is an explicit choice, not auto's fallback -- no notice.
        self.assertNotIn("falling back", buffer.getvalue())

    @needs_toolbox
    def test_source_music_reports_music_as_the_answering_source(self) -> None:
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return _music_result(
                0, "2001-03-15T10:00:00\tThe Invented Quartet\tPlaceholder Album\n"
            )

        out = self.root / "album.json"
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    str(out),
                    "--no-artwork",
                    "--source",
                    "music",
                ],
                fetch=no_store,
                run=run,
            )
        self.assertEqual(code, 0)
        self.assertIn("(source: music, toolbox: ", buffer.getvalue())

    @needs_toolbox
    def test_source_music_ignores_the_library_entirely(self) -> None:
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return _music_result(
                0, "2001-03-15T10:00:00\tThe Invented Quartet\tPlaceholder Album\n"
            )

        out = self.root / "album.json"
        code = main(
            [
                "--library",
                str(self.root / "does-not-exist"),
                "--output",
                str(out),
                "--no-artwork",
                "--source",
                "music",
            ],
            fetch=no_store,
            run=run,
        )
        self.assertEqual(code, 0)
        record = json.loads(out.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["artist"], "The Invented Quartet")

    @needs_toolbox
    def test_source_music_with_count_above_one_writes_that_many_distinct_albums(
        self,
    ) -> None:
        """`--source music --count N>1`, end to end through `main`.

        recently_added's own suite proves the collapse-then-rank logic in
        isolation, and `test_album_scan_count.py` the folder path's `--count` CLI
        wiring, but only this test threads `count` through `resolve_albums`'s MUSIC
        branch. Hard-coding that branch to `newest_albums_from_music(1, run=run)`
        instead of `count` would satisfy every other test in this file, at 100% branch
        coverage, while silently capping every `--source music` run at one album
        regardless of `--count` -- this is the test that fails on exactly that
        mutation (verified by hand).
        """
        rows = "\n".join(
            f"2026-08-2{n}T10:00:00\tArtist {n}\tAlbum {n}" for n in range(3)
        )

        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return _music_result(0, rows)

        out = self.root / "album.json"
        code = main(
            [
                "--library",
                str(self.root),
                "--output",
                str(out),
                "--no-artwork",
                "--no-favorites",
                "--source",
                "music",
                "--count",
                "3",
            ],
            fetch=no_store,
            run=run,
        )
        self.assertEqual(code, 0)
        records = json.loads(out.read_text(encoding="utf-8"))
        albums = {(record["artist"], record["album"]) for record in records}
        self.assertEqual(len(albums), 3)

    @needs_toolbox
    def test_source_music_with_no_result_names_music_not_the_library(self) -> None:
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return _music_result(1, "")

        out = self.root / "album.json"
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    str(out),
                    "--no-artwork",
                    "--source",
                    "music",
                ],
                fetch=no_store,
                run=run,
            )
        self.assertEqual(code, 1)
        self.assertIn("music.app", buffer.getvalue().lower())
        self.assertFalse(out.exists())

    @needs_toolbox
    def test_source_music_with_a_refused_argv_is_no_album_not_a_traceback(
        self,
    ) -> None:
        # The date-added seam raising ValueError -- `subprocess.Popen`'s answer to
        # an argv element holding a NUL -- must read as "Music.app reported
        # nothing" (exit 1), the same never-raises outcome an OSError or a timeout
        # already gets, rather than escaping `main` as a traceback.
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            raise ValueError("embedded null byte")

        out = self.root / "album.json"
        with redirect_stderr(io.StringIO()):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    str(out),
                    "--no-artwork",
                    "--source",
                    "music",
                ],
                fetch=no_store,
                run=run,
            )
        self.assertEqual(code, 1)
        self.assertFalse(out.exists())

    def test_source_folder_never_calls_the_injected_runner(self) -> None:
        (self.root / "Folder Artist" / "Folder Record").mkdir(parents=True)
        out = self.root / "album.json"
        code = main(
            [
                "--library",
                str(self.root),
                "--output",
                str(out),
                "--no-artwork",
                "--source",
                "folder",
            ],
            fetch=no_store,
            run=_explode_run,
        )
        self.assertEqual(code, 0)
        record = json.loads(out.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["artist"], "Folder Artist")


if __name__ == "__main__":
    unittest.main()
