"""Grouped suite: the HEAD request that reports a redirect instead of following it.

`build_request`'s `method`, the no-follow opener, and `answer_head`, which turns what
that opener returns or raises into a `HeadAnswer`, are proven here without a request
being sent: the opener's error chain is driven directly, and `answer_head` is handed
fakes in place of `opener.open`.
"""

from __future__ import annotations

import email.message
import http.client
import io
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bounded_fetch  # noqa: E402 - import follows the sys.path bootstrap above

_URL = "https://example.org/tmdb/7/"
_HEADERS = {"User-Agent": "probe/1.0"}


class _ClosingResponse(io.BytesIO):
    """A response stand-in: a status, and a close the test can see."""

    def __init__(self, status: int) -> None:
        super().__init__(b"")
        self.status = status


def _http_error(code: int, location: str | None = None) -> urllib.error.HTTPError:
    """An HTTP status as urllib raises it, its Location header set when given."""
    headers = email.message.Message()
    if location is not None:
        headers["Location"] = location
    return urllib.error.HTTPError(_URL, code, "status", headers, io.BytesIO(b""))


def _returning(response: _ClosingResponse) -> bounded_fetch.HeadOpen:
    """An `open_response` stand-in that returns `response`."""

    def open_response() -> bounded_fetch.HeadResponse:
        return response

    return open_response


def _raising(error: BaseException) -> bounded_fetch.HeadOpen:
    """An `open_response` stand-in that raises `error`."""

    def open_response() -> bounded_fetch.HeadResponse:
        raise error

    return open_response


class TestBuildRequestMethod(unittest.TestCase):
    """`method` picks GET or HEAD and nothing else; the headers are the same."""

    def test_the_default_is_get(self) -> None:
        request = bounded_fetch.build_request(_URL, _HEADERS)
        self.assertEqual(request.get_method(), "GET")

    def test_head_is_a_head_with_the_callers_headers_unredirected(self) -> None:
        request = bounded_fetch.build_request(_URL, _HEADERS, method="HEAD")
        self.assertEqual(request.get_method(), "HEAD")
        self.assertEqual(request.unredirected_hdrs, {"User-agent": "probe/1.0"})
        self.assertEqual(request.headers, {})

    def test_any_other_method_is_refused(self) -> None:
        for method in ("POST", "head", "PUT", ""):
            with self.subTest(method=method):
                with self.assertRaises(ValueError):
                    bounded_fetch.build_request(_URL, _HEADERS, method=method)

    def test_head_still_refuses_a_url_the_rule_refuses(self) -> None:
        with self.assertRaises(ValueError):
            bounded_fetch.build_request("http://example.org/", _HEADERS, method="HEAD")


class TestNoFollowOpener(unittest.TestCase):
    """The opener `head_url` opens through: no cookies, and no redirect followed."""

    def setUp(self) -> None:
        self.opener = bounded_fetch.build_no_follow_opener()
        # The stubs omit OpenerDirector.handlers, the installed-handler list CPython's
        # opener has always kept; reading it is the only way to see what is installed.
        self.handlers: list[object] = self.opener.handlers  # type: ignore[attr-defined]

    def test_it_keeps_no_cookies(self) -> None:
        self.assertFalse(
            any(
                isinstance(h, urllib.request.HTTPCookieProcessor) for h in self.handlers
            )
        )

    def test_its_one_redirect_handler_builds_no_next_hop(self) -> None:
        found = [
            h
            for h in self.handlers
            if isinstance(h, urllib.request.HTTPRedirectHandler)
        ]
        self.assertEqual(len(found), 1)
        request = bounded_fetch.build_request(_URL, _HEADERS, method="HEAD")
        hop = found[0].redirect_request(
            request, io.BytesIO(), 302, "Found", http.client.HTTPMessage(), _URL + "x"
        )
        self.assertIsNone(hop)

    def test_every_redirect_status_comes_back_unparsed(self) -> None:
        # A Location urllib's own handler would parse and refuse (a broken IPv6
        # bracket raises ValueError there, a file: target its own HTTPError quoting
        # it) still comes back as the 3xx, its header exactly as sent.
        for code in (301, 302, 303, 307, 308):
            for location in ("https://[::1/x", "file:///etc/hosts"):
                with self.subTest(code=code, location=location):
                    request = bounded_fetch.build_request(_URL, _HEADERS, method="HEAD")
                    headers = http.client.HTTPMessage()
                    headers["Location"] = location
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        self.opener.error(
                            "http", request, io.BytesIO(b""), code, "Moved", headers
                        )
                    caught.exception.close()
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.headers["Location"], location)

    def test_a_302_comes_back_as_an_http_error_carrying_its_location(self) -> None:
        # The opener's real error chain, driven without a connection: the redirect
        # handler declines the hop, so the default handler raises the 3xx itself.
        request = bounded_fetch.build_request(_URL, _HEADERS, method="HEAD")
        headers = http.client.HTTPMessage()
        headers["Location"] = "/film/placeholder/"
        body = io.BytesIO(b"")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.error("http", request, body, 302, "Found", headers)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(caught.exception.headers["Location"], "/film/placeholder/")


class TestAnswerHead(unittest.TestCase):
    """What `answer_head` makes of each thing `opener.open` can return or raise."""

    def test_a_2xx_is_its_status_with_no_location_and_is_closed(self) -> None:
        for status in (200, 204):
            with self.subTest(status=status):
                response = _ClosingResponse(status)
                answer = bounded_fetch.answer_head(_returning(response))
                self.assertEqual(answer, bounded_fetch.HeadAnswer(status, None))
                self.assertTrue(response.closed)

    def test_a_3xx_is_its_status_and_location_as_sent_and_is_closed(self) -> None:
        for code, location in (
            (302, "/film/placeholder/"),
            (301, "https://example.org/film/x/"),
            (308, None),
            (300, "/elsewhere/"),
        ):
            with self.subTest(code=code):
                error = _http_error(code, location)
                answer = bounded_fetch.answer_head(_raising(error))
                self.assertEqual(answer, bounded_fetch.HeadAnswer(code, location))
                self.assertTrue(error.fp.closed)

    def test_a_4xx_or_5xx_is_raised_unchanged_and_closed(self) -> None:
        for code in (404, 429, 500):
            with self.subTest(code=code):
                error = _http_error(code)
                error.headers["Retry-After"] = "5"
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    bounded_fetch.answer_head(_raising(error))
                self.assertIs(caught.exception, error)
                self.assertTrue(error.fp.closed)
                # Closing leaves the parsed headers readable: a 429's Retry-After.
                self.assertEqual(error.headers["Retry-After"], "5")

    def test_any_other_failure_passes_through(self) -> None:
        for failure in (urllib.error.URLError("down"), ValueError("refused")):
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(type(failure)):
                    bounded_fetch.answer_head(_raising(failure))


if __name__ == "__main__":
    unittest.main()
