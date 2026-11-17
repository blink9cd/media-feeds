"""`_music_probe.py`'s decisions, against a faked `run`.

The probe in `_music_probe.py` gates the live class of both live files, so every
branch of that decision is proved here, once, without a real `osascript` process: the
gate never depends on this machine's Music.app being awake and answering. So is the
timeout recorder both live files read an empty answer with: the toolbox absorbs a
timed-out scan into `[]`, and only the recorder can tell that `[]` from an empty
library. These run regardless of `ALBUM_SCAN_OFFLINE`, deterministic and
process-free.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _music_probe import (  # noqa: E402 - import follows the sys.path bootstrap above
    REACHABILITY_TIMEOUT_SECONDS,
    SCANNING_TIMEOUT_SECONDS,
    TimeoutRecordingRunner,
    live_toolbox,
    music_is_reachable,
    timeout_skip_reason,
)
from _seams import needs_toolbox  # noqa: E402 - import follows the sys.path bootstrap above


class MusicIsReachableTest(unittest.TestCase):
    def test_a_clean_zero_exit_is_reachable(self) -> None:
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["osascript"], returncode=0, stdout="12\n", stderr=""
            )

        self.assertIs(music_is_reachable(run=run), True)

    def test_a_nonzero_exit_is_unreachable(self) -> None:
        # Automation permission denied answers this way (-1743), as does a Music.app
        # that is present but not scriptable.
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["osascript"], returncode=1, stdout="", stderr="not authorized"
            )

        self.assertIs(music_is_reachable(run=run), False)

    def test_an_oserror_is_unreachable(self) -> None:
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            raise OSError("osascript not found")

        self.assertIs(music_is_reachable(run=run), False)

    def test_a_timeout_is_unreachable(self) -> None:
        # A boundary that accepts the call and never answers is just as
        # unexercisable as one that is absent, and the probe's own docstring already
        # counts "automation permission denied" as a skip, which is the state a
        # consent prompt nobody can answer actually produces.
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(["osascript"], REACHABILITY_TIMEOUT_SECONDS)

        self.assertIs(music_is_reachable(run=run), False)


def _times_out(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """A fake `run` whose call never answers inside its bound."""
    raise subprocess.TimeoutExpired(args, timeout)


def _answers_empty(
    _args: list[str], _timeout: float
) -> subprocess.CompletedProcess[str]:
    """A fake `run` that answers cleanly with nothing: a reachable, empty window."""
    return subprocess.CompletedProcess(
        args=["osascript"], returncode=0, stdout="", stderr=""
    )


class TimeoutRecordingRunnerTest(unittest.TestCase):
    def test_a_timeout_is_recorded_and_still_raised(self) -> None:
        # Re-raised, not swallowed: the subject must see exactly what it would have
        # seen from the wrapped run, so recording leaves its behavior unchanged.
        recorder = TimeoutRecordingRunner(_times_out)
        with self.assertRaises(subprocess.TimeoutExpired):
            recorder(["osascript"], SCANNING_TIMEOUT_SECONDS)
        self.assertEqual(len(recorder.timeouts), 1)
        self.assertEqual(recorder.timeouts[0].timeout, SCANNING_TIMEOUT_SECONDS)

    @needs_toolbox
    def test_a_timed_out_scan_answers_empty_and_is_recorded(self) -> None:
        toolbox = live_toolbox()
        assert toolbox is not None  # needs_toolbox found it; narrows for mypy
        recorder = TimeoutRecordingRunner(_times_out)
        refs = toolbox.recently_added.newest_albums_from_music(
            1, run=recorder, timeout=SCANNING_TIMEOUT_SECONDS
        )
        self.assertEqual(refs, [])
        self.assertEqual(len(recorder.timeouts), 1)

    @needs_toolbox
    def test_an_empty_answer_without_a_timeout_records_nothing(self) -> None:
        toolbox = live_toolbox()
        assert toolbox is not None  # needs_toolbox found it; narrows for mypy
        recorder = TimeoutRecordingRunner(_answers_empty)
        refs = toolbox.recently_added.newest_albums_from_music(
            1, run=recorder, timeout=SCANNING_TIMEOUT_SECONDS
        )
        self.assertEqual(refs, [])
        self.assertEqual(recorder.timeouts, [])


class TimeoutSkipReasonTest(unittest.TestCase):
    def test_a_recorded_timeout_reads_as_a_skip_naming_it(self) -> None:
        reason = timeout_skip_reason(
            [subprocess.TimeoutExpired(["osascript"], SCANNING_TIMEOUT_SECONDS)]
        )
        assert reason is not None  # narrows for mypy; asserted again below
        self.assertIn("timed out", reason)
        self.assertIn(f"{SCANNING_TIMEOUT_SECONDS:g}s", reason)
        # A hang that recurs on every run (an unanswered consent prompt, a wedged
        # Music.app) also lands here, so the message hedges rather than ruling the
        # contract out.
        self.assertIn("most likely", reason)

    def test_no_recorded_timeout_leaves_the_empty_answer_to_fail(self) -> None:
        self.assertIsNone(timeout_skip_reason([]))


if __name__ == "__main__":
    unittest.main()
