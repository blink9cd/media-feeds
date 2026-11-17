"""A title with no letter, digit or mark is compared by its own text.

The defect this pins: the flatten keeps only letters, digits and combining marks, so a
title made of symbols alone ("+", "×", "÷", "=", "★", "!!!") reduced to the empty base,
and `pick_album` read every such title by one artist as a name match and returned
whichever came first. An artist whose albums are each one symbol would get the wrong
album's cover, stated as fact. Such a base is now its own NFKC, casefolded text, with
outer whitespace stripped and each inner run one space.

That choice is made before the spelling folds, so "&" stays apart from "And": folding
first would turn the ampersand into the word "and" and match the two.

The same rule holds for each trailing qualifier group: one with no word character keeps
its own inner text, so "X (+)" and "X (×)" stay two editions. A name that is blank
altogether, on either side, is never a match for anything.

Every name here is invented, never library data. A sibling of
`test_artwork_match_scripts.py` rather than more of it, because that file sits near the
per-file line cap.
"""

from __future__ import annotations

import sys
import unicodedata
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent
# The pure matcher under test, and this folder for the offline tripwire.
sys.path[:0] = [str(_TOOL_ROOT / "src"), str(Path(__file__).resolve().parent)]

import artwork_match  # noqa: E402 - import follows the sys.path bootstrap above
from artwork_match import (  # noqa: E402 - import follows the sys.path bootstrap above
    _MAX_NAME_CHARS,
    _canonical,
    _flatten,
    _fold_ampersand,
    _has_word_character,
    normalize,
    pick_album,
    same_artist_ids,
    split_name,
)
from hypothesis import (  # noqa: E402 - import follows the sys.path bootstrap above
    HealthCheck,
    assume,
    example,
    given,
    settings,
    strategies as st,
)
from _offline_guard import setUpModule, tearDownModule  # noqa: E402, F401 - the bootstrap above; unittest calls the hooks by name

# The same settings as `test_artwork_url_properties.py`'s profile, for the reasons given
# there, under this module's own name so import order cannot decide which registration
# a property snapshotted. Loaded before the class bodies, because `@given` snapshots
# its settings when it decorates.
settings.register_profile(
    "artwork_match_symbols_properties",
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
settings.load_profile("artwork_match_symbols_properties")

#: The invented artist every candidate below is credited to.
_ARTIST = "The Paper Lanterns"


def _pick(names: list[str], album: str) -> str | None:
    """The `collectionName` `pick_album` chooses for `album` from same-artist `names`."""
    pool = [{"artistName": _ARTIST, "collectionName": name} for name in names]
    chosen = pick_album(pool, artist=_ARTIST, album=album, year=None, tracks=None)
    return None if chosen is None else str(chosen["collectionName"])


class SymbolTitlePickTest(unittest.TestCase):
    """`pick_album` tells one symbol title from another by the same artist."""

    def test_the_wanted_symbol_title_is_chosen_when_it_is_not_first(self) -> None:
        self.assertEqual(_pick(["+", "×"], "×"), "×")

    def test_a_different_symbol_title_is_not_chosen(self) -> None:
        self.assertIsNone(_pick(["+", "×"], "÷"))

    def test_a_symbol_title_with_an_edition_qualifier_is_still_its_album(self) -> None:
        # The symbols are the base and "(Deluxe)" the qualifier, as for any title.
        self.assertEqual(_pick(["× (Deluxe)", "+ (Deluxe)"], "+"), "+ (Deluxe)")

    def test_two_symbol_bases_with_one_qualifier_stay_two_albums(self) -> None:
        self.assertEqual(_pick(["+ (Live)", "× (Live)"], "× (Live)"), "× (Live)")

    def test_a_bracketed_symbol_title_is_not_another_one(self) -> None:
        # The whole name has no word character, so it is compared whole: the brackets
        # are not read as a qualifier that would leave both bases empty.
        self.assertIsNone(_pick(["(+)"], "(×)"))
        self.assertEqual(_pick(["(+)", "(×)"], "(×)"), "(×)")


class AmpersandIsNotAndTest(unittest.TestCase):
    """The no-word-character test runs before "&" is folded to "and"."""

    def test_an_ampersand_title_is_not_the_word_and(self) -> None:
        self.assertNotEqual(normalize("&"), normalize("And"))
        self.assertIsNone(_pick(["And"], "&"))
        self.assertIsNone(_pick(["&"], "And"))

    def test_an_ampersand_title_still_matches_itself(self) -> None:
        self.assertEqual(_pick(["And", "&"], "&"), "&")

    def test_an_ampersand_between_words_still_folds(self) -> None:
        self.assertEqual(normalize("Salt & Brine"), normalize("Salt and Brine"))


class SymbolSpellingTest(unittest.TestCase):
    """One symbol title spelled two ways is still one title."""

    def test_a_full_width_symbol_is_its_ascii_form(self) -> None:
        self.assertEqual(normalize("＋"), normalize("+"))
        self.assertEqual(normalize("！！！"), "!!!")
        self.assertEqual(_pick(["×", "＋"], "+"), "＋")

    def test_an_emoji_selector_does_not_change_the_title(self) -> None:
        # The selector is deleted before the no-word-character test, so a name whose
        # only mark was a selector takes the symbol comparison too.
        self.assertEqual(normalize("★\ufe0f"), normalize("★"))
        self.assertEqual(normalize("★\ufe0f"), "★")

    def test_outer_whitespace_is_ignored(self) -> None:
        self.assertEqual(normalize(" + "), normalize("+"))
        self.assertEqual(normalize("+ "), "+")

    def test_an_inner_whitespace_run_is_one_space(self) -> None:
        self.assertEqual(normalize("+  \t+"), "+ +")


class EmptyBaseTest(unittest.TestCase):
    """A base with nothing in it at all is still the empty base, as it always was."""

    def test_a_title_that_is_only_a_qualifier_keeps_an_empty_base(self) -> None:
        self.assertEqual(split_name("(Live)"), ("", "live"))

    def test_two_qualifier_only_titles_are_told_apart_by_the_qualifier(self) -> None:
        self.assertIsNone(_pick(["(Demo)"], "(Live)"))
        self.assertEqual(_pick(["(Demo)", "(Live)"], "(Live)"), "(Live)")

    def test_a_blank_name_has_an_empty_base(self) -> None:
        self.assertEqual(split_name(" \t"), ("", ""))


class SymbolArtistIdsTest(unittest.TestCase):
    """`same_artist_ids` reads a symbol-only artist by its own text too."""

    def test_a_symbol_artist_lends_only_its_own_id(self) -> None:
        pool = [
            {"artistId": 1, "artistName": "???"},
            {"artistId": 2, "artistName": " "},
            {"artistId": 3, "artistName": "!!!"},
        ]
        self.assertEqual(same_artist_ids(pool, artist="!!!"), [3])


class SymbolBoundTest(unittest.TestCase):
    """The symbol comparison text is bounded like every other."""

    def test_a_long_symbol_name_reads_only_the_first_cap_characters(self) -> None:
        self.assertEqual(normalize("+" * (_MAX_NAME_CHARS * 3)), "+" * _MAX_NAME_CHARS)


class SymbolQualifierTest(unittest.TestCase):
    """A qualifier group with no word character keeps its own inner text."""

    def test_two_symbol_qualifiers_on_one_base_stay_two_editions(self) -> None:
        self.assertEqual(split_name("X (+)"), ("x", "+"))
        self.assertEqual(split_name("X (×)"), ("x", "×"))
        self.assertIsNone(_pick(["X (+)"], "X (×)"))
        self.assertEqual(_pick(["X (+)", "X (×)"], "X (×)"), "X (×)")

    def test_a_symbol_group_beside_a_word_group_is_kept_in_order(self) -> None:
        self.assertEqual(split_name("(+) (Live)"), ("", "+ live"))
        self.assertIsNone(_pick(["(+) (Live)"], "(×) (Live)"))
        self.assertEqual(_pick(["(Live)", "(×) (Live)"], "(×) (Live)"), "(×) (Live)")

    def test_an_empty_group_contributes_nothing(self) -> None:
        self.assertEqual(split_name("Album ()"), ("album", ""))
        self.assertEqual(split_name("Album ( )"), split_name("Album"))
        self.assertEqual(_pick(["Album (Deluxe)"], "Album ()"), "Album (Deluxe)")

    def test_an_ampersand_group_keeps_its_own_text(self) -> None:
        # Decided before the fold, as for a base: "(&)" reads "&", never "and".
        self.assertEqual(split_name("X (&)"), ("x", "&"))
        self.assertIsNone(_pick(["X (And)"], "X (&)"))

    def test_an_invisible_group_keeps_its_own_text(self) -> None:
        # A zero-width space or a soft hyphen is neither a word character nor
        # whitespace, so the group is not blank: it is kept, as a symbol-only name
        # keeps one, and the edition no longer matches a different one.
        self.assertEqual(split_name("Album (\u200b)"), ("album", "\u200b"))
        self.assertEqual(split_name("Album (\xad)"), ("album", "\xad"))
        self.assertIsNone(_pick(["Album (Deluxe)"], "Album (\u200b)"))

    def test_a_stray_opening_bracket_is_kept_in_the_group(self) -> None:
        # The group runs from the first opening bracket, so the stray one is inner text.
        self.assertEqual(split_name("X ((+)"), ("x", "(+"))

    def test_a_symbol_qualifier_on_one_side_only_still_matches(self) -> None:
        # Only one side qualified is the same work, as for any qualifier.
        self.assertEqual(_pick(["X"], "X (+)"), "X")


#: U+02BC MODIFIER LETTER APOSTROPHE: a letter (category Lm) that the flatten deletes.
_MODIFIER_APOSTROPHE = "ʼ"


class FoldedToNothingTest(unittest.TestCase):
    """A name or group the folds reduce to nothing keeps its own text, if it has any.

    U+02BC is a letter, so the no-word-character test passes it to the flatten, and the
    flatten deletes it as an apostrophe. Before, "X (ʼ)" read as a plain "X" and "ʼ"
    alone as a blank name, while "X (’)" (U+2019, punctuation) kept its own text.
    """

    def test_a_modifier_apostrophe_group_keeps_its_own_text(self) -> None:
        self.assertEqual(split_name(f"X ({_MODIFIER_APOSTROPHE})"), ("x", "ʼ"))
        self.assertEqual(split_name("X (’)"), ("x", "’"))

    def test_a_modifier_apostrophe_group_is_not_another_edition(self) -> None:
        self.assertIsNone(_pick(["X (Live)"], "X (ʼ)"))
        self.assertIsNone(_pick(["X (’)"], "X (ʼ)"))
        self.assertEqual(_pick(["X (Live)", "X (ʼ)"], "X (ʼ)"), "X (ʼ)")

    def test_a_modifier_apostrophe_group_on_one_side_only_still_matches(self) -> None:
        # Only one side qualified is the same work, as for any qualifier.
        self.assertEqual(_pick(["X"], "X (ʼ)"), "X")

    def test_a_modifier_apostrophe_group_beside_a_word_group_keeps_both(self) -> None:
        self.assertEqual(split_name("X (ʼ) (Live)"), ("x", "ʼ live"))
        self.assertIsNone(_pick(["X (Live)"], "X (ʼ) (Live)"))

    def test_a_modifier_apostrophe_name_is_not_blank(self) -> None:
        self.assertEqual(split_name(_MODIFIER_APOSTROPHE), ("ʼ", ""))
        self.assertEqual(_pick(["(Live)", "ʼ"], "ʼ"), "ʼ")
        self.assertIsNone(_pick(["’"], "ʼ"))

    def test_a_modifier_apostrophe_base_keeps_its_own_text(self) -> None:
        self.assertEqual(split_name("ʼ (Live)"), ("ʼ", "live"))
        self.assertEqual(split_name(" ʼ' "), ("ʼ'", ""))

    def test_every_deleted_letter_keeps_its_own_text(self) -> None:
        # Every character the flatten deletes that the word test calls a letter: the
        # module's own deletion table, so a new entry is checked without a new test.
        deleted = [chr(code) for code in artwork_match._ELIDED]
        letters = [char for char in deleted if _has_word_character(char)]
        self.assertIn(_MODIFIER_APOSTROPHE, letters)
        for char in letters:
            with self.subTest(char=char):
                self.assertEqual(split_name(f"X ({char})"), ("x", char.casefold()))
                self.assertEqual(split_name(char), (char.casefold(), ""))

    def test_a_lone_combining_mark_was_never_folded_away(self) -> None:
        # A mark is kept by the flatten, so it never needed the fallback.
        self.assertEqual(split_name("X (́)"), ("x", "́"))


class BlankNameTest(unittest.TestCase):
    """A blank name, on either side, matches nothing."""

    def test_a_blank_candidate_is_not_a_qualifier_only_album(self) -> None:
        self.assertIsNone(_pick([""], "(Live)"))
        self.assertIsNone(_pick([" \t"], "(Live)"))
        self.assertEqual(_pick(["", "(Live)"], "(Live)"), "(Live)")

    def test_a_blank_wanted_name_matches_nothing(self) -> None:
        self.assertIsNone(_pick(["", "X"], ""))
        # A name of variation selectors alone is blank too: they are deleted first.
        self.assertIsNone(_pick([" "], "\ufe0f"))

    def test_a_blank_candidate_is_skipped_on_the_year_and_tracks_route(self) -> None:
        pool = [
            {
                "artistName": _ARTIST,
                "collectionName": name,
                "releaseDate": "2019-05-01T07:00:00Z",
                "trackCount": 10,
            }
            for name in ("", "\ufe0f")
        ]
        chosen = pick_album(pool, artist=_ARTIST, album="X", year=2019, tracks=10)
        self.assertIsNone(chosen)


def _has_nonblank_group(text: str) -> bool:
    """Whether a word name has a trailing qualifier group with non-blank inner text.

    A name with no word character is compared whole, so it has no qualifier at all.
    """
    remaining = _canonical(text)[:_MAX_NAME_CHARS]
    if not any(c.isalnum() or unicodedata.category(c)[0] == "M" for c in remaining):
        return False
    nonblank = False
    while (found := artwork_match._TRAILING_QUALIFIER.search(remaining)) is not None:
        nonblank = nonblank or bool(found.group(0).strip()[1:-1].split())
        remaining = remaining[: found.start()]
    return nonblank


#: Text that may sit inside a bracketed group: any character but a bracket, with the
#: symbol and blank cases drawn often enough to be met on every run.
_GROUP_INNER = st.text(
    alphabet=st.one_of(
        st.characters(blacklist_characters="()[]"), st.sampled_from(" \t+×&")
    ),
    max_size=8,
)

#: A name: any text, then zero or more trailing bracketed groups.
_NAMES = st.builds(
    lambda base, groups: base + "".join(f" ({inner})" for inner in groups),
    st.text(max_size=20),
    st.lists(_GROUP_INNER, max_size=3),
)


def _trailing_groups(text: str) -> list[str]:
    """The trailing qualifier groups `split_name` strips from `text`, in reading order."""
    remaining = _canonical(text)[:_MAX_NAME_CHARS]
    groups: list[str] = []
    while (found := artwork_match._TRAILING_QUALIFIER.search(remaining)) is not None:
        groups.append(found.group(0))
        remaining = remaining[: found.start()]
    return list(reversed(groups))


def _word_group_qualifier(groups: list[str]) -> str:
    """The qualifier as it was read before symbol groups kept their own text.

    Every group, brackets and all, joined and then folded and flattened as one text.
    """
    return _flatten(_fold_ampersand(" ".join(groups)))


#: Group text sure to hold a word character, with symbols and spaces around it.
_WORD_GROUP_INNER = st.builds(
    lambda before, word, after: before + word + after,
    _GROUP_INNER,
    st.sampled_from(["a", "Z", "7", "é", "Ж"]),
    _GROUP_INNER,
)

#: A name with a word in its base and a word in every trailing group.
_WORD_GROUP_NAMES = st.builds(
    lambda base, groups: "X" + base + "".join(f" ({inner})" for inner in groups),
    st.text(max_size=20),
    st.lists(_WORD_GROUP_INNER, max_size=3),
)


class QualifierPropertyTest(unittest.TestCase):
    """The qualifier is empty exactly when every group is blank inside."""

    @given(_NAMES)
    # A group holding only U+02BC, a letter the flatten deletes, falsified this once.
    @example(" (ʼ)")
    def test_the_qualifier_is_nonempty_iff_a_group_is_nonblank(self, text: str) -> None:
        self.assertEqual(bool(split_name(text)[1]), _has_nonblank_group(text))

    @given(_WORD_GROUP_NAMES)
    def test_word_groups_read_as_they_always_did(self, text: str) -> None:
        # NFKC can turn a drawn character into a bracket and so regroup the name;
        # only names whose stripped groups all hold a word character the folds keep
        # are this case. A group whose one letter is U+02BC folds to nothing, so it
        # keeps its own text instead (see `FoldedToNothingTest`); full-width brackets
        # in the drawn text can make one, as "X (a）（ʼ)" does.
        groups = _trailing_groups(text)
        assume(all(_word_group_qualifier([group]) for group in groups))
        self.assertEqual(split_name(text)[1], _word_group_qualifier(groups))


if __name__ == "__main__":
    unittest.main()
