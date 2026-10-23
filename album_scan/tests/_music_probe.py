"""The Music.app reachability probe both live suites decide their skip with.

Held once, here, because `tests/test_live_music.py` and `tests/test_live_favorites.py`
gate their live class on the same question: can Music.app answer a minimal AppleScript
at all? Each subject they exercise (`newest_albums_from_music`, `album_facts`) swallows
every failure by contract, so probing through either could never separate "Music.app
unreachable" from "reachable but the contract moved" -- the one distinction a live
file exists to make. So the probe calls the toolbox's own `_real_run` directly, the one
thing that does not swallow a broken invocation, and only a genuine `OSError`, a
timeout or a non-zero exit from this MINIMAL, read-only script counts as unreachable.
`live_toolbox` finds that toolbox the way `album_scan` does, so a live file skips where
none is installed rather than failing to import.

Not discovered (no `test_` prefix); `tests/test_music_probe.py` proves its
classification against a faked `run`, once, for both callers.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import album_scan  # noqa: E402 - imported for its bootstrap, and its location
from album_scan import Runner  # noqa: E402 - import follows the sys.path bootstrap above

# album_scan's bootstrap above is what puts its private src/ on sys.path.
from apple_source import (  # noqa: E402 - import follows the sys.path bootstrap above
    ToolboxModules,
    ToolboxNotFoundError,
    import_toolbox,
    locate_toolbox,
)

# Bounds the SCANNING calls the live files make -- a `whose` scan visits every track
# in the library, measured at many seconds against a large real library --
# mirroring recently_added's own _MUSIC_TIMEOUT_SECONDS: a short bound would
# misreport a genuinely slow-but-working query as a broken contract, since each call
# answers a timeout and a broken contract identically (`[]` from the lookup, `None`
# from a probe or from `album_facts`).
SCANNING_TIMEOUT_SECONDS = 60.0
# The reachability probe scans NOTHING -- it is a bare count of one playlist, measured
# at ~0.2s -- so it gets its own far tighter bound rather than borrowing the scanning
# one. Not tidiness: a stalled boundary costs a live file exactly its bounds, once per
# call, and `python_gate` budgets 600s for the WHOLE unit. Sized at 50x the measured
# cost, which is generous for a call that does no scanning and still leaves the unit's
# worst case an order of magnitude inside its budget.
REACHABILITY_TIMEOUT_SECONDS = 10.0
# Read-only and minimal on purpose: a live test reads no more of a real library than
# it needs, so this is a bare count, not a track dump. Verified live against a large
# real library.
PROBE_SCRIPT = 'tell application "Music" to (count of tracks of library playlist 1)'


def live_toolbox() -> ToolboxModules | None:
    """The apple-music-toolbox `album_scan` would import, or None where none is found."""
    try:
        root = locate_toolbox(None, environ=os.environ, start=Path(album_scan.__file__))
    except ToolboxNotFoundError:
        return None
    return import_toolbox(root)


def music_is_reachable(*, run: Runner) -> bool:
    """True unless Music.app itself cannot answer a minimal AppleScript probe.

    A non-zero exit, an `OSError`, or a TIMEOUT from this probe means osascript
    itself, or Music.app, is not exercisable on this host -- absent, not running, not
    scriptable, or automation permission denied -- and only that counts as a skip.

    `run` is injected so every branch of the decision that gates a live class is
    provable without a real `osascript` process, so the gate never depends on this
    machine's Music.app being awake. A live class passes the toolbox's own runner.

    A timeout counts as unreachable rather than escaping. `TimeoutExpired` is a
    `SubprocessError`, NOT an `OSError`, so catching only the latter would let a
    stalled boundary raise straight out of this helper -- turning the skip it exists
    to decide into an unhandled error. "Accepted the call and never answered" is
    exactly as unexercisable as "absent", and it is the shape an automation-consent
    prompt that nobody can answer actually takes on an unattended host.
    """
    try:
        result = run(["osascript", "-e", PROBE_SCRIPT], REACHABILITY_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
