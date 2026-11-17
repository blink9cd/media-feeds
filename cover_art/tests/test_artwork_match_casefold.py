"""The matcher composes again after casefold, so a name reads as its own normalize does.

The defect this pins: the matcher put a name in NFKC once, before casefold, and
casefold can split a letter into a base and separate marks (or an iota) that a second
NFKC composes: "ΐ" casefolds to "ι" with a diaeresis and an acute apart, and "ᾂ́"
leaves its iota beside the acute. The apostrophe deletion can do the same, leaving a
letter beside a mark: "Writer’́s" reads "writer" with a bare acute after its "r". So a
name's `normalize` was not its own `normalize` again, and two caseless spellings of
one name ("ᾂ́" and "ἂί") were two names to `pick_album`.

The own-text comparison takes no second NFKC, and the exhaustive checks below are
why: casefold never makes a character without a word character into anything NFKC
would change, and nothing NFKC composes ever pairs with one.

Every name here is invented, never library data. A file of its own rather than more of
`test_artwork_match_scripts.py`, because that file sits near the per-file line cap.
"""

from __future__ import annotations

import sys
import unicodedata
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent.parent
# The pure matcher under test, and this folder for the offline tripwire.
sys.path[:0] = [str(_TOOL_ROOT / "src"), str(Path(__file__).resolve().parent)]

from artwork_match import (  # noqa: E402 - import follows the sys.path bootstrap above
    _canonical,
    _flatten,
    _is_word_character,
    _own_text,
    normalize,
    pick_album,
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
# there, under this module's own name so import order cannot decide which registration
# a property snapshotted. Loaded before the class bodies, because `@given` snapshots
# its settings when it decorates.
settings.register_profile(
    "artwork_match_casefold_properties",
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
settings.load_profile("artwork_match_casefold_properties")

#: The invented artist every candidate below is credited to.
_ARTIST = "The Paper Lanterns"
#: One past the last Unicode code point, for the exhaustive checks.
_CODE_POINTS = 0x110000
#: The modifier letter apostrophe: a letter the flatten deletes, so a name of it and
#: symbols alone reaches the own-text comparison with a word character in it.
_MODIFIER_APOSTROPHE = "\u02bc"
#: Letters casefold splits into a base and separate marks (or an iota) that NFKC
#: composes again, Latin and Greek, and a capital that casefolds to one of them once an
#: acute follows it ("Ϊ" then U+0301, drawn as two characters). A
#: plain text strategy rarely draws one before a mark, so the property draws them on
#: purpose.
_CASEFOLD_DECOMPOSES = (
    "\u01f0", "\u1e96", "\u0390", "\u03b0", "\u1f50", "\u1fb7", "\u1f82", "\u1ff7",
    "\u03aa",
)  # fmt: skip
#: The marks that compose with what casefold split off: grave, acute, diaeresis, caron,
#: psili and perispomeni.
_COMPOSING_MARKS = ("\u0300", "\u0301", "\u0308", "\u030c", "\u0313", "\u0342")
#: The apostrophes the flatten deletes, which can leave a letter beside a mark.
_APOSTROPHES = ("'", "\u2019", _MODIFIER_APOSTROPHE)
#: The property's alphabet: the splitting letters, their marks, a plain letter, the
#: apostrophes, and a space and brackets so qualifiers are drawn too.
_CASEFOLD_ALPHABET = (
    _CASEFOLD_DECOMPOSES + _COMPOSING_MARKS + ("r", " ", "(", ")") + _APOSTROPHES
)


def _nfc(text: str) -> str:
    """`text` composed, the spelling a name's normalize must come out in."""
    return unicodedata.normalize("NFC", text)


def _pick(names: list[str], album: str) -> str | None:
    """The `collectionName` `pick_album` chooses for `album` from same-artist `names`."""
    pool = [{"artistName": _ARTIST, "collectionName": name} for name in names]
    chosen = pick_album(pool, artist=_ARTIST, album=album, year=None, tracks=None)
    return None if chosen is None else str(chosen["collectionName"])


class SplittingLetterTest(unittest.TestCase):
    """A letter casefold splits reads composed, in each spelling of it."""

    def test_each_splitting_letter_normalizes_to_its_composed_self(self) -> None:
        for letter in ("\u0390", "\u01f0", "\u1e96", "\u03b0"):
            with self.subTest(letter=ascii(letter)):
                self.assertEqual(normalize(letter), letter)
                decomposed = unicodedata.normalize("NFD", letter)
                self.assertEqual(normalize(decomposed), letter)

    def test_a_capital_reads_as_the_composed_lower_case_letter(self) -> None:
        # Capital iota with dialytika and an acute casefolds to the three-part "ΐ".
        self.assertEqual(normalize("\u03aa\u0301"), "\u0390")

    def test_a_stacked_iota_and_acute_compose(self) -> None:
        # Casefold splits the iota off U+1F82, and the acute then composes with it.
        self.assertEqual(normalize("\u1f82\u0301"), "\u1f02\u03af")
        self.assertEqual(normalize("\u1f02\u03af"), "\u1f02\u03af")

    def test_a_dotted_capital_i_keeps_its_dot_in_both_spellings(self) -> None:
        # "İ" casefolds to "i" and a combining dot, which has no composed form, so the
        # second NFKC leaves it, and the precomposed and decomposed spellings agree.
        self.assertEqual(normalize("\u0130"), "i\u0307")
        self.assertEqual(normalize("I\u0307"), "i\u0307")
        self.assertEqual(normalize("i\u0307"), "i\u0307")

    def test_a_splitting_letter_in_a_qualifier_reads_composed(self) -> None:
        self.assertEqual(split_name("X (\u0390)"), ("x", "\u0390"))


class ApostropheThenAccentTest(unittest.TestCase):
    """An accent the apostrophe deletion leaves beside a letter composes with it."""

    def test_the_accent_composes_with_the_letter_before_the_apostrophe(self) -> None:
        for apostrophe in _APOSTROPHES:
            with self.subTest(apostrophe=ascii(apostrophe)):
                self.assertEqual(
                    normalize(f"Writer{apostrophe}\u0301s"), "write\u0155s"
                )
        self.assertEqual(normalize("write\u0155s"), "write\u0155s")


class CaselessSpellingsPickTest(unittest.TestCase):
    """Two caseless spellings of one album are one album to `pick_album`."""

    def test_an_iota_subscript_title_finds_its_caseless_spelling(self) -> None:
        self.assertEqual(_pick(["\u1f02\u03af"], "\u1f82\u0301"), "\u1f02\u03af")
        self.assertEqual(_pick(["\u1f82\u0301"], "\u1f02\u03af"), "\u1f82\u0301")

    def test_an_apostrophe_then_accent_title_finds_its_composed_spelling(
        self,
    ) -> None:
        self.assertEqual(_pick(["Write\u0155s"], "Writer\u2019\u0301s"), "Write\u0155s")


class CasefoldPropertyTest(unittest.TestCase):
    """Over casefold's splitting letters, their marks and the apostrophes."""

    @given(st.text(st.sampled_from(_CASEFOLD_ALPHABET), max_size=20))
    def test_a_normalized_name_normalizes_to_itself(self, text: str) -> None:
        once = normalize(text)
        self.assertEqual(normalize(once), once)

    @given(st.text(st.sampled_from(_CASEFOLD_ALPHABET), max_size=20))
    def test_a_split_name_comes_out_composed(self, text: str) -> None:
        base, qualifier = split_name(text)
        self.assertEqual((_nfc(base), _nfc(qualifier)), (base, qualifier))

    @given(st.text(st.sampled_from(_CASEFOLD_ALPHABET), max_size=20))
    def test_the_flatten_comes_out_in_nfkc(self, text: str) -> None:
        flat = _flatten(_canonical(text))
        self.assertEqual(unicodedata.normalize("NFKC", flat), flat)


class OwnTextNeedsNoSecondNfkcTest(unittest.TestCase):
    """The own-text comparison's casefold leaves nothing for NFKC to change.

    Its text holds no word character, or only the modifier apostrophe, which NFKC
    leaves alone. Checked over every code point rather than sampled, because a
    sample would almost never meet the one character that broke it.
    """

    def test_no_character_without_a_word_casefolds_to_what_nfkc_changes(
        self,
    ) -> None:
        changed = [
            hex(code)
            for code in range(_CODE_POINTS)
            if not _is_word_character(char := chr(code))
            and unicodedata.normalize("NFKC", char) == char
            and unicodedata.normalize("NFKC", char.casefold()) != char.casefold()
        ]
        self.assertEqual(changed, [])

    def test_nothing_composes_onto_or_reorders_past_a_non_word_character(
        self,
    ) -> None:
        # A composition's second half, and any character with a nonzero combining
        # class, is a word character: so a run of characters without one, beside the
        # modifier apostrophe or not, holds no pair NFKC could compose or reorder.
        partners = {
            chr(int(parts[1], 16))
            for code in range(_CODE_POINTS)
            if len(parts := unicodedata.decomposition(chr(code)).split()) == 2
            and not parts[0].startswith("<")
        }
        combining = {
            chr(code)
            for code in range(_CODE_POINTS)
            if unicodedata.combining(chr(code))
        }
        self.assertEqual(
            [hex(ord(c)) for c in partners | combining if not _is_word_character(c)],
            [],
        )

    @given(
        st.text(
            st.characters(categories=("P", "S", "Z")) | st.just(_MODIFIER_APOSTROPHE),
            max_size=20,
        )
    )
    def test_the_own_text_of_symbols_is_already_in_nfkc(self, text: str) -> None:
        own = _own_text(_canonical(text))
        self.assertEqual(unicodedata.normalize("NFKC", own), own)


if __name__ == "__main__":
    unittest.main()
