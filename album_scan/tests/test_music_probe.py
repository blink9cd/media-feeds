"""`music_is_reachable`'s classification, against a faked `run`.

The probe in `_music_probe.py` gates the live class of both live files, so every
branch of that decision is proved here, once, without a real `osascript` process: the
gate never depends on this machine's Music.app being awake and answering. These run
regardless of `ALBUM_SCAN_OFFLINE`, deterministic and process-free.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _music_probe import (  # noqa: E402 - import follows the sys.path bootstrap above
    REACHABILITY_TIMEOUT_SECONDS,
    music_is_reachable,
)


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


if __name__ == "__main__":
    unittest.main()
