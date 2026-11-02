"""Grouped suite: only an id made of ASCII digits is allowed to reach the search.

`search_url` percent-encodes the id, which stops it reshaping the URL, but the value
lands inside a CirrusSearch expression, and encoding does nothing to the expression's
own grammar: `12 OR haswbstatement:P4947=550` is a valid two-film query. The id
arrives from a third-party feed, so `directors_for` refuses anything that is not a
plain decimal id before any request is made, and answers None under its never-raises
contract.

`fetch` is injected here just as it is in the parent suite, so nothing in this file
reaches the network either.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))

import wikidata_director  # noqa: E402 - import follows the sys.path bootstrap above

# The shape of the attack: a search operator and a second statement, smuggled inside
# what the feed presents as one id.
_TWO_FILM_QUERY = "12 OR haswbstatement:P4947=550"


def _explode_fetch(url: str, _timeout: float) -> bytes:
    """A fetch that fails the test if any request is made at all."""
    raise AssertionError(f"no request may be made for a refused id, got: {url}")


def _one_director_fetch(url: str, _timeout: float) -> bytes:
    """A fetch answering the three requests for a single-director film."""
    pages: dict[str, object] = {
        "list=search": {"query": {"search": [{"title": "Q1"}]}},
        "wbgetclaims": {
            "claims": {"P57": [{"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}]}
        },
        "wbgetentities": {
            "entities": {"Q2": {"labels": {"en": {"value": "A Director"}}}}
        },
    }
    for fragment, payload in pages.items():
        if fragment in url:
            return json.dumps(payload).encode("utf-8")
    raise AssertionError(f"unexpected url: {url}")


class TmdbIdGuardTest(unittest.TestCase):
    def test_a_search_operator_smuggled_into_the_id_makes_no_request(self) -> None:
        self.assertIsNone(
            wikidata_director.directors_for(_TWO_FILM_QUERY, fetch=_explode_fetch)
        )

    def test_a_non_ascii_digit_is_refused(self) -> None:
        # `str.isdigit` alone accepts a superscript two; the guard must not.
        self.assertTrue("²".isdigit(), "the premise of this test moved")
        self.assertIsNone(wikidata_director.directors_for("²", fetch=_explode_fetch))

    def test_every_other_non_decimal_shape_is_refused_without_a_request(self) -> None:
        for shape in ("", " 12", "12 ", "-1", "1.5", "0x1f", "12&action=delete"):
            with self.subTest(tmdb_id=shape):
                self.assertIsNone(
                    wikidata_director.directors_for(shape, fetch=_explode_fetch)
                )

    def test_a_decimal_id_still_resolves(self) -> None:
        self.assertEqual(
            wikidata_director.directors_for("550", fetch=_one_director_fetch),
            "A Director",
        )


if __name__ == "__main__":
    unittest.main()
