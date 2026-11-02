"""Decide which iTunes search result is actually the album that was asked for.

The defect this exists to fix: requesting `limit=1` and using `results[0]` with no check
that it is the right album (however thorough the `except` list is about failures)
has no check at all for a successful response about the wrong thing, so any album
the store cannot find returns some other album's cover, stated as fact. *Fictionals
(Gold)* would come back wearing the *Teal Album*'s art.

The name is the primary signal; the year and the track count are tiebreakers, never
gates. The question this answers is "is this the same work?" A different edition of the
right album is a fine answer, because its art is the same one. Only a different work is a
defect. (The one place the two facts do more than break a tie: a same-artist candidate
whose name does not match is still admitted when both agree; see `pick_album`.)

That ordering was not a preference. Gating on artist and year and track count all
matching was measured against a real library and rejected: it broke more matches that
already worked than it corrected. A tag year and a store release year legitimately
differ across reissues and regions, and a deluxe edition has a different track count,
so an and of the three rejects correct answers. A wrong cover traded for more missing
ones is a net loss.

This module is pure: it does no I/O and knows nothing about how the candidates were
fetched, so every branch is testable without a network.
"""

from __future__ import annotations

import re

# One trailing parenthesized or bracketed qualifier at a time, applied until the name
# stops changing so that "Fictionals (Red Album) [Deluxe Edition]" reduces the whole way.
# This is what lets "Fictionals (Gold)" match the store's plain "Fictionals", and
# "Stand Still (Deluxe 10th Anniversary Edition)" match a library's plain "Stand Still".
_TRAILING_QUALIFIER = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")

# Apostrophes are deleted rather than turned into a space, unlike other punctuation:
# "Writer's Block" must reduce to "writers block", not "writer s block", so that a
# library and a store spelling the possessive differently still meet. Every other mark
# separates words and so becomes a space.
_ELIDED = str.maketrans({"'": "", "\u2019": "", "\u02bc": ""})

#: How many characters of an ISO-8601 date are the year.
_YEAR_LENGTH = 4

# The longest name the matcher will look at. Real artist and album titles are shorter
# by an order of magnitude, and the qualifier strip is quadratic in the name's length
# (see `split_name`), so a cap this size keeps every strip to a fraction of a
# millisecond. Candidates' names arrive from the network, and `pick_album` applies the
# cap before any of them is normalized, so a reply cannot use a name to hold the CPU.
_MAX_NAME_CHARS = 512


def _flatten(text: str) -> str:
    """Casefolded, punctuation-free, single-spaced."""
    return _NON_ALPHANUMERIC.sub(" ", text.casefold().translate(_ELIDED)).strip()


def _over_cap(text: str) -> bool:
    """Whether a name is too long to be normalized at all (see `_MAX_NAME_CHARS`)."""
    return len(text) > _MAX_NAME_CHARS


def split_name(text: str) -> tuple[str, str]:
    """A name as (base, qualifier): "Fictionals (Gold)" -> ("fictionals", "gold").

    The qualifier is every trailing parenthesized or bracketed group, joined. It is
    kept rather than discarded because which side carries one decides whether two
    names are the same work; see `pick_album`.

    `&` folds to `and` before punctuation is stripped, not after. Stripping first would
    erase the ampersand entirely and leave "peter bjorn john", which matches neither
    spelling the store uses. Measured: stripping first turned a correct match into a
    miss.

    The cost is quadratic in the name's length: the qualifier pattern is searched from
    every start position, and an unmatched opening bracket makes each of those
    searches scan to the end of the string. `pick_album` bounds it by refusing any
    name longer than `_MAX_NAME_CHARS` before calling here; a caller feeding this
    function directly owes it the same bound.
    """
    text = text.replace("&", " and ")
    qualifiers: list[str] = []
    while True:
        found = _TRAILING_QUALIFIER.search(text)
        if found is None:
            break
        qualifiers.append(found.group(0))
        text = text[: found.start()]
    return _flatten(text), _flatten(" ".join(reversed(qualifiers)))


def normalize(text: str) -> str:
    """A name reduced to the part worth comparing, qualifiers dropped."""
    return split_name(text)[0]


def release_year(candidate: dict) -> int | None:
    """The candidate's release year, or `None` when it has no usable date.

    Nothing on the far side of the search boundary is type-checked against this module,
    so a missing or non-string `releaseDate` is a case to survive rather than a case
    that cannot happen. Never raises.

    The one place the year is read out of the date, for `pick_album`'s comparison and
    the I/O half's fallback year alike, so neither the four-character slice nor the
    digit rule can drift between the two. Comparing the year as text instead let them
    disagree: the I/O half read an Arabic-Indic "٢٠١٩" as 2019 while the matcher never
    matched it against "2019".

    `str.isdecimal()`, not `str.isdigit()`: the superscript "²" satisfies `isdigit()`
    but `int()` rejects it, so a `releaseDate` of "²⁰²⁵-01-01" (remote input, straight
    out of the search response) used to pass the guard and raise `ValueError` on the
    conversion, from a line outside the try that guards the search, out of a function
    whose whole contract is that it never raises. Passing `isdecimal()` guarantees the
    conversion cannot raise. Not the stricter `isascii() and isdigit()` either: an
    Arabic-Indic "٢٠٢٥" is a real year that `int()` reads as 2025, and clamping to
    ASCII would stop the raise by discarding a good answer instead.
    """
    raw = candidate.get("releaseDate")
    if not isinstance(raw, str):
        return None
    head = raw[:_YEAR_LENGTH]
    if not head.isdecimal():
        return None
    return int(head)


def _same_work(
    wanted_base: str, wanted_qualifier: str, base: str, qualifier: str
) -> bool:
    """Whether two names denote the same work, not merely the same base title.

    Bases must match. Beyond that, which side carries a qualifier is what decides it,
    and getting this wrong reproduces the original defect:

    - Neither side qualified, or only the candidate is ("Stand Still" against "Stand Still
      (Deluxe 10th Anniversary Edition)"): the same work in a different edition, and
      its cover art is the one wanted. Accept.
    - Only the album is qualified ("Fictionals (Gold)" against the store's plain
      "Fictionals"): the store simply does not use the color name. Accept; this is the
      case the whole fix exists for.
    - Both are qualified and the qualifiers differ ("Fictionals (Gold)" against "Fictionals
      (Teal Album)"): two different works that happen to share a base title. Reject.
      Without this arm both reduce to "fictionals", and an album absent from the search
      results silently borrows a sibling album's cover, which is the exact defect
      this module was written to remove.
    """
    if base != wanted_base:
        return False
    if not wanted_qualifier or not qualifier:
        return True
    return qualifier == wanted_qualifier


def same_artist_ids(candidates: list[dict], *, artist: str) -> list[int]:
    """The distinct `artistId`s of candidates credited to `artist`, in pool order.

    These are what the I/O half may look up when no candidate is the album: the
    store's search index can leave an album out while the artist's own catalog lists
    it. Only a candidate whose artist name normalizes to the wanted one lends its id
    (another artist's catalog cannot hold this album), and only a positive JSON
    integer counts as an id, because the value goes into a URL. A JSON `true` decodes
    to a `bool`, which is an `int` to `isinstance`, so it is refused by name.

    Names past `_MAX_NAME_CHARS` are refused before normalizing, on either side, for
    the reason `pick_album` gives.
    """
    if _over_cap(artist):
        return []
    wanted_artist = normalize(artist)
    ids: list[int] = []
    for candidate in candidates:
        artist_id = candidate.get("artistId")
        if isinstance(artist_id, bool) or not isinstance(artist_id, int):
            continue
        if artist_id <= 0 or artist_id in ids:
            continue
        artist_name = str(candidate.get("artistName", ""))
        if _over_cap(artist_name) or normalize(artist_name) != wanted_artist:
            continue
        ids.append(artist_id)
    return ids


def pick_album(
    candidates: list[dict],
    *,
    artist: str,
    album: str,
    year: int | None,
    tracks: int | None,
) -> dict | None:
    """The candidate that is the requested album, or `None` when none of them is.

    `year` and `tracks` come from Music.app and are `None` when it could not be asked;
    the weaker name-only test then applies, and the answer may honestly be `None`.

    They must never be sourced from the iTunes fallback year. Ranking an iTunes
    candidate by a year taken from iTunes is self-confirming: it would agree with
    whatever it picked, which is the "any answer is the right answer" behavior
    this module exists to remove.

    No name longer than `_MAX_NAME_CHARS` is normalized, on either side: a candidate
    carrying one is skipped, and a wanted name that long is answered with `None`
    outright. The strip behind `normalize` is quadratic in length, and every
    candidate's names are the network's to choose.
    """
    if _over_cap(artist) or _over_cap(album):
        # The library's names come from folder names rather than the network, but
        # they are input all the same, and one folder name past the cap would hold
        # the CPU as surely as a candidate would.
        return None
    wanted_artist = normalize(artist)
    wanted_base, wanted_qualifier = split_name(album)
    scored: list[tuple[tuple[bool, bool, bool], dict]] = []
    for candidate in candidates:
        name = candidate.get("collectionName")
        if not isinstance(name, str):
            continue
        # Judged on the stringified artist name, because that is the text the
        # strip would receive: a non-string value renders to a string of its own
        # length, and a long enough one is the same hazard as a long string.
        artist_name = str(candidate.get("artistName", ""))
        if _over_cap(name) or _over_cap(artist_name):
            continue
        if normalize(artist_name) != wanted_artist:
            continue
        base, qualifier = split_name(name)
        name_matches = _same_work(wanted_base, wanted_qualifier, base, qualifier)
        year_matches = year is not None and release_year(candidate) == year
        tracks_match = tracks is not None and candidate.get("trackCount") == tracks
        # Two doors in, and one veto. A candidate earns consideration by being the
        # same name, or by agreeing on both independent facts at once. Either fact
        # alone, without the name, is too weak: a year matches across a whole year's
        # releases and a track count across most albums ever made. The veto: a
        # candidate that shares the base title but that `_same_work` refused is a
        # sibling work ("Fictionals (Teal Album)" for "Fictionals (Black Album)"), and
        # siblings can share a year and a track count too, so the facts must not
        # re-admit what the name rejected: that is the borrowed-cover defect again,
        # through the second door.
        sibling = base == wanted_base and not name_matches
        if sibling or not (name_matches or (year_matches and tracks_match)):
            continue
        scored.append(((name_matches, year_matches, tracks_match), candidate))
    if not scored:
        return None
    # Tuple order is the precedence: name, then year, then track count. Sorting on the
    # tuple keeps that ranking in one place rather than spread across comparisons.
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return scored[0][1]
