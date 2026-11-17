"""Grouped suite: `artwork_url`'s never-raise contract, as Hypothesis properties.

One property over arbitrary and hostile-but-decodable response bodies, and one over
arbitrary `AlbumRef` artist and album text. The profile below is registered here,
beside the `@given` tests it governs, before any class body.
"""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cover_art import _is_recordable_url, artwork_url  # noqa: E402 - position only; keep below the bootstrap above
from cover_art import AlbumRef  # noqa: E402 - position only; keep below the bootstrap above
from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402 - position only; keep below the bootstrap above
from _offline_guard import setUpModule, tearDownModule  # noqa: E402, F401 - the bootstrap above; unittest calls the hooks by name
from _artwork_url_fixtures import REF, fake_body, matching_result  # noqa: E402 - beside this module, via the loader's top_level_dir

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
# cache, not a slow strategy. tests/test_hypothesis_profile_order.py reads this file's
# syntax tree to hold the load above the class bodies. The in-suite run keeps its
# default randomization, which the acceptance figures recorded at
# `_decodable_response_bodies` below were measured under.
# deadline=None: a per-example wall-clock deadline measures machine load, not
# correctness, so a busy machine would report a passing property as flaky.
settings.register_profile(
    "cover_art_properties",
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
settings.load_profile("cover_art_properties")


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
# pick_album's own suite in tests/, by the different-album example test in
# test_artwork_url.py, and by the ref-side property test below. The gap was never the
# match; it was everything the parse does to a field's value afterward.
#
# Acceptance, measured rather than eyeballed: each real defect was reverted in turn and
# this property run 10 times, each against a fresh example database so every run is an
# independent discovery rather than a replay. The year guard is caught 9/10, the dedup
# key 10/10, against 1/5 for the year guard when the hazard was sampled for rather
# than constructed by `_adversarial_year`. The two deterministic tests in
# test_artwork_url.py (the superscript year and the unhashable collectionId) are what
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
