"""Find the `--count` most recently added albums, resolve their cover art, and emit their JSON record(s).

Run by hand whenever a fresh record is wanted; never as a build step and never at
page-view time. Two sources answer "most recently added": the library folder (`--source
folder`, the default), which ranks album folders by birth time and reads each album's
tracks, year and favorites from its own ID3 tags, and Apple Music (`--source music`),
which reads its own "date added" and counts through its supported AppleScript API, by
way of the separate apple-music-toolbox project. `--source auto` asks Music.app first
and falls back to the folder. See `album_scan.md`, "`--source`: choosing the date-added
source" and "The two favorites signals", for what each one reads and where each
misleads.

Network cost scales with `--count`: resolving one album's cover costs TWO iTunes Search
requests, not one, because `artwork_url` pools an artist-and-album query with an
artist-alone one and sends both unconditionally. Every one of them runs on the machine
that runs this tool, at refresh time, never by whatever consumes the record afterward.
See `album_scan.md`, "Who consumes this record", for what each key means -- this
tool has no knowledge of any particular consumer.

Private `src/` modules carry most of the work: every lookup (`src/album_lookups.py`),
the Apple boundary that finds and loads the toolbox (`src/apple_source.py`), the
folder-scan discovery (`src/folder_scan.py`), the folder's own tags
(`src/folder_facts.py`), the record's shape (`src/record_builder.py`), the stdout
writer (`src/record_writer.py`) and the stderr notices beyond the outcome lines
(`src/notices.py`). A record file is written by `feed_read.write_records`, the writer
every adapter in this project shares. `__all__` below is what this module re-exports
on their behalf, so `album_scan.<name>` resolves for each; the rest is reached only
through `main`.

NOTHING THIS TOOL WRITES MAY CONTAIN A FILESYSTEM PATH. See `album_scan.md`, "Why
the record carries no filesystem path", for the full reasoning; `album_record` carries
only names, a date, a year, a URL, counts and a signal name, and a test pins that.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# This tool's private src/ modules, then the shared libraries and the sibling tool
# folders, each at ONE fixed place: directly under the project root, which is this
# file's grandparent. Deliberately not a search up the ancestors -- that would also put
# any same-named folder ABOVE the checkout on sys.path, ahead of the standard library,
# and whatever it holds would be imported in place of the real module. A helper that is
# absent fails loudly at the import below rather than being silently skipped. See
# stdio_utf8/stdio_utf8.md, "How this project's tools import it". The
# apple-music-toolbox is NOT put here: only a Music.app source needs it, and
# `src/apple_source.py` finds it then.
sys.path[:0] = [str(Path(__file__).resolve().parent / "src")]
sys.path += [
    str(Path(__file__).resolve().parents[1] / name)
    for name in (
        "stdio_utf8",
        "help_formatter",
        "cover_art",
        "library_walk",
        "feed_read",
    )
]

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
from cover_art import (  # noqa: E402 - import follows the sys.path bootstrap above
    ARTWORK_FORMATS,
    DEFAULT_ARTWORK_FORMAT,
    DEFAULT_ARTWORK_PX,
    AlbumRef,
    Fetcher,
    _real_fetch,
)
from library_walk import DEFAULT_LIBRARY  # noqa: E402 - follows the sys.path bootstrap

from folder_scan import newest_albums  # noqa: E402 - follows the sys.path bootstrap

from album_lookups import (  # noqa: E402 - follows the sys.path bootstrap
    FOLDER,
    FactsOptions,
    artwork_for,
    facts_for,
    resolve_albums,
)
from apple_source import (  # noqa: E402 - follows the sys.path bootstrap
    Runner,
    Toolbox,
    ToolboxNotFoundError,
    open_toolbox,
)
from notices import (  # noqa: E402 - follows the sys.path bootstrap
    report_degradation,
    terminal_safe_text,
)

from record_builder import (  # noqa: E402 - follows the bootstrap
    AlbumFacts,
    AlbumRecord,
    album_record,
)
from record_writer import (  # noqa: E402 - follows the bootstrap
    STDOUT_TARGET,
    write_records_to_stdout,
)
from scan_options import (  # noqa: E402 - follows the bootstrap
    DEFAULT_OUTPUT,
    DESCRIPTION,
    EPILOG,
    SOURCES,
    STAR_CHOICES,
    ScanHelpFormatter,
    positive_int,
)

#: What `album_scan.<name>` resolves to. Most of it is re-exported from a private
#: `src/` module or a sibling tool rather than defined here, so the list is also what
#: keeps those imports honest: drop a name and the import above reads as unused.
__all__ = [
    "AlbumFacts",
    "AlbumRecord",
    "AlbumRef",
    "Fetcher",
    "Runner",
    "album_record",
    "build_parser",
    "main",
    "newest_albums",
    "open_toolbox",
    "positive_int",
]

_EXIT_OK = 0
_EXIT_NO_ALBUM = 1
#: A path argument that cannot be used: `--library` is not a directory, or cannot be
#: listed, when a folder scan actually runs; `--output` cannot be written; or a
#: Music.app source finds no apple-music-toolbox. argparse's own usage errors exit 2
#: as well, so every "fix the command line" outcome shares one code.
_EXIT_UNUSABLE_PATH = 2


def _write_records(records: list[AlbumRecord], target: str) -> int:
    """Emit the record(s) where `--output` says; the exit code.

    A file goes through `feed_read.write_records`, the atomic writer every adapter in
    this project shares: it stages the record beside the target and swaps it in
    whole, so whatever record was there before is untouched by a failed write, and it
    gives the finished file mode 0644 whatever the umask or the replaced file's mode.
    A file that cannot be written is exit 2 like any other unusable path argument,
    and the report names the file by its final component only: nothing this tool
    writes may carry a filesystem path, which the exception's own text would. `-` is
    stdout, whose failures are not a path problem and propagate as they always did.

    A leading `~` is the home directory, read the way `main` already reads
    `--library`'s: quoted on a command line the shell leaves it alone, and without
    the expansion the tool made a directory literally named `~`.
    """
    if target == STDOUT_TARGET:
        write_records_to_stdout(records)
        return _EXIT_OK
    output = Path(target).expanduser()
    try:
        feed_read.write_records(output, records)
    except OSError as exc:
        print(
            f"album_scan: cannot write {output.name}: {exc.strerror or exc}",
            file=sys.stderr,
        )
        return _EXIT_UNUSABLE_PATH
    return _EXIT_OK


def _toolbox_for(args: argparse.Namespace, run: Runner | None) -> Toolbox | None:
    """The opened toolbox for a Music.app source, or None for `--source folder`.

    Raises `ToolboxNotFoundError` when a Music.app source finds none, or finds one
    predating a `--favorite-min-stars` the run sets. The folder
    source never looks, so a run that does not ask for Music.app needs no toolbox.
    """
    if args.source == FOLDER:
        return None
    # The threshold the facts query will carry: none when --no-favorites skips it.
    min_stars = None if args.no_favorites else args.favorite_min_stars
    return open_toolbox(
        args.toolbox,
        environ=os.environ,
        start=Path(__file__),
        run=run,
        min_stars=min_stars,
    )


def _toolbox_note(toolbox: Toolbox | None) -> str:
    """The success line's toolbox clause: the folder imported, so a wrong pick shows."""
    if toolbox is None:
        return ""
    return f", toolbox: {terminal_safe_text(toolbox.folder)}"


def _record_for(
    ref: AlbumRef,
    source: str,
    args: argparse.Namespace,
    options: FactsOptions,
    fetch: Fetcher,
) -> AlbumRecord:
    """One album's record: its facts, then its cover, reported where degraded.

    FACTS BEFORE ARTWORK: the facts are resolved once and reused by both the record
    and the artwork match, whose tiebreakers they are (see `artwork_for`).
    """
    facts = facts_for(ref, source, options)
    match = artwork_for(
        ref,
        facts,
        no_artwork=args.no_artwork,
        fetch=fetch,
        width=args.artwork_px,
        image_format=args.artwork_format,
    )
    report_degradation(
        ref,
        facts,
        match,
        source=source,
        no_favorites=args.no_favorites,
        no_artwork=args.no_artwork,
    )
    return album_record(ref, match.url, facts=facts, fallback_year=match.year)


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description=DESCRIPTION,
        formatter_class=ScanHelpFormatter,
        # Prerequisites and exit codes: the epilog, in src/scan_options.py.
        epilog=EPILOG,
    )
    _add_source_options(parser)
    _add_output_options(parser)
    _add_favorites_options(parser)
    return parser


def _add_source_options(parser: argparse.ArgumentParser) -> None:
    """Where the albums come from: the source, the library, the toolbox, the count."""
    parser.add_argument(
        "--source",
        choices=SOURCES,
        default=SOURCES[0],
        help=(
            "Where the newest albums, and their counts, year and favorites, come "
            "from: the library folder's birth times and ID3 tags (folder), Music.app "
            "via AppleScript (music), or Music.app falling back to folder when it "
            "has nothing to report (auto)."
        ),
    )
    parser.add_argument(
        "--library",
        default=DEFAULT_LIBRARY,
        help="Music library root, laid out as Artist/Album/. Ignored by --source music.",
    )
    parser.add_argument(
        "--toolbox",
        default=None,
        help=(
            "The apple-music-toolbox folder (holding recently_added/ and "
            "album_stats/), read by --source music and auto only. Unset: "
            "$APPLE_MUSIC_TOOLBOX, then a folder named apple_music_toolbox or "
            "apple-music-toolbox beside one of this tool's ancestors, up to the "
            "home folder, owned by you and writable by no one else."
        ),
    )
    parser.add_argument(
        "--count",
        type=positive_int,
        default=1,
        help="How many most-recently-added albums to record.",
    )


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    """Where the record goes, and the cover art it carries."""
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=(
            "Where to write the album record(s). - means stdout: a general tool "
            "prints its record, and a caller names its own destination if it "
            "wants a file, whose missing parent directories are created. A "
            "leading ~ is the home directory, as it is for --library."
        ),
    )
    parser.add_argument(
        "--artwork-px",
        type=positive_int,
        default=DEFAULT_ARTWORK_PX,
        help="Cover-art edge length to request, in pixels (the art is square).",
    )
    parser.add_argument(
        "--artwork-format",
        choices=ARTWORK_FORMATS,
        default=DEFAULT_ARTWORK_FORMAT,
        help=(
            "Cover-art file format to request. webp is markedly smaller than jpg at "
            "the same dimensions; the choices are the formats measured against the "
            "CDN, not every format it might serve."
        ),
    )
    parser.add_argument(
        "--no-artwork",
        action="store_true",
        help="Skip the cover-art lookup and record a null artwork URL.",
    )


def _add_favorites_options(parser: argparse.ArgumentParser) -> None:
    """Which favorites the record counts, or none."""
    parser.add_argument(
        "--favorite-min-stars",
        type=int,
        choices=STAR_CHOICES,
        default=None,
        help=(
            "Count a track as a favorite when its rating reaches this many stars. "
            "Unset: the folder source counts any track its ID3 POPM rating gives "
            "one star or more, and the Music.app sources count Music.app's own "
            "favorited flag; set, the Music.app sources count its user rating."
        ),
    )
    parser.add_argument(
        "--no-favorites",
        action="store_true",
        help=(
            "Skip the favorites count and record null favorites. Under the "
            "Music.app sources the track count and year go with it, since they are "
            "one query, so the record's year is the store's release year, or null; "
            "the folder source still reads both from its tags."
        ),
    )


def main(
    argv: list[str] | None = None,
    *,
    fetch: Fetcher = _real_fetch,
    run: Runner | None = None,
) -> int:
    """Scan the library, resolve each newest album's cover art, and write the record(s).

    Returns the exit code. `fetch` threads through to `artwork_url` -- the injection
    seam `cover_art` defines (`Fetcher`, imported above) -- so a test can drive the
    artwork-resolving arm without reaching the network. `run` is the same kind of seam
    for every Music.app query: bound into the toolbox handle a Music.app source opens,
    so a test can drive ANY `--source` outcome without spawning a real `osascript`
    process. Left None, it is the toolbox's own bounded runner.
    """
    # First statement, before anything is printed: this tool's output is captured by a
    # pipe (redirected into a file, a scheduled run, another tool's caller) as often as
    # it is read in a terminal, and a locale-derived encoding would fail on any
    # non-ASCII artist or album name.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    args = build_parser().parse_args(argv)

    library = Path(args.library).expanduser()

    try:
        toolbox = _toolbox_for(args, run)
        result = resolve_albums(args.source, library, args.count, toolbox=toolbox)
    except (NotADirectoryError, PermissionError, ToolboxNotFoundError) as exc:
        # Each names a folder, never its path: the scan raises the first two with the
        # root's final component only (src/folder_scan.py, `newest_albums`), and the
        # toolbox lookup names a folder it was given the same way.
        print(f"album_scan: {exc}", file=sys.stderr)
        return _EXIT_UNUSABLE_PATH

    if not result.refs:
        # Exit 1 means ZERO albums found and nothing written. Finding fewer than
        # --count asked for is success, not this case.
        if args.source == "music":
            # --source folder, and auto once it falls back, both consulted the
            # library and found nothing there -- the message below covers both.
            # --source music never touches the library at all, so naming it here
            # would misdiagnose what actually happened.
            print(
                "album_scan: Music.app reported no recently added album",
                file=sys.stderr,
            )
        else:
            print(f"album_scan: no album found under {library.name}", file=sys.stderr)
        return _EXIT_NO_ALBUM

    # One facts lookup and one artwork lookup PER ALBUM, not one for the whole run:
    # --count's network and subprocess cost scales with how many albums are recorded.
    options = FactsOptions(
        library=library,
        favorites=not args.no_favorites,
        min_stars=args.favorite_min_stars,
        toolbox=toolbox,
    )
    records = [
        _record_for(ref, result.resolved_source, args, options, fetch)
        for ref in result.refs
    ]
    code = _write_records(records, args.output)
    if code != _EXIT_OK:
        return code
    # Names the source that answered on every success, not just the fallback case
    # above, so an operator never has to infer it from --source alone (under auto,
    # either source can answer). On stderr unconditionally, not just when writing a
    # file: a program whose messages change stream depending on how it was invoked
    # cannot be reasoned about, and stdout is reserved for the record(s) alone so
    # `--output -` piped anywhere gets nothing but parseable JSON.
    print(
        f"album_scan: wrote {len(records)} album(s), newest "
        f"{terminal_safe_text(records[0]['artist'])} - "
        f"{terminal_safe_text(records[0]['album'])} "
        f"(source: {result.resolved_source}{_toolbox_note(toolbox)})",
        file=sys.stderr,
    )
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
