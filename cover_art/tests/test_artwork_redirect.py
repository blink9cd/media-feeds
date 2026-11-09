"""A redirect off https is never followed, so a downgraded reply is never parsed.

The search's first hop is a fixed https endpoint, but every later hop is the server's
choice. urllib's stock opener follows a redirect onto plain `http:` or `ftp:`, and the
reply read there would be parsed as the store's own, choosing or nulling a cover. The
transport is faked one layer below the redirect machinery: urllib's own https and http
handlers are replaced with scripted ones, so the real opener, its redirect handler and
`_real_fetch` all run, and nothing reaches a socket.
"""

from __future__ import annotations

import datetime as dt
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

_TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TOOL_ROOT / "src"), str(_TOOL_ROOT)]

import cover_art  # noqa: E402 - import follows the sys.path bootstrap above
from artwork_redirect import HttpsOnlyRedirect  # noqa: E402 - the same bootstrap

_REF = cover_art.AlbumRef(
    artist="An Artist", album="A Record", added=dt.date(2026, 8, 30)
)

#: A reply that verifies as `_REF`'s album: what a downgraded hop would hand back.
_FOUND = {
    "resultCount": 1,
    "results": [
        {
            "artistName": "An Artist",
            "collectionName": "A Record",
            "collectionId": 1,
            "releaseDate": "1994-01-01T00:00:00Z",
            "trackCount": 10,
            "artworkUrl100": "https://example.com/a/100x100bb.jpg",
        }
    ],
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
        """Record the request and answer with the album found."""
        self.http.append(req.full_url)
        body = json.dumps(_FOUND).encode("utf-8")
        return _response(req.full_url, 200, ("Content-Type", "text/json"), body)


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

    def test_a_search_redirected_onto_http_finds_no_cover(self) -> None:
        match = cover_art.artwork_url(_REF)
        self.assertEqual(self.wire.http, [], "a hop onto http was followed")
        self.assertTrue(self.wire.https, "the search never reached the transport")
        self.assertIsNone(match.url)

    def test_an_https_hop_is_followed(self) -> None:
        request = urllib.request.Request("https://itunes.apple.com/search")
        hop = HttpsOnlyRedirect().redirect_request(
            request,
            io.BytesIO(),
            302,
            "Found",
            http.client.HTTPMessage(),
            "https://itunes.apple.com/search?x=1",
        )
        self.assertEqual(
            hop.full_url if hop else None, "https://itunes.apple.com/search?x=1"
        )

    def test_every_other_hop_is_refused_without_naming_it(self) -> None:
        request = urllib.request.Request("https://itunes.apple.com/search")
        for target in (
            "http://itunes.apple.com/secret",
            "ftp://itunes.apple.com/secret",
            "https:///secret",
            "https://[::1/secret",
        ):
            fp = io.BytesIO()
            with self.subTest(target=target), self.assertRaises(ValueError) as caught:
                HttpsOnlyRedirect().redirect_request(
                    request, fp, 302, "Found", http.client.HTTPMessage(), target
                )
            self.assertNotIn("secret", str(caught.exception))
            self.assertTrue(fp.closed)


if __name__ == "__main__":
    unittest.main()
