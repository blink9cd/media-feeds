"""Parse the Goodreads per-shelf RSS feed into book records, and write the site's record.

The shared `feed_read` library supplies the RSS items and the RFC-822 date parsing;
this module owns only what is Goodreads-specific: picking the largest usable cover
image out of each item's four size variants, reading a rating that uses "0" to mean
"unrated", reading a publication year the feed gives as a bare year, and re-sorting
by date READ rather than trusting feed order (the shelf RSS is ordered by date
ADDED). Below that sits the CLI that builds the shelf URL, fetches the feed, picks
the newest `--count` books, and writes `src/data/books.json`.

No card rendering lives here: the page reads the written records and renders them in
the browser.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import os
import re
import sys
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TypedDict

# Put the vendored libraries (stdio_utf8, help_formatter, bounded_fetch) and the
# sibling feed_read tool folder on sys.path from this repository's root, the folder
# above this file's own. One fixed candidate per name, deliberately: a search up the
# ancestors would also take any same-named folder ABOVE the checkout and put it ahead
# of the standard library, where a module planted in it would be imported in place
# of the real one (stdio_utf8/tests/test_bootstrap.py plants exactly that).
sys.path += [
    str(Path(__file__).resolve().parents[1] / name)
    for name in ("stdio_utf8", "help_formatter", "bounded_fetch", "feed_read")
]

import feed_read
import help_formatter
import stdio_utf8


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
    read: str
    cover: str | None


def _cover(item: ET.Element) -> str | None:
    """The largest cover variant Goodreads offers, or None.

    MEASURED 2026-08-31: `book_image_url` and `book_small_image_url` are BOTH the
    `_SX50_` thumbnail -- 50 pixels wide, unusable on a card -- and
    `book_medium_image_url` is `_SX98_`. Only `book_large_image_url` (`_SX318_`) is
    big enough.

    `_SX318_` scales by WIDTH, so the height varies per book.

    Only an https URL with a host is a cover (`feed_read.https_url_or_none`): the
    string is a third party's, and the record publishes it for the page to load an
    image from, so anything else is recorded as no cover -- which the card degrades
    to text exactly as it does for a book with no large variant at all. The
    helper answers None for the empty string too, so a missing element needs no
    branch of its own.
    """
    return feed_read.https_url_or_none(
        (item.findtext("book_large_image_url") or "").strip()
    )


def cover_at_width(url: str, width: int) -> str:
    """`url` re-pointed at the Goodreads cover variant `width` pixels wide.

    A URL carrying no `_SX<n>_` token is returned UNCHANGED -- Goodreads serves the
    master that way, so there is nothing to rewrite and the URL is already honest.

    NOT a guarantee of the delivered size. Goodreads honors a request only DOWNWARD:
    above a book's own master it returns the master with a 200, so a URL naming a width
    larger than the master silently lies about it. Each book's master differs, so this
    function cannot detect that -- `book_scan.md` records the ceiling and why the
    default stays where it is.
    """
    if width < 1:
        raise ValueError(
            f"cover width must be a positive number of pixels, not {width}"
        )
    return _COVER_SIZE_TOKEN.sub(f"._SX{width}_.", url)


def _rating(item: ET.Element) -> int | None:
    """The member's rating 1-5, or None when unrated.

    Goodreads writes "0" for an unrated book, which must become None rather than a
    zero-star card. Written as a statement-form if/else rather than `value if value
    else None`: a conditional EXPRESSION emits no branch arcs under coverage.py, so
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

    `book_published` is a BARE year (e.g. "2018"), not a date -- so it is carried
    through as a string rather than parsed to an int, matching `FilmRecord.year`'s
    `str | None` so the two feed-derived records share one convention for the same
    concept. Nothing downstream does arithmetic on it; the card prints it.

    A book with no `book_published` element, or one present but blank, records None --
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
    shelf item, unlike the poster/rating fields, which genuinely vary) -- only a
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


def newest_books(items: list[ET.Element], count: int) -> list[BookRecord]:
    """The `count` most recently READ books on the shelf, newest first.

    Re-sorts rather than trusting feed order. MEASURED 2026-08-31: the shelf RSS is
    ordered by `user_date_added`, NOT by `user_read_at`, and on a real feed the two
    orders diverge just below the top. Returns fewer than `count` when the shelf
    holds fewer -- that is the site's short-carousel case, not an error.
    """
    records = [r for r in (_record(i) for i in items) if r is not None]
    records.sort(key=lambda r: r["read"], reverse=True)
    return records[:count]


#: Verified 2026-08-31: the per-shelf RSS survived the 2020 API shutdown and needs NO
#: key on a public profile -- a deliberately bogus key returned byte-identical output
#: to no key at all. `--key` exists only so a profile later made private keeps working.
FEED_TEMPLATE = "https://www.goodreads.com/review/list_rss/{user_id}"

DEFAULT_SHELF = "read"

#: Wider than the card count ON PURPOSE. The feed is ordered by date ADDED and this
#: tool re-sorts by date READ, so the books a card wants can sit below the first few
#: items -- on a real shelf measured 2026-08-31, the 4th and 5th most recently read sat
#: at feed positions 5 and 6. A narrow window would silently cut them off before the
#: re-sort ever saw them.
DEFAULT_WINDOW = 50

_DEFAULT_OUTPUT = "src/data/books.json"

#: Read when `--user-id` is absent, so the user id -- a personal identifier -- never
#: has to appear in a shell history or a scheduled command line: `--key`'s treatment,
#: extended to the identity. tests/test_book_scan_live_feed.py reads the same name, so
#: one environment serves the refresh and the live suite alike.
USER_ID_ENV = "BOOK_SCAN_USER_ID"

#: Read when `--key` is absent. One constant, so the help text and the lookup in
#: `main` cannot spell the name two ways.
KEY_ENV = "BOOK_SCAN_KEY"

# MEASURED 2026-09-01 in-browser: the card's cover box maxes out at 168.7 CSS px
# (`.shell` caps at 704px), so a 2x display needs 338 physical pixels. This default is
# left at the feed's own `_SX318_` DELIBERATELY, and the measurement is why: the sample
# book's master image is only 348x475, `_SX338_` measured WORSE on SSIM (0.9661 vs
# 0.9673) for 932 MORE bytes, and every width at or above the master returns the master
# unchanged. This cover has no saving to give -- the option exists so the size stays
# the caller's choice, not because the default should move.
_DEFAULT_COVER_WIDTH = 318

# Goodreads uses the Amazon sizing token: `_SX<n>_` scales to n pixels WIDE, with the
# height following the book's own aspect. Matched between the literal dots that delimit
# it so nothing else in the path can be rewritten.
_COVER_SIZE_TOKEN = re.compile(r"\._SX\d+_\.")

_EXIT_OK = 0
_EXIT_NO_BOOK = 1
_EXIT_BAD_INPUT = 2


def feed_url(user_id: str, shelf: str, window: int, key: str | None) -> str:
    """The shelf's RSS URL.

    Every component is percent-encoded: the user id and key are runtime arguments and
    must not be able to inject query parameters. `key` is omitted ENTIRELY when None
    -- not sent empty -- because a public shelf was verified to need no key at all,
    and an empty `key=` parameter would misrepresent that measured fact.
    """
    params = {"shelf": shelf, "per_page": str(window)}
    if key is not None:
        params["key"] = key
    base = FEED_TEMPLATE.format(user_id=urllib.parse.quote(user_id, safe=""))
    return f"{base}?{urllib.parse.urlencode(params)}"


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a member's Goodreads shelf and write the site's book record."
        ),
        # The shared formatter, not argparse's stock one: below CPython 3.13 the stock
        # formatter stamps a misleading "(default: None)" on a REQUIRED option, and this
        # project's floor is 3.12 (pyproject.toml's [project].requires-python).
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=(
            "Prerequisites: none beyond a public Goodreads profile with a "
            "populated shelf -- the shelf RSS needs no account and, on a public "
            "profile, no key at all (see book_scan.md). Run "
            "by hand whenever the record should be refreshed; never as a build "
            "step and never at page-view time. Makes ONE network call, the shelf "
            "fetch itself, unless --feed-file is given, in which case it makes "
            "none.\n\n"
            "Run order: run this tool (writes --output), review the written "
            "record, then commit it -- there is no separate render/paste step, "
            "since no card rendering lives here; the page reads the written "
            "record directly in the browser.\n\n"
            "Exit codes: 0 wrote the record(s) - 1 the shelf holds no dateable "
            "book (every item lacked a usable read or added date, or the shelf "
            "was genuinely empty) - 2 the feed could not be read (a network "
            "failure, an unreadable --feed-file, or a feed that is not "
            "parseable XML)."
        ),
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help=(
            "Goodreads user id whose public shelf feed to read. Falls back to the "
            f"{USER_ID_ENV} environment variable when omitted, so it never needs "
            "to appear in a shell history; one of the two must be set."
        ),
    )
    parser.add_argument(
        "--count",
        type=feed_read.positive_int,
        default=1,
        help="How many most-recently-read books to record.",
    )
    parser.add_argument(
        "--cover-width",
        type=feed_read.positive_int,
        default=_DEFAULT_COVER_WIDTH,
        help=(
            "Cover width to record, as the Goodreads `_SX<n>_` token. Honored "
            "DOWNWARD only: above a book's own master image Goodreads returns the "
            "master unchanged, so a larger number buys nothing."
        ),
    )
    parser.add_argument(
        "--shelf",
        default=DEFAULT_SHELF,
        help="Which Goodreads shelf to read.",
    )
    parser.add_argument(
        "--window",
        type=feed_read.positive_int,
        default=DEFAULT_WINDOW,
        help=(
            "How many shelf items to request (the feed's per_page) -- wider than "
            "the card count so the read-date re-sort sees the true newest book."
        ),
    )
    parser.add_argument(
        "--key",
        default=None,
        help=(
            "Goodreads API key. Optional on a public profile"
            f" -- falls back to the {KEY_ENV} environment variable when "
            "omitted, so it never needs to appear in a shell history."
        ),
    )
    parser.add_argument(
        "--output",
        default=_DEFAULT_OUTPUT,
        help="Where to write the book records, relative to the working directory.",
    )
    parser.add_argument(
        "--feed-file",
        type=Path,
        default=None,
        help=(
            "Read the shelf feed from this local file instead of fetching it -- "
            "makes a refresh reproducible with no network call."
        ),
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    fetch: feed_read.Fetcher = feed_read.real_fetch,
) -> int:
    """Fetch, parse, and write up to `--count` book records. Returns the exit code.

    `fetch` is the `feed_read.Fetcher` injection seam: every unit test replaces it, so
    no unit test reaches the network.
    """
    # First statement, before anything is printed: this tool's output is captured by
    # a pipe (a scheduled run, another tool's caller) as often as it is read in a
    # terminal, and a locale-derived encoding would fail on any non-ASCII book title.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate the identity BEFORE the feed branch, from either source: the parser
    # cannot see the environment, so the refusal cannot be `required=True`. It lives
    # here and stays a USAGE error (exit 2 with the usage line) rather than a feed
    # error. Statement form, not a conditional expression, for `_rating`'s coverage
    # reason. Falsiness, not `is None`, on the second test: a half-filled identities
    # file exports `BOOK_SCAN_USER_ID=`, and an empty id would otherwise be fetched.
    user_id = args.user_id
    if user_id is None:
        user_id = os.environ.get(USER_ID_ENV)
    if not user_id:
        parser.error(
            f"--user-id is required: pass it, or set {USER_ID_ENV} in the environment"
        )

    # Statement form again, for the same reason. The env fallback keeps the key out of
    # shell histories and scheduled command lines.
    key = args.key
    if key is None:
        key = os.environ.get(KEY_ENV)

    try:
        if args.feed_file is not None:
            body = args.feed_file.read_bytes()
        else:
            body = fetch(
                feed_url(user_id, args.shelf, args.window, key),
                feed_read.FEED_TIMEOUT_SECONDS,
            )
        items = feed_read.feed_items(body)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # Never echoes the user id or key: urllib's own exceptions (URLError,
        # HTTPError, socket.timeout) report a reason or an HTTP status, never the
        # request URL, and feed_read.feed_items' ValueError messages describe the
        # XML, not the request that fetched it -- so `exc` here never carries either.
        # Diagnostics go to stderr: stdout is the success summary, and a pipeline
        # reading it must not take a failure line for output.
        print(f"book_scan: could not read the shelf feed: {exc}", file=sys.stderr)
        # An HTTPError IS the refused response, body and all: close it here rather
        # than leak the connection.
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        return _EXIT_BAD_INPUT

    records = newest_books(items, args.count)
    if not records:
        print("book_scan: no book found in the shelf feed", file=sys.stderr)
        return _EXIT_NO_BOOK

    # Re-point every cover here, not in `_cover`, which keeps returning the feed's URL
    # verbatim once it passes the https rule: the size stays the caller's choice
    # rather than a constant buried in the parser. Guarded per record: a book with
    # no large-image variant, or one the rule refused, degrades to cover: None, and
    # resizing None would raise inside `cover_at_width`'s regex substitution rather
    # than staying None.
    for record in records:
        if record["cover"] is not None:
            record["cover"] = cover_at_width(record["cover"], args.cover_width)

    try:
        feed_read.write_records(Path(args.output), records)
    except OSError as exc:
        # Exit 2, not a traceback: an unusable --output is bad input, and the
        # previous record is still in place (the rename never happened).
        print(f"book_scan: could not write the record file: {exc}", file=sys.stderr)
        return _EXIT_BAD_INPUT
    print(
        f"book_scan: wrote {len(records)} book(s), newest {feed_read.terminal_safe_text(str(records[0]['title']))} "
        f"({records[0]['read']})"
    )
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
