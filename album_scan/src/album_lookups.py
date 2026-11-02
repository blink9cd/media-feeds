"""Every lookup one refresh makes, and the shape one answers with.

Three of them: which albums are newest (the library folder, Music.app, or `auto`'s
preference between the two), what the source knows about each one (its counts, year,
and favorites), and which cover art the store has for it. They are one module because
they are one kind of thing, the code that reads beyond the record itself, and
because each carries the arm that skips it when its flag says so (`--source`,
`--no-favorites`, `--no-artwork`), so the skip lives beside the call it replaces
instead of in `main`'s loop.

The seams stay `main`'s to own: the toolbox handle (whose `run` every Music.app query
goes through) and `fetch` arrive as parameters and are never read from this module's
globals, so a test drives any outcome here by passing a fake.

`main` keeps everything that is not a lookup: the outcome lines, the per-album loop
whose order matters (see `artwork_for`), and the record writing.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from apple_source import Toolbox, music_facts, music_refs
from cover_art import AlbumRef, ArtworkMatch, Fetcher, artwork_url
from folder_facts import DEFAULT_MIN_STARS, folder_facts
from folder_scan import newest_albums
from notices import (
    print_fallback_notice,
    report_skipped_folder,
    report_unreadable_tags,
)
from record_builder import AlbumFacts

#: The two values `NewestAlbumResult.resolved_source` takes.
MUSIC = "music"
FOLDER = "folder"


class NewestAlbumResult(NamedTuple):
    """`resolve_albums`'s answer: the chosen albums, and which source produced them.

    `refs` is a list because both sources can return more than one album once
    `--count` asks for it. An empty list is the one spelling of "no album found".

    `resolved_source` is always `"music"` or `"folder"`, even when `refs` is empty:
    for `--source folder`, and for a `--source auto` fallback, it names the folder
    scan that was actually consulted; for `--source music`, and a `--source auto`
    Music.app hit, it names Music.app. `main` reports it on every success, and the
    per-album facts come from the same source. The loud notice for auto's fallback is
    printed by `resolve_albums` at the moment it falls back, so a fallback that then
    finds nothing, or finds no directory, is still announced.
    """

    refs: list[AlbumRef]
    resolved_source: str


class FactsOptions(NamedTuple):
    """What a facts lookup needs beyond the album: the flags, the library, the toolbox."""

    library: Path
    favorites: bool
    #: The operator's star threshold, or None: Music.app's own `favorited` flag, and
    #: `DEFAULT_MIN_STARS` for the folder's tags.
    min_stars: int | None
    #: None exactly when the run asked for no Music.app source.
    toolbox: Toolbox | None


def _required(toolbox: Toolbox | None, source: str) -> Toolbox:
    """`toolbox`, which `main` opens for every source that reads Music.app."""
    if toolbox is None:
        raise ValueError(
            f"--source {source} reads Music.app, and no toolbox was opened"
        )
    return toolbox


def resolve_albums(
    source: str, library: Path, count: int, *, toolbox: Toolbox | None
) -> NewestAlbumResult:
    """Resolve up to `count` newest albums for `--source`: folder, music, or auto.

    "auto" prefers Music.app's own record and falls back to the folder heuristic
    only when Music.app has nothing to say, which is an empty list (see album_scan.md, "The
    folder source"). The short-circuit holds: a successful Music.app answer never
    touches `library` at all, so an invalid `--library` under `--source music`, or
    under a successful `--source auto` lookup, never raises `NotADirectoryError`, nor
    the `PermissionError` of a root that cannot be listed: only a folder scan that
    is actually attempted can. Auto does not top up a short Music.app answer from the
    folder scan: fewer than `count` from Music.app is still a genuine answer.
    """
    if source == FOLDER:
        return NewestAlbumResult(_scan_folders(library, count), FOLDER)
    music = music_refs(_required(toolbox, source), count)
    if music or source == MUSIC:
        return NewestAlbumResult(music, MUSIC)
    print_fallback_notice()
    return NewestAlbumResult(_scan_folders(library, count), FOLDER)


def _scan_folders(library: Path, count: int) -> list[AlbumRef]:
    """The folder scan with its skipped folders reported: both callers above use it."""
    return newest_albums(library, count, on_skip=report_skipped_folder)


def facts_for(
    ref: AlbumRef, resolved_source: str, options: FactsOptions
) -> AlbumFacts | None:
    """This album's facts, from the source that named it.

    The folder source reads the album folder's own tags: its tracks and year always,
    and its favorites unless `--no-favorites`. Music.app's counts and year are one
    query, so `--no-favorites` skips that query and all three with it. Neither lookup
    raises, so an unreadable folder or an unreachable Music.app degrades this refresh
    to null facts rather than failing it.
    """
    if resolved_source == FOLDER:
        min_stars = options.min_stars
        if min_stars is None:
            min_stars = DEFAULT_MIN_STARS
        return folder_facts(
            options.library / ref.artist / ref.album,
            min_stars=min_stars,
            favorites=options.favorites,
            on_unreadable=report_unreadable_tags,
        )
    if not options.favorites:
        return None
    return music_facts(
        _required(options.toolbox, resolved_source), ref, min_stars=options.min_stars
    )


def artwork_for(
    ref: AlbumRef,
    facts: AlbumFacts | None,
    *,
    no_artwork: bool,
    fetch: Fetcher,
    width: int,
    image_format: str,
) -> ArtworkMatch:
    """This album's cover art, or a null match when `--no-artwork` is passed.

    Takes the already-resolved `facts` rather than looking them up itself. Facts
    before artwork, in `main`'s per-ref loop, is load-bearing rather than incidental:
    the year and track count the source reports decide which search result really is
    this album, as tiebreakers between name matches and, when both agree, as a way
    to admit a candidate whose name does not match. Unpacked into two optional numbers
    here rather than passed as a whole `AlbumFacts`: the artwork adapter is a sibling
    tool and does not import this tool's type. Statement form, not a conditional
    expression: coverage.py emits branch arcs for an if/else statement but none for
    `x if cond else y`, so the expression form lets an arm that never runs under test
    pass the 100% branch-coverage gate.
    """
    if no_artwork:
        return ArtworkMatch(url=None, year=None)
    if facts is None:
        known_year, known_tracks = None, None
    else:
        known_year, known_tracks = facts.year, facts.tracks
    return artwork_url(
        ref,
        fetch=fetch,
        width=width,
        image_format=image_format,
        year=known_year,
        tracks=known_tracks,
    )
