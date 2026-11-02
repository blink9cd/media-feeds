"""Grouped suite: what a redirect may carry and where it may go, proven by calling the
redirect handler directly, so no connection is opened.

urllib's stock redirect handler copies every header in `Request.headers` onto the next
hop, whichever host or scheme that is, and the https rule at the sink only sees the
first hop. So a caller's credential could follow a redirect to another host, or over
plain http, unless this module stops it.
"""

from __future__ import annotations

import http.client
import io
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bounded_fetch  # noqa: E402 - import follows the sys.path bootstrap above

#: What the caller sends, and what must not reach a host it did not name.
_CALLER_HEADERS = {"User-Agent": "probe/1.0", "Authorization": "Bearer t"}

_FOUND = 302


def _follow(
    handler: urllib.request.HTTPRedirectHandler, newurl: str
) -> urllib.request.Request | None:
    """The next hop `handler` builds for a 302 answering the caller's request."""
    request = bounded_fetch.build_request("https://example.org/x", _CALLER_HEADERS)
    return handler.redirect_request(
        request, io.BytesIO(), _FOUND, "Found", http.client.HTTPMessage(), newurl
    )


def _redirect_handler() -> urllib.request.HTTPRedirectHandler:
    """The redirect handler `build_opener` installs for the caller's headers."""
    opener = bounded_fetch.build_opener(_CALLER_HEADERS)
    # The stubs omit OpenerDirector.handlers, the installed-handler list CPython's
    # opener has always kept; reading it is the only way to see which redirect
    # handler an opener will call.
    installed: list[object] = opener.handlers  # type: ignore[attr-defined]
    found = [h for h in installed if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert len(found) == 1, found  # exactly one: ours replaces the stock handler
    return found[0]


class TestRedirects(unittest.TestCase):
    """The opener `fetch_url` uses: caller headers stay home, and http is refused."""

    def test_the_stock_handler_does_not_forward_a_caller_header(self) -> None:
        # The first half of the defense on its own: the headers are attached as
        # "unredirected", which urllib's own handler never copies to the next hop.
        hop = _follow(urllib.request.HTTPRedirectHandler(), "https://example.org/y")
        assert hop is not None  # narrows the type for mypy
        for name in _CALLER_HEADERS:
            self.assertIsNone(hop.get_header(name.capitalize()), name)

    def test_a_redirect_to_another_host_does_not_forward_a_caller_header(self) -> None:
        hop = _follow(_redirect_handler(), "https://elsewhere.example.net/x")
        assert hop is not None  # narrows the type for mypy
        self.assertEqual(hop.full_url, "https://elsewhere.example.net/x")
        for name in _CALLER_HEADERS:
            self.assertIsNone(hop.get_header(name.capitalize()), name)

    def test_a_redirect_to_another_port_does_not_forward_a_caller_header(self) -> None:
        # The origin is scheme, host AND port: another port can be another service.
        hop = _follow(_redirect_handler(), "https://example.org:8443/x")
        assert hop is not None  # narrows the type for mypy
        for name in _CALLER_HEADERS:
            self.assertIsNone(hop.get_header(name.capitalize()), name)

    def test_a_redirect_within_the_same_origin_keeps_the_callers_headers(self) -> None:
        # A server moving a path on its own host (a trailing slash, a renamed feed)
        # still gets the User-Agent it requires, as before the move to this library.
        hop = _follow(_redirect_handler(), "https://EXAMPLE.org/x/")
        assert hop is not None  # narrows the type for mypy
        self.assertEqual(hop.get_header("User-agent"), "probe/1.0")
        self.assertEqual(hop.get_header("Authorization"), "Bearer t")

    def test_a_redirect_to_http_is_refused(self) -> None:
        for newurl in ("http://example.org/x", "http://elsewhere.example.net/x"):
            with self.subTest(newurl=newurl):
                with self.assertRaises(ValueError) as caught:
                    _follow(_redirect_handler(), newurl)
                # The refusal names the rule, never the URL it refused.
                self.assertNotIn("example", str(caught.exception))

    def test_a_refused_redirect_closes_the_response_it_was_handed(self) -> None:
        # The redirect's own body is never read once the hop is refused, so the
        # handler closes it rather than leaving the socket to the collector.
        request = bounded_fetch.build_request("https://example.org/x", _CALLER_HEADERS)
        body = io.BytesIO(b"moved")
        with self.assertRaises(ValueError):
            _redirect_handler().redirect_request(
                request,
                body,
                _FOUND,
                "Found",
                http.client.HTTPMessage(),
                "http://example.org/y",
            )
        self.assertTrue(body.closed)

    def test_a_redirect_to_an_invalid_port_is_refused_without_the_url(self) -> None:
        # urlsplit parses a port lazily: reading `.port` raises its own ValueError,
        # which may quote what it could not read. It becomes the redirect refusal.
        for newurl in ("https://example.org:99999/x", "https://example.org:p0rt/x"):
            with self.subTest(newurl=newurl):
                body = io.BytesIO(b"moved")
                request = bounded_fetch.build_request(
                    "https://example.org/x", _CALLER_HEADERS
                )
                with self.assertRaises(ValueError) as caught:
                    _redirect_handler().redirect_request(
                        request,
                        body,
                        _FOUND,
                        "Found",
                        http.client.HTTPMessage(),
                        newurl,
                    )
                for leaked in ("example", "99999", "p0rt"):
                    self.assertNotIn(leaked, str(caught.exception))
                self.assertTrue(body.closed)

    def test_a_redirect_the_stock_handler_declines_is_still_declined(self) -> None:
        # A 302 answering a PUT is not followed by urllib (it raises HTTPError);
        # this module changes where a redirect may go, not which ones are followed.
        request = bounded_fetch.build_request("https://example.org/x", _CALLER_HEADERS)
        request.method = "PUT"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            _redirect_handler().redirect_request(
                request,
                io.BytesIO(),
                _FOUND,
                "Found",
                http.client.HTTPMessage(),
                "https://example.org/y",
            )
        # The error wraps the response body it was handed; closing it is ours.
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
