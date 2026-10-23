"""Fetch a member's Letterboxd diary and write the site's film record: the CLI entry point.

The parsing -- separating diary entries from list items, picking the poster URL
out of each item's HTML description, ranking and slicing to the newest `--count`
films, and resolving each chosen film's director -- lives in the private sibling
`src/film_parse.py`. This module owns only what a CLI needs: building the feed URL,
fetching or reading the feed body, calling `film_parse.newest_films`, resizing each
record's poster to the caller's chosen width and verifying Letterboxd serves it,
writing the records, and printing a one-line confirmation. Every `film_parse` name
this file's own code does not otherwise need is re-exported below anyway, because
`tests/` drives them as `film_scan.<name>`.

No card rendering lives here: the consumer reads and renders the records this tool
writes, and this tool writes records and nothing else.
"""

from __future__ import annotations

import argparse
import http.client
import os
import sys
import urllib.error
import urllib.parse
from pathlib import Path

# Put the vendored libraries (stdio_utf8, help_formatter, bounded_fetch) and the
# sibling tool folders (feed_read, wikidata_director) on sys.path from this
# repository's root, the folder above this file's own. One fixed candidate per name,
# deliberately: a search up the ancestors would also take any same-named folder ABOVE
# the checkout and put it ahead of the standard library, where a module planted in it
# would be imported in place of the real one (stdio_utf8/tests/test_bootstrap.py
# plants exactly that).
sys.path += [
    str(Path(__file__).resolve().parents[1] / name)
    for name in (
        "stdio_utf8",
        "help_formatter",
        "bounded_fetch",
        "feed_read",
        "wikidata_director",
    )
]

import feed_read
import help_formatter
import stdio_utf8
from wikidata_director import (
    directors_for,
)

# The private sibling module, reachable only through this primary script, which is
# why the insert targets this file's own directory rather than the repository root
# like the bootstrap above.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# Used by this file's own code: DEFAULT_POSTER_WIDTH and POSTER_SIZES
# (build_parser's --poster-width default/choices), DirectorLookup, _no_director and
# newest_films (main's seams), and poster_at_width (_served_poster). FilmRecord is
# the record type this tool writes, re-exported so a caller can name it without
# reaching into src/. diary_entries, poster_from_description, LETTERBOXD_NS,
# _rating, _record and _tmdb_id are used only by `tests/`, driven as
# `film_scan.<name>`.
from film_parse import (  # noqa: F401 - re-exports
    LETTERBOXD_NS,
    DEFAULT_POSTER_WIDTH,
    POSTER_SIZES,
    DirectorLookup,
    FilmRecord,
    _no_director,
    _rating,
    _record,
    _tmdb_id,
    diary_entries,
    newest_films,
    poster_at_width,
    poster_from_description,
)

#: The public diary feed. No key, no account, no API -- verified 2026-08-31.
FEED_TEMPLATE = "https://letterboxd.com/{username}/rss/"

_DEFAULT_OUTPUT = "src/data/films.json"

#: Read when `--username` is absent, so the username -- a personal identifier -- never
#: has to appear in a shell history or a scheduled command line. The name is the one
#: tests/test_live_feed.py already reads, so one environment serves the refresh and
#: the live suite alike.
USERNAME_ENV = "FILM_SCAN_USERNAME"

_EXIT_OK = 0
_EXIT_NO_FILM = 1
_EXIT_BAD_INPUT = 2


def feed_url(username: str) -> str:
    """The RSS URL for a member's diary.

    The username is a RUNTIME argument with no default, and is never written into the
    record, this repository's docs, or a commit message -- it identifies the account
    holder, and no keyword scanner would flag a bare username, so it is kept out by
    construction. Percent-encoded so a value containing `/` or `?` cannot reshape the URL.
    """
    return FEED_TEMPLATE.format(username=urllib.parse.quote(username, safe=""))


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a member's Letterboxd diary and write the site's film record."
        ),
        # The shared formatter, not argparse's stock one: below CPython 3.13 the stock
        # formatter stamps a misleading "(default: None)" on a REQUIRED option, and this
        # project's floor is 3.12 (pyproject.toml's [project].requires-python).
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=(
            "Prerequisites: a public Letterboxd profile with diary entries -- no "
            "account, key, or API needed. Run by hand whenever the record should "
            "refresh; never as a build step or at page-view time. Network cost: "
            "one feed fetch, one GET per film recorded to verify its resized "
            "poster, plus about three Wikidata calls per film recorded. "
            "--no-director skips the lookups entirely and is the lever for a "
            "fast refresh. --feed-file also skips the fetch and the poster "
            "verification, for a fully offline run (how samples/ is "
            "regenerated).\n\n"
            "Run order: run this tool (writes --output), review the written "
            "record(s), then commit them -- no separate render/paste step; "
            "the page reads the record(s) directly in the browser.\n\n"
            "Exit codes: 0 wrote the record(s) - 1 no diary entries in the feed "
            "(every item was a list, or the feed was empty) - 2 the feed could "
            "not be read (a network failure, an unreadable --feed-file, or a "
            "feed that is not parseable XML)."
        ),
    )
    parser.add_argument(
        "--username",
        default=None,
        help=(
            "Letterboxd username whose public diary feed to read. Falls back to the "
            f"{USERNAME_ENV} environment variable when omitted, so it never needs "
            "to appear in a shell history; one of the two must be set."
        ),
    )
    parser.add_argument(
        "--count",
        type=feed_read.positive_int,
        default=1,
        help="How many most-recently-watched films to record.",
    )
    parser.add_argument(
        "--output",
        default=_DEFAULT_OUTPUT,
        help="Where to write the film record(s), relative to the working directory.",
    )
    parser.add_argument(
        "--no-director",
        action="store_true",
        help="Skip the Wikidata director lookup and record a null director.",
    )
    parser.add_argument(
        "--poster-width",
        type=int,
        choices=sorted(POSTER_SIZES),
        default=DEFAULT_POSTER_WIDTH,
        help=(
            "Poster width to record, from the set Letterboxd pre-generates for SOME "
            "posters (it answers 403 for any other, so the choices are the measured "
            "set, not a range). A live run verifies each rewritten URL and falls "
            "back to the feed's own poster where that size is refused; a "
            "--feed-file run keeps the rewrite unverified."
        ),
    )
    parser.add_argument(
        "--feed-file",
        type=Path,
        default=None,
        help=(
            "Read the diary feed from this local file instead of fetching it -- "
            "makes a refresh reproducible with no network call."
        ),
    )
    return parser


def _served_poster(poster: str, width: int, fetch: feed_read.Fetcher | None) -> str:
    """`poster` re-pointed at `width`, kept only when Letterboxd actually serves it.

    MEASURED 2026-09-04: the pre-generated sizes are PER POSTER, not one universal
    set -- one real film's 400-wide poster answered 403 while its 300, 500 and 600
    all answered 200, and the other recorded films' 400s served fine. The page drops
    a poster whose image fails to load, so an unverified rewrite surfaced as a
    text-only card. This GETs the rewritten URL once, through the same `fetch` seam
    the feed uses (one seam keeps every test's injection unchanged; one GET per
    recorded film at a by-hand refresh is negligible), and falls back to the feed's
    own URL -- the one guaranteed to exist -- when the rewrite is refused.

    `fetch` is None on an offline (`--feed-file`) run, which keeps the UNVERIFIED
    rewrite: that path must stay fully offline (samples/ regenerates through it with
    an exploding fetch), and film_scan.md documents the trade. Statement-form
    guards throughout, not conditional expressions, for the coverage reason `main`
    records.
    """
    resized = poster_at_width(poster, width)
    if fetch is None:
        return resized
    if resized == poster:
        # Nothing was rewritten (no size suffix), so there is nothing to verify.
        return resized
    try:
        fetch(resized, feed_read.FEED_TIMEOUT_SECONDS)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # An HTTPError IS the refused response (urllib's addinfourl, socket and
        # all): close it here, or CPython warns at garbage collection that the
        # file was implicitly cleaned up. A timeout or a parse error carries no
        # response, so the same tuple `main` catches for the feed stays as is.
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        return poster
    return resized


def main(
    argv: list[str] | None = None,
    *,
    fetch: feed_read.Fetcher = feed_read.real_fetch,
    lookup: DirectorLookup = directors_for,
) -> int:
    """Fetch, parse, and write up to `--count` film records. Returns the exit code.

    Runs `validate -> act -> confirm -> record`: parse args; resolve the feed body;
    pick the newest `--count` films; write the records; print a one-line
    confirmation. `fetch` and `lookup` are the two network injection seams every
    unit test replaces, so no unit test reaches the network. Unlike `newest_films`,
    `lookup`'s default here DOES resolve over the network: a bare command-line run is
    exactly the case that should name the director, while the library default stays
    inert.
    """
    # First statement, before anything is printed: this tool's output is captured by
    # a pipe (a scheduled run, another tool's caller) as often as it is read in a
    # terminal, and a locale-derived encoding would fail on any non-ASCII film title.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate the identity BEFORE the feed branch, from either source: the parser
    # cannot see the environment, so the refusal lives here, and stays a USAGE error
    # (exit 2 with the usage line) rather than a feed error. Statement form, not a
    # conditional EXPRESSION: coverage.py emits branch arcs for an if/else STATEMENT
    # but none for `x if cond else y`, so an untested side would hide behind a
    # truthful 100%. Falsiness, not `is None`, on the second test: a half-filled
    # identities file exports `FILM_SCAN_USERNAME=`, and an empty username would
    # otherwise fetch `https://letterboxd.com//rss/`.
    username = args.username
    if username is None:
        username = os.environ.get(USERNAME_ENV)
    if not username:
        parser.error(
            f"--username is required: pass it, or set {USERNAME_ENV} in the environment"
        )

    try:
        # Statement form again, for the reason given above.
        if args.feed_file is not None:
            body = args.feed_file.read_bytes()
        else:
            body = fetch(feed_url(username), feed_read.FEED_TIMEOUT_SECONDS)
        items = feed_read.feed_items(body)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # Never echoes the username: urllib's own exceptions (URLError, HTTPError,
        # socket.timeout) report a reason or an HTTP status, never the request URL,
        # and feed_read.feed_items' ValueError messages describe the XML, not the
        # request that fetched it -- so `exc` here never carries the username.
        # Diagnostics go to stderr: stdout is the success summary, and a pipeline
        # reading it must not take a failure line for output.
        print(f"film_scan: could not read the diary feed: {exc}", file=sys.stderr)
        # An HTTPError IS the refused response, body and all: close it here, the
        # same way _served_poster closes its own, rather than leak the connection.
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        return _EXIT_BAD_INPUT

    # Statement form, not a conditional expression, for the reason the feed-file
    # branch above records. Substituting the no-op lookup rather than branching at
    # the call site keeps `newest_films` with exactly one way to be called.
    # Annotated, not inferred: without this mypy narrows `resolve` to the exact
    # signature of _no_director and then rejects the `lookup` arm as incompatible.
    resolve: DirectorLookup
    if args.no_director:
        resolve = _no_director
    else:
        resolve = lookup

    records = newest_films(items, args.count, lookup=resolve)
    if not records:
        print("film_scan: no film found in the diary feed", file=sys.stderr)
        return _EXIT_NO_FILM
    # Statement form again: the probe seam is the feed's own `fetch`, withheld on
    # a --feed-file run so that path stays fully offline (see _served_poster).
    probe: feed_read.Fetcher | None
    if args.feed_file is None:
        probe = fetch
    else:
        probe = None
    for record in records:
        # Re-point the poster at the smaller pre-generated size before the record
        # is written, verified against the CDN. Done HERE rather than inside
        # `_record` so the parsing layer keeps returning the feed's own URL,
        # verbatim once it passes the https rule, and the size stays the caller's
        # choice. KEEP THE None GUARD: a Letterboxd entry can carry no poster at
        # all, or one the rule refused, and poster_at_width would crash on it --
        # fixtures/diary.xml's "Posterless" entry exists to catch a loop that
        # quietly drops this check.
        if record["poster"] is not None:
            record["poster"] = _served_poster(
                record["poster"], args.poster_width, probe
            )

    try:
        feed_read.write_records(Path(args.output), records)
    except OSError as exc:
        # Exit 2, not a traceback: an unusable --output is bad input, and the
        # previous record is still in place (the rename never happened).
        print(f"film_scan: could not write the record file: {exc}", file=sys.stderr)
        return _EXIT_BAD_INPUT
    print(
        f"film_scan: wrote {len(records)} film(s), newest {feed_read.terminal_safe_text(str(records[0]['title']))} "
        f"({records[0]['watched']})"
    )
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
