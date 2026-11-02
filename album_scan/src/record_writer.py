"""Write the album record(s) `album_scan.py` emits to stdout: `--output -`.

This module owns only the stream branch: the JSON text and the stdout write. A file
target goes through `feed_read.write_records`, the atomic writer every adapter in this
project shares, so `album_scan.py`'s `main` hands that one the finished list instead.
Nothing here prints a message: a failure surfaces as the exception it is.
"""

from __future__ import annotations

import io
import json
import sys

from record_builder import AlbumRecord

#: `--output -` means stdout, the usual convention. A file genuinely named `-` is
#: addressed as `./-`.
STDOUT_TARGET = "-"


def pin_stdout_newlines() -> None:
    r"""Stop Windows text mode translating the record's LF line endings to CRLF.

    `stdio_utf8.force_utf8_streams` pins the encoding and deliberately leaves newline
    alone, so stdout still translates `\n` to `os.linesep` on write. The file writer
    pins `newline="\n"` explicitly; without this the same record would differ
    byte-for-byte between `--output <path>` and `--output -` on Windows and not on
    macOS, two paths agreeing by accident rather than by construction.

    Guarded the way `stdio_utf8` guards its own reconfigure: the isinstance narrowing
    skips `io.StringIO`, which every test substitutes for stdout and which has no
    `reconfigure`; the `ValueError` a closed or detached stream raises is skipped too,
    so the write that follows reports that failure in its own words.
    """
    if not isinstance(sys.stdout, io.TextIOWrapper):
        return
    try:
        sys.stdout.reconfigure(newline="\n")
    except ValueError:
        return


def render_records(records: list[AlbumRecord]) -> str:
    """The JSON text the stream branch emits, newline-terminated.

    Always a JSON array, even when `records` holds one album: a bare
    object and a one-element array are different shapes for a reader to branch on.
    Built exactly as `feed_read.write_records` builds a file's text (an indent of
    two, no NaN, one trailing newline), so a piped record and a written one are the
    same bytes; the file tests pin the written bytes, and the stdout tests the same.
    """
    return json.dumps(records, indent=2, allow_nan=False) + "\n"


def write_records_to_stdout(records: list[AlbumRecord]) -> None:
    """`--output -`: the record(s) to stdout, LF-terminated on every platform."""
    pin_stdout_newlines()
    sys.stdout.write(render_records(records))
