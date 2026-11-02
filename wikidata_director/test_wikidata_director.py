"""Unit tests for the keyless Wikidata director lookup, plus the tests/ aggregator.

Every test here drives an injected `fetch`: no unit test reaches the network. The
fake is keyed on a URL fragment rather than on call order, so a test fails loudly if
the module ever asks for a URL it did not expect; call-order keying would silently
accept a reordered or dropped request.

The malformed-payload family below is not defensive padding. Wikidata's JSON is
third-party, untrusted, structured input arriving over the network: every level of it
(`query`, `search`, `claims`, `mainsnak`, `datavalue`, `labels`) can be absent or the
wrong type on an error page, a maintenance response, or a future schema change, and
`directors_for`'s whole contract is that none of those raises. Values that arrive
well-formed and are still unusable, and `test_fuzz_directors_for`, which proves the
contract over the input space rather than over these hand-picked paths, live in
`tests/test_never_raises.py`.

`load_tests` at the bottom folds in the grouped `tests/` suite, which is what makes the
live-boundary and CLI suites part of the real run and part of this folder's one
coverage unit: a coverage run that prunes a `tests/` dir from its own walk reports it
as skipped unless this aggregator reaches it.
"""

from __future__ import annotations

import email.message
import http.client
import json
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wikidata_director  # noqa: E402 - import follows the sys.path bootstrap above
from wikidata_director import (  # noqa: E402 - same bootstrap
    Fetcher,
    directors_for,
)

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"

# Restated, not derived: a floor computed from discovery would compare discovery
# against itself and pass for an empty suite.
_MIN_TESTS_BY_FILE: dict[str, int] = {
    "test_wikidata_director_cli.py": 9,
    # Always at least 1: the live-boundary class is loaded (and counted) regardless of
    # whether WIKIDATA_DIRECTOR_OFFLINE, or an unreachable service, later skips it at
    # run time: discovery happens before either skip decision.
    "test_live_wikidata.py": 1,
    "test_label_batch_limit.py": 4,
    "test_never_raises.py": 4,
    "test_tmdb_id_guard.py": 4,
}
# This module's own tests, kept apart from the grouped floors rather than summed into one
# total: a combined number carries grouped growth as slack, and a deleted test here would
# vanish inside it. Restated rather than counted off the classes below (an introspected
# count falls by one with the deletion it exists to catch), and exact rather than a floor,
# since under ``>=`` each added test becomes room a later deletion can hide in. Adding or
# removing a test in this module means changing this number.
_OWN_GUARD_TESTS = 27

#: An empty header set for the synthetic HTTPErrors below. `HTTPError` is typed as
#: taking an `email.message.Message`, and a bare `{}` would need a type: ignore.
_NO_HEADERS = email.message.Message()

# The real payload shapes, reduced to the fields this module reads. TMDB id 12 is
# Finding Nemo: co-directors are not a rare edge case, so the primary fixture has two
# and the join runs on the main path rather than on an exotic one.
_NEMO_QID = "Q132863"
_STANTON_QID = "Q347395"
_UNKRICH_QID = "Q3772"


def _fake_fetch(pages: dict[str, object]) -> Fetcher:
    """A `fetch` that answers each URL from the first fragment it contains.

    An unmatched URL raises `AssertionError`, which `directors_for` does not catch,
    so an unexpected request surfaces as a test error rather than as a quiet `None`.
    """

    def fetch(url: str, _timeout: float) -> bytes:
        for fragment, payload in pages.items():
            if fragment in url:
                return json.dumps(payload).encode("utf-8")
        raise AssertionError(f"unexpected url: {url}")

    return fetch


def _search_hit(qid: str) -> dict[str, object]:
    """A `list=search` payload whose single hit is `qid`."""
    return {"query": {"search": [{"title": qid}]}}


def _claims(*qids: str) -> dict[str, object]:
    """A `wbgetclaims` payload naming `qids` as the film's P57 values."""
    return {
        "claims": {
            "P57": [{"mainsnak": {"datavalue": {"value": {"id": qid}}}} for qid in qids]
        }
    }


def _labels(**names: str) -> dict[str, object]:
    """A `wbgetentities` payload mapping each QID keyword to an English label."""
    return {
        "entities": {
            qid: {"labels": {"en": {"value": name}}} for qid, name in names.items()
        }
    }


def _nemo_fetch() -> Fetcher:
    """The fully-populated three-request answer for TMDB id 12."""
    return _fake_fetch(
        {
            "list=search": _search_hit(_NEMO_QID),
            "wbgetclaims": _claims(_STANTON_QID, _UNKRICH_QID),
            "wbgetentities": _labels(
                **{_STANTON_QID: "Andrew Stanton", _UNKRICH_QID: "Lee Unkrich"}
            ),
        }
    )


class DirectorsForTest(unittest.TestCase):
    def test_two_directors_are_joined_and_sorted(self) -> None:
        # Sorted because Wikidata does not guarantee statement order: the claims
        # payload above lists Stanton first, but the record must not depend on that.
        self.assertEqual(
            directors_for("12", fetch=_nemo_fetch()), "Andrew Stanton & Lee Unkrich"
        )

    def test_the_join_does_not_reuse_the_cards_own_separator(self) -> None:
        # The card separates its fields with a middle dot, so a co-directed film
        # joined with one would read as two separate card fields.
        self.assertNotIn("·", wikidata_director.DIRECTOR_SEPARATOR)

    def test_statement_order_does_not_change_the_answer(self) -> None:
        reversed_claims = _fake_fetch(
            {
                "list=search": _search_hit(_NEMO_QID),
                "wbgetclaims": _claims(_UNKRICH_QID, _STANTON_QID),
                "wbgetentities": _labels(
                    **{_STANTON_QID: "Andrew Stanton", _UNKRICH_QID: "Lee Unkrich"}
                ),
            }
        )
        self.assertEqual(
            directors_for("12", fetch=reversed_claims), "Andrew Stanton & Lee Unkrich"
        )

    def test_one_director_is_returned_alone(self) -> None:
        fetch = _fake_fetch(
            {
                "list=search": _search_hit("Q133654"),
                "wbgetclaims": _claims("Q54085"),
                "wbgetentities": _labels(Q54085="David Fincher"),
            }
        )
        self.assertEqual(directors_for("550", fetch=fetch), "David Fincher")

    def test_a_director_named_twice_is_named_once(self) -> None:
        # Wikidata can carry the same value twice under different qualifiers or
        # ranks; "X & X" would be a visibly wrong card line.
        fetch = _fake_fetch(
            {
                "list=search": _search_hit("Q1"),
                "wbgetclaims": _claims(_STANTON_QID, _STANTON_QID),
                "wbgetentities": _labels(**{_STANTON_QID: "Andrew Stanton"}),
            }
        )
        self.assertEqual(directors_for("12", fetch=fetch), "Andrew Stanton")

    def test_no_wikidata_item_is_no_director(self) -> None:
        fetch = _fake_fetch({"list=search": {"query": {"search": []}}})
        self.assertIsNone(directors_for("999999999", fetch=fetch))

    def test_an_item_with_no_director_statement_is_no_director(self) -> None:
        fetch = _fake_fetch(
            {"list=search": _search_hit("Q1"), "wbgetclaims": {"claims": {}}}
        )
        self.assertIsNone(directors_for("12", fetch=fetch))

    def test_a_label_served_by_the_language_fallback_is_used(self) -> None:
        # What `languagefallback=1` actually returns for a `mul`-only name: the value
        # still arrives under the "en" key, annotated with the language it came from.
        # This is the regression fixture for the dropped-co-director defect.
        fetch = _fake_fetch(
            {
                "list=search": _search_hit("Q83495"),
                "wbgetclaims": _claims("Q9545711"),
                "wbgetentities": {
                    "entities": {
                        "Q9545711": {
                            "labels": {
                                "en": {
                                    "value": "Lana Wachowski",
                                    "language": "mul",
                                    "for-language": "en",
                                }
                            }
                        }
                    }
                },
            }
        )
        self.assertEqual(directors_for("603", fetch=fetch), "Lana Wachowski")

    def test_a_director_with_no_label_at_all_is_dropped(self) -> None:
        fetch = _fake_fetch(
            {
                "list=search": _search_hit("Q1"),
                "wbgetclaims": _claims("Q2"),
                "wbgetentities": {"entities": {"Q2": {"labels": {}}}},
            }
        )
        self.assertIsNone(directors_for("12", fetch=fetch))

    def test_a_non_json_body_is_no_director(self) -> None:
        def fetch(_url: str, _timeout: float) -> bytes:
            return b"<html>Wikimedia is having a moment</html>"

        self.assertIsNone(directors_for("12", fetch=fetch))

    def test_a_network_failure_is_no_director(self) -> None:
        def fetch(_url: str, _timeout: float) -> bytes:
            raise OSError("name or service not known")

        self.assertIsNone(directors_for("12", fetch=fetch))

    def test_a_protocol_failure_is_no_director(self) -> None:
        def fetch(_url: str, _timeout: float) -> bytes:
            raise http.client.HTTPException("truncated response")

        self.assertIsNone(directors_for("12", fetch=fetch))

    def test_an_unavailable_service_is_no_director_not_an_error(self) -> None:
        # 429 is the one measured in the field: WDQS rate-limited to 1 req/min during
        # a declared outage. 503 is the same class. Neither may reach the caller as an
        # exception: a missing director is a normal outcome.
        for status in (429, 503):
            with self.subTest(status=status):

                def fetch(url: str, _timeout: float, _status: int = status) -> bytes:
                    raise urllib.error.HTTPError(
                        url, _status, "unavailable", _NO_HEADERS, None
                    )

                self.assertIsNone(directors_for("12", fetch=fetch))

    def test_an_http_error_response_is_closed_rather_than_leaked(self) -> None:
        # An HTTPError owns the error page's open body (see `_api_object`). Asserted
        # on the stream directly rather than by catching the ResourceWarning a leak
        # raises, which would depend on when the collector happens to run.
        raised: list[urllib.error.HTTPError] = []

        def fetch(url: str, _timeout: float) -> bytes:
            error = urllib.error.HTTPError(url, 429, "unavailable", _NO_HEADERS, None)
            raised.append(error)
            raise error

        self.assertIsNone(directors_for("12", fetch=fetch))
        self.assertTrue(raised[0].fp.closed, "the HTTP error response was left open")


class MalformedPayloadTest(unittest.TestCase):
    """Every level of the three payloads, absent or the wrong type, yields None."""

    def _answering(self, **pages: object) -> Fetcher:
        return _fake_fetch(dict(pages))

    def test_a_search_payload_that_is_not_an_object_is_no_item(self) -> None:
        payloads: tuple[object, ...] = (
            [],
            "nope",
            {"query": []},
            {"query": {"search": {}}},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                fetch = _fake_fetch({"list=search": payload})
                self.assertIsNone(directors_for("12", fetch=fetch))

    def test_a_search_hit_without_a_usable_title_is_no_item(self) -> None:
        hits: tuple[object, ...] = ([], {}, {"title": 7}, {"title": ""})
        for hit in hits:
            with self.subTest(hit=hit):
                fetch = _fake_fetch({"list=search": {"query": {"search": [hit]}}})
                self.assertIsNone(directors_for("12", fetch=fetch))

    def test_a_malformed_claims_payload_is_no_director(self) -> None:
        payloads: tuple[object, ...] = (
            [],
            {"claims": []},
            {"claims": {"P57": {}}},
            {"claims": {"P57": ["not a statement"]}},
            {"claims": {"P57": [{}]}},
            {"claims": {"P57": [{"mainsnak": []}]}},
            {"claims": {"P57": [{"mainsnak": {"datavalue": []}}]}},
            {"claims": {"P57": [{"mainsnak": {"datavalue": {"value": []}}}]}},
            {"claims": {"P57": [{"mainsnak": {"datavalue": {"value": {"id": 7}}}}]}},
            {"claims": {"P57": [{"mainsnak": {"datavalue": {"value": {"id": ""}}}}]}},
        )
        for claims in payloads:
            with self.subTest(claims=claims):
                fetch = self._answering(
                    **{"list=search": _search_hit("Q1"), "wbgetclaims": claims}
                )
                self.assertIsNone(directors_for("12", fetch=fetch))

    def test_a_malformed_labels_payload_is_no_director(self) -> None:
        payloads: tuple[object, ...] = (
            [],
            {"entities": []},
            {"entities": {"Q2": []}},
            {"entities": {"Q2": {"labels": []}}},
            {"entities": {"Q2": {"labels": {"en": []}}}},
            {"entities": {"Q2": {"labels": {"en": {"value": 7}}}}},
            {"entities": {"Q2": {"labels": {"en": {"value": ""}}}}},
            {"entities": {}},
        )
        for labels in payloads:
            with self.subTest(labels=labels):
                fetch = self._answering(
                    **{
                        "list=search": _search_hit("Q1"),
                        "wbgetclaims": _claims("Q2"),
                        "wbgetentities": labels,
                    }
                )
                self.assertIsNone(directors_for("12", fetch=fetch))


class UrlBuildingTest(unittest.TestCase):
    def test_the_search_url_asks_for_the_tmdb_film_statement(self) -> None:
        url = wikidata_director.search_url("12")
        self.assertIn("list=search", url)
        self.assertIn("haswbstatement%3AP4947%3D12", url)

    def test_the_search_url_percent_encodes_the_id(self) -> None:
        # The id arrives from a third-party feed; nothing may let it reshape the URL.
        url = wikidata_director.search_url("12&action=delete")
        self.assertNotIn("&action=delete", url)

    def test_the_claims_url_asks_only_for_the_director_property(self) -> None:
        url = wikidata_director.claims_url("Q132863")
        self.assertIn("action=wbgetclaims", url)
        self.assertIn("property=P57", url)
        self.assertIn("entity=Q132863", url)

    def test_the_labels_url_joins_the_ids_and_asks_for_english_labels(self) -> None:
        url = wikidata_director.labels_url(["Q1", "Q2"])
        self.assertIn("action=wbgetentities", url)
        self.assertIn("Q1%7CQ2", url)
        self.assertIn("props=labels", url)
        self.assertIn("languages=en", url)

    def test_the_labels_url_asks_for_the_language_fallback(self) -> None:
        # Measured, not defensive: without the fallback, The Matrix resolved to
        # "Lilly Wachowski" alone; see `labels_url`.
        self.assertIn("languagefallback=1", wikidata_director.labels_url(["Q1"]))

    def test_every_url_asks_for_json(self) -> None:
        # The Action API defaults to its own HTML-ish "jsonfm" help page in a browser
        # context; without format=json the parser would be handed markup.
        for url in (
            wikidata_director.search_url("12"),
            wikidata_director.claims_url("Q1"),
            wikidata_director.labels_url(["Q1"]),
        ):
            with self.subTest(url=url):
                self.assertIn("format=json", url)


class RequestBuildingTest(unittest.TestCase):
    def test_the_request_carries_a_descriptive_user_agent(self) -> None:
        # Wikimedia blocks a request with no descriptive User-Agent, and that
        # rejection arrives looking like a network fault. Constructing the request is
        # separated from sending it precisely so this is provable with no network.
        request = wikidata_director._build_request("https://example.invalid/")
        agent = request.get_header("User-agent")
        self.assertIsInstance(agent, str)
        assert agent is not None
        self.assertIn("/", agent, "a product identifier needs a version")
        self.assertNotIn("Python-urllib", agent)


def _grouped_suite() -> unittest.TestSuite:
    """Every test in tests/, discovered by walking the directory."""
    loader = unittest.TestLoader()
    return loader.discover(
        str(_SUITE_DIR), pattern="test_*.py", top_level_dir=str(_SUITE_DIR)
    )


def _tests_by_module(suite: unittest.TestSuite) -> dict[str, int]:
    """Flatten a (possibly nested) suite into a per-module test count."""
    counts: dict[str, int] = {}
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for module, count in _tests_by_module(item).items():
                counts[module] = counts.get(module, 0) + count
        else:
            filename = f"{type(item).__module__}.py"
            counts[filename] = counts.get(filename, 0) + 1
    return counts


class GroupedSuiteDiscoveryTest(unittest.TestCase):
    """Fail loudly when this module's real test load is missing a grouped suite.

    Deliberately loads the module the way a bare run or a coverage runner does, rather
    than calling `_grouped_suite()` directly: a guard that called the helper would share its
    subject's failure mode, staying green after `load_tests` was deleted, which is
    the very change that silently stops every suite under `tests/` from running.
    """

    def test_module_load_includes_the_grouped_suites(self) -> None:
        loaded = _tests_by_module(
            unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        )
        own_found = loaded.pop(f"{__name__}.py", 0)
        self.assertEqual(
            own_found,
            _OWN_GUARD_TESTS,
            f"this module contributed {own_found} of its own tests, expected exactly "
            f"{_OWN_GUARD_TESTS}: a guard above may have been deleted, or a new one "
            f"added without raising _OWN_GUARD_TESTS",
        )
        # Derived from the per-file floors rather than restated as a second total: that
        # dict is a written-down literal, not a count taken from discovery, so summing it
        # still measures the suite against a number from outside it.
        grouped_found = sum(loaded.values())
        grouped_floor = sum(_MIN_TESTS_BY_FILE.values())
        self.assertGreaterEqual(
            grouped_found,
            grouped_floor,
            f"loading this module yielded {grouped_found} grouped tests, expected at "
            f"least {grouped_floor}: load_tests may have been removed or renamed, "
            f"silently dropping a tests/ suite from the real run",
        )

    def test_grouped_suite_meets_its_own_per_file_floor(self) -> None:
        found_by_file = _tests_by_module(_grouped_suite())
        # The full key set both ways: a new tests/test_*.py with no floor entry would
        # otherwise be silently unfloored, and a mapped file that no longer exists
        # would leave a floor nothing measures.
        self.assertEqual(
            found_by_file.keys(),
            _MIN_TESTS_BY_FILE.keys(),
            "tests/ discovery and _MIN_TESTS_BY_FILE disagree on which files exist: "
            f"found {sorted(found_by_file)}, mapped {sorted(_MIN_TESTS_BY_FILE)}",
        )
        for filename, floor in _MIN_TESTS_BY_FILE.items():
            found = found_by_file.get(filename, 0)
            self.assertGreaterEqual(
                found,
                floor,
                f"tests/{filename} yielded {found} tests, expected at least {floor}",
            )


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """unittest's hook: fold the grouped suite in beside this file's own tests."""
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
