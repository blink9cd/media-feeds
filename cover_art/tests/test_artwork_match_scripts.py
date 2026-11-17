"""The artwork matcher reads every script's letters, digits and marks, not ASCII alone.

The defect this pins: an earlier flatten kept only `[a-z0-9]`, so a Cyrillic, CJK,
Greek, Arabic, Hebrew or Devanagari title flattened to "", every non-Latin name equaled
every other, and `pick_album` answered a non-Latin album with whichever non-Latin
candidate came first. "Björk" flattened to "bj rk".

What stays as it was: the spelling folds ("&" reads "and", an apostrophe is deleted),
and no accent folding, because an accent can be the whole difference between two
names: "Björk" is not "Bjork". The ASCII equivalence property below holds the new rule
to the old one on every ASCII name.

Every name here is invented, never library data. A sibling of `test_artwork_match.py`
rather than more of it, because that file sits near the per-file line cap.
"""

from __future__ import annotations

import re
import sys
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

_TOOL_ROOT = Path(__file__).resolve().parent.parent
# The pure matcher under test, and this folder for the offline tripwire.
sys.path[:0] = [str(_TOOL_ROOT / "src"), str(Path(__file__).resolve().parent)]

import artwork_match  # noqa: E402 - import follows the sys.path bootstrap above
from artwork_match import (  # noqa: E402 - import follows the sys.path bootstrap above
    _ELIDED,
    _MAX_NAME_CHARS,
    _canonical,
    _flatten,
    _without_variation_selectors,
    normalize,
    pick_album,
    same_artist_ids,
    split_name,
)
from hypothesis import (  # noqa: E402 - import follows the sys.path bootstrap above
    HealthCheck,
    given,
    settings,
    strategies as st,
)
from _offline_guard import setUpModule, tearDownModule  # noqa: E402, F401 - the bootstrap above; unittest calls the hooks by name

# The same settings as `test_artwork_url_properties.py`'s profile, for the reasons given
# there (a cold unicode cache trips `too_slow`; a wall-clock deadline measures load),
# under this module's own name: `load_profile` sets one global default, and a shared
# name would let import order decide which registration each property had
# snapshotted. Loaded before the class bodies, because `@given` snapshots its settings
# when it decorates.
settings.register_profile(
    "artwork_match_scripts_properties",
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
settings.load_profile("artwork_match_scripts_properties")

#: The ASCII-only flatten rule the matcher replaced, pinned here so the ASCII property
#: can compare against it: anything outside `[a-z0-9]` became a space.
_OLD_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")

#: One character NFKC expands to eighteen (U+FDFA, an Arabic ligature), so a name
#: short enough to pass a raw length check is far past the cap once normalized.
_EXPANDING = "\ufdfa"
_EXPANDED_PAST_CAP = _EXPANDING * (_MAX_NAME_CHARS // 18 + 1)

#: A single word in each script the old flatten erased, and what it must flatten to.
_WORDS = {
    "Cyrillic": ("Тишь", "тишь"),
    "CJK": ("木曽水", "木曽水"),
    "Greek": ("Θάλασσα", "θάλασσα"),
    "Arabic": ("بحر", "بحر"),
    "Hebrew": ("ים", "ים"),
}  # fmt: skip

#: A Devanagari word whose vowel signs and virama are combining marks, not letters.
_DEVANAGARI = "कविता"


def _old_flatten(text: str) -> str:
    """The ASCII-only `_flatten` this module's rule replaced, verbatim in behavior."""
    return _OLD_NON_ALPHANUMERIC.sub(" ", text.casefold().translate(_ELIDED)).strip()


def _is_word_character(char: str) -> bool:
    """A letter or digit of any script, or a combining mark."""
    return char.isalnum() or unicodedata.category(char).startswith("M")


def _candidate(name: str, artist: str) -> dict[str, object]:
    """One iTunes result, carrying only the fields the name match reads."""
    return {"artistName": artist, "collectionName": name}


class EveryScriptTest(unittest.TestCase):
    """Each script keeps its own letters through `normalize`."""

    def test_each_script_keeps_its_letters(self) -> None:
        for script, (word, expected) in _WORDS.items():
            with self.subTest(script=script):
                self.assertEqual(normalize(f"{word}!"), expected)

    def test_devanagari_keeps_its_vowel_signs_inside_the_word(self) -> None:
        # The marks are part of the word: a rule keeping only `isalnum` would split
        # it at every vowel sign.
        self.assertEqual(normalize(_DEVANAGARI), _DEVANAGARI)

    def test_two_different_non_latin_names_stay_different(self) -> None:
        self.assertNotEqual(normalize(_WORDS["CJK"][0]), normalize(_WORDS["Greek"][0]))

    def test_a_latin_accent_is_kept_whole(self) -> None:
        self.assertEqual(normalize("Björk"), "björk")

    def test_composed_and_decomposed_spellings_are_one_name(self) -> None:
        self.assertEqual(normalize("Björk"), normalize("Bjo\u0308rk"))

    def test_an_accent_is_not_folded_away(self) -> None:
        # No accent folding: an accented name and its unaccented spelling are two
        # names, because the accent can be all that tells two works apart.
        self.assertNotEqual(normalize("Björk"), normalize("Bjork"))

    def test_a_name_of_punctuation_alone_is_compared_by_its_own_text(self) -> None:
        # Flattened, it would be nothing, and every such title would match every
        # other (see `test_artwork_match_symbols.py`). NFKC reads "…" as "...".
        self.assertEqual(normalize("!!! ?… -"), "!!! ?... -")

    def test_full_width_letters_read_as_their_ascii_forms(self) -> None:
        self.assertEqual(normalize("\uff28\uff41\uff52\uff42\uff4f\uff52"), "harbor")


class FullWidthQualifierTest(unittest.TestCase):
    """NFKC runs before the qualifier search, so full-width brackets strip too."""

    def test_a_full_width_qualifier_is_split_off(self) -> None:
        self.assertEqual(
            split_name("Harbor Lights\uff08Teal\uff09"), ("harbor lights", "teal")
        )

    def test_a_full_width_qualifier_is_the_same_as_an_ascii_one(self) -> None:
        self.assertEqual(
            split_name("Harbor Lights\uff08Teal\uff09"),
            split_name("Harbor Lights (Teal)"),
        )


class NonLatinPickAlbumTest(unittest.TestCase):
    """`pick_album` tells non-Latin albums apart instead of taking the first."""

    ARTIST = _WORDS["Cyrillic"][0]

    def test_the_right_non_latin_album_is_chosen_when_it_is_not_first(self) -> None:
        pool = [
            _candidate("Ночь", self.ARTIST),
            _candidate(_WORDS["Greek"][0], self.ARTIST),
        ]
        chosen = pick_album(
            pool, artist=self.ARTIST, album=_WORDS["Greek"][0], year=None, tracks=None
        )
        assert chosen is not None
        self.assertEqual(chosen["collectionName"], _WORDS["Greek"][0])

    def test_a_different_non_latin_album_is_not_chosen(self) -> None:
        pool = [_candidate("Ночь", self.ARTIST)]
        self.assertIsNone(
            pick_album(
                pool,
                artist=self.ARTIST,
                album=_WORDS["CJK"][0],
                year=None,
                tracks=None,
            )
        )

    def test_another_non_latin_artist_is_not_chosen(self) -> None:
        pool = [_candidate(_WORDS["CJK"][0], _WORDS["Hebrew"][0])]
        self.assertIsNone(
            pick_album(
                pool,
                artist=self.ARTIST,
                album=_WORDS["CJK"][0],
                year=None,
                tracks=None,
            )
        )


class CapAfterNormalizationTest(unittest.TestCase):
    """No entry point runs the qualifier strip on more than `_MAX_NAME_CHARS`.

    NFKC can lengthen a name, so the cap is measured on the normalized text: a name
    short enough raw can be far past it once U+FDFA has expanded.
    """

    def test_the_expanding_name_is_short_raw_and_long_normalized(self) -> None:
        # The premise of every test below, checked rather than assumed.
        self.assertLessEqual(len(_EXPANDED_PAST_CAP), _MAX_NAME_CHARS)
        expanded = unicodedata.normalize("NFKC", _EXPANDED_PAST_CAP)
        self.assertGreater(len(expanded), _MAX_NAME_CHARS)

    def test_a_candidate_that_expands_past_the_cap_is_skipped(self) -> None:
        # The wanted names are what each over-cap candidate name reads as after the
        # cut, so the candidate would match if it were normalized: being skipped is
        # the only way it can fail to be chosen.
        cut_album = normalize(_EXPANDED_PAST_CAP)
        self.assertTrue(cut_album)
        pool = [_candidate(_EXPANDED_PAST_CAP, "A")]
        self.assertIsNone(
            pick_album(pool, artist="A", album=cut_album, year=None, tracks=None)
        )
        cut_artist = normalize("A" + _EXPANDED_PAST_CAP)
        pool = [_candidate("A", "A" + _EXPANDED_PAST_CAP)]
        self.assertIsNone(
            pick_album(pool, artist=cut_artist, album="A", year=None, tracks=None)
        )

    def test_the_cut_names_match_a_candidate_inside_the_cap(self) -> None:
        # The control for the test above: the same wanted names choose a candidate
        # that carries them itself, so they are not refused for their own sake.
        cut_album = normalize(_EXPANDED_PAST_CAP)
        cut_artist = normalize("A" + _EXPANDED_PAST_CAP)
        pool = [_candidate(cut_album, cut_artist)]
        self.assertIsNotNone(
            pick_album(pool, artist=cut_artist, album=cut_album, year=None, tracks=None)
        )

    def test_a_wanted_name_that_expands_past_the_cap_matches_nothing(self) -> None:
        pool = [_candidate(_EXPANDED_PAST_CAP, _EXPANDED_PAST_CAP)]
        self.assertIsNone(
            pick_album(
                pool,
                artist=_EXPANDED_PAST_CAP,
                album=_EXPANDED_PAST_CAP,
                year=None,
                tracks=None,
            )
        )

    def test_the_strip_never_sees_more_than_the_cap(self) -> None:
        pattern = mock.Mock(wraps=artwork_match._TRAILING_QUALIFIER)
        with mock.patch.object(artwork_match, "_TRAILING_QUALIFIER", pattern):
            normalize(_EXPANDED_PAST_CAP + " (Gold)")
            split_name("(" * (_MAX_NAME_CHARS + 100))
            pick_album(
                [_candidate(_EXPANDED_PAST_CAP, "A")],
                artist="A",
                album="A",
                year=None,
                tracks=None,
            )
        self.assertTrue(pattern.search.called)
        longest = max(len(call.args[0]) for call in pattern.search.call_args_list)
        self.assertLessEqual(longest, _MAX_NAME_CHARS)

    def test_the_ampersand_fold_never_hands_the_strip_more_than_the_cap(self) -> None:
        # The fold makes each "&" five characters, so a name of them at the cap was
        # five times the cap by the time the strip read it.
        ampersands = "&" * _MAX_NAME_CHARS
        pattern = mock.Mock(wraps=artwork_match._TRAILING_QUALIFIER)
        with mock.patch.object(artwork_match, "_TRAILING_QUALIFIER", pattern):
            split_name(ampersands + "(")
            pick_album(
                [_candidate(ampersands, "A"), _candidate("A", ampersands)],
                artist="A",
                album="A",
                year=None,
                tracks=None,
            )
            same_artist_ids([{"artistName": ampersands, "artistId": 1}], artist="A")
        longest = max(len(call.args[0]) for call in pattern.search.call_args_list)
        self.assertLessEqual(longest, _MAX_NAME_CHARS)

    def test_a_name_the_fold_lengthens_past_the_cap_matches_nothing(self) -> None:
        ampersands = "&" * _MAX_NAME_CHARS
        # Short enough raw, and alike on both sides, so only the cap can refuse it.
        pool = [_candidate("A", ampersands)]
        self.assertIsNone(
            pick_album(pool, artist=ampersands, album="A", year=None, tracks=None)
        )
        artists = [{"artistName": ampersands, "artistId": 1}]
        self.assertEqual(same_artist_ids(artists, artist=ampersands), [])

    def test_a_direct_call_reads_only_the_first_cap_characters(self) -> None:
        # The least surprising bound for a direct caller: the first `_MAX_NAME_CHARS`
        # normalized characters, rather than a raise. `pick_album` still refuses an over-cap name outright.
        prefix = "a" * _MAX_NAME_CHARS
        self.assertEqual(normalize(prefix + " (Gold) more"), prefix)


class VariationSelectorTest(unittest.TestCase):
    """Variation selectors are deleted first, ahead of NFKC and the qualifier strip.

    A selector is a combining mark, so the every-script rule would otherwise keep
    one as a word of its own: "I \u2764\ufe0f NY" read "i \ufe0f ny" while the
    same name without the emoji selector read "i ny".
    """

    def test_an_emoji_selector_is_not_a_word(self) -> None:
        self.assertEqual(normalize("I \u2764\ufe0f NY"), "i ny")

    def test_a_name_with_and_without_the_selector_is_one_name(self) -> None:
        self.assertEqual(normalize("I \u2764\ufe0f NY"), normalize("I \u2764 NY"))

    def test_an_ideographic_selector_leaves_its_letter_alone(self) -> None:
        # U+E0100, a selector from the supplementary range, after a Han letter.
        self.assertEqual(normalize("\u6728\U000e0100\u66fd"), "\u6728\u66fd")

    def test_a_selector_in_a_qualifier_is_deleted_too(self) -> None:
        self.assertEqual(
            split_name("Harbor Lights (Gold\ufe0e)"), ("harbor lights", "gold")
        )

    def test_both_selector_ranges_are_deleted_end_to_end(self) -> None:
        for low, high in ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF)):
            for code in (low, high):
                with self.subTest(code=hex(code)):
                    self.assertEqual(normalize(f"a{chr(code)}b"), "ab")

    def test_a_neighbor_of_each_range_is_not_a_selector(self) -> None:
        # The code point just outside each end of both ranges survives the deletion.
        # Read at the deletion itself, not through `normalize`: NFKC folds U+FE10 (a
        # vertical comma) to ",", and every one of these is a separator to the
        # flatten, so `normalize` would read "a b" whether the table took it or not.
        for code in (0xFDFF, 0xFE10, 0xE00FF, 0xE01F0):
            with self.subTest(code=hex(code)):
                kept = f"a{chr(code)}b"
                self.assertEqual(_without_variation_selectors(kept), kept)

    def test_a_trailing_selector_does_not_hide_the_qualifier(self) -> None:
        # The selector is deleted before the qualifier strip, so a name ending in one
        # still ends in its bracket when the strip looks for it.
        self.assertEqual(split_name("Fictionals (Gold)\ufe0f"), ("fictionals", "gold"))

    def test_a_selector_after_each_of_two_qualifiers_is_deleted(self) -> None:
        self.assertEqual(
            split_name("Fictionals (Gold)\ufe0e [Deluxe]\U000e0100"),
            ("fictionals", "gold deluxe"),
        )


class QualifierScriptVetoTest(unittest.TestCase):
    """A non-Latin qualifier is a qualifier, so it vetoes a sibling work."""

    def test_a_non_latin_qualifier_refuses_a_latin_sibling(self) -> None:
        # Under the ASCII-only flatten "(\u0417\u043e\u043b\u043e\u0442\u043e)"
        # flattened to "", so the album read as unqualified and borrowed the "(Teal)"
        # cover, even through the year-and-tracks door, which the sibling veto closes.
        pool = [
            {
                "artistName": "A",
                "collectionName": "Album (Teal)",
                "releaseDate": "2019-01-01",
                "trackCount": 10,
            }
        ]
        self.assertIsNone(
            pick_album(
                pool,
                artist="A",
                album="Album (\u0417\u043e\u043b\u043e\u0442\u043e)",
                year=2019,
                tracks=10,
            )
        )


class AnyUnicodePropertyTest(unittest.TestCase):
    """Invariants of `split_name` and `normalize` over arbitrary Unicode."""

    @given(st.text(max_size=80))
    def test_split_name_and_normalize_never_raise(self, text: str) -> None:
        base, qualifier = split_name(text)
        self.assertIsInstance(base, str)
        self.assertIsInstance(qualifier, str)
        self.assertEqual(normalize(text), base)

    @given(st.text(max_size=80))
    def test_the_nfc_and_nfd_spellings_split_alike(self, text: str) -> None:
        self.assertEqual(
            split_name(unicodedata.normalize("NFC", text)),
            split_name(unicodedata.normalize("NFD", text)),
        )


class FlattenPropertyTest(unittest.TestCase):
    """Invariants of the flatten rule over arbitrary text."""

    @given(st.text(max_size=80))
    def test_the_flatten_keeps_only_word_characters_and_single_spaces(
        self, text: str
    ) -> None:
        flat = _flatten(text)
        self.assertEqual(flat, flat.strip())
        self.assertNotIn("  ", flat)
        self.assertTrue(all(c == " " or _is_word_character(c) for c in flat))

    @given(st.text(max_size=80))
    def test_the_flatten_is_idempotent(self, text: str) -> None:
        # Over the text the flatten is given, which `_canonical` has put in NFKC. Its
        # own NFKC after casefold can make a capital of a letter casefold leaves alone
        # ("\U0001d54a" is "S"), so a name never put in NFKC first need not hold.
        flat = _flatten(_canonical(text))
        self.assertEqual(_flatten(flat), flat)

    @given(st.text(alphabet=st.characters(max_codepoint=0x7F), max_size=80))
    def test_an_ascii_name_flattens_exactly_as_it_did_before(self, text: str) -> None:
        self.assertEqual(_flatten(text), _old_flatten(text))

    @given(st.text(alphabet=st.characters(max_codepoint=0x7F), max_size=80))
    def test_an_ascii_name_normalizes_exactly_as_it_did_before(self, text: str) -> None:
        # The whole of the old `normalize` on ASCII: the fold, the strip, the flatten,
        # for every name whose base has a letter or digit. A name or base without one
        # is compared by its own text instead, judged before the fold.
        self.assertEqual(normalize(text), _ascii_base(text))

    @given(st.text(alphabet=st.characters(categories=("P", "S", "Zs")), max_size=40))
    def test_a_name_without_a_word_character_keeps_a_nonblank_base(
        self, text: str
    ) -> None:
        # Unless it is blank, a name with no letter, digit or mark never reduces to
        # the empty base, which every other such name would share.
        canonical = unicodedata.normalize("NFKC", text)
        if any(_is_word_character(c) for c in canonical):
            return  # NFKC made a letter or digit of it ("™" is "TM"): a word name
        self.assertEqual(bool(normalize(text)), bool(canonical.strip()))

    @given(st.text(alphabet=st.characters(categories=("P", "S", "Zs")), max_size=40))
    def test_outer_whitespace_never_changes_a_base(self, text: str) -> None:
        self.assertEqual(normalize(f" {text}\t"), normalize(text))


def _ascii_strip(text: str) -> str:
    """`text` with every trailing qualifier stripped, as the old `normalize` did."""
    while (found := artwork_match._TRAILING_QUALIFIER.search(text)) is not None:
        text = text[: found.start()]
    return text


def _ascii_base(text: str) -> str:
    """The base an ASCII name must reduce to: the old rule, or its own text."""
    if not any(c.isalnum() for c in text):
        return " ".join(text.casefold().split())
    base = _ascii_strip(text)
    if not any(c.isalnum() for c in base):
        return " ".join(base.casefold().split())
    return _old_flatten(_ascii_strip(text.replace("&", " and ")))


if __name__ == "__main__":
    unittest.main()
