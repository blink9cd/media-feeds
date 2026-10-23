"""The two boundary fakes an offline test injects into `main`: each raises if reached.

`main`'s defaults are the real boundaries -- `_real_run` spawns an `osascript`
process and `_real_fetch` opens an HTTPS connection -- and a flag such as `--source
folder`, `--no-favorites` or `--no-artwork` keeps a call off one of them only while
the code honoring that flag is correct, which is exactly what a regression, or a
mutant, breaks. So every `main` call passes both `run=` and `fetch=` (the aggregator's
AST guard fails one that does not), and a case that needs neither boundary passes
these two, which turn any reach into a failure naming the boundary reached. A case
that needs one boundary to ANSWER defines its own answering fake beside its tests and
still passes one of these for the other.

It also holds the one Windows skip the POSIX-filesystem cases share, so each guard
names the same reason instead of restating it, and the skip every case driving the REAL
apple-music-toolbox shares: those exercise the toolbox's own parsing of a faked
`osascript` reply, so they need the toolbox installed where `album_scan` would find it,
which a standalone clone of this project need not have. `music_answering` is the fake
`run` most of them pass.

Not discovered (no `test_` prefix). The suites import it from their own directory,
which each puts on `sys.path` beside the tool folder.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from collections.abc import Callable
from pathlib import Path

# The tool folder, whose bootstrap puts its private src/ (and the libraries src/ needs)
# on sys.path when it is imported: the toolbox lookup this module's skip asks is there.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import album_scan  # noqa: E402 - imported for its bootstrap, and its location
from apple_source import ToolboxNotFoundError, locate_toolbox  # noqa: E402 - follows the bootstrap

#: Windows has no POSIX file modes or control-character file names: `os.chmod` on a
#: directory toggles only its read-only attribute, which blocks neither listing it nor
#: writing inside it, stat reports no group/other bits, and the filesystem refuses a
#: control character in a name. A case that builds one of those cannot set up there.
_POSIX_ONLY_REASON = "needs POSIX file modes and file names, which Windows lacks"

#: Decorator for a case whose setup only a POSIX filesystem can build.
posix_filesystem_only = unittest.skipIf(sys.platform == "win32", _POSIX_ONLY_REASON)


def _toolbox_is_found() -> bool:
    """Whether `album_scan` would find the apple-music-toolbox from where it stands."""
    try:
        locate_toolbox(
            None,
            environ=os.environ,
            start=Path(album_scan.__file__),
        )
    except ToolboxNotFoundError:
        return False
    return True


#: Decorator for a case that drives the real apple-music-toolbox through a fake `run`.
needs_toolbox = unittest.skipUnless(
    _toolbox_is_found(),
    "needs the apple-music-toolbox beside this project (or --toolbox / "
    "APPLE_MUSIC_TOOLBOX); the folder-source cases need none",
)


def music_answering(
    newest: str, facts: str, *, facts_code: int = 0
) -> Callable[[list[str], float], subprocess.CompletedProcess[str]]:
    """A fake `run` answering the date-added query with `newest` and any other with `facts`.

    A `--source music` run asks Music.app two different questions through one seam:
    which albums were added most recently, and then each album's counts. They are told
    apart by the script itself, since only the first asks about "date added".
    """

    def run(args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        if "date added" in args[-1]:
            return subprocess.CompletedProcess(
                args=["osascript"], returncode=0, stdout=newest, stderr=""
            )
        return subprocess.CompletedProcess(
            args=["osascript"], returncode=facts_code, stdout=facts, stderr=""
        )

    return run


def no_music(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
    """The `run` seam for a case that must never reach Music.app."""
    raise AssertionError("test reached the real Music.app boundary")


def no_store(_url: str, _timeout: float) -> bytes:
    """The `fetch` seam for a case that must never reach the iTunes Search API."""
    raise AssertionError("test reached the real iTunes boundary")
