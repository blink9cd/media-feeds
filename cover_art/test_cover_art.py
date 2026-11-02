"""Artwork resolution against a fake fetcher.

No test in this file touches the network. The live boundary test lives in
tests/test_live_artwork.py, whose live class skips when offline; `load_tests` below
folds it, and the matcher's own suite in tests/, into this module's run.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import sys
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(_TOOL_ROOT)]

from cover_art import (  # noqa: E402 - import follows the sys.path bootstrap above
    _is_recordable_url,
    artwork_url,
)
from cover_art import AlbumRef  # noqa: E402 - import follows the sys.path bootstrap above
from hypothesis import (  # noqa: E402 - import follows the sys.path bootstrap above
    HealthCheck,
    given,
    settings,
    strategies as st,
)

# A registered profile, loaded here before any class body, never an `@settings`
# decorator: a `@given` test snapshots its settings when it is decorated, so a load
# after the class bodies would be a no-op, and a longer fuzzing run re-applies its
# own settings object to each target, which Hypothesis refuses on a function already
# carrying one. Its own name: `load_profile` sets one global default, so a
# name shared with another module's profile would let import order decide which
# settings each had snapshotted.
#
# `too_slow` is the one health check suppressed. The first draw of a text strategy
# builds Hypothesis's unicode tables, once per storage directory, inside the ~1 s that
# check allows the first examples, so on a fresh clone whichever property drew text
# first failed with `FailedHealthCheck: Input generation is slow`, then passed: a cold
# cache, not a slow strategy. tests/test_cold_hypothesis_cache.py holds the timings and
# is the regression test. The in-suite run keeps its default randomization, which the
# acceptance figures recorded at `_decodable_response_bodies` below were measured under.
# deadline=None: a per-example wall-clock deadline measures machine load, not
# correctness, so a busy machine would report a passing property as flaky.
settings.register_profile(
    "cover_art_properties",
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
settings.load_profile("cover_art_properties")

REF = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2026, 8, 30))


def fake_body(payload: object) -> bytes:
    """Encode a JSON-able payload the way an HTTP response body would arrive."""
    return json.dumps(payload).encode("utf-8")


def matching_result(**overrides: object) -> dict[str, object]:
    """A search result that verifies as REF's album.

    A result is only used when it is demonstrably the album that was asked for, so a
    fixture meaning "the store found it" has to carry the artist and title as well as
    an artwork URL. A bare `{"artworkUrl100": ...}` means "some other album"; accepting
    that payload is the defect this suite guards.
    """
    result: dict[str, object] = {
        "artistName": "An Artist",
        "collectionName": "A Record",
        "collectionId": 1,
        "releaseDate": "1994-01-01T00:00:00Z",
        "trackCount": 10,
        "artworkUrl100": "https://example.com/a/100x100bb.jpg",
    }
    result.update(overrides)
    return result


class ArtworkUrlTest(unittest.TestCase):
    """`artwork_url` against a fake fetcher covering every branch of the parse."""

    def test_a_hit_returns_an_upsized_artwork_url(self) -> None:
        body = fake_body({"resultCount": 1, "results": [matching_result()]})
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertEqual(match.url, "https://example.com/a/400x400bb.webp")

    def test_a_result_for_a_DIFFERENT_album_yields_no_url(self) -> None:
        # The wrong-cover defect, as a unit test: taking the first result would return
        # this cover, and it belongs to a different record entirely.
        body = fake_body(
            {
                "resultCount": 1,
                "results": [
                    matching_result(collectionName="Some Other Record", trackCount=7)
                ],
            }
        )
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: body).url)

    def test_the_default_format_is_webp(self) -> None:
        # Measured 2026-09-01: the same 400x400 art is 14,192 bytes as WebP against
        # 34,757 as JPEG, 59% smaller at identical dimensions, indistinguishable at
        # the size it is rendered at.
        body = fake_body({"resultCount": 1, "results": [matching_result()]})
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertEqual(match.url, "https://example.com/a/400x400bb.webp")

    def test_the_format_is_the_callers_choice(self) -> None:
        body = fake_body({"resultCount": 1, "results": [matching_result()]})
        match = artwork_url(REF, fetch=lambda _u, _t: body, image_format="jpg")
        self.assertEqual(match.url, "https://example.com/a/400x400bb.jpg")

    def test_only_the_FINAL_extension_is_rewritten(self) -> None:
        # The trap. A real artwork URL carries the source file's own ".jpg" mid-path
        # (".../075679854247.jpg/100x100bb.jpg"), so a naive replace of ".jpg" would
        # corrupt the path and 404. Only the last segment's extension may change.
        body = fake_body(
            {
                "resultCount": 1,
                "results": [
                    matching_result(
                        artworkUrl100="https://example.com/a/075679854247.jpg/100x100bb.jpg"
                    )
                ],
            }
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertEqual(
            match.url, "https://example.com/a/075679854247.jpg/400x400bb.webp"
        )

    def test_the_requested_width_is_what_gets_substituted(self) -> None:
        # Measured 2026-09-01: Apple's resizer honored every width tried exactly, so
        # the size is the caller's to choose rather than a constant fixed in here.
        body = fake_body({"resultCount": 1, "results": [matching_result()]})
        match = artwork_url(REF, fetch=lambda _u, _t: body, width=250)
        self.assertEqual(match.url, "https://example.com/a/250x250bb.webp")

    def test_an_empty_result_set_yields_no_url(self) -> None:
        match = artwork_url(
            REF, fetch=lambda _u, _t: fake_body({"resultCount": 0, "results": []})
        )
        self.assertIsNone(match.url)

    def test_a_result_with_no_artwork_field_yields_no_url(self) -> None:
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(artworkUrl100=None)]}
        )
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: body).url)

    def test_a_non_string_artwork_field_yields_no_url(self) -> None:
        # The field exists but is the wrong type (e.g. a malformed upstream record),
        # a distinct branch from the field being absent entirely.
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(artworkUrl100=12345)]}
        )
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: body).url)

    def test_a_results_field_that_is_not_a_list_yields_no_url(self) -> None:
        # A 2xx body of the wrong shape: valid JSON, wrong contract. Distinct from
        # unparseable, and reachable from a proxy or an API change.
        body = fake_body({"resultCount": 1, "results": {"not": "a list"}})
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: body).url)

    def test_the_right_album_with_no_release_date_still_yields_its_url(self) -> None:
        # The album is identified by name; the year is simply unavailable. The cover
        # must still be used: the year is a bonus, not a precondition.
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(releaseDate=None)]}
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertEqual(match.url, "https://example.com/a/400x400bb.webp")
        self.assertIsNone(match.year)

    def test_an_unparseable_release_date_yields_a_url_with_no_year(self) -> None:
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(releaseDate="not-a-date")]}
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertIsNotNone(match.url)
        self.assertIsNone(match.year)

    def test_a_release_date_in_SUPERSCRIPT_digits_yields_a_url_with_no_year(
        self,
    ) -> None:
        # `releaseDate` is remote input, and "²⁰²⁵" satisfies str.isdigit() while int()
        # rejects it, so an isdigit() year guard admits a value the conversion then
        # refuses, raising ValueError out of the year parse at a call site outside the try
        # that guards the search. Regression for that uncaught raise: the never-raises
        # contract has to hold for every digit-ish year, not only for text that is
        # obviously not a date like the case above.
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(releaseDate="²⁰²⁵-01-01")]}
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertIsNotNone(match.url)
        self.assertIsNone(match.year)

    def test_a_release_date_in_NON_ASCII_decimal_digits_still_yields_its_year(
        self,
    ) -> None:
        # The other half of why that guard is isdecimal() rather than the stricter
        # `isascii() and isdigit()`: Arabic-Indic "٢٠٢٥" is a real year and int()
        # parses it to 2025, so clamping to ASCII would stop the raise above by
        # discarding a good answer instead. This pins the accepted half, so
        # over-tightening the guard fails here rather than passing silently.
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(releaseDate="٢٠٢٥-01-01")]}
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertIsNotNone(match.url)
        self.assertEqual(match.year, 2025)

    def test_an_UNHASHABLE_collection_id_yields_a_url_rather_than_raising(
        self,
    ) -> None:
        # `collectionId` is remote input and is used as the pool's dedup key, so it
        # lands in a set. A JSON array or object there is legal JSON that decodes to
        # an unhashable list/dict, and an unguarded `key in seen` raises TypeError out
        # of `artwork_url`, from a line outside the try that guards `_search`, so the
        # never-raises contract breaks just as it would for the release year. An id
        # that cannot be hashed carries no usable identity, so it is treated as a
        # missing one: the candidate is kept and simply never deduped against.
        for unusable in ([1, 2], {"a": 1}):
            with self.subTest(collection_id=unusable):
                body = fake_body(
                    {
                        "resultCount": 1,
                        "results": [matching_result(collectionId=unusable)],
                    }
                )
                match = artwork_url(REF, fetch=lambda _u, _t: body)
                self.assertEqual(match.url, "https://example.com/a/400x400bb.webp")

    def test_a_non_json_body_yields_no_url(self) -> None:
        # A proxy can return a non-JSON body with a 2xx status; json.loads must be guarded.
        self.assertIsNone(
            artwork_url(REF, fetch=lambda _u, _t: b"<html>nope</html>").url
        )

    def test_a_network_failure_yields_no_url_rather_than_raising(self) -> None:
        def boom(_url: str, _timeout: float) -> bytes:
            raise OSError("no route to host")

        self.assertIsNone(artwork_url(REF, fetch=boom).url)

    def test_a_truncated_http_response_yields_no_url_rather_than_raising(self) -> None:
        # http.client.HTTPException (e.g. IncompleteRead, raised out of a real
        # response.read() on a truncated chunked body) is neither OSError nor
        # ValueError, so it needs its own arm in `_search`'s except clause, or it
        # propagates straight out of artwork_url.
        def truncated(_url: str, _timeout: float) -> bytes:
            raise http.client.IncompleteRead(b"partial")

        self.assertIsNone(artwork_url(REF, fetch=truncated).url)

    def test_a_pathologically_nested_body_yields_no_url_rather_than_raising(
        self,
    ) -> None:
        # json.loads' recursive-descent parser raises RecursionError (a RuntimeError,
        # not a ValueError) once nesting exceeds the interpreter's stack budget.
        # Real network bodies are not bounded the way the property test's
        # st.binary(max_size=2048) samples are, so this is reachable in production
        # even though it never surfaces from the capped fuzz strategy.
        deeply_nested = b"[" * 2_000_000
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: deeply_nested).url)

    def test_the_query_carries_both_artist_and_album(self) -> None:
        seen: list[str] = []

        def record(url: str, _timeout: float) -> bytes:
            seen.append(url)
            return fake_body({"resultCount": 0, "results": []})

        artwork_url(REF, fetch=record)
        self.assertIn("An+Artist", seen[0])
        self.assertIn("A+Record", seen[0])
        # both queries run and are pooled before ranking. Returning after the first
        # lets the wrong album win: the store does not always use the library's name,
        # so the right record can appear only in the second.
        self.assertEqual(len(seen), 2)
        self.assertNotIn("A+Record", seen[1])

    def test_the_timeout_is_passed_through_to_the_fetcher(self) -> None:
        seen: list[float] = []

        def record(_url: str, timeout: float) -> bytes:
            seen.append(timeout)
            return fake_body({"resultCount": 0, "results": []})

        artwork_url(REF, fetch=record, timeout=2.5)
        self.assertEqual(seen, [2.5, 2.5])

    def test_a_surrogate_in_the_artist_yields_no_url_rather_than_raising(self) -> None:
        # Regression guard: with urllib.parse.urlencode outside `_search`'s try
        # block, a lone surrogate in ref.artist (or ref.album) raises
        # UnicodeEncodeError straight out of this "never raises" function.
        # Unreachable via a real macOS folder scan (APFS rejects such names), but
        # reachable when the folder scan runs off macOS, where a lossy filename decode
        # can hand this exact input. UnicodeEncodeError is a ValueError subclass,
        # already in `_search`'s except tuple, so building the query inside the try is
        # the entire fix: this test fails without that and passes with it.
        bad_ref = AlbumRef(
            artist="An Artist \udcff", album="A Record", added=dt.date(2026, 8, 30)
        )
        body = fake_body({"resultCount": 0, "results": []})
        self.assertIsNone(artwork_url(bad_ref, fetch=lambda _u, _t: body).url)

    def test_a_surrogate_in_the_album_yields_no_url_rather_than_raising(self) -> None:
        bad_ref = AlbumRef(
            artist="An Artist", album="A Record \udcff", added=dt.date(2026, 8, 30)
        )
        body = fake_body({"resultCount": 0, "results": []})
        self.assertIsNone(artwork_url(bad_ref, fetch=lambda _u, _t: body).url)


# The two halves of `str.isdigit()`, computed rather than listed so the split cannot
# go stale against a new Unicode version. `DECIMAL` is the set `int()` accepts;
# NON_DECIMAL is the rest of isdigit() (superscripts and the like), and a year
# guard's bugs live on that boundary. The scan costs ~0.09s at import, which
# is the price of not hardcoding a character table that would quietly rot.
_DECIMAL_DIGITS = tuple(
    character
    for character in map(chr, range(sys.maxunicode + 1))
    if character.isdecimal()
)
_NON_DECIMAL_DIGITS = tuple(
    character
    for character in map(chr, range(sys.maxunicode + 1))
    if character.isdigit() and not character.isdecimal()
)

# A four-character year holding at least one non-decimal digit, built rather than
# sampled: drawing four characters from the combined pool leaves the interesting case
# to chance, and chance is what a never-raises property must not depend on. Sampling
# was measured: the class this constructs turned up in 13% of draws, and the whole
# property caught a reverted year guard in only 1 run out of 5.
_adversarial_year = st.tuples(
    st.lists(st.sampled_from(_DECIMAL_DIGITS), min_size=3, max_size=3),
    st.sampled_from(_NON_DECIMAL_DIGITS),
).flatmap(lambda parts: st.permutations([*parts[0], parts[1]]).map("".join))

_date_like = st.builds(
    lambda year, tail: year + tail,
    st.one_of(
        st.text(alphabet=st.sampled_from(_DECIMAL_DIGITS), min_size=4, max_size=4),
        _adversarial_year,
    ),
    st.sampled_from(("", "-01-01", "-01-01T00:00:00Z")),
)

# Every type a JSON document can hand a field, containers included: a list or dict
# is what makes an unhashable value reachable for a field used as a set key.
_json_values = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(max_size=20),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=8), children, max_size=3),
    ),
    max_leaves=4,
)

# A body the parse can get through, which st.binary() cannot produce: arbitrary bytes
# essentially never decode to JSON carrying a name-matching album, so every branch
# past `json.loads` (the dedup key, the year parse, the URL rewrite) would sit
# outside this target's input space. A ValueError in the year guard and a TypeError
# in the dedup key both passed a green fuzz gate that way.
#
# The two name fields are deliberately left realistic, so the candidate always
# verifies as REF's album and the downstream path always runs. Fuzzing them too would
# spend most draws on the no-match early return, an axis already covered by
# pick_album's own suite in tests/, by the different-album example test above, and by
# the ref-side property test below. The gap was never the match; it was everything the
# parse does to a field's value afterward.
#
# Acceptance, measured rather than eyeballed: each real defect was reverted in turn and
# this property run 10 times, each against a fresh example database so every run is an
# independent discovery rather than a replay. The year guard is caught 9/10, the dedup
# key 10/10, against 1/5 for the year guard when the hazard was sampled for rather
# than constructed by `_adversarial_year`. The two deterministic tests above are what
# pin those defects exactly; this target's job is the next one nobody has named yet.
#
# A thumbnail that is URL-shaped enough to reach the final-segment rewrite, with
# generated text (control characters and all) in its path. A bare `_json_values`
# string almost never starts with a scheme, so without this arm the rewrite and the
# recording rule after it would only ever see refusals of the cheapest kind.
_url_like = st.text(max_size=20).map(
    lambda tail: f"https://example.com/{tail}/100x100bb.jpg"
)

_decodable_response_bodies = st.fixed_dictionaries(
    {
        "collectionId": _json_values,
        "releaseDate": st.one_of(_date_like, _json_values),
        "trackCount": _json_values,
        "artworkUrl100": st.one_of(_url_like, _json_values),
    }
).map(
    lambda overrides: fake_body(
        {"resultCount": 1, "results": [matching_result(**overrides)]}
    )
)


class ArtworkUrlPropertyTest(unittest.TestCase):
    """The never-crash invariant, over arbitrary response bodies."""

    @given(st.one_of(st.binary(max_size=2048), _decodable_response_bodies))
    def test_fuzz_artwork_url(self, body: bytes) -> None:
        # Whatever the endpoint returns (truncated JSON, HTML, a proxy error page,
        # binary noise, or a well-formed response whose field values are hostile),
        # the caller gets a URL or None, never an exception. A cover that cannot be
        # resolved is a normal outcome. And whatever is returned satisfies the recording
        # rule: a URL the page may put in an `<img src>`. Asserting the rule here, not
        # merely `str`, is what makes this property notice the rule's call site being
        # removed: the generated bodies carry hostile thumbnails on nearly every draw.
        result = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertTrue(result.url is None or _is_recordable_url(result.url))


# Printable text mixed with a deliberate chance of a lone surrogate: the class of
# input the folder scan, run off macOS, can hand artwork_url via a lossy filename
# decode (surrogateescape), and specifically the class the response-body-only fuzz
# target above cannot reach. Hypothesis's default st.text() never generates a lone
# surrogate on its own (verified: 2000 samples, zero hits), so proving the
# never-raises contract over this input space needs it explicitly in the alphabet,
# not merely a wide codepoint range.
_hazardous_text = st.text(
    alphabet=st.one_of(
        st.characters(min_codepoint=0x20, max_codepoint=0x10FFFF),
        st.characters(min_codepoint=0xD800, max_codepoint=0xDFFF),
    ),
    max_size=40,
)

_ref_strategy = st.builds(
    AlbumRef,
    artist=_hazardous_text,
    album=_hazardous_text,
    added=st.just(dt.date(2026, 8, 30)),  # never touches the encoding path
)


class ArtworkUrlAlbumRefPropertyTest(unittest.TestCase):
    """The never-crash invariant, over arbitrary AlbumRef artist/album text.

    A second, deliberately differently-named property test rather than a second
    `# fuzz-target`-matching `test_fuzz_artwork_url`: a marker pairs with exactly one
    `test_fuzz_<name>`, and a second one sharing that prefix without a matching
    marker of its own would read as an orphan property test.
    `artwork_url` has exactly one `# fuzz-target` marker either way, already satisfied
    by `test_fuzz_artwork_url` above; this class extends the input space that marker's
    contract is proven over, not the marker count.
    """

    @given(_ref_strategy, st.binary(max_size=256))
    def test_the_never_raises_contract_holds_over_arbitrary_album_ref_fields(
        self, ref: AlbumRef, body: bytes
    ) -> None:
        result = artwork_url(ref, fetch=lambda _u, _t: body)
        self.assertTrue(result.url is None or isinstance(result.url, str))


def _grouped_suite() -> unittest.TestSuite:
    """Every test in tests/, discovered by walking the directory."""
    loader = unittest.TestLoader()
    suite_dir = Path(__file__).resolve().parent / "tests"
    return loader.discover(
        str(suite_dir), pattern="test_*.py", top_level_dir=str(suite_dir)
    )


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """unittest's hook: fold every suite in tests/ in beside this file's own tests.

    That is the matcher's suite and the live boundary suite. Their files are unpaired
    to any subject, so the gate prunes tests/ and reaches it only through this hook:
    without it neither suite would run under the gate at all, and
    `src/artwork_match.py` would be measured without its own tests.
    """
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
