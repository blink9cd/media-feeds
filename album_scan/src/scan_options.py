"""What `album_scan`'s command line is built from: its constants, a type, the help text.

`album_scan.py` keeps `build_parser` and every `add_argument` call itself, so the
parser and its options stay in the one file a CLI-to-doc check reads beside
`album_scan.md`; this module holds the pieces that are not options -- the defaults it
names, the `--count` type, the formatter, and the long description and epilog -- so the
entry point stays under the file-length bar. Nothing here reads a library, a tag or
Music.app.
"""

from __future__ import annotations

import argparse

import help_formatter
from folder_facts import STAR_THRESHOLDS

# A general tool PRINTS its record; a caller names its own destination. Defaulting to
# one consumer's data directory is the rejected alternative: this tool knows nothing
# about its consumers, and the file writer creates a missing parent directory.
DEFAULT_OUTPUT = "-"

#: The three sources, the default first: the folder needs nothing installed beyond
#: this project, so a run that names no source reads no other program.
SOURCES = ("folder", "music", "auto")

#: The star thresholds `--favorite-min-stars` accepts: one per star on the scale.
STAR_CHOICES = range(1, len(STAR_THRESHOLDS) + 1)


def positive_int(text: str) -> int:
    """An option value that must be an integer of at least one: --count, --artwork-px.

    Raised as an ArgumentTypeError rather than checked in main, so argparse renders
    the usage message and exits 2 -- the same treatment a non-integer already gets,
    rather than a second spelling of "bad argument". It also means the rejection
    happens before any library scan, Music.app query or store lookup: without the
    floor on --artwork-px, a zero or negative edge length went straight into a
    `0x0bb.webp` URL in the record. Defined locally, not in a shared module, on
    purpose: a shared module for so small a function would need its own gate-checked
    folder, a companion doc, and its own tests.
    """
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


class ScanHelpFormatter(
    help_formatter.ReqAwareDefaultsFormatter, argparse.RawDescriptionHelpFormatter
):
    """Show every default AND keep the epilog's own line breaks.

    Neither stock formatter does both, and using the defaults formatter alone silently
    re-wraps the epilog into one unbroken block: its paragraph breaks vanish and even a
    short heading like "Exit codes:" can be split across a line.

    The two bases override different methods -- `_get_help_string` for the defaults,
    `_fill_text` for the wrapping -- so they compose with no conflict to resolve.
    Because `_fill_text` no longer wraps, the epilog below is hard-wrapped by hand.
    The mix stays local rather than imported from the sibling tool that builds the same
    pair: the tools import each other's library functions, not their CLIs.

    The defaults half is the SHARED formatter, which below CPython 3.13 suppresses the
    misleading "(default: None)" argparse stamps onto a REQUIRED option. This tool has
    none today, so binding it here is what keeps that true of the option added next.
    """


# Hard-wrapped like the epilog: the raw formatter keeps this text's own line breaks
# and adds none.
DESCRIPTION = (
    "Find the --count most recently added albums, resolve their cover art,\n"
    "and emit their JSON records."
)

# Prerequisites and exit codes live in the epilog, not in help= strings. This tool
# only emits the record; rendering it is the consumer's business -- see
# album_scan.md, "Who consumes this record".
EPILOG = (
    "Prerequisites:\n"
    "  --source folder (the default) needs a music library laid out as\n"
    "  <library>/Artist/Album/, and reads each album's tracks, year and\n"
    "  favorites from its own files' ID3 tags. --source music and auto need\n"
    "  Music.app installed, running and scriptable (macOS only; System\n"
    "  Settings > Privacy & Security > Automation must allow it), and the\n"
    "  apple-music-toolbox, found by --toolbox, then $APPLE_MUSIC_TOOLBOX,\n"
    "  then a clone of it beside this one. Run by hand whenever a fresh\n"
    "  record is wanted; never as a build step.\n"
    "\n"
    "What a run spends:\n"
    "  Network: TWO iTunes Search requests PER ALBUM recorded, because\n"
    "  resolving one cover pools an artist-and-album query with an\n"
    "  artist-alone one and sends both -- so --count 3 spends six. Pass\n"
    "  --no-artwork to stay fully offline.\n"
    "  Music.app, as local osascript subprocesses (never network):\n"
    "  --source folder runs none; --source music and auto run one\n"
    "  date-added query per run, then one query PER ALBUM Music.app named\n"
    "  for its counts and year, unless --no-favorites. Auto's fallback\n"
    "  reads the folder's tags instead and adds no query.\n"
    "\n"
    "Output:\n"
    "  Prints the record(s) to stdout by default (--output -), or writes them\n"
    "  to --output when that names a file, creating missing parent\n"
    "  directories and replacing the file in one step, so a write that fails\n"
    "  leaves any previous record untouched; a written file is mode 0644.\n"
    "  Always a JSON array, even at the default --count of one. Every\n"
    "  human-readable message goes to stderr, unconditionally, so\n"
    "  `--output -` piped or redirected to a file gets nothing but the JSON\n"
    "  record(s): the final confirmation, the fallback notice, one line per\n"
    "  album whose counts or favorites (unless --no-favorites) or cover art\n"
    "  (unless --no-artwork) could not be resolved, and one line for each\n"
    "  artist folder the scan, or track tag the reader, could not read.\n"
    "\n"
    "Exit codes:\n"
    "  0  wrote the record(s); finding fewer than --count asked for is\n"
    "     success\n"
    "  1  no album found for --source (music: Music.app had nothing to\n"
    "     report; folder, or auto once it falls back: nothing under\n"
    "     --library)\n"
    "  2  a usage error, --library not a directory or not readable when a\n"
    "     folder scan actually runs, --output cannot be written, or no\n"
    "     apple-music-toolbox for --source music or auto"
)
