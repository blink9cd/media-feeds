"""Parse the Letterboxd diary RSS feed into film records: no CLI, no network fetch.

Private to `film_scan/`: the sibling `film_scan.py` primary script is this module's
only importer, and re-exports everything `tests/` needs as `film_scan.<name>`.

Consumes `feed_read.feed_items` (every `<item>` in the RSS body) and
`feed_read.iso_date` (Letterboxd's already-ISO `watchedDate`) from the shared
`feed_read` library -- this module owns only what is Letterboxd-specific: separating diary
entries from list items, picking the poster image URL out of each item's HTML
description fragment, and picking the newest `count` films.

The director is the one field the feed cannot supply -- its `creator` is the
Letterboxd MEMBER, not the director -- so it is resolved from the `tmdb:movieId`
the feed does carry, through the sibling `wikidata_director` tool, for the CHOSEN
films only, AFTER they are picked, never per candidate -- see `newest_films`.
"""

from __future__ import annotations

import math
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict

# Put the sibling feed_read tool folder on sys.path from this repository's root, two
# folders above this file (film_scan/src/). It needs nothing else film_scan.py puts
# there: no stdio policy work happens here, and the director lookup is injected. One
# fixed candidate rather than a search up the ancestors, for film_scan.py's reason.
sys.path += [
    str(Path(__file__).resolve().parents[2] / "feed_read"),
]

import feed_read

#: Letterboxd's own namespace. Its diary fields are namespaced; the standard RSS ones
#: (title, link, description) are not.
LETTERBOXD_NS = "{https://letterboxd.com}"

#: The Movie Database's namespace, which Letterboxd embeds in every diary item it has
#: a TMDB match for. It is the ONLY join key the feed offers to any outside metadata
#: source -- the feed's own `creator` is the Letterboxd MEMBER, not the director.
TMDB_NS = "{https://themoviedb.org}"

#: How the director is resolved from a TMDB id. Injected everywhere rather than called
#: directly, so no unit test reaches the network.
DirectorLookup = Callable[[str], str | None]


class FilmRecord(TypedDict):
    """Exactly what src/data/films.json holds.

    No filesystem path, ever: the record is published, so a machine path in it would
    leak the operator's home-directory layout -- which is why src/data/*.json stays
    inside the pii_scan sweep. Nothing here is derived from the local machine at
    all; every field comes from the feed.
    """

    title: str
    year: str | None
    rating: float | None
    watched: str
    rewatch: bool
    poster: str | None
    director: str | None


class _FirstImageSrc(HTMLParser):
    """Collect the `src` of the FIRST `<img>` in an HTML fragment.

    The description is a FRAGMENT with no single root (`<p>..</p> <p>..</p>`), so
    ElementTree cannot parse it -- and it is CDATA-wrapped in the raw feed, so a regex
    over raw bytes has to account for that too. HTMLParser handles fragments by design
    and is stdlib, which is what keeps `[project].dependencies` empty.
    """

    def __init__(self) -> None:
        super().__init__()
        self.src: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record the first `<img>`'s src and ignore every later tag."""
        if tag == "img" and self.src is None:
            self.src = dict(attrs).get("src")


def poster_from_description(description: str) -> str | None:
    """The poster URL inside a diary entry's description HTML, or None.

    Measured over 150 diary items: the `<img>` is reliably the FIRST element, but a
    variable number of `<p>`/`<i>` review paragraphs may follow it, and 1 item in 150
    had no image at all. Returns the `src` VERBATIM once `feed_read.https_url_or_none`
    accepts it -- poster paths vary (`/resized/film-poster/...` and
    `/resized/sm/upload/...` both occur), so nothing here may pattern-match the
    path -- and a src that is not https with a host is no poster at all. The
    string is a third party's; the probe fetches it and the record publishes it,
    so it is refused HERE, where the probe and the written record see the same
    None on a live and a `--feed-file` run alike. The card degrades a record with
    no poster to text, the same as for the 1 in 150.
    """
    parser = _FirstImageSrc()
    parser.feed(description)
    parser.close()
    if parser.src is None:
        return None
    return feed_read.https_url_or_none(parser.src)


# MEASURED 2026-09-01 by fetching every candidate against one poster: Letterboxd
# served this set of PRE-GENERATED sizes and answered 403 Forbidden -- not 404, and
# never a live resize -- for any other dimensions (345x518 and 568x852 were both
# refused). A width the caller invents would therefore break the cover at PAGE-VIEW
# time, so `poster_at_width` refuses it here, where a build fails loudly instead.
#
# MEASURED 2026-09-04: the set is what Letterboxd pre-generates for SOME posters, not
# a universal one -- a real film's 400-wide poster answered 403 while its 300, 500 and
# 600 all served. So membership here makes a width a CANDIDATE; film_scan.py's
# `_served_poster` verifies each rewrite with one GET at refresh time and falls back
# to the feed's own URL, the one guaranteed to exist.
#
# The heights are the ones Letterboxd itself pairs with each width, NOT a computed 2:3:
# 125x187 would be 187.5 on the ratio, so a calculation would emit a URL that 403s.
#
# Byte size is NOT monotonic in these dimensions (measured: 230x345 is 34,015 bytes,
# LARGER than 400x600's 29,840), because each size is independently encoded at its own
# JPEG quality -- so a smaller width is not automatically a smaller download.
#
# Public (no leading underscore): `film_scan.py`'s build_parser reads this across
# the module boundary, for --poster-width's `choices`.
POSTER_SIZES: dict[int, int] = {
    70: 105,
    125: 187,
    150: 225,
    200: 300,
    230: 345,
    250: 375,
    300: 450,
    400: 600,
    460: 690,
    500: 750,
    600: 900,
    1000: 1500,
}

# The smallest pre-generated width that still covers a 2x display. MEASURED 2026-09-01
# in-browser: the card's cover box maxes out at 168.7 CSS px (`.shell` caps at 704px),
# so 338 physical pixels is the 2x requirement and 400 is the next size up -- the
# headroom absorbs a layout change without a re-measure. Saves 26,180 of the 56,020
# bytes the 600x900 default cost, with no visible difference at the rendered size.
# Public for the same cross-module reason as POSTER_SIZES: build_parser's own default.
DEFAULT_POSTER_WIDTH = 400

# Matches ONLY the dimension suffix, so the differing path shapes above it are
# untouched. Anchored on "-crop" because that literal ends every poster filename
# Letterboxd generates, which keeps the pattern from eating a digit run elsewhere
# in the path. Stays private: only `poster_at_width` below ever needs it.
_POSTER_DIMENSIONS = re.compile(r"-0-\d+-0-\d+-crop")


def poster_at_width(url: str, width: int) -> str:
    """`url` re-pointed at the `width`-pixel poster Letterboxd MAY have pre-generated.

    Rewrites only the `-0-<w>-0-<h>-crop` DIMENSION suffix and never the path: both
    `/resized/film-poster/...` and `/resized/sm/upload/...` occur in a real feed, which
    is why `poster_from_description` returns the src verbatim in the first place.

    A URL carrying no such suffix is returned UNCHANGED. Nothing to rewrite is not an
    error -- the original URL is still honest about its own size, it merely misses the
    saving -- and raising here would drop a cover the card would otherwise show.

    Raises ValueError for a width Letterboxd pre-generates for NO poster. A width it
    passes is a candidate, not a guarantee: the sizes are per poster (POSTER_SIZES'
    comment), so the caller must verify the result -- `film_scan.py`'s
    `_served_poster` GETs it and falls back to `url` when it is refused. Emitting an
    unverified URL would 403 at page-view time, which no test of this tool could catch.
    """
    if width not in POSTER_SIZES:
        raise ValueError(
            f"Letterboxd serves no {width}px poster -- "
            f"pre-generated widths are {sorted(POSTER_SIZES)}"
        )
    return _POSTER_DIMENSIONS.sub(f"-0-{width}-0-{POSTER_SIZES[width]}-crop", url)


def diary_entries(items: list[ET.Element]) -> list[ET.Element]:
    """Only the items that are films.

    HALF a real Letterboxd feed is lists, not films (measured: 50 of 100). A list item
    carries no film elements at all, so the discriminator is STRUCTURAL -- the presence
    of `filmTitle` -- never a position or a link pattern.
    """
    return [
        item for item in items if item.find(LETTERBOXD_NS + "filmTitle") is not None
    ]


def _rating(item: ET.Element) -> float | None:
    """The member's rating, or None when the film was logged unrated.

    A 0.0 maps to None: Goodreads writes a literal "0" for an unrated book, and
    nothing about the Letterboxd feed rules out the equivalent 0.0 memberRating. The
    page treats a numeric 0 as a real value, not a missing one, so a 0.0 would
    render as five empty stars instead of omitting the rating segment the way every
    other unrated record does. Written as a statement-form if/else: a conditional
    EXPRESSION emits no branch arcs under coverage.py, so an untested side would
    stay invisible at a truthful 100%.

    A non-finite value maps to None too. `float()` accepts "nan" and "inf", and
    `json.dumps` would write those as the bare tokens NaN and Infinity, which are
    not JSON: the page's JSON.parse would fail on the whole record file and every
    film card would disappear. `write_records` refuses such a value as well; this
    is the mapping that keeps a feed from ever producing one.
    """
    raw = (item.findtext(LETTERBOXD_NS + "memberRating") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    if value:
        return value
    return None


def _no_director(_tmdb_id: str) -> str | None:
    """Answer "no director" without asking anything.

    The DEFAULT lookup, deliberately. `newest_films` is called bare by
    tests/test_live_feed.py, which makes exactly ONE live call -- to Letterboxd --
    so a resolving default would quietly add a second boundary. It is also what
    `--no-director` substitutes, so the opt-out needs no branch of its own.
    """
    return None


def _tmdb_id(item: ET.Element) -> str | None:
    """The item's TMDB id, or None when the feed carries no match for the film.

    Statement form, for the reason `_rating` above records: a trailing `or None` is
    a short-circuit EXPRESSION and emits no branch arc under coverage.py, so the
    None arm would stay untested behind a truthful 100%.
    """
    value = (item.findtext(TMDB_NS + "movieId") or "").strip()
    if value:
        return value
    return None


def _director(item: ET.Element, lookup: DirectorLookup) -> str | None:
    """The film's director via `lookup`, or None when there is nothing to ask with.

    A missing TMDB id short-circuits before the seam is called at all: an empty id
    could only ever come back None, so asking would spend a round trip to learn
    what is already known.
    """
    tmdb_id = _tmdb_id(item)
    if tmdb_id is None:
        return None
    return lookup(tmdb_id)


def _record(item: ET.Element) -> FilmRecord | None:
    """One diary item as a record, or None if it is missing what a card needs."""
    title = (item.findtext(LETTERBOXD_NS + "filmTitle") or "").strip()
    watched = feed_read.iso_date(item.findtext(LETTERBOXD_NS + "watchedDate") or "")
    if not title or watched is None:
        return None
    year = (item.findtext(LETTERBOXD_NS + "filmYear") or "").strip() or None
    return FilmRecord(
        title=title,
        year=year,
        rating=_rating(item),
        watched=watched.isoformat(),
        rewatch=(item.findtext(LETTERBOXD_NS + "rewatch") or "").strip() == "Yes",
        poster=poster_from_description(item.findtext("description") or ""),
        # Resolved by `newest_films` for the CHOSEN films only -- see its docstring.
        director=None,
    )


def newest_films(
    items: list[ET.Element], count: int, *, lookup: DirectorLookup = _no_director
) -> list[FilmRecord]:
    """The `count` most recently watched films, newest first.

    Ranked explicitly by watched date, not feed order (a contract Letterboxd does
    not make). Returns fewer than `count` when the diary holds fewer -- the site's
    short-carousel case.

    The director is resolved AFTER the slice, never inside `_record`: each lookup
    spends about three API calls and a real diary carries about fifty entries, so
    resolving per record would aim roughly 150 requests at Wikimedia every
    refresh. Slicing first also makes a short diary cheap by construction -- three
    against a one-film diary costs one lookup. Items ride alongside their records
    through the ranking because the TMDB id lives on the ITEM, never the record.
    """
    ranked = [
        (item, record)
        for item, record in ((i, _record(i)) for i in diary_entries(items))
        if record is not None
    ]
    ranked.sort(key=lambda pair: pair[1]["watched"], reverse=True)
    chosen = ranked[:count]
    for item, record in chosen:
        record["director"] = _director(item, lookup)
    return [record for _, record in chosen]
