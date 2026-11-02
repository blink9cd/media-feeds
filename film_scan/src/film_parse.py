"""Parse the Letterboxd diary RSS feed into film records: no CLI, no network fetch.

Private to `film_scan/`: the sibling `film_scan.py` primary script is this module's
only importer, and re-exports everything `tests/` needs as `film_scan.<name>`.

Consumes `feed_read.feed_items` (every `<item>` in the RSS body) and
`feed_read.iso_date` (Letterboxd's already-ISO `watchedDate`) from the shared
`feed_read` library; this module owns only what is Letterboxd-specific: separating diary
entries from list items and picking the newest `count` films.

The director and the poster are the two fields the feed cannot supply (its
`creator` is the Letterboxd member, not the director, and the poster in its
description lives on a host that refuses scripted requests), so both are resolved
from the `tmdb:movieId` the feed does carry, each through an injected lookup (the
sibling `wikidata_director` tool's, and `tmdb_poster`'s), for the chosen films only,
after they are picked, never per candidate; see `newest_films`.
"""

from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
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
#: a TMDB match for. It is the only join key the feed offers to any outside metadata
#: source: the feed's own `creator` is the Letterboxd member, not the director.
TMDB_NS = "{https://themoviedb.org}"

#: How the director is resolved from a TMDB id. Injected everywhere rather than called
#: directly, so no unit test reaches the network.
DirectorLookup = Callable[[str], str | None]

#: How the poster URL is resolved from a TMDB id, injected for the same reason.
#: `film_scan.main` hands over `tmdb_poster.tmdb_poster_lookup`'s on a live run.
PosterLookup = Callable[[str], str | None]


class FilmRecord(TypedDict):
    """Exactly what src/data/films.json holds.

    No filesystem path, ever: the record is published, so a machine path in it would
    leak the operator's home-directory layout, which is why src/data/*.json belongs
    inside any secret scan of the site. Nothing here is derived from the local machine at
    all; every field comes from the feed or from a lookup keyed on its TMDB id.
    """

    title: str
    year: str | None
    rating: float | None
    watched: str
    rewatch: bool
    poster: str | None
    director: str | None


def diary_entries(items: list[ET.Element]) -> list[ET.Element]:
    """Only the items that are films.

    Half a real Letterboxd feed is lists, not films (measured: 50 of 100). A list item
    carries no film elements at all, so the discriminator is structural (the presence
    of `filmTitle`), never a position or a link pattern.
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
    expression emits no branch arcs under coverage.py, so an untested side would
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

    The default lookup, deliberately. `newest_films` is called bare by
    tests/test_live_feed.py, which makes one live call, to Letterboxd,
    so a resolving default would quietly add a second boundary. It is also what
    `--no-director` substitutes, so the opt-out needs no branch of its own.
    """
    return None


def _no_poster(_tmdb_id: str) -> str | None:
    """Answer "no poster" without asking anything.

    The default poster lookup, for the reason `_no_director` gives, and what an
    offline (`--feed-file`) run or a run with no TMDB token substitutes.
    """
    return None


def _tmdb_id(item: ET.Element) -> str | None:
    """The item's TMDB id, or None when the feed carries no match for the film.

    Statement form, for the reason `_rating` above records: a trailing `or None` is
    a short-circuit expression and emits no branch arc under coverage.py, so the
    None arm would stay untested behind a truthful 100%.
    """
    value = (item.findtext(TMDB_NS + "movieId") or "").strip()
    if value:
        return value
    return None


def _by_tmdb_id(item: ET.Element, lookup: Callable[[str], str | None]) -> str | None:
    """`lookup`'s answer for the item's TMDB id, or None when the item has none.

    The director and the poster lookups alike. A missing TMDB id short-circuits
    before the seam is called at all: an empty id could only ever come back None, so
    asking would spend a round trip to learn what is already known.
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
        # Both resolved by `newest_films` for the chosen films only; see its
        # docstring. The description's own <img> is never read: its host refuses
        # scripted requests, so the deploy could not fetch it.
        poster=None,
        director=None,
    )


def newest_films(
    items: list[ET.Element],
    count: int,
    *,
    lookup: DirectorLookup = _no_director,
    poster_lookup: PosterLookup = _no_poster,
) -> list[FilmRecord]:
    """The `count` most recently watched films, newest first.

    Ranked explicitly by watched date, not feed order (a contract Letterboxd does
    not make). Returns fewer than `count` when the diary holds fewer: the site's
    short-carousel case.

    The director is resolved after the slice, never inside `_record`: each lookup
    spends about three API calls and a real diary carries about fifty entries, so
    resolving per record would aim roughly 150 requests at Wikimedia every
    refresh. Slicing first also makes a short diary cheap by construction: three
    against a one-film diary costs one lookup. Items ride alongside their records
    through the ranking because the TMDB id lives on the item, never the record.
    The poster is resolved the same way, one TMDB request per chosen film.
    """
    ranked = [
        (item, record)
        for item, record in ((i, _record(i)) for i in diary_entries(items))
        if record is not None
    ]
    ranked.sort(key=lambda pair: pair[1]["watched"], reverse=True)
    chosen = ranked[:count]
    for item, record in chosen:
        record["director"] = _by_tmdb_id(item, lookup)
        record["poster"] = _by_tmdb_id(item, poster_lookup)
    return [record for _, record in chosen]
