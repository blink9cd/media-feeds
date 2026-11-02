"""`--cover-report`: each recorded album's cover result, written as JSON for a caller.

The stderr notice (`notices.py`) says why a cover is null for a person. A scheduled
caller that counts persistent misses needs the same answer as data, without matching
that text, so this module names one word per album: `found`, one of `cover_art`'s null
causes (`no-match`, `lookup-failed`, `no-art`), `not-asked` under `--no-artwork`, or
`unknown` for a null that names no cause. `cover_art.md`, "Why a cover is null", says
what each cause asks of its reader.

The report carries names only, never a filesystem path, like the record itself
(`album_scan.md`, "Why the record carries no filesystem path"). It is written through
`feed_read.write_records`, the atomic writer the record goes through. A report that
cannot be written is said on stderr by its final component and returns False: the
record was already written, so the run itself has not failed.

Entry point assumptions: imported by `album_scan.py`, with `src/` and the sibling
`cover_art` and `feed_read` folders on `sys.path`.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import feed_read
from cover_art import AlbumRef, ArtworkMatch

#: The cover resolved to a URL.
FOUND: Final = "found"
#: `--no-artwork`: no lookup ran, so no cause can be claimed.
NOT_ASKED: Final = "not-asked"
#: A null match naming no cause: one a caller built itself, not `artwork_url`'s.
UNKNOWN: Final = "unknown"


def cover_status(match: ArtworkMatch, *, no_artwork: bool) -> str:
    """One word for this album's cover result; a null cause is passed through as named."""
    if match.url is not None:
        return FOUND
    if no_artwork:
        return NOT_ASKED
    return match.reason or UNKNOWN


def report_entry(ref: AlbumRef, status: str) -> dict[str, str]:
    """One album's report entry: its artist, its title, and its cover result."""
    return {"artist": ref.artist, "album": ref.album, "cover": status}


def write_cover_report(target: str, entries: Sequence[dict[str, str]]) -> bool:
    """Write `entries` to `target` atomically; False, said on stderr, when it cannot be.

    A leading `~` is the home directory, as it is for `--output`.
    """
    path = Path(target).expanduser()
    try:
        feed_read.write_records(path, entries)
    except OSError as exc:
        print(
            f"album_scan: cannot write {path.name}: {exc.strerror or exc}",
            file=sys.stderr,
        )
        return False
    return True
