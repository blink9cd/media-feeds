"""One live call against the real `osascript` / Music.app boundary.

Unit tests (`recently_added/test_recently_added.py`) fake the `run` seam,
which proves the PARSING but not the CONTRACT: a renamed AppleScript property, a moved
dictionary, or Music.app's own response shape changing would leave every faked test
green. This test is the only thing in the suite that would notice -- which requires it
to FAIL, not skip, on a broken contract.

`newest_albums_from_music` never raises, so Music.app being absent, not running, not
scriptable, or automation-denied would ALL come back as a plain `[]` --
indistinguishable, from inside that function alone, from "reachable but nothing was
added recently". Skipping in that case would defeat the whole point of this file, so
reachability is decided separately, by the probe `_music_probe.py` holds for both live
files, which calls the module's own `_real_run` directly (the one thing that does NOT
swallow a broken invocation): only a genuine `OSError`, a timeout, or a non-zero exit
from a MINIMAL, read-only probe script counts as "unreachable" and skips. Once the
probe succeeds, the contract assertion below runs unconditionally, so a moved API
fails this test instead of blending into "maybe unavailable" -- as
`cover_art/tests/test_live_artwork.py` does for the iTunes Search API boundary.

A SECOND probe, `_any_recent_track_exists`, closes a related gap:
`newest_albums_from_music` returning `[]` on a reachable Music.app
is ALSO ambiguous between "genuinely nothing added within the lookback window" (a
legitimate state -- an empty library, or a long gap since the last refresh) and "the
AppleScript contract broke" (a renamed property, a changed date format). Without this
second probe, the assertion below would go red on any Mac with an empty library,
which is not the failure this file exists to catch. Only a CONFIRMED, clean zero-count
from that probe counts as emptiness and skips; anything else -- including the probe
itself failing to answer -- falls through to the real assertion, so a broken contract
still fails loudly rather than blending into a skip.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# This directory too, for the shared probe beside this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _music_probe import (  # noqa: E402 - import follows the sys.path bootstrap above
    SCANNING_TIMEOUT_SECONDS,
    live_toolbox,
    music_is_reachable,
)
from album_scan import Runner  # noqa: E402 - import follows the sys.path bootstrap above

OFFLINE = os.environ.get("ALBUM_SCAN_OFFLINE") == "1"

#: A lookback for the deterministic cases below, which never send the script.
_ANY_LOOKBACK_DAYS = 200


def _recent_count_script(lookback_days: int) -> str:
    """The bare recent-track count, asked over the toolbox's own lookback window.

    Mirrors recently_added's own cutoff arithmetic exactly (the same lookback, the
    same "(current date) - (N * days)" expression), so this probe asks Music.app the
    identical question the real contract depends on -- a bare count, not a track
    dump, for the same read-no-more-than-needed reason as the reachability probe in
    _music_probe.py.
    """
    return (
        'tell application "Music" to (count of tracks of library playlist 1 whose '
        f"date added > ((current date) - ({lookback_days} * days)))"
    )


def _any_recent_track_exists(*, run: Runner, lookback_days: int) -> bool | None:
    """True/False for an unambiguous answer; `None` when the probe itself could not
    answer at all.

    `run` is injectable, as `music_is_reachable`'s in `_music_probe.py` is, so every
    branch here is provable without a real `osascript` process -- see
    `RecentTrackExistsTest`. `None` covers an `OSError` from `run`, a TIMEOUT, and a
    non-zero exit: each means the same "date added" property the real query also
    needs just failed to answer even a bare count, which is a CONTRACT problem, not
    emptiness -- the caller must not treat it as a reason to skip. Only a clean,
    confirmed zero-count is genuine emptiness.

    The timeout arm is listed explicitly because `TimeoutExpired` is a
    `SubprocessError` and not an `OSError`: catching only the latter would let a
    stalled boundary escape this helper entirely rather than classify as the
    ambiguous case it plainly is.
    """
    try:
        result = run(
            ["osascript", "-e", _recent_count_script(lookback_days)],
            SCANNING_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() not in ("", "0")


class RecentTrackExistsTest(unittest.TestCase):
    """`_any_recent_track_exists`'s own classification, against a faked `run`.

    Deterministic and process-free (unlike `LiveMusicLookupTest` below), so it runs
    regardless of `ALBUM_SCAN_OFFLINE` and proves the confirmed-empty-vs-ambiguous
    split without depending on this machine's real library state.
    """

    def test_a_positive_count_counts_as_recent_activity(self) -> None:
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["osascript"], returncode=0, stdout="3\n", stderr=""
            )

        self.assertIs(
            _any_recent_track_exists(run=run, lookback_days=_ANY_LOOKBACK_DAYS), True
        )

    def test_a_confirmed_zero_count_counts_as_genuinely_empty(self) -> None:
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["osascript"], returncode=0, stdout="0\n", stderr=""
            )

        self.assertIs(
            _any_recent_track_exists(run=run, lookback_days=_ANY_LOOKBACK_DAYS), False
        )

    def test_a_nonzero_exit_is_ambiguous_not_empty(self) -> None:
        # e.g. the "date added" property itself no longer exists -- a contract
        # break, which must NOT be mistaken for "nothing recent".
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["osascript"], returncode=1, stdout="", stderr="error"
            )

        self.assertIsNone(
            _any_recent_track_exists(run=run, lookback_days=_ANY_LOOKBACK_DAYS)
        )

    def test_an_oserror_is_ambiguous_not_empty(self) -> None:
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            raise OSError("osascript not found")

        self.assertIsNone(
            _any_recent_track_exists(run=run, lookback_days=_ANY_LOOKBACK_DAYS)
        )

    def test_a_timeout_is_ambiguous_not_empty(self) -> None:
        # A stalled boundary -- Music.app accepted the call and never answered, the
        # shape an unanswerable automation-consent prompt takes -- reaches the caller
        # as TimeoutExpired, which is a SubprocessError and NOT an OSError, so an
        # OSError-only handler would let it escape the helper instead of classifying.
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(["osascript"], 60.0)

        self.assertIsNone(
            _any_recent_track_exists(run=run, lookback_days=_ANY_LOOKBACK_DAYS)
        )


@unittest.skipIf(OFFLINE, "ALBUM_SCAN_OFFLINE=1 - live Music.app lookup skipped")
class LiveMusicLookupTest(unittest.TestCase):
    """Exercise the real Music.app boundary once, so a changed contract is caught."""

    def setUp(self) -> None:
        """Skip only for genuine unreachability or a confirmed-empty lookback
        window, both decided independently of the subject
        (`newest_albums_from_music`) -- see module docstring. A probe that itself
        could not answer (`None`) falls through to the real assertion below
        instead of skipping, so a broken contract still fails loudly rather than
        blending into a skip.
        """
        toolbox = live_toolbox()
        if toolbox is None:
            self.skipTest("no apple-music-toolbox is installed beside this project")
        self.recently_added = toolbox.recently_added
        run = self.recently_added._real_run
        if not music_is_reachable(run=run):
            self.skipTest("Music.app is not reachable on this host")
        lookback = self.recently_added._MUSIC_LOOKBACK_DAYS
        if _any_recent_track_exists(run=run, lookback_days=lookback) is False:
            self.skipTest(
                "Music.app is reachable but reports no track added within the "
                "lookback window -- a genuinely empty result, not a contract break"
            )

    def test_the_real_library_resolves_to_a_dated_album(self) -> None:
        try:
            refs = self.recently_added.newest_albums_from_music(
                1, timeout=SCANNING_TIMEOUT_SECONDS
            )
        except (
            Exception
        ) as exc:  # pragma: no cover - defensive; must not raise, see module docstring
            self.fail(
                f"newest_albums_from_music raised instead of returning []: {exc!r}"
            )
        # setUp already proved Music.app is reachable, so an empty list here is NOT
        # "maybe unavailable" -- it means either the AppleScript contract has moved (a
        # renamed property, a changed date format) or genuinely nothing was added to
        # this library within the lookback window; this assertion turns that into a
        # loud failure rather than a silent pass.
        self.assertTrue(
            refs,
            "Music.app is reachable but newest_albums_from_music returned [] -- "
            "either the AppleScript contract (a property name, the date format) has "
            "moved, or no track was added within the lookback window",
        )
        ref = refs[0]
        self.assertTrue(ref.artist)
        self.assertTrue(ref.album)
        self.assertIsInstance(ref.added, dt.date)


if __name__ == "__main__":
    unittest.main()
