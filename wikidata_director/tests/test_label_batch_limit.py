"""Grouped suite: the labels request never asks for more ids than the API answers.

`wbgetentities` takes at most fifty ids per request from a client without the bot
right. A film with more distinct director statements than that is not a credit this
tool can render whole, and a partial credit is worse than none (the ruling behind
`languagefallback`, in wikidata_director.md), so `directors_for` answers None for it
without making the labels request at all.

`fetch` is injected here exactly as it is in the parent suite, so nothing in this file
reaches the network.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))

import wikidata_director  # noqa: E402 - import follows the sys.path bootstrap above

#: Restated from the API's documentation rather than read off the module: a test
#: that derived it from the subject could not catch the subject asking for more.
_API_IDS_PER_REQUEST = 50


def _recording_fetch(
    qids: list[str],
) -> tuple[wikidata_director.Fetcher, list[str]]:
    """A fetch answering a film whose director statements name `qids`, in order.

    Every URL asked for is appended to the returned list, so a test can assert which
    requests were made and not only what came back.
    """
    urls: list[str] = []
    pages: dict[str, object] = {
        "list=search": {"query": {"search": [{"title": "Q1"}]}},
        "wbgetclaims": {
            "claims": {
                "P57": [
                    {"mainsnak": {"datavalue": {"value": {"id": qid}}}} for qid in qids
                ]
            }
        },
        "wbgetentities": {
            "entities": {
                qid: {"labels": {"en": {"value": f"Director {qid}"}}} for qid in qids
            }
        },
    }

    def fetch(url: str, _timeout: float) -> bytes:
        urls.append(url)
        for fragment, payload in pages.items():
            if fragment in url:
                return json.dumps(payload).encode("utf-8")
        raise AssertionError(f"unexpected url: {url}")

    return fetch, urls


def _distinct(count: int) -> list[str]:
    """`count` distinct QIDs."""
    return [f"Q{n}" for n in range(1, count + 1)]


def _labels_requests(urls: list[str]) -> list[str]:
    """The labels requests among `urls`."""
    return [url for url in urls if "wbgetentities" in url]


class LabelBatchLimitTest(unittest.TestCase):
    def test_the_limit_is_the_apis_documented_one(self) -> None:
        self.assertEqual(wikidata_director.LABELS_PER_REQUEST, _API_IDS_PER_REQUEST)

    def test_one_more_director_than_a_request_takes_is_no_director(self) -> None:
        # No labels request either: the API would answer an error for the whole
        # batch, and a batch trimmed to fit would be the partial credit refused.
        fetch, urls = _recording_fetch(_distinct(_API_IDS_PER_REQUEST + 1))
        self.assertIsNone(wikidata_director.directors_for("12", fetch=fetch))
        self.assertEqual(_labels_requests(urls), [])
        self.assertEqual(len(urls), 2, "the search and the claims request, no more")

    def test_exactly_as_many_as_a_request_takes_still_asks_for_labels(self) -> None:
        fetch, urls = _recording_fetch(_distinct(_API_IDS_PER_REQUEST))
        names = wikidata_director.directors_for("12", fetch=fetch)
        assert names is not None, "the boundary is inclusive"
        self.assertEqual(
            names.count(wikidata_director.DIRECTOR_SEPARATOR),
            _API_IDS_PER_REQUEST - 1,
        )
        self.assertEqual(len(_labels_requests(urls)), 1)

    def test_repeated_statements_count_once_toward_the_limit(self) -> None:
        # Fifty distinct ids named twice over is a hundred statements and still one
        # request: the limit is on distinct ids, and the dedup runs first.
        fetch, urls = _recording_fetch(_distinct(_API_IDS_PER_REQUEST) * 2)
        self.assertIsNotNone(wikidata_director.directors_for("12", fetch=fetch))
        self.assertEqual(len(_labels_requests(urls)), 1)


if __name__ == "__main__":
    unittest.main()
