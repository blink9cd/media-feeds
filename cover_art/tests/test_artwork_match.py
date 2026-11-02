"""The artwork matcher: name first, year and track count as tiebreakers.

The candidate sets below are modeled on a real reply: their qualifiers, years and track
counts are what the iTunes Search API returned for a band with a run of self-titled
albums, queried as `"<band> <band> (Gold)"`, plus (in `WIDE`) the one entry the
artist-alone query holds that the narrow one does not. The band's name is an invented
stand-in, and the ids, the artwork URLs, and the day-and-month of each release date are
placeholders. They are the regression
fixture for the `results[0]` defect: taking the first result with no check that it is
the album asked for would hand *Fictionals (Gold)* the *Teal Album*'s cover. The album
actually wanted is absent from the narrow query entirely (the store calls it plain
"Fictionals"), which is why no amount of better ranking on that one query could have found
it.

Gating on artist and year and track count all matching was measured and rejected: against
a real library it broke more matches that worked than it fixed, because a tag year and a
store release year legitimately differ across reissues and regions, and a deluxe edition
has a different track count.
`test_a_different_edition_of_the_right_album_is_accepted` below is that lesson as a test:
a different edition of the right album is a fine answer.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_TOOL_ROOT = Path(__file__).resolve().parent.parent
# The pure matcher under test, plus (for the end-to-end class at the bottom) the
# I/O half that calls it and the sibling tool that owns `AlbumRef`.
sys.path[:0] = [
    str(_TOOL_ROOT / "src"),
    str(_TOOL_ROOT),
]

from cover_art import artwork_url  # noqa: E402 - import follows the sys.path bootstrap above
from cover_art import AlbumRef  # noqa: E402 - import follows the sys.path bootstrap above
import artwork_match  # noqa: E402 - import follows the sys.path bootstrap above
from artwork_match import (  # noqa: E402 - import follows the sys.path bootstrap above
    _MAX_NAME_CHARS,
    normalize,
    pick_album,
)

#: A stack of trailing qualifiers long enough to push any name past the cap on its
#: own. Each one strips cleanly, so a name wearing it still reduces to its base,
#: which is what makes an over-cap candidate a match without the cap, not a miss.
_OVER_CAP = " (Deluxe)" * (_MAX_NAME_CHARS // len(" (Deluxe)") + 1)


def _candidate(
    name: str, year: str, tracks: int, artist: str = "Fictionals"
) -> dict[str, object]:
    """One iTunes result, carrying only the fields the matcher reads."""
    return {
        "artistName": artist,
        "collectionName": name,
        "collectionId": f"{artist}/{name}",
        "releaseDate": f"{year}-01-01T00:00:00Z",
        "trackCount": tracks,
        "artworkUrl100": f"https://example.com/{year}/100x100bb.jpg",
    }


#: What the artist-and-album query really returns. The wanted album is not here.
NARROW = [
    _candidate("Fictionals (Teal Album)", "2019", 10),
    _candidate("Fictionals (2024 Remaster)", "1994", 10),
    _candidate("Fictionals (Deluxe Edition)", "1994", 24),
    _candidate("Fictionals (White Album)", "2016", 10),
    _candidate("Fictionals (Red Album) [Deluxe Edition]", "2008", 15),
]

#: The artist-only query also contains the record actually wanted.
WIDE = [*NARROW, _candidate("Fictionals", "2026", 10)]


class NormalizeTest(unittest.TestCase):
    """Reducing a name to the part worth comparing."""

    def test_a_trailing_qualifier_is_stripped(self) -> None:
        self.assertEqual(normalize("Fictionals (Gold)"), "fictionals")
        self.assertEqual(
            normalize("Stand Still (Deluxe 10th Anniversary Edition)"), "stand still"
        )

    def test_stacked_qualifiers_are_all_stripped(self) -> None:
        self.assertEqual(
            normalize("Fictionals (Red Album) [Deluxe Edition]"), "fictionals"
        )

    def test_an_ampersand_folds_to_and_before_punctuation_is_dropped(self) -> None:
        # Measured: without this, an exact comparison rejects every candidate for
        # "Peter Bjorn & John" and a correct match turns into a miss. Folding
        # after stripping punctuation would erase the ampersand entirely and leave
        # "peter bjorn john", matching neither spelling the store uses.
        self.assertEqual(
            normalize("Peter Bjorn & John"), normalize("Peter Bjorn and John")
        )

    def test_case_and_punctuation_are_ignored(self) -> None:
        self.assertEqual(normalize("Writer's Block"), normalize("writers block"))


class PickAlbumTest(unittest.TestCase):
    """Which candidate, if any, actually is the album that was asked for."""

    def test_the_right_album_wins_when_the_pool_contains_it(self) -> None:
        chosen = pick_album(
            WIDE, artist="Fictionals", album="Fictionals (Gold)", year=2026, tracks=10
        )
        assert chosen is not None
        self.assertEqual(chosen["collectionName"], "Fictionals")

    def test_nothing_is_chosen_when_the_right_album_is_absent(self) -> None:
        # The `results[0]` defect in one assertion: results[0] of this set is the Teal
        # Album, and every member of it is a different Fictionals album.
        self.assertIsNone(
            pick_album(
                NARROW,
                artist="Fictionals",
                album="Fictionals (Gold)",
                year=2026,
                tracks=10,
            )
        )

    def test_a_different_edition_of_the_right_album_is_accepted(self) -> None:
        # Stand Still: only the deluxe edition is listed, with a different year and a
        # different track count. It is the same work and its art is right, so
        # rejecting it would trade a correct cover for no cover, which is what
        # gating on year and track count did, measured on two real albums.
        candidates = [
            _candidate(
                "Stand Still (Deluxe 10th Anniversary Edition)",
                "2013",
                20,
                artist="The Sample Collective",
            )
        ]
        chosen = pick_album(
            candidates,
            artist="The Sample Collective",
            album="Stand Still",
            year=2002,
            tracks=10,
        )
        assert chosen is not None
        self.assertEqual(
            chosen["collectionName"], "Stand Still (Deluxe 10th Anniversary Edition)"
        )

    def test_another_artists_album_is_never_chosen(self) -> None:
        candidates = [_candidate("Fictionals", "2026", 10, artist="Not Fictionals")]
        self.assertIsNone(
            pick_album(
                candidates,
                artist="Fictionals",
                album="Fictionals (Gold)",
                year=2026,
                tracks=10,
            )
        )

    def test_the_year_separates_two_candidates_of_the_same_name(self) -> None:
        # An unqualified "Fictionals" name-matches every candidate here, since each one
        # normalizes to "fictionals", so the name alone cannot choose and the year must.
        chosen = pick_album(
            WIDE, artist="Fictionals", album="Fictionals", year=2019, tracks=10
        )
        assert chosen is not None
        self.assertEqual(chosen["collectionName"], "Fictionals (Teal Album)")

    def test_the_year_breaks_the_tie_when_the_right_candidate_is_not_first(
        self,
    ) -> None:
        # The test above wants the Teal Album, which is also results[0], so a rank
        # blind to the year passes it by list order alone. The White Album sits
        # fourth: only the year can lift it over the three ten-track records
        # ahead of it.
        chosen = pick_album(
            WIDE, artist="Fictionals", album="Fictionals", year=2016, tracks=10
        )
        assert chosen is not None
        self.assertEqual(chosen["collectionName"], "Fictionals (White Album)")

    def test_a_release_year_in_non_ascii_decimal_digits_still_matches(self) -> None:
        # The I/O half already reads an Arabic-Indic "٢٠١٩" as 2019 for the
        # record's fallback year; the matcher compared the same characters as text
        # against "2019" and never matched them, so the two halves disagreed about
        # what year a candidate was from. One numeric rule for both.
        candidates = [
            _candidate("Fictionals (White Album)", "2016", 10),
            _candidate("Fictionals (Teal Album)", "٢٠١٩", 10),
        ]
        chosen = pick_album(
            candidates, artist="Fictionals", album="Fictionals", year=2019, tracks=10
        )
        assert chosen is not None
        self.assertEqual(chosen["collectionName"], "Fictionals (Teal Album)")

    def test_a_renamed_album_is_admitted_when_year_and_track_count_agree(
        self,
    ) -> None:
        # The store's title shares nothing with the library's (a different base,
        # not a sibling), so the name cannot vouch for it and the two facts do.
        # A self-titled double album, filed by its nickname "The White Album" in many
        # a library and as plain "The Invented Quartet" in the store.
        candidates = [
            _candidate(
                "The Invented Quartet", "1968", 30, artist="The Invented Quartet"
            )
        ]
        chosen = pick_album(
            candidates,
            artist="The Invented Quartet",
            album="The White Album",
            year=1968,
            tracks=30,
        )
        assert chosen is not None
        self.assertEqual(chosen["collectionName"], "The Invented Quartet")

    def test_a_renamed_album_needs_both_facts_not_one(self) -> None:
        # Either fact alone, without the name, is too weak: a year matches across
        # a whole year's releases and a track count across most albums ever made.
        candidates = [
            _candidate(
                "The Invented Quartet", "1968", 30, artist="The Invented Quartet"
            )
        ]
        for year, tracks in ((1968, 17), (1987, 30)):
            with self.subTest(year=year, tracks=tracks):
                self.assertIsNone(
                    pick_album(
                        candidates,
                        artist="The Invented Quartet",
                        album="The White Album",
                        year=year,
                        tracks=tracks,
                    )
                )

    def test_a_sibling_album_is_not_re_admitted_by_matching_facts(self) -> None:
        # Fictionals released the Teal Album and the Black Album in the same year, ten
        # tracks each. Asked for the Black Album when only the Teal is listed: same
        # base, both qualified, qualifiers differ, so the name refuses it, and the
        # year and track count must not let it back in, or an album absent from
        # the results borrows its sibling's cover: the original defect, through a
        # second door.
        candidates = [_candidate("Fictionals (Teal Album)", "2019", 10)]
        self.assertIsNone(
            pick_album(
                candidates,
                artist="Fictionals",
                album="Fictionals (Black Album)",
                year=2019,
                tracks=10,
            )
        )

    def test_the_track_count_separates_candidates_the_year_cannot(self) -> None:
        # Two 1994 records; only the track count tells the remaster from the deluxe.
        chosen = pick_album(
            NARROW, artist="Fictionals", album="Fictionals", year=1994, tracks=24
        )
        assert chosen is not None
        self.assertEqual(chosen["collectionName"], "Fictionals (Deluxe Edition)")

    def test_without_music_facts_only_a_name_match_is_accepted(self) -> None:
        # Music.app unreachable: no tiebreakers exist, so the weaker check applies.
        # It must still never fall back to "whatever came first".
        self.assertIsNone(
            pick_album(
                NARROW,
                artist="Fictionals",
                album="Fictionals (Gold)",
                year=None,
                tracks=None,
            )
        )
        chosen = pick_album(
            WIDE, artist="Fictionals", album="Fictionals", year=None, tracks=None
        )
        assert chosen is not None

    def test_an_empty_pool_is_no_match(self) -> None:
        self.assertIsNone(
            pick_album(
                [], artist="Fictionals", album="Fictionals (Gold)", year=2026, tracks=10
            )
        )

    def test_a_candidate_with_no_usable_name_is_skipped(self) -> None:
        # Nothing on the far side of this boundary is type-checked against us.
        candidates = [{"artistName": "Fictionals", "collectionName": None}]
        self.assertIsNone(
            pick_album(
                candidates,
                artist="Fictionals",
                album="Fictionals",
                year=2026,
                tracks=10,
            )
        )

    def test_a_candidate_with_no_release_date_can_still_match_by_name(self) -> None:
        candidates = [
            {"artistName": "Fictionals", "collectionName": "Fictionals (Gold)"}
        ]
        chosen = pick_album(
            candidates,
            artist="Fictionals",
            album="Fictionals (Gold)",
            year=2026,
            tracks=10,
        )
        assert chosen is not None
        self.assertEqual(chosen["collectionName"], "Fictionals (Gold)")


class ArtworkUrlOverTheCapturedReplyTest(unittest.TestCase):
    """The `results[0]` defect end to end: the store's reply, through `artwork_url`.

    `PickAlbumTest` proves the pure half refuses this reply; this proves that the I/O
    half calling it does too, with Music.app's facts handed in the way `album_scan`
    hands them: as two optional numbers, not its `AlbumFacts` object, because the
    artwork adapter is a sibling tool and does not import album_stats' type. It
    lives beside the matcher's tests rather than in the aggregator because the fixture
    is the point: this is the reply that would hand *Fictionals (Gold)* the Teal Album's
    cover, and the claim is that the whole lookup refuses it, not only the matcher.
    """

    def test_a_result_set_without_this_album_yields_no_cover(self) -> None:
        body = json.dumps({"resultCount": len(NARROW), "results": NARROW}).encode(
            "utf-8"
        )
        ref = AlbumRef(
            artist="Fictionals", album="Fictionals (Gold)", added=dt.date(2001, 3, 15)
        )
        match = artwork_url(ref, year=2026, tracks=10, fetch=lambda _u, _t: body)
        self.assertIsNone(
            match.url,
            "a cover was returned for an album absent from the results; this is "
            "the defect that hands Fictionals (Gold) the Teal Album's art",
        )


class NameLengthCapTest(unittest.TestCase):
    """A name past `_MAX_NAME_CHARS` never reaches the qualifier strip.

    The strip is quadratic in the name's length and every candidate's names come from
    the network, so the cap is what keeps a hostile reply from holding the CPU. None
    of these tests measures time: they pin which strings reach the strip and what the
    matcher answers, which is what the cap actually changes.
    """

    def test_no_name_over_the_cap_is_ever_normalized(self) -> None:
        pool: list[dict[str, object]] = [
            _candidate(
                "Placeholder Album" + _OVER_CAP,
                "1969",
                17,
                artist="The Invented Quartet",
            ),
            _candidate(
                "Placeholder Album",
                "1969",
                17,
                artist="The Invented Quartet" + _OVER_CAP,
            ),
            # A non-string artist name is stringified before it is normalized, so
            # the cap has to be judged on that string: a list this long renders
            # far past it.
            {
                "artistName": ["("] * _MAX_NAME_CHARS,
                "collectionName": "Placeholder Album",
            },
            _candidate("Placeholder Album", "1969", 17, artist="The Invented Quartet"),
        ]
        with mock.patch.object(
            artwork_match, "split_name", wraps=artwork_match.split_name
        ) as spy:
            chosen = pick_album(
                pool,
                artist="The Invented Quartet",
                album="Placeholder Album",
                year=1969,
                tracks=17,
            )
            pick_album(
                pool,
                artist="The Invented Quartet" + _OVER_CAP,
                album="Placeholder Album",
                year=None,
                tracks=None,
            )
            pick_album(
                pool,
                artist="The Invented Quartet",
                album="Placeholder Album" + _OVER_CAP,
                year=None,
                tracks=None,
            )
        assert chosen is not None
        self.assertEqual(
            chosen["collectionId"], "The Invented Quartet/Placeholder Album"
        )
        # The spy saw the normal names, so the bound below is not vacuously true.
        self.assertTrue(spy.called)
        longest = max(len(call.args[0]) for call in spy.call_args_list)
        self.assertLessEqual(longest, _MAX_NAME_CHARS)

    def test_a_candidate_over_the_cap_is_skipped_even_when_it_would_win(self) -> None:
        # Without the cap the long candidate is the stronger match: its qualifiers
        # strip to the same base, and it alone agrees on the year. Skipping it is the
        # point, and the plain candidate in the same pool is still chosen.
        pool = [
            _candidate(
                "Placeholder Album" + _OVER_CAP,
                "1969",
                17,
                artist="The Invented Quartet",
            ),
            _candidate("Placeholder Album", "1987", 17, artist="The Invented Quartet"),
        ]
        chosen = pick_album(
            pool,
            artist="The Invented Quartet",
            album="Placeholder Album",
            year=1969,
            tracks=17,
        )
        assert chosen is not None
        self.assertEqual(chosen["collectionName"], "Placeholder Album")

    def test_a_wanted_name_over_the_cap_matches_nothing(self) -> None:
        # Either wanted name over the cap is answered with None before any candidate
        # is looked at: the library's names are input too, and without this a
        # single folder name could hold the CPU as surely as a candidate.
        pool = [
            _candidate("Placeholder Album", "1969", 17, artist="The Invented Quartet")
        ]
        for artist, album in (
            ("The Invented Quartet" + _OVER_CAP, "Placeholder Album"),
            ("The Invented Quartet", "Placeholder Album" + _OVER_CAP),
        ):
            with self.subTest(artist_chars=len(artist), album_chars=len(album)):
                self.assertIsNone(
                    pick_album(pool, artist=artist, album=album, year=1969, tracks=17)
                )


if __name__ == "__main__":
    unittest.main()
