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
import unicodedata

# One trailing parenthesized or bracketed qualifier at a time, applied until the name
# stops changing so that "Fictionals (Red Album) [Deluxe Edition]" reduces the whole way.
# This is what lets "Fictionals (Gold)" match the store's plain "Fictionals", and
# "Stand Still (Deluxe 10th Anniversary Edition)" match a library's plain "Stand Still".
_TRAILING_QUALIFIER = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")

# The Unicode category prefix of a combining mark. A Devanagari vowel sign or a
# decomposed accent is part of its word, not a separator, so it is kept beside the
# letters and digits of every script.
_MARK = "M"

# Apostrophes are deleted rather than turned into a space, unlike other punctuation:
# "Writer's Block" must reduce to "writers block", not "writer s block", so that a
# library and a store spelling the possessive differently still meet. Every other mark
# separates words and so becomes a space.
_ELIDED = str.maketrans({"'": "", "\u2019": "", "\u02bc": ""})

# Variation selectors, the emoji and text ones (U+FE00 to U+FE0F) and the ideographic
# ones (U+E0100 to U+E01EF), are deleted first of all, in `_canonical`, ahead of NFKC
# and the qualifier strip. Each is a combining mark, so the every-script rule would
# keep one, and "I \u2764\ufe0f NY" would read "i \ufe0f ny" while "I \u2764 NY"
# reads "i ny": a selector chooses how a character is drawn, never which name it
# spells. Deleted ahead of the strip because a selector after a closing bracket,
# "Fictionals (Gold)\ufe0f", would otherwise hide the qualifier from it.
_VARIATION_SELECTORS = dict.fromkeys(
    [*range(0xFE00, 0xFE0F + 1), *range(0xE0100, 0xE01EF + 1)]
)

#: How many characters of an ISO-8601 date are the year.
_YEAR_LENGTH = 4

# The longest name the matcher will look at. Real artist and album titles are shorter
# by an order of magnitude, and the qualifier strip is quadratic in the name's length
# (see `split_name`), so a cap this size keeps every strip to a fraction of a
# millisecond. Candidates' names arrive from the network, and `pick_album` applies the
# cap before any of them is normalized, so a reply cannot use a name to hold the CPU
# (a name past `_MAX_RAW_NAME_CHARS`, below, is refused on its raw length first).
# It is measured on the longest form any step produces (`_expanded`): after NFKC (U+FDFA
# alone becomes eighteen characters) and the "&" fold (five). That bounds the strip's
# input, which is the shorter unfolded text, and the flatten's, which is folded.
_MAX_NAME_CHARS = 512

#: The most characters any character NFKC leaves as itself spells out to under
#: NFKD (U+1F82, "ᾂ", is alpha and three marks). Checked over every code point.
_LONGEST_DECOMPOSITION = 4

#: Variation selectors a real name carries per character, at most: one, after the
#: emoji or ideograph whose drawing it chooses.
_SELECTORS_PER_CHARACTER = 1

# The raw length past which a name is refused before any of it is normalized, so that
# deciding a name is too long costs constant time rather than an NFKC pass over all of
# a network reply. It must be looser than the cap, because the steps ahead of the
# measurement can shorten a name: NFKC composes a run of raw characters into one, a run
# no longer than that one character's NFKD (each raw character spells out to at least
# one, and the NFKD of both sides is the same), so at most `_LONGEST_DECOMPOSITION`
# characters; and the selector deletion removes one more per character. A name at the
# cap once normalized is therefore at most this long raw, and a longer one is refused
# for a length no real title has.
_MAX_RAW_NAME_CHARS = _MAX_NAME_CHARS * (
    _LONGEST_DECOMPOSITION + _SELECTORS_PER_CHARACTER
)


def _is_word_character(char: str) -> bool:
    """Whether `char` belongs to a word: a letter or digit of any script, or a mark."""
    return char.isalnum() or unicodedata.category(char).startswith(_MARK)


def _without_variation_selectors(text: str) -> str:
    """`text` with every variation selector deleted (see `_VARIATION_SELECTORS`)."""
    return text.translate(_VARIATION_SELECTORS)


def _nfkc(text: str) -> str:
    """`text` in NFKC, so composed and decomposed accents are one spelling.

    Full-width forms read as their ASCII ones too: "（Gold）" is "(Gold)".
    """
    return unicodedata.normalize("NFKC", text)


def _fold_case(text: str) -> str:
    """`text` casefolded, then in NFKC again.

    Casefold can split a letter NFKC composed into a base and separate marks, or an
    iota: "ΐ" becomes "ι" with its diaeresis and acute apart, and "ᾂ" puts an "ι"
    where a following acute composes with it. The second NFKC composes them, as
    Unicode's caseless match does, so a name's `normalize` is its own `normalize`
    again.
    """
    return _nfkc(text.casefold())


def _join_words(text: str) -> str:
    """Each run of non-word characters in `text` one space, then NFKC once more.

    The last NFKC is for the apostrophe deletion ahead of it, which can leave a letter
    beside a mark that stood after the apostrophe ("Writer’́s"), and NFKC composes
    the two.
    """
    return _nfkc(
        " ".join("".join(c if _is_word_character(c) else " " for c in text).split())
    )


def _flatten(text: str) -> str:
    """Casefolded, apostrophes deleted, and each run of non-word characters one space.

    A word character is a letter or digit of any script or a combining mark, so a
    Cyrillic or CJK title keeps its letters. No accent is folded away: "Björk" is
    not "Bjork", because an accent can be the whole difference between two names.
    Variation selectors are already gone by the time a name reaches here: `_canonical`
    deleted them. NFKC runs again after the casefold and once more on the joined
    words (see `_fold_case` and `_join_words`), so what either splits apart composes.
    """
    return _join_words(_fold_case(text).translate(_ELIDED))


def _canonical(text: str) -> str:
    """`text` with its variation selectors deleted, then in NFKC.

    The selectors go first, so everything after reads a name without them: deleting
    one can only shorten the text the cap measures, and it lets NFKC compose an
    accent a selector stood between.
    """
    return _nfkc(_without_variation_selectors(text))


def _fold_ampersand(text: str) -> str:
    """`text` with each "&" read as the word "and"."""
    return text.replace("&", " and ")


def _expanded(text: str) -> str:
    """`text` at its longest: selectors deleted, NFKC, and "&" folded.

    NFKC and the fold can lengthen a name, which is why the cap is measured on this
    text. The fold comes after NFKC, so a full-width "＆" folds as well.
    """
    return _fold_ampersand(_canonical(text))


def _has_word_character(text: str) -> bool:
    """Whether any character of `text` belongs to a word (see `_is_word_character`)."""
    return any(_is_word_character(c) for c in text)


def _own_text(text: str) -> str:
    """A name the flatten would erase, as itself: casefolded, whitespace single-spaced.

    That is a name with no word character, or one whose only letters the flatten
    deletes (see `_base_key`). Flattened, such a name would be nothing, and every
    title made of symbols alone ("+", "×", "★", "!!!") would match every other, so a
    same-artist search would hand one symbol album another's cover. The outer
    whitespace goes, and each inner run is one space, because the flatten treats
    whitespace that way in every other name: "+  +" and "+ +" are one spelling, as
    "a  b" and "a b" are.

    It takes no second NFKC after the casefold, unlike the flatten, because none would
    change it: casefold turns no character without a word character into anything NFKC
    alters, every character NFKC composes onto or reorders is a word character, and
    the one word character this text can hold, U+02BC, is one NFKC leaves alone. Tests
    check the first two over every code point.
    """
    return " ".join(text.casefold().split())


def _base_key(text: str) -> str:
    """The comparable form of a name's base, its qualifiers already stripped.

    Also the form of each qualifier group's inner text (see `_qualifier_key`).

    Whether it has a word character is decided before "&" is folded to "and":
    deciding after would turn a title that is only "&" into the word "and" and match
    it with an album called "And".

    Text the folds reduce to nothing keeps its own text too, as a symbol does. U+02BC
    MODIFIER LETTER APOSTROPHE is a letter, so it passes the word test, and then the
    flatten deletes it as an apostrophe: without this, "X (ʼ)" read as a plain "X"
    and "ʼ" alone as a blank name, while "X (’)" kept its "’". Blank text still
    reads as nothing, because its own text is empty too.
    """
    if not _has_word_character(text):
        return _own_text(text)
    return _flatten(_fold_ampersand(text)) or _own_text(text)


def _qualifier_key(groups: list[str]) -> str:
    """The comparable form of a name's qualifier groups, given in reading order.

    Each group is read on its own, by its inner text (its brackets dropped), under the
    rule a base takes (`_base_key`): one with no word character, or one the folds
    reduce to nothing, keeps its own text, so "(+)" and "(×)" stay apart instead of
    both flattening to nothing, and "(ʼ)" is not a blank group. A group blank
    inside, "()" or "( )", contributes nothing, so "Album ()" still reads as "Album".
    The non-empty keys are joined with a space.
    """
    keys = (_base_key(group.strip()[1:-1]) for group in groups)
    return " ".join(key for key in keys if key)


def _is_blank(name: tuple[str, str]) -> bool:
    """Whether a split name has neither a base nor a qualifier: nothing to compare."""
    return name == ("", "")


def _over_cap(text: str) -> bool:
    """Whether a name is too long to be normalized at all (see `_MAX_NAME_CHARS`).

    The raw length is judged first, and alone when it is already past
    `_MAX_RAW_NAME_CHARS`, so no step of the normalization ever reads more of an
    untrusted name than that bound on the way to refusing it.
    """
    if len(text) > _MAX_RAW_NAME_CHARS:
        return True
    return len(_expanded(text)) > _MAX_NAME_CHARS


def split_name(text: str) -> tuple[str, str]:
    """A name as (base, qualifier): "Fictionals (Gold)" -> ("fictionals", "gold").

    The qualifier is every trailing parenthesized or bracketed group, each read by its
    inner text and the non-empty ones joined with a space (see `_qualifier_key`). It is
    kept rather than discarded because which side carries one decides whether two
    names are the same work; see `pick_album`. A group with no word character keeps
    its own text, as a base does, so "X (+)" is ("x", "+") and "X (&)" is ("x", "&"),
    never "and"; so does one the folds reduce to nothing, so "X (ʼ)" is ("x", "ʼ");
    a group blank inside adds nothing, so "Album ()" is ("album", "").

    `&` folds to `and` before punctuation is stripped, not after. Stripping first would
    erase the ampersand entirely and leave "peter bjorn john", which matches neither
    spelling the store uses. Measured: stripping first turned a correct match into a
    miss. The fold is applied to the base and the qualifier once they are split, which
    splits them exactly where folding first did, because "&" is neither a bracket nor
    whitespace.

    A name, or a base, with no letter, digit or mark is compared by its own text
    (see `_own_text`), and that is decided before the fold, so "&" is not "And". So
    is a base the folds reduce to nothing: "ʼ" is ("ʼ", ""), not a blank name. A
    name with none at all is compared whole, its brackets included, and has no
    qualifier. A title that is only a qualifier, "(Live)", keeps an empty base: there
    is no text left to compare, and its qualifier still tells it from "(Demo)". A
    blank name (empty, whitespace, or variation selectors alone) is ("", ""), which
    `pick_album` refuses on either side.

    Variation selectors are deleted first, so "Fictionals (Gold)\ufe0f" still ends
    in its qualifier and "★\ufe0f" is "★". NFKC runs next, before the qualifier
    search, so a full-width bracket is a bracket and "Harbor Lights（Teal）" splits
    like "Harbor Lights (Teal)", and a composed and a decomposed accent are one
    spelling. The casefold that follows the strip takes NFKC again, and so do the
    flattened words (see `_flatten`), so "ᾂ́" and "ἂί" are one spelling too.

    The cost is quadratic in the name's length: the qualifier pattern is searched from
    every start position, and an unmatched opening bracket makes each of those
    searches scan to the end of the string. So only the first `_MAX_NAME_CHARS`
    characters after NFKC are read, by the strip and by the own-text comparison
    alike. The fold that follows can lengthen the cut text, but only the linear
    flatten reads it. `pick_album` and `same_artist_ids` go further and refuse a name
    past the cap, measured after the fold too, before calling here, so for them the
    bound never truncates; they refuse a name past `_MAX_RAW_NAME_CHARS` on its raw
    length, before any of it is normalized.
    """
    text = _canonical(text)[:_MAX_NAME_CHARS]
    if not _has_word_character(text):
        # Compared whole: "(+)" stripped as a qualifier would leave an empty base
        # that "(×)" shares.
        return _own_text(text), ""
    qualifiers: list[str] = []
    while True:
        found = _TRAILING_QUALIFIER.search(text)
        if found is None:
            break
        qualifiers.append(found.group(0))
        text = text[: found.start()]
    return _base_key(text), _qualifier_key(list(reversed(qualifiers)))


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

    Names past `_MAX_NAME_CHARS` after NFKC and the "&" fold, or past
    `_MAX_RAW_NAME_CHARS` raw, are refused before normalizing, on either side, for
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

    No name longer than `_MAX_NAME_CHARS` after NFKC and the "&" fold is normalized,
    on either side, and no name longer than `_MAX_RAW_NAME_CHARS` raw is so much as
    measured that way, so refusing one costs constant time: a candidate carrying one is skipped, and a wanted name that long
    is answered with `None` outright, rather than matched on a truncated prefix. The
    strip behind `normalize` is quadratic in length, and every candidate's names are
    the network's to choose.

    A blank album name, one that splits to ("", ""), is refused on either side: a
    blank wanted name is answered with `None`, and a candidate with a blank
    `collectionName` is skipped, by the name and by the year-and-track-count route
    alike. Blank has nothing to compare, so it would otherwise meet any qualifier-only
    title such as "(Live)" as the same work. A blank base alone is not refused:
    "(Live)" still picks "(Live)" over "(Demo)".
    """
    if _over_cap(artist) or _over_cap(album):
        # The library's names come from folder names rather than the network, but
        # they are input all the same, and one folder name past the cap would hold
        # the CPU as surely as a candidate would.
        return None
    wanted_artist = normalize(artist)
    wanted = split_name(album)
    if _is_blank(wanted):
        return None
    wanted_base, wanted_qualifier = wanted
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
        split = split_name(name)
        if _is_blank(split):
            continue
        base, qualifier = split
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
