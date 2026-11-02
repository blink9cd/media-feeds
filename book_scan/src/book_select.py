"""Which source(s) a `book_scan` run reads, and how their answers combine.

Private to `book_scan/`: the sibling `book_scan.py` primary script is this module's
only importer, and re-exports what `tests/` needs as `book_scan.<name>`. It resolves
`--source` and `--csv-dir` against their environment fallbacks, reads the library
export, and combines the shelf feed's answer with the export's into the candidates a
run records or the exit code it ends with. The shelf feed itself is read by
`book_scan.py`, which owns the feed URL. Diagnostics go to stderr only; stdout is the
caller's success summary.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from book_sources import Candidate, merge_candidates
from goodreads_csv import read_library_export

#: `book_scan`'s exit codes this module returns; the full table is in `book_scan.md`.
EXIT_NO_BOOK = 1
EXIT_BAD_INPUT = 2

#: Which source(s) a run reads: the shelf RSS, the newest library export, both
#: merged (book_scan.md, "The library export"), or the shelf widget alone. The
#: variable lets a scheduled caller's environment switch the books source with no
#: change to its command line.
SOURCES = ("rss", "csv", "both", "widget")
DEFAULT_SOURCE = "rss"
SOURCE_ENV = "BOOK_SCAN_SOURCE"

#: Read when `--csv-dir` is absent: the folder each new export is moved into.
CSV_DIR_ENV = "BOOK_SCAN_CSV_DIR"

#: How each source setting is named in a "no book found" line. The `rss` wording is
#: the line this tool printed before the export existed, kept byte for byte. No
#: `widget` entry: the widget never reaches `combine_sources`, and an empty widget
#: must stay exit 2, never this quiet exit 1.
_SOURCE_NAMES = {
    "rss": "the shelf feed",
    "csv": "the library export",
    "both": "the shelf feed or the library export",
}

#: A source's outcome: its candidates, or the exit code its failure maps to.
SourceResult = list[Candidate] | int


def resolve_source(parser: argparse.ArgumentParser, given: str | None) -> str:
    """`--source`, else `BOOK_SCAN_SOURCE`, else the default; a usage error if invalid.

    Statement form, not a conditional expression, for the coverage reason `goodreads_rss._rating` gives, and
    falsiness on the second test: an empty variable counts as unset. Only the variable
    can be invalid here, since the parser checks the option's choices itself; the
    refusal names the variable, never its value, though the value is no secret.
    """
    source = given
    if source is None:
        source = os.environ.get(SOURCE_ENV)
    if not source:
        source = DEFAULT_SOURCE
    if source not in SOURCES:
        parser.error(f"{SOURCE_ENV} must be one of: {', '.join(SOURCES)}")
    return source


def resolve_csv_dir(
    parser: argparse.ArgumentParser, given: Path | None, source: str
) -> Path | None:
    """`--csv-dir`, else `BOOK_SCAN_CSV_DIR`; a usage error when csv is read without one.

    Only `csv` and `both` read the export, so only they refuse a missing folder.

    A leading `~` is expanded, since a value from a feeds env file never passes
    through a shell that would expand it.
    """
    csv_dir = given
    if csv_dir is None:
        from_env = os.environ.get(CSV_DIR_ENV)
        if from_env:
            csv_dir = Path(from_env)
    if csv_dir is None:
        if source in ("csv", "both"):
            parser.error(
                f"--csv-dir is required for --source {source}: pass it, or set "
                f"{CSV_DIR_ENV} in the environment"
            )
        return None
    return csv_dir.expanduser()


def read_export(csv_dir: Path, shelf: str) -> SourceResult:
    """The newest export's candidates, or exit 2 after one stderr line.

    `goodreads_csv`'s messages name a column, the file's name, or a decoder reason,
    never a row's content, so the line is safe to print.
    """
    try:
        return read_library_export(csv_dir, shelf)
    except ValueError as exc:
        print(f"book_scan: could not read the library export: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT


def _answered(result: SourceResult) -> list[Candidate]:
    """A source's candidates, or none when it failed."""
    if isinstance(result, int):
        return []
    return result


def combine_sources(
    source: str, shelf: SourceResult, export: SourceResult
) -> SourceResult:
    """Both sources' candidates merged, the shelf's preferred, or the run's exit code.

    With no book from either: the feed's failure code first, so a gone feed stays `3`
    and a timeout `2`; else the export's; else `1`, every source having answered.
    Under `both` a book from one source carries the run, with one stderr line naming
    the source that failed.
    """
    merged = merge_candidates(_answered(shelf), _answered(export))
    if not merged:
        for result in (shelf, export):
            if isinstance(result, int):
                return result
        print(f"book_scan: no book found in {_SOURCE_NAMES[source]}", file=sys.stderr)
        return EXIT_NO_BOOK
    if isinstance(shelf, int):
        print("book_scan: recording from the library export alone", file=sys.stderr)
    elif isinstance(export, int):
        print("book_scan: recording from the shelf feed alone", file=sys.stderr)
    return merged
