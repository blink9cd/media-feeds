"""An over-long name is refused on its raw length, before any of it is normalized.

The defect this pins: the name cap was measured on the normalized text alone, so
deciding that a name was too long first ran NFKC over all of it. A store reply a few
MiB long, its names packed with U+FDFA (which NFKC expands eighteenfold), cost
seconds of CPU per album just to be refused. The raw bound is checked first, in
constant time, so no step of the normalization ever reads more than
`_MAX_RAW_NAME_CHARS` characters on the way to refusing a name.

The control: the raw bound is looser than the cap by the derivation its comment
gives, so a name whose every character arrives fully decomposed and wearing a
selector, at the cap once normalized, is still matched. Every name here is invented.
"""

from __future__ import annotations

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
    _LONGEST_DECOMPOSITION,
    _MAX_NAME_CHARS,
    _MAX_RAW_NAME_CHARS,
    pick_album,
    same_artist_ids,
)
from _offline_guard import setUpModule, tearDownModule  # noqa: E402, F401 - the bootstrap above; unittest calls the hooks by name

#: Far past the raw bound, and each character one NFKC expands eighteenfold: the
#: shape of the reply that held the CPU, scaled down to stay quick when refused.
_HOSTILE = "ﷺ" * (_MAX_RAW_NAME_CHARS * 40)

#: GREEK SMALL LETTER ALPHA WITH PSILI AND VARIA AND YPOGEGRAMMENI (U+1F82), spelled
#: as its four-character canonical decomposition, then an emoji variation selector:
#: the most raw characters one normalized character can come from.
_DECOMPOSED = "ᾂ️"

#: The highest code point there is, for the sweep below.
_LAST_CODE_POINT = 0x10FFFF


def _candidate(name: str, artist: str) -> dict[str, object]:
    """One iTunes result, carrying only the fields the name match reads."""
    return {"artistName": artist, "collectionName": name, "artistId": 1}


class RawBoundTest(unittest.TestCase):
    """The raw-length refusal ahead of the normalized cap."""

    def test_an_over_long_name_is_refused_without_normalizing_it(self) -> None:
        seen: list[int] = []
        real = artwork_match._canonical

        def counting(text: str) -> str:
            seen.append(len(text))
            return real(text)

        with mock.patch.object(artwork_match, "_canonical", counting):
            self.assertIsNone(
                pick_album(
                    [_candidate(_HOSTILE, "A"), _candidate("A", _HOSTILE)],
                    artist="A",
                    album="A",
                    year=None,
                    tracks=None,
                )
            )
            for artist, album in ((_HOSTILE, "A"), ("A", _HOSTILE)):
                self.assertIsNone(
                    pick_album([], artist=artist, album=album, year=None, tracks=None)
                )
            self.assertEqual(
                same_artist_ids([_candidate("A", _HOSTILE)], artist="A"), []
            )
            self.assertEqual(same_artist_ids([], artist=_HOSTILE), [])
        # The short names were normalized, so the bound below is not vacuous.
        self.assertTrue(seen)
        self.assertLessEqual(max(seen), _MAX_RAW_NAME_CHARS)

    def test_a_fully_decomposed_name_at_the_cap_still_matches(self) -> None:
        name = _DECOMPOSED * _MAX_NAME_CHARS
        self.assertEqual(len(name), _MAX_RAW_NAME_CHARS)
        composed = unicodedata.normalize("NFKC", name.replace("️", ""))
        self.assertEqual(composed, "ᾂ" * _MAX_NAME_CHARS)
        chosen = pick_album(
            [_candidate(composed, composed)],
            artist=name,
            album=name,
            year=None,
            tracks=None,
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(same_artist_ids([_candidate("A", name)], artist=composed), [1])

    def test_no_normalized_character_decomposes_past_the_named_longest(self) -> None:
        # The derivation's premise, over every code point: a character NFKC leaves as
        # itself spells out to at most `_LONGEST_DECOMPOSITION` characters, so no
        # run of raw characters longer than that composes into one.
        longest = 0
        for code_point in range(_LAST_CODE_POINT + 1):
            char = chr(code_point)
            if unicodedata.category(char) == "Cs":
                continue
            if unicodedata.normalize("NFKC", char) == char:
                longest = max(longest, len(unicodedata.normalize("NFKD", char)))
        self.assertEqual(longest, _LONGEST_DECOMPOSITION)


if __name__ == "__main__":  # pragma: no cover - script entry point
    unittest.main()
