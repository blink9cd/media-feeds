"""Read a member's Goodreads books: shelf RSS, library export, both, or shelf widget.

The shelf RSS (`src/goodreads_rss.py`) and widget (`src/goodreads_widget.py`) are
fetched and parsed; a hand-exported library CSV is read from a folder
(`src/goodreads_csv.py`). `--source` picks one, or the feed and CSV both, which
`src/book_sources.py` merges and ranks by date read; a widget run keeps the widget's
order. Below sits the CLI that slices to `--count`, fills covers, and writes the record.

No card rendering lives here: the page reads the written records and renders them in
the browser.
"""

from __future__ import annotations

import argparse
import http.client
import os
import re
import sys
import urllib.error
import urllib.parse
from pathlib import Path

# Put the vendored libraries (stdio_utf8, help_formatter, bounded_fetch) and the
# sibling feed_read tool folder on sys.path from this repository's root, the folder
# above this file's own. One fixed candidate per name, deliberately: a search up the
# ancestors would also take any same-named folder above the checkout and put it ahead
# of the standard library, where a module planted in it would be imported in place
# of the real one (stdio_utf8/tests/test_bootstrap.py plants such a folder).
sys.path += [
    str(Path(__file__).resolve().parents[1] / name)
    for name in ("stdio_utf8", "help_formatter", "bounded_fetch", "feed_read")
]

import feed_read
import help_formatter
import stdio_utf8

# The private sibling module, reachable only through this primary script, which is
# why the insert targets this file's own directory rather than searching upward like
# the bootstrap above.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# Each private module below is re-exported: this file uses what its own code calls, and
# `tests/` drives the rest as `book_scan.<name>` (book_page_url, book_page_from_page,
# BOOK_PAGE_URL, usable_cover, the RSS parsing helpers, and the export parser).
from book_select import (  # noqa: E402,F401 - follows the insert above; re-exports
    CSV_DIR_ENV,
    DEFAULT_SOURCE,
    SOURCE_ENV,
    SOURCES,
    SourceResult,
    combine_sources,
    read_export,
    resolve_csv_dir,
    resolve_source,
)
from book_sources import (  # noqa: E402,F401 - follows the insert above; re-exports
    BookRecord,
    Candidate,
    fill_from_pages,
    merge_candidates,
    newest_kept_read,
    pick_newest,
)
from goodreads_cover import (  # noqa: E402,F401 - follows the insert above; re-exports
    BOOK_PAGE_URL,
    BookPage,
    PageLookup,
    book_page_from_page,
    book_page_url,
    goodreads_page_lookup,
    no_page,
    og_image_from_page,
    usable_cover,
)
from goodreads_csv import (  # noqa: E402,F401 - follows the insert above; re-exports
    REQUIRED_EXPORT_COLUMNS,
    newest_export,
    parse_library_export,
    read_library_export,
)
from goodreads_rss import (  # noqa: E402,F401 - follows the insert above; re-exports
    _record,
    _rating,
    newest_books,
    shelf_candidates,
)
from goodreads_widget import (  # noqa: E402,F401 - follows the insert above; re-exports
    WIDGET_URL,
    read_widget,
    widget_candidates,
    widget_html,
    widget_url,
)


def cover_at_width(url: str, width: int) -> str:
    """`url` re-pointed at the Goodreads cover variant `width` pixels wide.

    A Goodreads cover master with no modifier at all (`.../books/<stamp>/<name>.jpg`,
    the shape a book page's `og:image` gives) is given the token: without it every
    page-lookup cover was recorded at full size. Any other URL
    carrying no `_SX<n>_` token is returned unchanged: there is no known token to add,
    and the URL stays honest about its own size.

    Not a guarantee of the delivered size. Goodreads honors a request only downward:
    above a book's own master it returns the master with a 200, so a URL naming a width
    larger than the master silently lies about it. Each book's master differs, so this
    function cannot detect that; `book_scan.md` records the ceiling and why the
    default stays where it is.
    """
    if width < 1:
        raise ValueError(
            f"cover width must be a positive number of pixels, not {width}"
        )
    sized, rewritten = _COVER_SIZE_TOKEN.subn(f"._SX{width}_.", url)
    if rewritten:
        return sized
    return _BARE_GOODREADS_MASTER.sub(rf"\1._SX{width}_\2", url, count=1)


#: Verified 2026-08-31: the per-shelf RSS survived the 2020 API shutdown and needs no
#: key on a public profile: a deliberately bogus key returned byte-identical output
#: to no key at all. `--key` exists only so a profile later made private keeps working.
FEED_TEMPLATE = "https://www.goodreads.com/review/list_rss/{user_id}"

DEFAULT_SHELF = "read"

#: Wider than the card count on purpose. The feed is ordered by date added and this
#: tool re-sorts by date read, so the books a card wants can sit below the first few
#: items. On a real shelf measured 2026-08-31, the 4th and 5th most recently read sat
#: at feed positions 5 and 6. A narrow window would silently cut them off before the
#: re-sort ever saw them.
DEFAULT_WINDOW = 50

_DEFAULT_OUTPUT = "src/data/books.json"

#: Read when `--user-id` is absent, so the user id, a personal identifier, never
#: has to appear in a shell history or a scheduled command line: `--key`'s treatment,
#: extended to the identity. tests/test_book_scan_live_feed.py reads the same name, so
#: one environment serves the refresh and the live suite alike.
USER_ID_ENV = "BOOK_SCAN_USER_ID"

#: Read when `--key` is absent. One constant, so the help text and the lookup in
#: `main` cannot spell the name two ways.
KEY_ENV = "BOOK_SCAN_KEY"

# Measured 2026-09-01 in-browser: the card's cover box maxes out at 168.7 CSS px
# (`.shell` caps at 704px), so a 2x display needs 338 physical pixels. This default is
# left at the feed's own `_SX318_` deliberately, and the measurement is why: the sample
# book's master image is only 348x475, `_SX338_` measured worse on SSIM (0.9661 vs
# 0.9673) for 932 more bytes, and every width at or above the master returns the master
# unchanged. This cover has no saving to give: the option exists so the size stays
# the caller's choice, not because the default should move.
_DEFAULT_COVER_WIDTH = 318

# Goodreads uses the Amazon sizing token: `_SX<n>_` scales to n pixels wide, with the
# height following the book's own aspect. Matched between the literal dots that delimit
# it so nothing else in the path can be rewritten.
_COVER_SIZE_TOKEN = re.compile(r"\._SX\d+_\.")

# A Goodreads cover master with no modifier: the file name right after the
# `<stamp><letter>` folder, then its extension and nothing else. Measured 2026-10-28:
# `m.media-amazon.com` and `i.gr-assets.com` both serve this path, and both honor a
# `._SX<n>_` inserted before the extension. A name holding any dot (another modifier
# such as `_SY475_`) does not match, so no second size is ever asked for.
_BARE_GOODREADS_MASTER = re.compile(
    r"(/compressed\.photo\.goodreads\.com/books/\d+[a-z]/[^/.?#]+)"
    r"(\.(?:jpe?g|png))(?=$|[?#])"
)

_EXIT_OK = 0
_EXIT_NO_BOOK = 1
_EXIT_BAD_INPUT = 2


def feed_url(user_id: str, shelf: str, window: int, key: str | None) -> str:
    """The shelf's RSS URL.

    Every component is percent-encoded: the user id and key are runtime arguments and
    must not be able to inject query parameters. `key` is omitted entirely when None,
    not sent empty, because a public shelf was verified to need no key at all,
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
            "Read a member's Goodreads books, from the shelf RSS, a hand-exported "
            "library CSV, both, or the shelf widget, and write the site's book record."
        ),
        # The shared formatter, not argparse's stock one. Below CPython 3.13 the stock
        # formatter stamps a misleading "(default: None)" on a required option, and this
        # project's floor is 3.12 (pyproject.toml's [project].requires-python). This one
        # also hides the default of an option left unset, whose help says what leaving
        # it out means.
        formatter_class=help_formatter.UnsetHidingFormatter,
        epilog=(
            "Prerequisites: for --source widget, rss, or both, a public Goodreads "
            "profile with a populated shelf; neither the widget nor the shelf RSS "
            "needs an account or, on a public profile, a key (see book_scan.md). "
            "Since 2026-10-28 Goodreads has answered 404 for the shelf RSS, the "
            "default source, so choose widget or csv. For csv or both, a "
            "folder holding a library export (My Books, Import and export), the "
            "newest .csv in it read. Run "
            "by hand whenever the record should be refreshed; never as a build "
            "step and never at page-view time. Makes at most one network call for "
            "the books, the shelf feed or widget fetch (none for csv), plus one "
            "public book-page fetch for each recorded book with no usable cover "
            "(at most --count, never retried); with --feed-file or --no-cover-lookup "
            "it asks no book page.\n\n"
            "Run order: run this tool (writes --output), review the written "
            "record, then commit it. There is no separate render/paste step, "
            "since no card rendering lives here; the page reads the written "
            "record directly in the browser.\n\n"
            "Exit codes: 0 wrote the record(s), or, under both, kept a record "
            "newer than either source - 1 no dateable book (every item or row "
            "lacked a usable read or added date, or the shelf was genuinely "
            "empty) - 2 a source could not be read (a network failure, an "
            "unreadable --feed-file, a feed that is not parseable XML, or a "
            "missing, unreadable, or malformed library export; or a shelf widget "
            "holding no readable book) - 3 the server answered that the feed or "
            "widget is gone (HTTP 404 or 410), which a retry will "
            "not change. Under both, one source failing is not a failure while the "
            "other gives a book; when neither does, the feed's code wins."
        ),
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help=(
            "Goodreads user id whose public shelf feed or widget to read. Falls back "
            f"to the {USER_ID_ENV} environment variable when omitted, so it never needs "
            "to appear in a shell history; one of the two must be set."
        ),
    )
    parser.add_argument(
        "--count",
        type=feed_read.positive_int,
        default=1,
        help="How many books to record: the newest read, or the widget's first.",
    )
    parser.add_argument(
        "--cover-width",
        type=feed_read.positive_int,
        default=_DEFAULT_COVER_WIDTH,
        help=(
            "Cover width to record, as the Goodreads `_SX<n>_` token. Honored "
            "downward only: above a book's own master image Goodreads returns the "
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
            "How many shelf items to request (the feed's per_page), wider than "
            "the card count so the read-date re-sort sees the true newest book."
        ),
    )
    parser.add_argument(
        "--key",
        default=None,
        help=(
            "Goodreads API key. Optional on a public profile;"
            f" falls back to the {KEY_ENV} environment variable when "
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
            "Read the shelf feed (or widget) from this local file instead of "
            "fetching it, which makes a refresh reproducible with no network call."
        ),
    )
    parser.add_argument(
        "--source",
        choices=SOURCES,
        default=None,
        help=(
            "Which source to read: the shelf RSS, the newest library export, both "
            f"merged, or the shelf widget alone. Falls back to the {SOURCE_ENV} "
            f"environment variable, then to {DEFAULT_SOURCE}."
        ),
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=None,
        help=(
            "Folder of Goodreads library exports; the most recently modified .csv "
            f"in it is read. Falls back to the {CSV_DIR_ENV} environment variable; "
            "one of the two is required for --source csv or both."
        ),
    )
    parser.add_argument(
        "--cover-lookup",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Ask a coverless recorded book's public Goodreads page for its cover. "
            "Off means no page fetch at all."
        ),
    )
    return parser


def _resolve_user_id(parser: argparse.ArgumentParser, given: str | None) -> str:
    """`--user-id`, else `BOOK_SCAN_USER_ID`; a usage error when neither is set.

    The parser cannot see the environment, so the refusal cannot be `required=True`.
    It stays a usage error (exit 2 with the usage line) rather than a feed error.
    Falsiness, not `is None`, on the second test: a half-filled identities file
    exports `BOOK_SCAN_USER_ID=`, and an empty id would otherwise be fetched.
    """
    user_id = given
    if user_id is None:
        user_id = os.environ.get(USER_ID_ENV)
    if not user_id:
        parser.error(
            f"--user-id is required: pass it, or set {USER_ID_ENV} in the environment"
        )
    return user_id


def _read_shelf(
    args: argparse.Namespace, user_id: str, fetch: feed_read.Fetcher
) -> SourceResult:
    """The shelf feed's candidates, or its failure's exit code after one stderr line."""
    # Statement form, for `goodreads_rss._rating`'s coverage reason. The env fallback
    # keeps the key out of shell histories and scheduled command lines.
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
        # XML, not the request that fetched it, so `exc` here never carries either.
        # Diagnostics go to stderr: stdout is the success summary, and a pipeline
        # reading it must not take a failure line for output.
        print(f"book_scan: could not read the shelf feed: {exc}", file=sys.stderr)
        # An HTTPError is the refused response, body and all: close it here rather
        # than leak the connection.
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        # A 404 or 410 is final: its own exit, so a scheduled refresh does not retry.
        return feed_read.EXIT_FEED_GONE if feed_read.feed_gone(exc) else _EXIT_BAD_INPUT
    return shelf_candidates(items)


def _gather(
    parser: argparse.ArgumentParser, args: argparse.Namespace, fetch: feed_read.Fetcher
) -> tuple[str, SourceResult]:
    """Resolve the source settings, read each named source, and combine them.

    The shelf widget is read alone and never combined. A source not named reads as
    empty; each is read once, waiting on no other, and retrying is the caller's job.
    """
    source = resolve_source(parser, args.source)
    if source == "widget":
        user_id = _resolve_user_id(parser, args.user_id)
        return source, read_widget(user_id, fetch, args.feed_file)
    csv_dir = resolve_csv_dir(parser, args.csv_dir, source)
    shelf: SourceResult = []
    export: SourceResult = []
    if source != "csv":
        shelf = _read_shelf(args, _resolve_user_id(parser, args.user_id), fetch)
    if csv_dir is not None and source in ("csv", "both"):
        export = read_export(csv_dir, args.shelf)
    return source, combine_sources(source, shelf, export)


def _kept_is_newer(output: Path, chosen: list[Candidate]) -> bool:
    """Whether the record at `output` already holds a book read after every chosen one.

    The never-go-backward guard of `--source both`: a stale export must not replace a
    newer record written from the feed. It prints its own one-line summary to stdout,
    since keeping the record is a success.
    """
    kept = newest_kept_read(output)
    if kept is None or kept <= (chosen[0][0]["read"] or ""):
        return False
    print(
        f"book_scan: kept the record; its newest book ({kept}) is newer than "
        "anything the sources gave"
    )
    return True


def _page_lookup(
    args: argparse.Namespace, cover_fetch: feed_read.Fetcher
) -> PageLookup:
    """The page lookup, or `no_page` for a run that promised no page fetch.

    `--feed-file` promises a run with no network call at all, and `--no-cover-lookup`
    asks for no page fetch, whatever the source.
    """
    if args.feed_file is not None or not args.cover_lookup:
        return no_page
    return goodreads_page_lookup(cover_fetch)


def main(
    argv: list[str] | None = None,
    *,
    fetch: feed_read.Fetcher = feed_read.real_fetch,
    cover_fetch: feed_read.Fetcher = feed_read.real_fetch,
) -> int:
    """Read the chosen source(s) and write up to `--count` book records.

    Returns the exit code. `fetch` (the shelf feed) and `cover_fetch` (a coverless
    book's public page) are the two `feed_read.Fetcher` injection seams: every unit
    test replaces both, so no unit test reaches the network.
    """
    # First statement, before anything is printed: this tool's output is captured by
    # a pipe (a scheduled run, another tool's caller) as often as it is read in a
    # terminal, and a locale-derived encoding would fail on any non-ASCII book title.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    source, gathered = _gather(parser, args, fetch)
    if isinstance(gathered, int):
        return gathered
    if source == "widget":
        # The widget lists books in finishing order and gives no read date: no re-sort.
        chosen = gathered[: args.count]
    else:
        chosen = pick_newest(gathered, args.count)
    # Before the page lookups, so a kept record costs no page fetch.
    if source == "both" and _kept_is_newer(Path(args.output), chosen):
        return _EXIT_OK
    records = fill_from_pages(chosen, _page_lookup(args, cover_fetch))

    # Re-point every cover here, not in the parsers or the page lookup, which keep
    # returning the vendor's URL verbatim once it passes the cover rule: the size stays the caller's choice
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
    read = records[0]["read"]
    print(
        f"book_scan: wrote {len(records)} book(s), newest {feed_read.terminal_safe_text(str(records[0]['title']))}"
        + (f" ({read})" if read else "")
    )
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
