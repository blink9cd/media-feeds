"""Build the album record `album_scan.py` emits, from a discovered album and its facts.

This module owns only the RECORD SHAPE -- combining an already-discovered `AlbumRef`,
an already-resolved artwork URL and store year, and the album's already-resolved facts
(from Music.app or from the album folder's own tags) into what the emitted record
actually holds. It also defines `AlbumFacts`, the one shape both sources' facts arrive
in, so the record never needs to know which source answered. It has no side effects of its own:
`album_scan.py`'s `main` resolves everything this module needs before calling it,
once per album recorded. `album_scan.py` re-exports both names below, so
`album_scan.AlbumRecord` / `album_scan.album_record` work too.

NOTHING THIS TOOL WRITES MAY CONTAIN A FILESYSTEM PATH. See `album_scan.md`, "Why
the record carries no filesystem path", for the full reasoning; `album_record` carries
only names, a date, a year, a URL, counts and the favorites signal's name, and a test
pins that.
"""

from __future__ import annotations

from typing import NamedTuple, TypedDict

from cover_art import AlbumRef


class AlbumFacts(NamedTuple):
    """What one source knows about one album: two counts, a year, and the favorites signal.

    `tracks` is at least 1. `favorited` is between 0 and `tracks`, or `None` when no
    favorites were read -- declined, or carried by no tag on the album -- which is a
    different fact from a counted zero. `favorite_signal` names which favorites
    `favorited` counts (`music.favorited`, `music.rating>=N` or `id3.popm>=N`), and is
    `None` exactly when `favorited` is. `year` is the year tag, never a release date.

    Built by `apple_source` from the toolbox's own facts, and by `folder_facts` from
    the album folder's ID3 tags.
    """

    tracks: int
    favorited: int | None
    year: int | None
    favorite_signal: str | None


class AlbumRecord(TypedDict):
    """The record this tool emits.

    See `album_scan.md`, "Who consumes this record", for what each key means and
    why null counts are deliberately not the same fact as zero ones.
    The key list is the annotations below, never a count restated in prose. No path,
    ever -- see the module docstring.
    """

    artist: str
    album: str
    added: str
    artwork: str | None
    tracks: int | None
    favorited: int | None
    #: TEXT, not a number, though both sources report an integer. One field name, one
    #: type: a consumer reading this record beside feed-sourced ones gets the year as
    #: text from all of them, rather than an int it has to coerce.
    year: str | None
    #: Which favorites `favorited` counts, or null when none were read. One string
    #: rather than a structure: `music.favorited`, `music.rating>=N` or `id3.popm>=N`.
    favorite_signal: str | None


def album_record(
    ref: AlbumRef,
    artwork: str | None,
    *,
    facts: AlbumFacts | None = None,
    fallback_year: int | None = None,
) -> AlbumRecord:
    """Build the committed record from a discovered album, its cover, and its counts.

    Takes the already-resolved artwork and counts rather than looking either up, so
    every side effect stays in its own function and this one is pure -- which is also
    what keeps a CLOCK out of it: a rule that depends on how long ago `added` was
    (how to display a zero count on a newly added album, say) belongs to the consumer,
    not to this record. The record carries facts (`added`, and the two counts); the
    consumer owns the presentation rule.

    `facts` defaults to `None` -- the same value an unreachable Music.app produces --
    so a caller with nothing to offer need not fabricate it.

    `fallback_year` is the STORE's release year for the album, used only when the
    source has no year of its own. It fills an absence and never corrects a tag: both
    sources report the file's year, which for a remaster is the reissue, and nothing
    here can tell a wrong tag from a right one. Preferring the store's year outright would replace a
    year that is right about THIS library's copy with one about a different edition.
    """
    # Statement form, not a conditional EXPRESSION -- see `album_scan.py`'s own
    # `artwork_for` for why coverage.py sees no arcs at all for `x if cond else y`.
    if facts is None:
        tracks, favorited, tagged_year, signal = None, None, None, None
    else:
        tracks, favorited, tagged_year = facts.tracks, facts.favorited, facts.year
        signal = facts.favorite_signal
    if tagged_year is None:
        year = fallback_year
    else:
        year = tagged_year
    # Statement form again, and this one is load-bearing rather than stylistic: a
    # bare str() over the optional turns a genuine absence into the string "None",
    # which is truthy, publishes as "None" in the JSON, and renders as the word
    # None wherever it is displayed -- a fabricated fact where the record means "not
    # known".
    if year is None:
        published_year = None
    else:
        published_year = str(year)
    return AlbumRecord(
        artist=ref.artist,
        album=ref.album,
        added=ref.added.isoformat(),
        artwork=artwork,
        tracks=tracks,
        favorited=favorited,
        year=published_year,
        favorite_signal=signal,
    )
