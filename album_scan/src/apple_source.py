"""The Apple boundary: find the apple-music-toolbox, load it, and convert what it answers.

The only module in this project that touches the toolbox, a separate project holding
the Music.app tools `--source music` and `--source auto` need: `recently_added` (which
albums Music.app added most recently) and `album_stats` (one album's track count,
favorites count, and year). `--source folder`, the default, never reaches this module's
loader, so a run that does not ask for Music.app needs no toolbox installed at all.

The toolbox is located at run time, in this order:

1. `--toolbox DIR`, the folder holding the toolbox's `recently_added/` and
   `album_stats/` tool folders;
2. the `APPLE_MUSIC_TOOLBOX` environment variable, naming that same folder;
3. a search up this checkout's ancestors for a folder beside one of them named
   `apple_music_toolbox` (the toolbox project in the tree both came from) or
   `apple-music-toolbox` (a clone of the toolbox beside a clone of this one).

The search deliberately looks above this checkout, which is where a sibling clone is.
What keeps that from importing whatever happens to be there: the walk stops at the home
folder (home is the last ancestor searched, for a checkout under it); a candidate counts
only when it holds both tool modules and it, its two tool folders and their modules are
owned by the current user and writable by no other (a named one failing that is
refused); album_scan appends the folders to `sys.path`, so the standard library and this
project's own modules keep every name they already resolve; and nothing is searched for
unless the operator asked for Music.app. Appending is album_scan's half only: the
toolbox's own modules run their own bootstraps once imported, and those may insert their
own folders ahead of everything else.

On Windows the user and mode check is skipped: stat reports neither a POSIX user id
nor group/other write bits there, so the check would refuse nothing it could see, and
folder access is governed by ACLs this module does not read.

The toolbox's answers come back in its own types, and are converted here into this
project's: `cover_art.AlbumRef` and `record_builder.AlbumFacts`. Each project keeps its
own album reference so that neither export needs the other.
"""

from __future__ import annotations

import datetime as dt
import importlib
import inspect
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import NamedTuple, Protocol

from cover_art import AlbumRef
from record_builder import AlbumFacts

#: The environment variable naming the toolbox folder, read when `--toolbox` is unset.
TOOLBOX_ENV = "APPLE_MUSIC_TOOLBOX"

#: The toolbox folder's two spellings the search accepts: the project folder in the
#: tree this one came from, and the published repository's clone name.
TOOLBOX_FOLDER_NAMES = ("apple_music_toolbox", "apple-music-toolbox")

#: The toolbox's two tool folders this project imports, each holding `<name>.py`.
_RECENTLY_ADDED = "recently_added"
_ALBUM_STATS = "album_stats"
_TOOLBOX_MODULES = (_RECENTLY_ADDED, _ALBUM_STATS)

#: The group- and world-write bits: a toolbox path either is set on is one another
#: account could swap code into, so it is not imported.
_SHARED_WRITE = stat.S_IWGRP | stat.S_IWOTH

#: Said whenever no toolbox can be had, so every refusal names every fix.
_HOW_TO_POINT_AT_IT = (
    f"pass --toolbox DIR, set {TOOLBOX_ENV}, or clone apple-music-toolbox beside "
    "this checkout"
)

# (argv, timeout_seconds) -> completed process: the toolbox's own `Runner` shape, so a
# test injects one fake for every Music.app query without the toolbox being imported.
Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


class ToolboxNotFoundError(LookupError):
    """No usable apple-music-toolbox: none found, or the one named does not import."""


class _ToolboxRef(Protocol):
    """The toolbox's album reference, by the three fields this module reads."""

    @property
    def artist(self) -> str: ...  # noqa: D102 - a field declaration, documented above

    @property
    def album(self) -> str: ...  # noqa: D102 - a field declaration, documented above

    @property
    def added(self) -> dt.date: ...  # noqa: D102 - a field declaration, documented above


class _ToolboxFacts(Protocol):
    """The toolbox's album facts, by the three fields this module reads."""

    @property
    def tracks(self) -> int: ...  # noqa: D102 - a field declaration, documented above

    @property
    def favorited(self) -> int: ...  # noqa: D102 - a field declaration, documented above

    @property
    def year(self) -> int | None: ...  # noqa: D102 - a field declaration, documented above


class NewestCall(Protocol):
    """`recently_added.newest_albums_from_music`, as this module calls it."""

    def __call__(  # noqa: D102 - the call's contract is the class docstring
        self, count: int, *, run: Runner
    ) -> Sequence[_ToolboxRef]: ...


class FactsCall(Protocol):
    """`album_stats.album_facts`, as this module calls it.

    `min_stars` is passed only when the operator set a star threshold, so a call with
    none reads the same to a toolbox that predates its rating mode; with one set,
    `open_toolbox` refuses such a toolbox before any album is queried.
    """

    def __call__(  # noqa: D102 - the call's contract is the class docstring
        self, *, artist: str, album: str, run: Runner, min_stars: int | None = ...
    ) -> _ToolboxFacts | None: ...


class ToolboxModules(NamedTuple):
    """The toolbox's two modules, as imported."""

    recently_added: ModuleType
    album_stats: ModuleType


class Toolbox(NamedTuple):
    """The two toolbox calls, the runner, and the toolbox folder's final component.

    The runner is what every query goes through; the folder name is printed on every
    success line, so a wrong pick shows.
    """

    newest_albums: NewestCall
    album_facts: FactsCall
    run: Runner
    folder: str


def is_toolbox(folder: Path) -> bool:
    """Whether `folder` holds both toolbox tool modules this project imports."""
    return all((folder / name / f"{name}.py").is_file() for name in _TOOLBOX_MODULES)


def current_uid() -> int | None:
    """This process's effective user id, or None on Windows, where none is checked."""
    if sys.platform == "win32":
        return None
    return os.geteuid()


def untrusted_reason(folder: Path, uid: int | None) -> str | None:
    """Why the toolbox at `folder` must not be imported, or None when it may be.

    Checks the folder, its two tool folders, and their two modules: each must be owned
    by `uid` and writable by neither group nor others, since any one of them writable
    by another account lets that account replace the code imported. `uid` None (the
    Windows case, `current_uid`) checks nothing. `folder` must already pass
    `is_toolbox`, so every path checked exists.
    """
    if uid is None:
        return None
    tool_folders = [folder / name for name in _TOOLBOX_MODULES]
    modules = [folder / name / f"{name}.py" for name in _TOOLBOX_MODULES]
    for path in (folder, *tool_folders, *modules):
        info = path.stat()
        if info.st_uid != uid:
            return "is not owned by the current user"
        if info.st_mode & _SHARED_WRITE:
            return "is writable by other users"
    return None


def _named_toolbox(value: str, source: str, uid: int | None) -> Path:
    """The toolbox folder an option or the environment named, refused by name if unfit."""
    folder = Path(value).expanduser()
    if not is_toolbox(folder):
        raise ToolboxNotFoundError(
            f"{source} names {folder.name}, which holds no recently_added/ and "
            f"album_stats/ tool folders; {_HOW_TO_POINT_AT_IT}"
        )
    reason = untrusted_reason(folder, uid)
    if reason is not None:
        raise ToolboxNotFoundError(
            f"{source} names {folder.name}, which {reason}, so it is not imported"
        )
    return folder


def search_folders(start: Path, home: Path) -> list[Path]:
    """The ancestors of `start` the search looks beside, nearest first.

    For a checkout under `home` the walk stops at `home` itself, the last ancestor
    searched, so nothing above the home folder is ever imported from. A checkout
    outside `home` walks every ancestor, each candidate still held to
    `untrusted_reason`.
    """
    ancestors = list(start.resolve().parents)
    home = home.resolve()
    if home in ancestors:
        return ancestors[: ancestors.index(home) + 1]
    return ancestors


def locate_toolbox(
    option: str | None,
    *,
    environ: Mapping[str, str],
    start: Path,
    home: Path | None = None,
    user_id: Callable[[], int | None] = current_uid,
) -> Path:
    """The toolbox folder: `option`, else `environ`'s `TOOLBOX_ENV`, else the search.

    `start` is the file the search walks up from; `main` passes its own. `home` is
    where the search stops, the home folder when None; `user_id` answers the user id
    every candidate must belong to. Both are seams, so a test never reads the host's.
    A searched candidate that `untrusted_reason` rejects is passed over. Raises
    `ToolboxNotFoundError` naming every way to point at the toolbox, and naming an
    explicitly given folder by its final component only, never its path.
    """
    uid = user_id()
    if option:
        return _named_toolbox(option, "--toolbox", uid)
    named = environ.get(TOOLBOX_ENV)
    if named:
        return _named_toolbox(named, TOOLBOX_ENV, uid)
    for ancestor in search_folders(start, Path.home() if home is None else home):
        for spelling in TOOLBOX_FOLDER_NAMES:
            candidate = ancestor / spelling
            if is_toolbox(candidate) and untrusted_reason(candidate, uid) is None:
                return candidate
    raise ToolboxNotFoundError(
        f"--source music and auto need the apple-music-toolbox: {_HOW_TO_POINT_AT_IT}"
    )


def import_toolbox(root: Path) -> ToolboxModules:
    """Import the toolbox's two modules from `root`, its folders appended to `sys.path`.

    Appended, never prepended: the standard library and this project's own modules keep
    every name they already resolve, until the toolbox's own bootstraps run on import.
    An import that fails is a `ToolboxNotFoundError` naming the module that failed (or
    the error's type), never the error's message, which can carry an absolute path.
    """
    for name in _TOOLBOX_MODULES:
        folder = str(root / name)
        if folder not in sys.path:
            sys.path.append(folder)
    try:
        return ToolboxModules(
            recently_added=importlib.import_module(_RECENTLY_ADDED),
            album_stats=importlib.import_module(_ALBUM_STATS),
        )
    except ImportError as exc:
        raise ToolboxNotFoundError(
            "the apple-music-toolbox did not import: "
            f"{exc.name or type(exc).__name__}; check that checkout is complete, or "
            f"{_HOW_TO_POINT_AT_IT}"
        ) from exc


def _refuse_unrated_toolbox(
    album_facts: Callable[..., object], min_stars: int | None
) -> None:
    """Refuse a star threshold the toolbox's `album_facts` has no `min_stars` for.

    Asked of the call's own signature, before any album is queried: sent anyway, the
    keyword would be a `TypeError` inside the per-album loop, a traceback rather than
    a refusal naming the fix.
    """
    if min_stars is None:
        return
    if "min_stars" not in inspect.signature(album_facts).parameters:
        raise ToolboxNotFoundError(
            "the apple-music-toolbox predates --favorite-min-stars; update it"
        )


def open_toolbox(
    option: str | None,
    *,
    environ: Mapping[str, str],
    start: Path,
    run: Runner | None,
    min_stars: int | None = None,
) -> Toolbox:
    """Locate and import the toolbox; its two calls, bound to `run` or its own runner.

    `min_stars` is the star threshold the run will send `album_facts`, None for none.
    Raises `ToolboxNotFoundError` when no toolbox can be had, and when a threshold is
    set and the toolbox found predates it.
    """
    root = locate_toolbox(option, environ=environ, start=start)
    modules = import_toolbox(root)
    _refuse_unrated_toolbox(modules.album_stats.album_facts, min_stars)
    if run is None:  # pragma: no cover - the real boundary; every test injects `run`
        run = modules.recently_added._real_run
    return Toolbox(
        newest_albums=modules.recently_added.newest_albums_from_music,
        album_facts=modules.album_stats.album_facts,
        run=run,
        folder=root.name,
    )


def music_refs(toolbox: Toolbox, count: int) -> list[AlbumRef]:
    """Up to `count` albums Music.app added most recently, newest first, as our type."""
    return [
        AlbumRef(artist=ref.artist, album=ref.album, added=ref.added)
        for ref in toolbox.newest_albums(count, run=toolbox.run)
    ]


def music_signal(min_stars: int | None) -> str:
    """The `favorite_signal` a Music.app count carries for this star threshold."""
    if min_stars is None:
        return "music.favorited"
    return f"music.rating>={min_stars}"


def music_facts(
    toolbox: Toolbox, ref: AlbumRef, *, min_stars: int | None
) -> AlbumFacts | None:
    """Music.app's facts for this album as our type, or None when it cannot answer.

    `min_stars` None counts Music.app's own `favorited` flag; a number counts the
    tracks whose user rating reaches that many stars. The toolbox never raises, so an
    unreachable Music.app is a None here, as an album it does not hold is.
    """
    if min_stars is None:
        answer = toolbox.album_facts(
            artist=ref.artist, album=ref.album, run=toolbox.run
        )
    else:
        answer = toolbox.album_facts(
            artist=ref.artist, album=ref.album, run=toolbox.run, min_stars=min_stars
        )
    if answer is None:
        return None
    return AlbumFacts(
        tracks=answer.tracks,
        favorited=answer.favorited,
        year=answer.year,
        favorite_signal=music_signal(min_stars),
    )
