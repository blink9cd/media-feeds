"""Read Goodreads shelf RSS items into book candidates.

Private to `book_scan/`: the sibling `book_scan.py` primary script is this module's
only importer, and re-exports everything `tests/` needs as `book_scan.<name>`. Moved
here verbatim from `book_scan.py` when the library export became a second source, so
the script stays under the file-length bar.

Picks the largest usable cover image out of each item's four size variants, reads a
rating that uses "0" to mean "unrated", reads a publication year the feed gives as a
bare year, and re-sorts by date read rather than trusting feed order (the shelf RSS is
ordered by date added). The shared `feed_read` library supplies the RFC-822 dates.
"""

from __future__ import annotations

import datetime as dt
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Put the sibling feed_read tool folder on sys.path from this repository's root, two
# folders above this file (book_scan/src/), so this module imports alone as well. One
# fixed candidate rather than a search up the ancestors, for book_scan.py's reason.
sys.path += [
    str(Path(__file__).resolve().parents[2] / "feed_read"),
]

import feed_read
from book_sources import BookRecord, Candidate, fill_from_pages, pick_newest
from goodreads_cover import PageLookup, no_page, usable_cover


def _cover(item: ET.Element) -> str | None:
    """The largest cover variant Goodreads offers, or None.

    Measured 2026-08-31: `book_image_url` and `book_small_image_url` are both the
    `_SX50_` thumbnail, 50 pixels wide and unusable on a card, and
    `book_medium_image_url` is `_SX98_`. Only `book_large_image_url` (`_SX318_`) is
    big enough.

    `_SX318_` scales by width, so the height varies per book.

    Only an https URL with a host that is not Goodreads' "nophoto" placeholder is a
    cover (`usable_cover`): the string is a third party's, and the record publishes
    it for the page to load an image from, so anything else is recorded as no cover,
    which sends the book to the page lookup and, failing that, to the card's text.
    The rule answers None for the empty string too, so a missing element needs no
    branch of its own.
    """
    return usable_cover((item.findtext("book_large_image_url") or "").strip())


def _rating(item: ET.Element) -> int | None:
    """The member's rating 1-5, or None when unrated.

    Goodreads writes "0" for an unrated book, which must become None rather than a
    zero-star card. Written as a statement-form if/else rather than `value if value
    else None`: a conditional expression emits no branch arcs under coverage.py, so
    its untested side would stay invisible at a truthful 100%.
    """
    try:
        value = int((item.findtext("user_rating") or "").strip())
    except ValueError:
        return None
    if value:
        return value
    return None


def _year(item: ET.Element) -> str | None:
    """The book's publication year as the bare year string the feed carries, or None.

    `book_published` is a bare year (e.g. "2018"), not a date, so it is carried
    through as a string rather than parsed to an int, matching `FilmRecord.year`'s
    `str | None` so the two feed-derived records share one convention for the same
    concept. Nothing downstream does arithmetic on it; the card prints it.

    A book with no `book_published` element, or one present but blank, records None,
    never "". The card omits a line whose value is None, and an empty string would
    render a blank row instead of no row at all.

    Statement-form if/else for the coverage reason `_rating` gives; a trailing
    `or None` hides its untested side the same way.
    """
    value = (item.findtext("book_published") or "").strip()
    if value:
        return value
    return None


def _read_date(item: ET.Element) -> dt.date | None:
    """When the book was read, falling back to when it was added.

    The fallback is insurance, not a live case (all 49 items on the real shelf it was
    measured against carry `user_read_at`): a book marked read with no date would
    otherwise sort as if never read.
    """
    return feed_read.rfc822_date(
        item.findtext("user_read_at") or ""
    ) or feed_read.rfc822_date(item.findtext("user_date_added") or "")


def _record(item: ET.Element) -> BookRecord | None:
    """One shelf item as a record, or None if it has no usable read date.

    Title and author are taken as given (Goodreads always populates both on a real
    shelf item, unlike the poster/rating fields, which genuinely vary); only a
    missing read date drops the item, since a record with no date cannot be ranked.
    """
    read_date = _read_date(item)
    if read_date is None:
        return None
    return BookRecord(
        title=(item.findtext("title") or "").strip(),
        author=(item.findtext("author_name") or "").strip(),
        year=_year(item),
        rating=_rating(item),
        read=read_date.isoformat(),
        cover=_cover(item),
    )


def shelf_candidates(items: list[ET.Element]) -> list[Candidate]:
    """Every dateable shelf item as a candidate, in feed order, beside its `book_id`."""
    return [
        (record, (item.findtext("book_id") or "").strip())
        for item in items
        if (record := _record(item)) is not None
    ]


def newest_books(
    items: list[ET.Element],
    count: int,
    page_lookup: PageLookup = no_page,
) -> list[BookRecord]:
    """The `count` most recently read books on the shelf, newest first.

    Re-sorts rather than trusting feed order. Measured 2026-08-31: the shelf RSS is
    ordered by `user_date_added`, not by `user_read_at`, and on a real feed the two
    orders diverge just below the top. Returns fewer than `count` when the shelf
    holds fewer: that is the site's short-carousel case, not an error.

    A chosen book with no usable cover asks `page_lookup` by its `book_id`, after the
    slice, so a run makes at most `count` lookups however long the shelf. Ids ride
    beside their records because the id is not a record field. The default asks
    nothing, so a bare call makes no network request.
    """
    return fill_from_pages(pick_newest(shelf_candidates(items), count), page_lookup)
