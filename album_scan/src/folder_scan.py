"""Find the most recently added albums by scanning a library folder tree.

The folder source's discovery, and `--source auto`'s fallback when Music.app cannot
answer. It ranks by each album folder's birth time, which is a heuristic rather than a
recorded "date added": `album_scan.md`, "The folder source", holds the field evidence
for where it misleads, and why Music.app's own date is preferred wherever it exists.

Everything here answers one question (which album directories under the library root
were created most recently), and none of it touches Music.app, the network, or the
record's shape. Nothing here prints, either: an artist folder the scan cannot list is
handed to the caller's `on_skip` by name, and the caller decides what to say.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from cover_art import AlbumRef
from library_walk import SkipReporter, album_dirs, ignore_skipped


def _birth_time(path: Path) -> dt.datetime:
    """The folder's creation time, at the full precision the file system keeps.

    macOS records a birth time, which is what "date added" means here, though an
    iCloud Drive item that is evicted and re-materialized gets a new one (see
    `album_scan.md`, "Why the folder scan's date is a heuristic"). An album folder
    on a real library was created months before it was re-tagged, so `st_mtime`
    would have announced an old album as newly added. Platforms without
    `st_birthtime` fall back to `st_mtime`, which is wrong in that way but is
    the best available and keeps the tool importable off macOS for type checking and
    unit tests.

    A full timestamp, not a date: `newest_albums` ranks on it, and Music.app's own
    "date added" ranks to the second, so two albums added the same day must order by
    which came later rather than by name. The record still carries the date alone.
    """
    stat = path.stat()
    stamp = getattr(stat, "st_birthtime", stat.st_mtime)
    return dt.datetime.fromtimestamp(stamp)


def newest_albums(
    library_root: Path, count: int, *, on_skip: SkipReporter = ignore_skipped
) -> list[AlbumRef]:
    """The `count` most recently added album folders, newest first.

    Decorates before sorting so each folder is stat-ed exactly once, and each result
    carries the date of the time it was ranked by. The walk is sorted, so a tie
    between two folders with the same birth time resolves the same way on every run:
    Python's sort is stable, so equal times keep the walk's order.

    Raises NotADirectoryError if the root is not an existing directory, and
    PermissionError if it is one that cannot be listed: a mistyped or unreadable
    library path is an operator error worth reporting loudly, whereas an empty library
    is a legitimate state that simply produces no albums. Both name the root by its
    final component only: the listing's own PermissionError carries the whole path in
    its text, which is why it is re-raised rather than let through. Returns fewer than
    `count` when the library holds fewer albums: a short result, not an error.
    `on_skip` hears the name of each artist folder the scan could not
    list (`library_walk.album_dirs`, the walk every tool here shares).
    """
    if not library_root.is_dir():
        raise NotADirectoryError(
            f"library root is not a directory: {library_root.name}"
        )
    # Only the root's own listing can raise here: the walk catches each artist
    # folder's and reports it to `on_skip` instead.
    try:
        found = album_dirs(library_root, on_skip)
    except PermissionError as exc:
        raise PermissionError(
            f"library root is not readable: {library_root.name}"
        ) from exc

    timed = [(_birth_time(path), path) for path in found]
    timed.sort(key=lambda pair: pair[0], reverse=True)
    return [
        AlbumRef(artist=path.parent.name, album=path.name, added=born.date())
        for born, path in timed[:count]
    ]
