"""Grouped suite: which URLs `https_url_or_none` accepts, and what `build_request`
puts on the wire and refuses to, proven without a request being sent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bounded_fetch  # noqa: E402 - import follows the sys.path bootstrap above


class TestHttpsUrlOrNone(unittest.TestCase):
    """The one rule for a URL a record may carry or a request may be built from."""

    def test_an_https_url_with_a_host_is_returned_verbatim(self) -> None:
        # Verbatim, uppercase scheme included: urllib lowercases the scheme itself
        # when it picks a handler, so nothing here needs to normalize.
        for url in ("https://host/x", "HTTPS://host/x"):
            with self.subTest(url=url):
                self.assertEqual(bounded_fetch.https_url_or_none(url), url)

    def test_every_other_scheme_and_a_missing_host_are_none(self) -> None:
        # Each of these was fetched and recorded by a poster probe before the rule
        # existed: urlopen speaks file:, ftp: and data: as readily as https, and a
        # record carried whatever a feed description's first <img> said.
        for url in (
            "http://host/x",
            "file:///etc/hosts",
            "ftp://198.51.100.7:2121/x",
            "data:text/plain,x",
            "javascript:alert(1)",
            "https:///x",
            "//host/x",
            "",
            # urlsplit raises on an unclosed IPv6 bracket; a caller's string must
            # come back as "no URL", never as a traceback out of the parser.
            "https://[::1/x",
        ):
            with self.subTest(url=url):
                self.assertIsNone(bounded_fetch.https_url_or_none(url))

    def test_userinfo_whitespace_and_control_characters_are_none(self) -> None:
        # Parsing is not a filter: `urlsplit` STRIPS tab, CR and LF before it
        # parses, so a header-break URL and a tab spliced into the scheme both reach
        # the check already looking clean, and it never looks at a NUL, a zero-width
        # mark or a bidi override at all. The scan therefore has to run on the RAW
        # string, before the parse.
        for label, url in (
            ("userinfo", "https://user:pass@host/x"),
            ("header break", "https://host/x\r\nHost: internal"),
            ("tab inside the scheme", "ht\ttps://host/x"),
            ("zero-width space", "https://host/​x"),
            ("bidi override", "https://host/‮x"),
            ("a space", "https://host/ x"),
            ("a NUL", "https://host/x\x00"),
        ):
            with self.subTest(label):
                self.assertIsNone(bounded_fetch.https_url_or_none(url))

    def test_a_percent_encoded_path_still_passes(self) -> None:
        # The scan reads the characters actually in the string, so an ESCAPED space
        # or control character is ordinary URL text and must survive: refusing it
        # would reject legitimate URLs, which is the over-correction the scan has to
        # avoid.
        url = "https://host/cover%20art%0A.jpg"
        self.assertEqual(bounded_fetch.https_url_or_none(url), url)


class TestBuildRequest(unittest.TestCase):
    """`build_request`: the caller's headers on the request, and the sink's refusals."""

    def test_the_url_survives_construction_unchanged(self) -> None:
        url = "https://example.org/someone/rss/"
        request = bounded_fetch.build_request(url, {})
        self.assertEqual(request.full_url, url)

    def test_a_url_that_is_not_https_is_refused_before_a_request_exists(self) -> None:
        # The sink's own defense: whatever a caller was handed, no request for any
        # scheme but https can be built here, so `fetch_url` can never send one.
        for url in ("http://host/x", "file:///etc/hosts"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                bounded_fetch.build_request(url, {})

    def test_build_request_sends_the_callers_headers(self) -> None:
        request = bounded_fetch.build_request(
            "https://example.org/x",
            {"User-Agent": "probe/1.0", "Authorization": "Bearer t"},
        )
        self.assertEqual(request.get_header("User-agent"), "probe/1.0")
        self.assertEqual(request.get_header("Authorization"), "Bearer t")

    def test_build_request_refuses_a_header_break(self) -> None:
        for name, value in (
            ("X-Probe", "ok\r\nInjected: 1"),
            ("X-Probe\n", "ok"),
            ("X-Probe", "ok\x00"),
        ):
            with self.assertRaises(ValueError) as caught:
                bounded_fetch.build_request("https://example.org/x", {name: value})
            # The message names neither the header nor its value: a value may be a
            # credential.
            for leaked in ("X-Probe", "Injected", "ok"):
                self.assertNotIn(leaked, str(caught.exception))

    def test_build_request_refuses_a_header_that_is_not_printable_ascii(self) -> None:
        # http.client encodes header values as latin-1 and raises UnicodeEncodeError
        # for anything else, and that error's args and repr carry the WHOLE value
        # (a bearer token included). So the refusal comes first, here, without it.
        for name, value in (
            ("Authorization", "Bearer t\u2019k"),
            ("X-Pr\u00f6be", "plain"),
            ("X-Probe", "caf\u00e9"),
            ("X-Probe", "tab\there"),
            ("X-Probe", "del\x7fhere"),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaises(ValueError) as caught:
                    bounded_fetch.build_request("https://example.org/x", {name: value})
                for shown in (str(caught.exception), repr(caught.exception)):
                    self.assertNotIn(value, shown)
                    self.assertNotIn(name, shown)

    def test_build_request_accepts_printable_ascii_with_spaces(self) -> None:
        value = "probe/1.0 (+https://example.org/contact)"
        request = bounded_fetch.build_request(
            "https://example.org/x", {"User-Agent": value}
        )
        self.assertEqual(request.get_header("User-agent"), value)

    def test_build_request_refuses_a_non_https_url(self) -> None:
        with self.assertRaises(ValueError):
            bounded_fetch.build_request("http://example.org/x", {})


if __name__ == "__main__":
    unittest.main()
