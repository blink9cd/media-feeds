"""The book record, and what every source's candidates go through on the way to it.

Private to `book_scan/`: the sibling `book_scan.py` primary script is this module's
only importer, and re-exports what `tests/` needs as `book_scan.<name>`. The shelf RSS
(`goodreads_rss`) and the library export (`goodreads_csv`) each give a list of
`Candidate`s, a record beside its Goodreads Book Id; this module merges two such
lists, ranks them by read date, slices, fills missing covers and years, and reads the
existing record for the never-go-backward guard. No network and no printing here: the
page lookup is injected, and `book_scan.main` owns every message.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import TypedDict

from goodreads_cover import PageLookup, no_page


class BookRecord(TypedDict):
    """Exactly what src/data/books.json holds.

    No `link` field, though Goodreads' link (a review URL with no user id) would leak
    nothing: no renderer reads it, and the sibling `FilmRecord` omits its own because
    a Letterboxd item link embeds the account holder's username. The two record
    shapes stay alike for a field neither uses.
    """

    title: str
    author: str
    year: str | None
    rating: int | None
    read: str | None  # None from the widget, which carries no date
    cover: str | None


#: A record beside its Goodreads Book Id ("" when the source gave none usable). The
#: id rides beside the record, never in it: it is no record field, but the cover
#: lookup and the merge both key on it.
Candidate = tuple[BookRecord, str]


def merge_candidates(
    preferred: list[Candidate], other: list[Candidate]
) -> list[Candidate]:
    """`preferred`, then each of `other` whose Book Id `preferred` lacks.

    A book both sources carry is kept as `preferred` gives it: under `--source both`
    that is the shelf RSS, whose record may carry a feed cover the export cannot. A
    candidate with no id ("") cannot be matched, so it is always kept; an id repeated
    within `other` keeps its first row.
    """
    seen = {book_id for _, book_id in preferred if book_id}
    merged = list(preferred)
    for record, book_id in other:
        if book_id and book_id in seen:
            continue
        seen.add(book_id)
        merged.append((record, book_id))
    return merged


def pick_newest(candidates: list[Candidate], count: int) -> list[Candidate]:
    """The `count` candidates read most recently, newest first.

    A stable sort, so books read the same day keep their source order (the RSS ahead
    of the export under a merge). Fewer than `count` is the short-carousel case, not
    an error. An undated (None) record ranks after every dated one, in its given
    order.
    """
    return sorted(candidates, key=lambda pair: pair[0]["read"] or "", reverse=True)[
        :count
    ]


def fill_from_pages(
    chosen: list[Candidate], lookup: PageLookup = no_page
) -> list[BookRecord]:
    """The chosen records, each coverless one given what `lookup` finds by id.

    A coverless record takes the page's cover, and its year too when it has none:
    a year the source gave is kept. A record that already has a cover asks no page,
    year or not. Called after the slice, so a run asks at most `count` lookups however
    long the shelf or the library. The default asks nothing.
    """
    for record, book_id in chosen:
        if record["cover"] is not None:
            continue
        page = lookup(book_id)
        record["cover"] = page.cover
        if record["year"] is None:
            record["year"] = page.year
    return [record for record, _ in chosen]


def newest_kept_read(output: Path) -> str | None:
    """The newest `read` date in the record file already at `output`, or None.

    None for anything that is not a readable JSON list holding at least one object
    with an ISO-date `read`: an absent or damaged file gives the guard nothing to
    protect, so it must not stop a write.
    """
    try:
        kept = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(kept, list):
        return None
    dates = [read for entry in kept if (read := _iso_read(entry)) is not None]
    if not dates:
        return None
    return max(dates)


def _iso_read(entry: object) -> str | None:
    """`entry`'s `read` when `entry` is an object whose `read` is an ISO date."""
    if not isinstance(entry, dict):
        return None
    read = entry.get("read")
    if not isinstance(read, str):
        return None
    try:
        return dt.date.fromisoformat(read).isoformat()
    except ValueError:
        return None
