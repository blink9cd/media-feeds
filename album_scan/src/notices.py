"""The stderr notices `album_scan.py` prints beyond its outcome lines.

Four things an operator needs to hear about that are not the outcome itself: that
`--source auto` fell back to the folder scan, that a fact the record was asked for
came back null, that the scan passed over a folder it could not read, and that a
track's tags could not be read. Each is
one line, on stderr, and none changes the exit code, stdout or the record. The
outcome lines themselves -- the confirmation, "no album found", an unusable path --
stay in `main`, beside the return that each one explains.

On stderr unconditionally, every one of them: left on stdout, `--output -` piped
straight into a file would swallow the line INTO the record -- corrupting the JSON
and hiding the warning in the same stroke, right when it most needs to be seen.
"""

from __future__ import annotations

import sys
import unicodedata

from cover_art import AlbumRef, ArtworkMatch
from record_builder import AlbumFacts

#: The Unicode general category of the C0 and C1 control characters, escaped by
#: `terminal_safe_text` below. Not the wider `C` prefix `cover_art` scans a URL
#: for: a format character (a soft hyphen, a bidi mark) is legitimate in a track
#: name, and bidi in names is a risk this project ACCEPTED on 2026-09-22 rather than
#: one to re-open here.
_CONTROL_CATEGORY = "Cc"


def terminal_safe_text(value: str) -> str:
    r"""`value` with every control character replaced by a visible `\xNN` escape.

    For any name this tool ECHOES to the operator's terminal. Every one of them came
    out of a track tag or a folder name, both of which are arbitrary text: the
    AppleScript concatenates tag text unescaped, and `_rank_albums` refuses only NUL
    and the row terminator, so ESC, BEL and the C1 block all reach a name intact
    (measured 2026-09-22). Printed to a terminal, a control character is not text but
    an instruction to it -- a crafted tag can rewrite the window title, erase the
    lines above, or wrap a name in an OSC 8 hyperlink pointing anywhere.

    The RECORD is deliberately left alone. It is JSON a browser reads through
    `textContent`, where a control character is inert; escaping it there would change
    the data to protect a terminal.

    Escaped rather than dropped, so the operator sees that the name held something odd
    instead of reading a silently shortened name as the real one. Ordinary text is
    untouched, accents, CJK and emoji included: the rule is about control characters,
    not about non-ASCII.
    """
    return "".join(
        f"\\x{ord(char):02x}"
        if unicodedata.category(char) == _CONTROL_CATEGORY
        else char
        for char in value
    )


def print_fallback_notice() -> None:
    """Announce, before the folder scan runs, that `--source auto` fell back to it.

    Loud on purpose: a Music.app timeout, a denied automation permission, and a
    genuinely empty lookback window all come back as the identical empty list
    (`newest_albums_from_music`'s own never-raises contract draws no distinction
    between them), so this notice cannot name WHICH one happened -- only that the
    fallback fired, onto a source whose ranking is not to be trusted (see
    album_scan.md, "Why the folder scan's date is a heuristic"). A silent fallback
    would hide exactly that. BEFORE the scan rather than after a result: a fallback
    that goes on to find nothing, or to find that `--library` is no directory, is
    the same fallback, and an operator reading only "no album found" would diagnose
    an empty library where a Music.app that never answered is the story.
    """
    print(
        "album_scan: --source auto got nothing from Music.app (absent, not "
        "running, not scriptable, denied automation permission, or nothing "
        "added within the lookback window) -- falling back to the folder scan, "
        "which ranks by each folder's birth time; that is not a reliable "
        '"date added" (see album_scan.md, "Why the folder scan\'s date is a '
        'heuristic")',
        file=sys.stderr,
    )


def report_skipped_folder(name: str) -> None:
    """`newest_albums`' `on_skip`: one stderr line per artist folder it could not list.

    The scan goes on without that folder, so the operator learns which one was
    passed over rather than losing the refresh to it. By NAME, as the scan hands it
    over -- nothing this tool writes may carry a filesystem path.
    """
    print(
        f"album_scan: skipped unreadable folder {terminal_safe_text(name)}",
        file=sys.stderr,
    )


#: Why a source's facts can come back null, for the degradation line below.
_NO_FACTS_REASON = {
    "music": "not in Music.app's library, or Music.app could not answer",
    "folder": "no audio file in its folder, or the folder could not be read",
}


def report_unreadable_tags(name: str) -> None:
    """`folder_facts`' `on_unreadable`: one stderr line per track whose tags failed.

    The track still counts toward the album's tracks; it contributes no rating and no
    year. By file NAME, as the reader hands it over -- never a path.
    """
    print(
        f"album_scan: could not read the tags of {terminal_safe_text(name)}; "
        "counted as an unrated track",
        file=sys.stderr,
    )


def report_degradation(
    ref: AlbumRef,
    facts: AlbumFacts | None,
    match: ArtworkMatch,
    *,
    source: str,
    no_favorites: bool,
    no_artwork: bool,
) -> None:
    """One stderr line per fact that was asked for and not obtained, for this album.

    Null counts and a null cover are valid record states -- every lookup degrades to
    them by contract rather than failing the refresh -- so none changes the exit
    code or the record. But a null the operator ASKED for is a degraded refresh, and
    without a word about it the record looks complete; a null they declined with
    `--no-favorites` or `--no-artwork` is not, so only the former is reported --
    including a folder whose tracks carry no POPM rating, whose count is null while
    its tracks and year are known. The folder source reads the tracks and the year
    under `--no-favorites` too, so its null facts are reported either way; Music.app's
    are one query that `--no-favorites` skips, so declining it declines all three.
    """
    name = f"{terminal_safe_text(ref.artist)} - {terminal_safe_text(ref.album)}"
    if facts is None and (source == "folder" or not no_favorites):
        print(
            f"album_scan: counts unavailable for {name} ({_NO_FACTS_REASON[source]})",
            file=sys.stderr,
        )
    if facts is not None and facts.favorited is None and not no_favorites:
        print(
            f"album_scan: favorites unknown for {name} (no track carries an ID3 "
            "POPM rating)",
            file=sys.stderr,
        )
    if match.url is None and not no_artwork:
        print(
            f"album_scan: cover art not resolved for {name} "
            "(no verified store match, or the lookup failed)",
            file=sys.stderr,
        )
