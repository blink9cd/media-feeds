"""Why a Music.app lookup came back empty: a recording runner, and the cause it reads.

The apple-music-toolbox never raises: a timeout, an `osascript` that cannot run, a
query that exits nonzero, an empty lookback window and an answer with no usable row
all come back as the same `[]` from `newest_albums_from_music` (and the same `None`
from `album_facts`). That contract is the toolbox's to keep, so the cause is
recovered on this side of it instead: every Music.app query goes through `run`, and
`RecordingRunner` wraps that `run`, notes what came back, and hands the toolbox
exactly the same result or exception. The toolbox behaves as it always did; only the
record is new.

`newest_cause` and `facts_cause` read the last record and name what happened, as a
phrase the notices print. None means nothing was recorded (a toolbox that never
called `run`, or a lookup patched out under test), and the caller keeps the generic
wording it used before causes could be named.
"""

from __future__ import annotations

import subprocess
from typing import NamedTuple

from apple_source import Runner
from notices import terminal_safe_text

#: The failures the toolbox absorbs into its empty answer, and so the only ones
#: recorded: anything else escapes the toolbox, which then returns no answer at all.
#: ValueError is `subprocess.Popen`'s refusal of an argv element holding a NUL.
_ABSORBED = (OSError, subprocess.SubprocessError, ValueError)

#: osascript's error numbers for an Apple event the automation permission refused:
#: -1743 a refusal outright, -1744 an event that would need consent nobody can give
#: (an unattended run cannot show the prompt). Both are the same lost grant.
_PERMISSION_DENIED_CODES = ("-1743", "-1744")

#: The cause's opening words for a refused Apple event; `is_permission_denied` keys on it.
PERMISSION_DENIED_LABEL = "automation permission denied"

#: The longest stretch of osascript's stderr line echoed: it is diagnostic text, and a
#: runaway one must not swamp the terminal.
STDERR_ECHO_LIMIT = 200

#: The cause phrases the notices print, one per thing that can have happened. Each
#: names Music.app or osascript itself, because each is printed with no subject
#: around it: after "no album:" and inside the auto notice's parentheses.
_TIMED_OUT = "Music.app timed out after {bound:g}s"
_CANNOT_RUN = "osascript could not be run"
_EMPTY_WINDOW = "Music.app has nothing added within the last {days} days"
_EMPTY_WINDOW_UNKNOWN = "Music.app has nothing added within its lookback window"
_NO_ALBUM_ROW = "Music.app's answer held no usable album row"
_EMPTY_FACTS = "Music.app's answer was empty"
_NO_USABLE_COUNTS = "not in Music.app's library, or its answer held no usable counts"


class RunRecord(NamedTuple):
    """One `run` call: the bound it was given, and either its error or its result."""

    timeout: float
    error: BaseException | None
    result: subprocess.CompletedProcess[str] | None


class RecordingRunner:
    """A `Runner` that records each call's outcome and passes it through unchanged.

    The wrapped run's result is returned as the same object, and a failure the
    toolbox absorbs is re-raised as the same exception, so the toolbox sees exactly
    what the wrapped run alone would have given it.
    """

    def __init__(self, run: Runner) -> None:
        """Wrap `run`, with nothing recorded yet."""
        self._run = run
        self.records: list[RunRecord] = []

    def __call__(
        self, args: list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Call the wrapped run, recording its result or its absorbed failure."""
        try:
            result = self._run(args, timeout)
        except _ABSORBED as exc:
            self.records.append(RunRecord(timeout, exc, None))
            raise
        self.records.append(RunRecord(timeout, None, result))
        return result

    @property
    def last(self) -> RunRecord | None:
        """The most recent call's record, or None before any call."""
        if not self.records:
            return None
        return self.records[-1]


def _capped_escape(text: str) -> str:
    r"""`text` escaped, then cut to at most `STDERR_ECHO_LIMIT` characters.

    Escaped first, so the cap bounds what reaches the terminal: a control character
    grows fourfold as `\xNN`, and capping before escaping would let a line of them
    echo four times the limit. The cut falls between whole escapes, never inside one.
    """
    kept: list[str] = []
    length = 0
    for char in text:
        piece = terminal_safe_text(char)
        if length + len(piece) > STDERR_ECHO_LIMIT:
            return "".join(kept) + "..."
        kept.append(piece)
        length += len(piece)
    return "".join(kept)


def _first_stderr_line(stderr: str) -> str:
    """The first nonblank line osascript wrote to stderr, escaped and then capped."""
    for line in stderr.splitlines():
        text = line.strip()
        if text:
            return _capped_escape(text)
    return ""


def _query_failed(result: subprocess.CompletedProcess[str]) -> str:
    """A nonzero exit, named by osascript's own first stderr line where it gave one."""
    line = _first_stderr_line(result.stderr or "")
    if any(code in (result.stderr or "") for code in _PERMISSION_DENIED_CODES):
        label = PERMISSION_DENIED_LABEL
    else:
        label = "the Music.app query failed"
    if not line:
        return f"{label} (osascript exit {result.returncode})"
    return f"{label} (osascript exit {result.returncode}: {line})"


def is_permission_denied(cause: str | None) -> bool:
    """True when *cause* says macOS refused the Automation permission (-1743 or -1744)."""
    return cause is not None and cause.startswith(PERMISSION_DENIED_LABEL)


def _failure_cause(record: RunRecord) -> str | None:
    """The cause when the call failed outright or exited nonzero, else None."""
    if isinstance(record.error, subprocess.TimeoutExpired):
        return _TIMED_OUT.format(bound=record.error.timeout)
    if record.error is not None or record.result is None:
        return _CANNOT_RUN
    if record.result.returncode != 0:
        return _query_failed(record.result)
    return None


def _answered_empty(record: RunRecord) -> bool:
    """Whether a clean answer carried nothing but whitespace."""
    return record.result is None or not (record.result.stdout or "").strip()


def newest_cause(record: RunRecord | None, *, lookback_days: int | None) -> str | None:
    """Why the newest-albums lookup answered `[]`, or None when nothing was recorded.

    `lookback_days` is the window Music.app was asked about, named in the empty-window
    cause; None when the toolbox does not say, and the cause names no number.
    """
    if record is None:
        return None
    failure = _failure_cause(record)
    if failure is not None:
        return failure
    if not _answered_empty(record):
        return _NO_ALBUM_ROW
    if lookback_days is None:
        return _EMPTY_WINDOW_UNKNOWN
    return _EMPTY_WINDOW.format(days=lookback_days)


def facts_cause(record: RunRecord | None) -> str | None:
    """Why an album's facts lookup answered None, or None when nothing was recorded.

    A clean nonempty answer is either a zero track count (the album is not in the
    library) or a reply that does not parse; telling them apart would mean re-reading
    the toolbox's wire format here, so the cause names both.
    """
    if record is None:
        return None
    failure = _failure_cause(record)
    if failure is not None:
        return failure
    if _answered_empty(record):
        return _EMPTY_FACTS
    return _NO_USABLE_COUNTS
