"""Grouped suite: the never-raises contract under hostile input.

These tests share one subject: a value that arrives well-formed and is still unusable.
The parent suite's malformed-payload family covers values that arrive the wrong shape.

`fetch` is injected here just as it is in the parent suite, so nothing in this file
reaches the network either.
"""

from __future__ import annotations

import json
import sys
import unittest
import urllib.parse
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))

import wikidata_director  # noqa: E402 - import follows the sys.path bootstrap above

# A registered profile loaded at import time, never an `@settings` decorator on the
# test itself. A `@given` test snapshots its settings when it is decorated, so a
# profile loaded later would be a no-op; and a longer fuzzing run re-applies its own
# settings object to each target, which Hypothesis refuses on a function that already
# carries one. Registered here, beside the only property test in this folder, so it
# holds when this module is imported on its own; in the parent aggregator it would
# hold only when that happened to be imported first. deadline=None: a per-example
# wall-clock deadline measures machine load, not correctness, so a busy machine would
# report a passing property as flaky.
# too_slow is suppressed as well: on a cold .hypothesis cache the first draw also
# pays Hypothesis's one-time setup (the unicode tables a text strategy needs, a scan
# of the local modules for constants), which under load outlasts even the limit
# left without a deadline.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

# No URL can carry an unpaired surrogate: `urlencode` encodes to UTF-8, and UTF-8 has no
# encoding for one. `json.dumps` below escapes it as `\udcff`, the same wire form a
# server would send, and a legal JSON escape that `json.loads` decodes straight back.
_LONE_SURROGATE = "\udcff"


def _answering(pages: dict[str, object]) -> wikidata_director.Fetcher:
    """A fetch answering each URL from the first fragment it contains.

    An unmatched URL raises `AssertionError`, which `directors_for` does not catch, so a
    request a test did not expect fails that test instead of passing as a quiet `None`.
    """

    def fetch(url: str, _timeout: float) -> bytes:
        for fragment, payload in pages.items():
            if fragment in url:
                return json.dumps(payload).encode("utf-8")
        raise AssertionError(f"unexpected url: {url}")

    return fetch


def _statement(qid: object) -> dict[str, object]:
    """One `P57` statement whose item id is `qid`, whatever type a payload gave it."""
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


class UnencodableValueTest(unittest.TestCase):
    """A value no URL can carry must end the lookup, not escape it as an exception.

    Each of the three request URLs is built from a value the module does not control.
    Built as an argument to `_api_object`, a URL is evaluated before that function's
    `try` is entered, so `urlencode`'s `UnicodeEncodeError` would escape
    `directors_for` even though its exception family is caught one frame further in.
    """

    def test_a_SURROGATE_in_the_tmdb_id_is_no_director_rather_than_raising(
        self,
    ) -> None:
        # Reachable with no hostile server at all: POSIX argv decodes undecodable bytes
        # with surrogateescape, so `--tmdb-id` can carry one. No page is answered,
        # because no request may be made with a value the URL could not hold.
        self.assertIsNone(
            wikidata_director.directors_for(_LONE_SURROGATE, fetch=_answering({}))
        )

    def test_a_SURROGATE_in_the_search_hits_title_is_no_director_rather_than_raising(
        self,
    ) -> None:
        # The title becomes the claims request's `entity`, so any server can reach
        # this one by answering the search with an escaped surrogate.
        fetch = _answering(
            {"list=search": {"query": {"search": [{"title": _LONE_SURROGATE}]}}}
        )
        self.assertIsNone(wikidata_director.directors_for("12", fetch=fetch))

    def test_one_unusable_director_QID_drops_the_whole_credit_not_just_that_name(
        self,
    ) -> None:
        # `labels_url` carries every director in one request, so a single unusable QID
        # abandons it and the answer is None, not the other director alone. That is
        # the conservative reading, chosen on purpose: Q2 arrived in the same claims
        # payload that just proved corrupt, so it is no better attested than the value
        # beside it, and `labels_url` records a half-correct credit as worse than
        # none. `_director_names` does keep the rest when one director is merely
        # unlabeled, but that is a gap in a well-formed response, not a corrupt one.
        # Filtering the bad QID out instead would request Q2's label below and return
        # it, which this pins against.
        fetch = _answering(
            {
                "list=search": {"query": {"search": [{"title": "Q1"}]}},
                "wbgetclaims": {
                    "claims": {"P57": [_statement("Q2"), _statement(_LONE_SURROGATE)]}
                },
                "wbgetentities": {
                    "entities": {"Q2": {"labels": {"en": {"value": "A Director"}}}}
                },
            }
        )
        self.assertIsNone(wikidata_director.directors_for("12", fetch=fetch))


# The three requests, by the `action` each URL carries. `urlencode` escapes `=` inside a
# parameter value, so no hostile id can forge this key; the lookup below is exact.
_ACTIONS = ("query", "wbgetclaims", "wbgetentities")

_SURROGATES = tuple(map(chr, range(0xD800, 0xE000)))

# A string no URL can carry, by construction: arbitrary text either side of one
# guaranteed unpaired surrogate. Hypothesis's default `st.text()` draws no surrogates at
# all, so a plain `st.text()` arm can never reach a URL builder's raise, however long
# it runs.
_unencodable_text = st.tuples(
    st.text(max_size=6), st.sampled_from(_SURROGATES), st.text(max_size=6)
).map("".join)

# A decimal id: the only shape the guard in `directors_for` lets through to the search
# request, so this arm is what keeps the three request stages inside the input space.
_decimal_id = st.integers(min_value=0, max_value=10**9).map(str)

# Every type a JSON document can hand a field, containers included.
_json_values = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(max_size=20),
        _unencodable_text,
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=8), children, max_size=3),
    ),
    max_leaves=4,
)

# An id slot holds a real-shaped QID often enough to keep the next stage reachable: a
# value that stops the lookup at the claims request can never exercise the labels URL.
_id_value = st.one_of(
    st.integers(min_value=1, max_value=10**8).map(lambda n: f"Q{n}"),
    _unencodable_text,
    _json_values,
)


@st.composite
def _decodable_pages(draw: st.DrawFn) -> dict[str, bytes]:
    """One well-formed response per request, with hostile values in every value slot.

    Per request rather than one body for all three: the lookup makes three requests of
    three different shapes, and a single shared body reaches the labels stage only if it
    happens to satisfy all three at once. Labels are keyed by the drawn ids, so a run
    that gets that far has something to label, and the join is exercised too.
    """
    ids = draw(st.lists(_id_value, max_size=3))
    labels = draw(st.lists(_json_values, min_size=len(ids), max_size=len(ids)))
    pages: dict[str, object] = {
        "query": {"query": {"search": [{"title": draw(_id_value)}]}},
        "wbgetclaims": {"claims": {"P57": [_statement(qid) for qid in ids]}},
        "wbgetentities": {
            "entities": {
                qid: {"labels": {"en": {"value": label}}}
                for qid, label in zip(ids, labels, strict=True)
                if isinstance(qid, str)
            }
        },
    }
    return {action: json.dumps(page).encode("utf-8") for action, page in pages.items()}


# Arbitrary bytes as the body of every request: the arm that covers a response that is
# not JSON at all: the decoder's own failure family.
_same_bytes_everywhere = st.binary(max_size=512).map(
    lambda body: dict.fromkeys(_ACTIONS, body)
)


class NeverRaisesPropertyTest(unittest.TestCase):
    """The never-raises contract over the input space, beyond the paths.

    One `st.binary()` body for all three requests is not enough: arbitrary bytes
    essentially never decode to JSON, so every branch past `json.loads` (all three URL
    builders among them) sits outside its input space, and the raise the tests above
    pin passes a green fuzz gate at full branch coverage. The byte arm stays for what it
    does cover; the decoded arm is what reaches the rest.

    Measured, not assumed, before the id guard in `directors_for` existed: with each URL
    site built outside `_api_object`'s `try` on its own, this property alone, run as a
    fresh randomized 200-example campaign per trial, caught every site 10 times out of
    10, and the derandomized suite route caught all three. The byte-only strategy, run
    the same way against all three sites at once, caught it 0 times in 10. The guard
    now refuses every non-decimal id before the search URL is built, so that site is
    reached only through the decimal-id arm.
    """

    @given(
        st.one_of(_same_bytes_everywhere, _decodable_pages()),
        st.one_of(st.text(max_size=32), _unencodable_text, _decimal_id),
    )
    def test_fuzz_directors_for(self, pages: dict[str, bytes], tmdb_id: str) -> None:
        def fetch(url: str, _timeout: float) -> bytes:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            return pages[query["action"][0]]

        result = wikidata_director.directors_for(tmdb_id, fetch=fetch)
        self.assertTrue(result is None or isinstance(result, str))
