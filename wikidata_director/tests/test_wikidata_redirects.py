"""Grouped suite: a redirect off https is never followed, so its reply is never parsed.

Every request's first hop is a fixed https endpoint, but each later hop is the server's
choice. urllib's stock opener follows a redirect onto plain `http:` or `ftp:`, and the
reply read there would be parsed as Wikidata's own, putting any text in the director
line. The transport is faked one layer below the redirect machinery: urllib's own https
and http handlers are replaced with scripted ones, so the real opener, its redirect
handler and `_real_fetch` all run, and nothing reaches a socket.
"""

from __future__ import annotations

import http.client
import io
import json
import sys
import unittest
import urllib.parse
import urllib.request
import urllib.response
from pathlib import Path
from unittest import mock

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))

import wikidata_director  # noqa: E402 - import follows the sys.path bootstrap above

#: What the downgraded hop answers each of the three requests with: one film, one
#: director, and a label that would land in the record.
_PAGES: dict[str, object] = {
    "list=search": {"query": {"search": [{"title": "Q1"}]}},
    "wbgetclaims": {
        "claims": {"P57": [{"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}]}
    },
    "wbgetentities": {"entities": {"Q2": {"labels": {"en": {"value": "Planted"}}}}},
}


def _response(url: str, code: int, header: tuple[str, str], body: bytes) -> object:
    """A response urllib's own handlers accept, as a protocol handler returns it."""
    headers = http.client.HTTPMessage()
    headers[header[0]] = header[1]
    response = urllib.response.addinfourl(io.BytesIO(body), headers, url, code)
    # The stub omits `msg`, which urllib's error processor reads off every response.
    response.msg = "Found" if code == 302 else "OK"  # type: ignore[attr-defined]
    return response


class _Wire:
    """Every https request answers 302 to the same path on plain http, which answers."""

    def __init__(self) -> None:
        """Start with no requests seen on either scheme."""
        self.https: list[str] = []
        self.http: list[str] = []

    def https_open(self, req: urllib.request.Request) -> object:
        """Record the request and redirect it to plain http on loopback."""
        self.https.append(req.full_url)
        parts = urllib.parse.urlsplit(req.full_url)
        target = f"http://127.0.0.1{parts.path}?{parts.query}"
        return _response(req.full_url, 302, ("Location", target), b"")

    def http_open(self, req: urllib.request.Request) -> object:
        """Record the request and answer with the page its query asks for."""
        self.http.append(req.full_url)
        page = next(p for key, p in _PAGES.items() if key in req.full_url)
        body = json.dumps(page).encode("utf-8")
        return _response(req.full_url, 200, ("Content-Type", "application/json"), body)


class DowngradedRedirectTest(unittest.TestCase):
    """The real seam, with urllib's protocol handlers scripted."""

    def setUp(self) -> None:
        """Install the scripted handlers, and no proxy from the environment."""
        self.wire = _Wire()
        # A bound method stored on the class is not re-bound, so urllib's call
        # `handler.https_open(req)` reaches the wire with the request alone.
        for handler, name, method in (
            (urllib.request.HTTPSHandler, "https_open", self.wire.https_open),
            (urllib.request.HTTPHandler, "http_open", self.wire.http_open),
        ):
            self.enterContext(mock.patch.object(handler, name, method))
        self.enterContext(mock.patch.object(urllib.request, "getproxies", dict))

    def test_a_lookup_redirected_onto_http_finds_no_director(self) -> None:
        director = wikidata_director.directors_for("550")
        self.assertEqual(self.wire.http, [], "a hop onto http was followed")
        self.assertEqual(len(self.wire.https), 1, "the search was never sent")
        self.assertIsNone(director)

    def test_the_request_carries_the_user_agent(self) -> None:
        # The opener is no longer urllib's default, so the header is pinned here: a
        # request without it is what Wikimedia rejects.
        seen: list[str | None] = []

        def https_open(req: urllib.request.Request) -> object:
            seen.append(req.get_header("User-agent"))
            body = json.dumps({}).encode("utf-8")
            return _response(req.full_url, 200, ("Content-Type", "text/json"), body)

        scripted = staticmethod(https_open)
        with mock.patch.object(urllib.request.HTTPSHandler, "https_open", scripted):
            self.assertIsNone(wikidata_director.directors_for("550"))
        self.assertEqual(seen, [wikidata_director._USER_AGENT])


if __name__ == "__main__":
    unittest.main()
