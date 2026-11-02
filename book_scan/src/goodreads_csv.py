"""Read a hand-exported Goodreads library (My Books, Import and export) into candidates.

Private to `book_scan/`: the sibling `book_scan.py` primary script is this module's
only importer, and re-exports what `tests/` needs as `book_scan.<name>`.

The export is one CSV row per book in the whole library. Its structure was measured
2026-10-28 on a real export, whose values were never copied: UTF-8 with no byte order
mark and LF line ends, fields holding a comma quoted, 23 named columns, ratings written
both `4` and `4.0` in the same file, bare-year publication columns that are sometimes
empty, and `YYYY/MM/DD` dates. A BOM and CRLF are tolerated anyway, and columns are
found by name, never by position, so a reordered or widened export still reads.

No network and no printing here. Every refusal is a `ValueError` whose message names a
column, the file's name, or the decoder's reason, never a row's content: the rows are
the member's library, and the message is printed.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from pathlib import Path

from book_sources import BookRecord, Candidate

#: The columns a record is built from. A file lacking any of them is not a Goodreads
#: library export, and is refused rather than read as an empty library.
REQUIRED_EXPORT_COLUMNS = (
    "Book Id",
    "Title",
    "Author",
    "My Rating",
    "Year Published",
    "Original Publication Year",
    "Date Read",
    "Date Added",
    "Exclusive Shelf",
)

#: The suffix an export file carries, compared case-blind.
EXPORT_SUFFIX = ".csv"

_BOM = "﻿"

#: A whole-star rating 1-5, written bare or with a zero fraction (`4`, `4.0`): the
#: export uses both forms in one file. ASCII only, so no other script's digit passes.
_RATING = re.compile(r"([1-5])(?:\.0+)?", re.ASCII)

#: A publication year as the export writes it: a bare number. A leading minus is
#: allowed for a year before the common era.
_YEAR = re.compile(r"-?[0-9]{1,4}", re.ASCII)

#: The export's date shape, `YYYY/MM/DD`.
_DATE = re.compile(r"([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})", re.ASCII)

#: A Goodreads Book Id: all ASCII digits.
_BOOK_ID = re.compile(r"[0-9]+", re.ASCII)


def _rating(value: str) -> int | None:
    """The whole-star rating `value` names, or None when unrated or anything else.

    `0` and `0.0` mean unrated; `4.5`, `7`, and junk are not a rating a card can show.
    """
    match = _RATING.fullmatch(value.strip())
    if match is None:
        return None
    return int(match.group(1))


def _year(original: str, published: str) -> str | None:
    """The original publication year, else this edition's, else None, never "".

    The card's `year` is the RSS `book_published`, which matches the export's Original
    Publication Year; Year Published (this edition's) is the fallback.
    """
    for value in (original.strip(), published.strip()):
        if _YEAR.fullmatch(value):
            return value
    return None


def _date(value: str) -> dt.date | None:
    """`value` as a date when it is a real `YYYY/MM/DD` day, else None."""
    match = _DATE.fullmatch(value.strip())
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _book_id(value: str) -> str:
    """`value` stripped when it is a Book Id, else "" (no lookup, no merge match)."""
    stripped = value.strip()
    if _BOOK_ID.fullmatch(stripped):
        return stripped
    return ""


def _candidate(row: dict[str, str]) -> Candidate | None:
    """One row as a candidate, or None when it has neither a read nor an added date.

    The read date falls back to the added date, as the RSS path does: Goodreads sets
    Date Read only on the `read` shelf, and a book with no date cannot be ranked.
    """
    read = _date(row["Date Read"]) or _date(row["Date Added"])
    if read is None:
        return None
    record = BookRecord(
        title=row["Title"].strip(),
        author=row["Author"].strip(),
        year=_year(row["Original Publication Year"], row["Year Published"]),
        rating=_rating(row["My Rating"]),
        read=read.isoformat(),
        cover=None,
    )
    return record, _book_id(row["Book Id"])


def _header(rows: csv.DictReader[str]) -> list[str]:
    """The export's column names; ValueError unless every required one is there."""
    names = rows.fieldnames
    if not names:
        raise ValueError("the file has no header row")
    missing = [column for column in REQUIRED_EXPORT_COLUMNS if column not in names]
    if missing:
        raise ValueError(
            "not a Goodreads library export: no column named "
            + ", ".join(repr(column) for column in missing)
        )
    return list(names)


# fuzz-target
def parse_library_export(text: str, shelf: str) -> list[Candidate]:
    """The dateable books on `shelf`, in file order, from an export's `text`.

    Raises ValueError for a file that has no header, lacks a required column, or is
    not readable as CSV; otherwise never raises, whatever the rows hold. A short row
    reads its missing fields as empty and a long one ignores the extras.
    """
    rows = csv.DictReader(io.StringIO(text.removeprefix(_BOM), newline=""), restval="")
    wanted = shelf.strip()
    candidates: list[Candidate] = []
    try:
        _header(rows)
        for row in rows:
            if row["Exclusive Shelf"].strip() != wanted:
                continue
            candidate = _candidate(row)
            if candidate is not None:
                candidates.append(candidate)
    except csv.Error as exc:
        # The csv module's messages describe the syntax ("field larger than field
        # limit"), never a field's text, so the reason is safe to carry.
        raise ValueError(f"not a readable CSV file ({exc})") from exc
    return candidates


def newest_export(folder: Path) -> Path:
    """The most recently modified `.csv` file in `folder`; the later name breaks a tie.

    ValueError when the folder cannot be listed or holds no export. The messages never
    name the folder: it is the operator's own path, and the file name is enough to act on.
    """
    try:
        exports = [
            (entry.stat().st_mtime, entry.name, entry)
            for entry in folder.iterdir()
            if entry.suffix.lower() == EXPORT_SUFFIX and entry.is_file()
        ]
    except OSError as exc:
        raise ValueError(f"could not list the export folder ({exc.strerror})") from exc
    if not exports:
        raise ValueError(f"no {EXPORT_SUFFIX} export in the export folder")
    return max(exports)[2]


def read_library_export(folder: Path, shelf: str) -> list[Candidate]:
    """The dateable books on `shelf` in the newest export in `folder`.

    Every failure is a ValueError naming the file by its name only.
    """
    path = newest_export(folder)
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except OSError as exc:
        raise ValueError(f"{path.name}: {exc.strerror}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path.name}: not UTF-8 text") from exc
    try:
        return parse_library_export(text, shelf)
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
