"""The albums of a music library: its non-hidden ``Artist/Album`` folders.

One walk, shared by every tool here that visits a library album by album, so they all
agree on what an album is. An album is a folder exactly two levels under the library
root, an artist folder and then an album folder, with any name starting with a dot
passed over at either level. Nothing deeper is an album of its own, so a multi-disc
album's ``Disc 1`` and ``Disc 2`` folders stay one album, and a file at either level
is never one.

A library module with no CLI. Callers put this folder on ``sys.path`` from the
repository root, the way every tool here reaches a library beside it.
Nothing here prints: an artist folder the walk cannot list is handed to the caller's
reporter by name, and the caller decides what to say.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

#: Where a library is looked for when the caller names none; expanded at run time.
DEFAULT_LIBRARY = "~/Music/Albums"

#: What `album_dirs` calls with the NAME of each artist folder it could not list.
SkipReporter = Callable[[str], None]


def ignore_skipped(name: str) -> None:
    """The default reporter, for a caller that wants no report of skipped folders."""


def album_dirs(
    library_root: Path, on_skip: SkipReporter = ignore_skipped
) -> list[Path]:
    """Every ``Artist/Album`` directory under *library_root*, dot-folders excluded.

    Sorted by artist folder, then album folder, so every caller walks one order. An
    artist folder that cannot be listed (no read permission on it) is reported to
    *on_skip* by NAME and passed over, so one unreadable folder costs the walk one
    entry rather than the whole library. By name only: the exception's own text
    carries the whole path. The root's own listing is not caught: a root that cannot
    be listed raises as `Path.iterdir` raises, and the caller names it.
    """
    albums: list[Path] = []
    for artist in sorted(library_root.iterdir()):
        if not artist.is_dir() or artist.name.startswith("."):
            continue
        try:
            entries = sorted(artist.iterdir())
        except PermissionError:
            on_skip(artist.name)
            continue
        for album in entries:
            if album.is_dir() and not album.name.startswith("."):
                albums.append(album)
    return albums
