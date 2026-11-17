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

* a transport failure (DNS, refused connection, timeout, a truncated reply) -> skip:
  no network here.
* `HTTP 429` or any `5xx` -> skip: the service is rate-limiting or down.
* **any other status, including `403`, does not skip.** A 403 is precisely what a
  changed User-Agent policy looks like, and such a rejection reads like a network
  fault. Falling through to the assertion turns it into a loud failure instead.

**The lookup itself is classified too, not re-probed.** Measured live while this tool
was written: the Action API rate-limits, answering `429` with
`x-envoy-ratelimited: true` and `Retry-After: 24` after enough requests in a window,
and one lookup is three requests, so a limit or a `5xx` can arrive *between* them,
long after `setUp`'s probe passed. The lookup runs through `_FailureRecorder`, so a
`None` is judged by the failure actually met, with the same rule as the probe. An
earlier design re-probed the search URL instead, and an outage that had cleared by
then was reported as "the contract moved" at a landing gate.

`WIKIDATA_DIRECTOR_OFFLINE=1` skips the whole class, for a host with no network at all.
That is a convenience for running the rest of the suite, not a way to satisfy
a coverage gate; see `../wikidata_director.md`.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))

import wikidata_director  # noqa: E402 - import follows the sys.path bootstrap above

OFFLINE = os.environ.get("WIKIDATA_DIRECTOR_OFFLINE") == "1"

#: The rate-limit status. With every 5xx it means "the service is not answering right
#: now", as opposed to "the contract moved"; every 4xx but this one falls through and
#: fails, 403 above all (see the module docstring).
_RATE_LIMITED = 429
_SERVER_ERRORS = range(500, 600)

#: What a request can raise when nothing well-formed came back: a transport failure
#: (DNS, refusal, socket or body timeout, all `OSError`, `HTTPError` included) or a
#: truncated or garbled HTTP exchange. `ValueError` is deliberately absent: bounded_fetch
#: raises it for a refused redirect or an oversized body, which is the service
#: answering wrongly, not failing to answer.
_FETCH_FAILURES = (OSError, http.client.HTTPException)

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


def _outage_reason(failure: BaseException) -> str | None:
    """A skip reason when `failure` means Wikidata did not answer, else None.

    The one place the outage-or-contract line is drawn, shared by the probe and by
    the recorder the contract call runs through.
    """
    if isinstance(failure, urllib.error.HTTPError):
        code = failure.code
        if code == _RATE_LIMITED or code in _SERVER_ERRORS:
            return f"Wikidata answered HTTP {code} - the service is unavailable"
        # Deliberately not a skip: see the module docstring on 403.
        return None
    if isinstance(failure, _FETCH_FAILURES):
        return f"the Wikidata Action API is unreachable: {failure!r}"
    return None


class _FailureRecorder:
    """A `Fetcher` that remembers the last failure it re-raised.

    `directors_for` swallows every failure into None, so from outside it a 502 on the
    second of its three requests looks exactly like a renamed response key. Running
    the lookup through this wrapper keeps the failure actually met, so the verdict
    rests on it rather than on a later probe that may find the service healthy again.
    """

    def __init__(self, fetch: wikidata_director.Fetcher) -> None:
        self._fetch = fetch
        self.last_failure: BaseException | None = None

    def __call__(self, url: str, timeout: float) -> bytes:
        """Fetch through the wrapped seam, recording any failure before re-raising."""
        try:
            return self._fetch(url, timeout)
        except BaseException as failure:
            self.last_failure = failure
            raise

    def outage_reason(self) -> str | None:
        """The skip reason for the recorded failure, or None when there is no outage."""
        if self.last_failure is None:
            return None
        return _outage_reason(self.last_failure)


def _contract_message(recorder: _FailureRecorder) -> str:
    """The assertion message for a None that no outage explains."""
    met = recorder.last_failure
    seen = (
        f"the request failed with {met!r}"
        if met is not None
        else "every request answered with a well-formed reply"
    )
    return (
        f"Wikidata is answering but directors_for returned None ({seen}); the "
        "search / wbgetclaims / wbgetentities contract has moved, or the User-Agent "
        "policy has changed"
    )


def _unavailable_reason(
    fetch: wikidata_director.Fetcher = wikidata_director._real_fetch,
) -> str | None:
    """A skip reason when Wikidata is not answering, else None; see the module docstring.

    Calls the raw `fetch` seam directly, not `directors_for`: the latter swallows
    every failure by contract, so probing through it could never tell "unavailable"
    from "answering, but the contract moved", the entire distinction this file exists
    to make.
    """
    try:
        fetch(wikidata_director.search_url(_KNOWN_TMDB_ID), _PROBE_TIMEOUT_SECONDS)
    except _FETCH_FAILURES as failure:
        if isinstance(failure, urllib.error.HTTPError):
            # Closed here, not left to the collector: an HTTPError owns the error
            # page's open body. `directors_for` closes the ones it discards
            # (`_api_object`); this probe calls the raw seam, so it owns this one.
            failure.close()
        return _outage_reason(failure)
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
        recorder = _FailureRecorder(wikidata_director._real_fetch)
        names = wikidata_director.directors_for(
            _KNOWN_TMDB_ID, fetch=recorder, timeout=_PROBE_TIMEOUT_SECONDS
        )
        reason = recorder.outage_reason()
        if names is None and reason is not None:
            # An outage met mid-lookup, after setUp's probe passed: the limit or a
            # 5xx can arrive between the three requests. See the module docstring.
            self.skipTest(reason)
        self.assertIsNotNone(names, _contract_message(recorder))
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


def _http_error(code: int) -> urllib.error.HTTPError:
    """An HTTPError carrying an open body, as urllib raises one for an error status."""
    return urllib.error.HTTPError(
        "https://www.wikidata.org/w/api.php", code, "fake", Message(), io.BytesIO(b"")
    )


def _raising(failure: BaseException) -> wikidata_director.Fetcher:
    """A fetch that fails every request with `failure`."""

    def fetch(_url: str, _timeout: float) -> bytes:
        raise failure

    return fetch


class OutageClassificationTest(unittest.TestCase):
    """Fakes only: which failures read as an outage, and which as a moved contract."""

    def test_a_server_error_or_rate_limit_is_an_outage(self) -> None:
        for code in (429, 500, 502, 503, 504):
            with self.subTest(code=code):
                reason = _outage_reason(_http_error(code))
                assert reason is not None
                self.assertIn(str(code), reason)

    def test_a_client_error_is_not_an_outage(self) -> None:
        # 403 is what a changed User-Agent policy looks like: it must fail, not skip.
        for code in (400, 403, 404):
            with self.subTest(code=code):
                self.assertIsNone(_outage_reason(_http_error(code)))

    def test_a_transport_failure_is_an_outage(self) -> None:
        failures: tuple[BaseException, ...] = (
            urllib.error.URLError("name resolution failed"),
            TimeoutError("timed out"),
            ConnectionRefusedError("refused"),
            http.client.IncompleteRead(b"partial"),
            http.client.RemoteDisconnected("closed"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIsNotNone(_outage_reason(failure))

    def test_a_refused_redirect_or_oversized_body_is_not_an_outage(self) -> None:
        # bounded_fetch raises ValueError for both: the service answered, wrongly.
        self.assertIsNone(_outage_reason(ValueError("redirect to http refused")))

    def test_the_recorder_passes_a_body_through_and_records_nothing(self) -> None:
        recorder = _FailureRecorder(lambda _url, _timeout: b"{}")
        self.assertEqual(recorder("https://example.invalid/", 1.0), b"{}")
        self.assertIsNone(recorder.outage_reason())
        self.assertIsNone(recorder.last_failure)

    def test_the_recorder_records_a_failure_and_re_raises_it(self) -> None:
        failure = TimeoutError("timed out")
        recorder = _FailureRecorder(_raising(failure))
        with self.assertRaises(TimeoutError):
            recorder("https://example.invalid/", 1.0)
        self.assertIs(recorder.last_failure, failure)

    def test_an_outage_on_the_second_request_reads_as_an_outage(self) -> None:
        # The landing-gate false alarm: the search answered, then the claims request
        # met a 502. A later re-probe of the search URL alone can find the service
        # healthy again, so only the failure actually met may decide.
        search = json.dumps({"query": {"search": [{"title": "Q1"}]}}).encode()

        def fetch(url: str, _timeout: float) -> bytes:
            if "list=search" in url:
                return search
            raise _http_error(502)

        recorder = _FailureRecorder(fetch)
        self.assertIsNone(wikidata_director.directors_for("12", fetch=recorder))
        reason = recorder.outage_reason()
        assert reason is not None
        self.assertIn("502", reason)

    def test_a_well_formed_reply_of_the_wrong_shape_is_not_an_outage(self) -> None:
        # The real contract change: every request answered, and nothing parsed.
        recorder = _FailureRecorder(
            lambda _url, _timeout: json.dumps({"query": {"renamed": []}}).encode()
        )
        self.assertIsNone(wikidata_director.directors_for("12", fetch=recorder))
        self.assertIsNone(recorder.outage_reason())

    def test_the_probe_skips_on_an_outage_and_closes_the_error_body(self) -> None:
        failure = _http_error(504)
        reason = _unavailable_reason(_raising(failure))
        assert reason is not None
        self.assertIn("504", reason)
        assert failure.fp is not None
        self.assertTrue(failure.fp.closed)

    def test_the_probe_does_not_skip_on_a_client_error_or_an_answer(self) -> None:
        self.assertIsNone(_unavailable_reason(_raising(_http_error(403))))
        self.assertIsNone(_unavailable_reason(lambda _url, _timeout: b"{}"))

    def test_the_failure_message_names_what_was_met(self) -> None:
        recorder = _FailureRecorder(_raising(_http_error(403)))
        self.assertIsNone(wikidata_director.directors_for("12", fetch=recorder))
        self.assertIn("403", _contract_message(recorder))
        quiet = _FailureRecorder(lambda _url, _timeout: b"{}")
        self.assertIn("every request answered", _contract_message(quiet))


if __name__ == "__main__":
    unittest.main()
