"""One live call against the real Wikidata Action API.

Every other test in this folder fakes `fetch`, which proves the parsing but not the
contract: a renamed response key, a moved endpoint, a changed User-Agent policy, or a
property that no longer means what it meant would leave all of them green. This file is
the only thing here that would notice, which requires it to fail, not skip, when that
happens.

**Unavailable and broken are separated deliberately, and the split is the point.**
`directors_for` never raises: an unreachable service, a rate limit, and a Wikidata
schema change all arrive at the caller as a plain `None`, indistinguishable from inside
that function. So availability is decided separately, by a probe that calls the
module's raw `_real_fetch` seam, the one thing here that does not swallow a failure:

* a transport failure (DNS, refused connection, timeout) -> skip: no network here.
* `HTTP 429` / `HTTP 503` -> skip: the service is rate-limiting or down.
* **any other status, including `403`, does not skip.** A 403 is precisely what a
  changed User-Agent policy looks like, and such a rejection reads like a network
  fault. Falling through to the assertion turns it into a loud failure instead.

**The probe runs twice, and the second one is not redundant.** Measured live while this
tool was written: the Action API rate-limits too, answering `429` with
`x-envoy-ratelimited: true` and `Retry-After: 24` after enough requests in a window,
and one lookup is three requests, so the limit can arrive *between* them, long after
`setUp`'s probe passed. Re-establishing the precondition before calling a `None` a
broken contract is what keeps that from being reported as "the contract moved".

`WIKIDATA_DIRECTOR_OFFLINE=1` skips the whole class, for a host with no network at all.
That is a convenience for running the rest of the suite, not a way to satisfy
a coverage gate; see `../wikidata_director.md`.
"""

from __future__ import annotations

import os
import sys
import unittest
import urllib.error
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))

import wikidata_director  # noqa: E402 - import follows the sys.path bootstrap above

OFFLINE = os.environ.get("WIKIDATA_DIRECTOR_OFFLINE") == "1"

#: Statuses that mean "the service is not answering right now", as opposed to "the
#: contract moved". Only these two skip; every other status falls through and fails.
_UNAVAILABLE_STATUSES = frozenset({429, 503})

#: The probe and the contract call share this bound. Generous on purpose: the Action
#: API is normally sub-second, and a slow answer is not a broken one; a tight timeout
#: here would report a working service as a moved contract.
_PROBE_TIMEOUT_SECONDS = 20.0

#: TMDB id 12 is Finding Nemo, a co-directed film, so the live call exercises the
#: multi-director join and not only the single-name path. Andrew Stanton is the stable
#: fact asserted below; the id and the name are public film credits, not identifiers of
#: anyone using this tool.
_KNOWN_TMDB_ID = "12"
_KNOWN_DIRECTOR = "Andrew Stanton"

#: No film carries this TMDB id, so the search step legitimately finds no item.
_ABSENT_TMDB_ID = "999999999"


def _unavailable_reason() -> str | None:
    """A skip reason when Wikidata is not answering, else None; see the module docstring.

    Calls `_real_fetch` directly, not `directors_for`: the latter swallows every
    failure by contract, so probing through it could never tell "unavailable" from
    "answering, but the contract moved", the entire distinction this file exists to
    make.
    """
    try:
        wikidata_director._real_fetch(
            wikidata_director.search_url(_KNOWN_TMDB_ID), _PROBE_TIMEOUT_SECONDS
        )
    except urllib.error.HTTPError as exc:
        # Closed here, not left to the collector: an HTTPError owns the error page's
        # open body. `directors_for` closes the ones it discards (`_api_object`); this
        # probe calls the raw seam, so it owns this one.
        code = exc.code
        exc.close()
        if code in _UNAVAILABLE_STATUSES:
            return f"Wikidata answered HTTP {code} - the service is unavailable"
        # Deliberately not a skip: see the module docstring on 403.
        return None
    except OSError as exc:
        return f"the Wikidata Action API is unreachable: {exc}"
    return None


@unittest.skipIf(OFFLINE, "WIKIDATA_DIRECTOR_OFFLINE=1 - live Wikidata lookup skipped")
class LiveWikidataTest(unittest.TestCase):
    """Exercise the real Action API once, so a moved contract is caught."""

    def setUp(self) -> None:
        """Skip only for genuine unavailability, decided independently of the subject."""
        reason = _unavailable_reason()
        if reason is not None:
            self.skipTest(reason)

    def test_a_known_film_resolves_to_its_director(self) -> None:
        names = wikidata_director.directors_for(
            _KNOWN_TMDB_ID, timeout=_PROBE_TIMEOUT_SECONDS
        )
        if names is None:
            # Re-establish the precondition before calling this a broken contract:
            # the limit can arrive between the three requests one lookup makes, long
            # after setUp's probe passed. See the module docstring.
            reason = _unavailable_reason()
            if reason is not None:
                self.skipTest(reason)
        self.assertIsNotNone(
            names,
            "Wikidata is answering but directors_for returned None; the search / "
            "wbgetclaims / wbgetentities contract has moved, or the User-Agent policy "
            "has changed",
        )
        assert names is not None  # narrows the type; assertIsNotNone does not
        self.assertIsInstance(names, str)
        self.assertIn(_KNOWN_DIRECTOR, names)

    def test_a_film_wikidata_does_not_carry_resolves_to_no_director(self) -> None:
        # The negative path, live: a missing director is a normal outcome, not an
        # error, and must not raise against the real service. A rate limit produces
        # the same None here, which is why this assertion is deliberately the weaker
        # of the two: the test above is the one that proves the contract.
        self.assertIsNone(
            wikidata_director.directors_for(
                _ABSENT_TMDB_ID, timeout=_PROBE_TIMEOUT_SECONDS
            )
        )


if __name__ == "__main__":
    unittest.main()
