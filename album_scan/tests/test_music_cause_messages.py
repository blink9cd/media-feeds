"""Every Music.app failure named by its cause, end to end through `main`.

The toolbox answers a timeout, an osascript that cannot run, a failed query, an empty
lookback window and an unusable answer all with the same `[]` (and the facts query
with the same `None`). These cases force each one offline through the `run` seam and
read what `main` says about it: under `--source music` the no-album line, under
`--source auto` the fallback notice, and under both the per-album counts line. The exit
codes stay what they were, save one: a denied automation permission that leaves no
album exits 3, not 1, so a scheduler reports the lost grant instead of a quiet day. A
lookup that never reaches `run` (nothing recorded) keeps the wording it had before
causes were named.

They drive the real apple-music-toolbox's parsing, so they skip where none is found.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the helpers beside it

from _music_probe import live_toolbox  # noqa: E402 - import follows the sys.path bootstrap above
from _seams import DENIED_STDERR, completed, needs_toolbox, no_store  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import main  # noqa: E402 - import follows the sys.path bootstrap above

#: One `osascript` call's outcome: an exception to raise, or a result to return.
Outcome = BaseException | subprocess.CompletedProcess[str]
Run = Callable[[list[str], float], subprocess.CompletedProcess[str]]

#: Music.app's date-added reply naming one album.
_NEWEST = "2026-08-20T10:00:00\tAn Artist\tA Record\n"

#: What osascript prints when an Apple event would need consent nobody can give (an
#: unattended run cannot show the prompt), with an invented script offset.
_CONSENT_STDERR = (
    "0:42: execution error: Sending Apple events to Music needs user consent. (-1744)\n"
)

_NO_ALBUM = "album_scan: no album: "
_FALLBACK = "album_scan: --source auto found no album ("
_CAVEAT = (
    "); falling back to the folder scan, which ranks by each folder's birth time; "
    'that is not a reliable "date added" (see album_scan.md, "Why the folder '
    "scan's date is a heuristic\")"
)
_COUNTS = "album_scan: counts unavailable for An Artist - A Record ("


def _answer(outcome: Outcome, args: list[str], timeout: float) -> Outcome:
    """`outcome`, with a timeout rebuilt to carry the bound the toolbox passed."""
    if isinstance(outcome, subprocess.TimeoutExpired):
        return subprocess.TimeoutExpired(args, timeout)
    return outcome


def _scripted(newest: Outcome, facts: Outcome) -> Run:
    """A fake `run`: `newest` for the date-added query, `facts` for any other."""

    def run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        outcome = _answer(newest if "date added" in args[-1] else facts, args, timeout)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return run


@needs_toolbox
class _CauseCase(unittest.TestCase):
    """Shared setup: a library with one album folder, and the toolbox's own bounds."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "Folder Artist" / "Folder Record").mkdir(parents=True)
        toolbox = live_toolbox()
        assert toolbox is not None  # needs_toolbox found it; narrows for mypy
        module = toolbox.recently_added
        self.bound = f"{module._MUSIC_TIMEOUT_SECONDS:g}s"
        self.window = module._MUSIC_LOOKBACK_DAYS

    def _main(self, source: str, run: Run, *extra: str) -> tuple[int, str]:
        """Drive `main` under `source`; the exit code and stderr."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    "-",
                    "--no-artwork",
                    "--source",
                    source,
                    *extra,
                ],
                fetch=no_store,
                run=run,
            )
        return code, stderr.getvalue()


class NoMusicAlbumCauseTest(_CauseCase):
    """`--source music` with no album: exit 1 (3 for a denial), and the line names why."""

    def _no_album_line(self, newest: Outcome, *, code: int = 1) -> str:
        got, stderr = self._main("music", _scripted(newest, completed(0)))
        self.assertEqual(got, code)
        return next(line for line in stderr.splitlines() if "album_scan: " in line)

    def test_a_timeout_names_the_bound(self) -> None:
        line = self._no_album_line(subprocess.TimeoutExpired([], 0))
        self.assertEqual(line, f"{_NO_ALBUM}Music.app timed out after {self.bound}")

    def test_a_missing_osascript_says_it_could_not_run(self) -> None:
        line = self._no_album_line(FileNotFoundError("osascript"))
        self.assertEqual(line, f"{_NO_ALBUM}osascript could not be run")

    def test_a_denied_permission_is_named_and_exits_3(self) -> None:
        # Exit 3, not 1: a lost grant is a failure to report, never "no album today".
        line = self._no_album_line(completed(1, stderr=DENIED_STDERR), code=3)
        self.assertEqual(
            line,
            f"{_NO_ALBUM}automation permission denied (osascript exit 1: "
            f"{DENIED_STDERR.strip()})",
        )

    def test_a_consent_refusal_is_a_denial_too(self) -> None:
        line = self._no_album_line(completed(1, stderr=_CONSENT_STDERR), code=3)
        self.assertEqual(
            line,
            f"{_NO_ALBUM}automation permission denied (osascript exit 1: "
            f"{_CONSENT_STDERR.strip()})",
        )

    def test_a_failed_query_echoes_stderr_with_controls_escaped(self) -> None:
        line = self._no_album_line(completed(1, stderr="it\x1b[2Jbroke\n"))
        self.assertEqual(
            line,
            f"{_NO_ALBUM}the Music.app query failed (osascript exit 1: "
            "it\\x1b[2Jbroke)",
        )

    def test_an_empty_window_names_its_width(self) -> None:
        line = self._no_album_line(completed(0, stdout="\n"))
        self.assertEqual(
            line,
            f"{_NO_ALBUM}Music.app has nothing added within the last {self.window} days",
        )

    def test_an_answer_with_no_album_row_says_so(self) -> None:
        line = self._no_album_line(completed(0, stdout="garbage\n"))
        self.assertEqual(
            line, f"{_NO_ALBUM}Music.app's answer held no usable album row"
        )

    def test_a_lookup_that_never_reached_run_keeps_the_generic_line(self) -> None:
        with mock.patch("album_lookups.music_refs", return_value=[]):
            code, stderr = self._main("music", _scripted(completed(0), completed(0)))
        self.assertEqual(code, 1)
        self.assertIn(
            "album_scan: no album: Music.app reported no recently added album\n", stderr
        )


class FallbackCauseTest(_CauseCase):
    """`--source auto` falls back on every cause, and its notice names the cause."""

    def _notice(self, newest: Outcome, *, code: int = 0) -> str:
        got, stderr = self._main("auto", _scripted(newest, completed(0)))
        # Fell back and found the folder's album: the exit is the folder's success,
        # save for a denial, which exits 3 with the record written all the same.
        self.assertEqual(got, code)
        return next(line for line in stderr.splitlines() if "falling back" in line)

    def test_each_cause_is_named_and_the_caveat_kept(self) -> None:
        causes: list[tuple[Outcome, str]] = [
            (
                subprocess.TimeoutExpired([], 0),
                f"Music.app timed out after {self.bound}",
            ),
            (FileNotFoundError("osascript"), "osascript could not be run"),
            (
                completed(1, stderr="it broke\n"),
                "the Music.app query failed (osascript exit 1: it broke)",
            ),
            (
                completed(0, stdout=""),
                f"Music.app has nothing added within the last {self.window} days",
            ),
            (
                completed(0, stdout="garbage"),
                "Music.app's answer held no usable album row",
            ),
        ]
        for newest, cause in causes:
            with self.subTest(cause=cause):
                self.assertEqual(self._notice(newest), f"{_FALLBACK}{cause}{_CAVEAT}")

    def test_a_denial_still_writes_the_folder_record_but_exits_3(self) -> None:
        # The site stays fresh from the folder, and the lost grant is still reported:
        # a denial never passes as a quiet day, whatever the fallback finds.
        for stderr_text in (DENIED_STDERR, _CONSENT_STDERR):
            with self.subTest(stderr=stderr_text):
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(
                        [
                            "--library",
                            str(self.root),
                            "--output",
                            "-",
                            "--no-artwork",
                            "--source",
                            "auto",
                        ],
                        fetch=no_store,
                        run=_scripted(completed(1, stderr=stderr_text), completed(0)),
                    )
                self.assertEqual(code, 3)
                records = json.loads(stdout.getvalue())
                self.assertEqual(
                    [(r["artist"], r["album"]) for r in records],
                    [("Folder Artist", "Folder Record")],
                )
                self.assertIn(
                    f"{_FALLBACK}automation permission denied (osascript exit 1: "
                    f"{stderr_text.strip()}){_CAVEAT}",
                    stderr.getvalue(),
                )
                self.assertIn("album_scan: wrote 1 album(s)", stderr.getvalue())

    def test_an_unknown_cause_lists_a_timeout_among_the_possibilities(self) -> None:
        with mock.patch("album_lookups.music_refs", return_value=[]):
            notice = self._notice(completed(0))
        self.assertEqual(
            notice,
            f"{_FALLBACK}Music.app is absent, not running or not scriptable, timed "
            "out, was denied automation permission, or has nothing added within its "
            "lookback window"
            f"{_CAVEAT}",
        )

    def test_an_empty_folder_after_a_fallback_still_exits_1(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                ["--library", str(empty), "--no-artwork", "--source", "auto"],
                fetch=no_store,
                run=_scripted(subprocess.TimeoutExpired([], 0), completed(0)),
            )
        self.assertEqual(code, 1)
        self.assertIn(
            f"{_FALLBACK}Music.app timed out after {self.bound}", stderr.getvalue()
        )

    def test_an_empty_folder_after_a_denied_fallback_exits_3(self) -> None:
        # The folder had nothing either, so the run ends with no album; the denial is
        # still why Music.app could not answer, and a scheduler must hear of it.
        empty = self.root / "empty"
        empty.mkdir()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                ["--library", str(empty), "--no-artwork", "--source", "auto"],
                fetch=no_store,
                run=_scripted(completed(1, stderr=DENIED_STDERR), completed(0)),
            )
        self.assertEqual(code, 3)
        self.assertIn(f"{_FALLBACK}automation permission denied", stderr.getvalue())


class FactsCauseLineTest(_CauseCase):
    """The counts line under a Music.app source names why the facts are null."""

    def _counts_line(self, source: str, facts: Outcome) -> str:
        code, stderr = self._main(source, _scripted(completed(0, _NEWEST), facts))
        self.assertEqual(code, 0)
        return next(line for line in stderr.splitlines() if _COUNTS in line)

    def test_each_cause_is_named_under_both_music_sources(self) -> None:
        causes: list[tuple[Outcome, str]] = [
            (
                subprocess.TimeoutExpired([], 0),
                f"Music.app timed out after {self.bound}",
            ),
            (FileNotFoundError("osascript"), "osascript could not be run"),
            (
                completed(1, stderr=DENIED_STDERR),
                "automation permission denied (osascript exit 1: "
                f"{DENIED_STDERR.strip()})",
            ),
            (
                completed(1, stderr="odd\x07bell"),
                "the Music.app query failed (osascript exit 1: odd\\x07bell)",
            ),
            (completed(0, stdout=""), "Music.app's answer was empty"),
            (
                completed(0, stdout="0\t0\t0"),
                "not in Music.app's library, or its answer held no usable counts",
            ),
            (
                completed(0, stdout="garbage"),
                "not in Music.app's library, or its answer held no usable counts",
            ),
        ]
        for source in ("music", "auto"):
            for facts, cause in causes:
                with self.subTest(source=source, cause=cause):
                    self.assertEqual(
                        self._counts_line(source, facts), f"{_COUNTS}{cause})"
                    )

    def test_facts_that_never_reached_run_keep_the_generic_reason(self) -> None:
        with mock.patch("album_lookups.music_facts", return_value=None):
            line = self._counts_line("music", completed(0))
        self.assertEqual(
            line,
            f"{_COUNTS}not in Music.app's library, or Music.app could not answer)",
        )


if __name__ == "__main__":
    unittest.main()
