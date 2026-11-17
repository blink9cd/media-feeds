"""cover_art's copies of bounded_fetch's read, redirect and URL rules agree with it.

`cover_art._read_body`, `src/artwork_redirect.py`'s `HttpsOnlyRedirect` and
`cover_art._is_recordable_url` are kept as copies of the shared `bounded_fetch`
library's `read_body`, its redirect handler and `https_url_or_none`, because
cover_art depends on nothing outside its own folder (`cover_art.md`, "Why this is its
own folder"). A copy drifts in silence, so this module runs both sides on the
same cases and fails the moment they disagree. Where they differ on purpose, the
difference is listed here, asserted case by case, and documented in
`src/artwork_redirect.py`'s docstring: a change on either side then fails a test
rather than passing unnoticed.

The body deadline is the one bound that is NOT compared: cover_art keeps its own, and
every case here passes the same `limit`, `deadline` and `clock` to both readers, so
only the rule is under test, never the constants.

`bounded_fetch` is vendored at one fixed place, directly under the repository root, and
put on `sys.path` from there. A tree without it fails the import loudly rather than
skipping, because a missing library is a broken tree, not a case to skip.
"""

from __future__ import annotations

import http.client
import io
import sys
import unittest
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_TOOL_ROOT = _TESTS_DIR.parent
_LIBRARY = "bounded_fetch"
sys.path[:0] = [str(_TOOL_ROOT / "src"), str(_TOOL_ROOT), str(_TESTS_DIR)]
# The shared bounded_fetch library, vendored at one fixed place: directly under the
# repository root. Appended, and deliberately not a search up the ancestors, which would
# also take any same-named folder above the checkout and could put it ahead of the
# standard library. A missing folder fails at the import below rather than skipping.
sys.path += [str(_TOOL_ROOT.parent / _LIBRARY)]

import bounded_fetch  # noqa: E402 - import follows the sys.path bootstrap above
import cover_art  # noqa: E402 - import follows the sys.path bootstrap above
from artwork_redirect import HttpsOnlyRedirect  # noqa: E402 - the same bootstrap
from _offline_guard import setUpModule, tearDownModule  # noqa: E402, F401 - the bootstrap above; unittest calls the hooks by name

#: Sized so each case reads as arithmetic: two-byte chunks against a four-byte cap.
_LIMIT_BYTES = 4
_DEADLINE_SECONDS = 1.0
_JUST = 0.01

#: Each body case: its chunks, the clock's readings (the first at entry), and the
#: outcome both readers must give, as `_outcome` spells it. The expected outcome is
#: stated as well as compared, so the two drifting together still fails.
_BODY_CASES: dict[str, tuple[list[bytes], list[float], str]] = {
    "empty": ([], [0.0], "b''"),
    "under the limit": ([b"ab", b"c"], [0.0, 0.1, 0.2], "b'abc'"),
    "at the limit": ([b"ab", b"cd"], [0.0, 0.0, 0.0], "b'abcd'"),
    "over the limit": (
        [b"ab", b"cd", b"e"],
        [0.0, 0.0, 0.0, 0.0],
        "ValueError: response body exceeds 4 bytes",
    ),
    "just before the deadline": (
        [b"ab", b"cd"],
        [0.0, 0.5, _DEADLINE_SECONDS - _JUST],
        "b'abcd'",
    ),
    "exactly at the deadline": (
        [b"ab", b"cd"],
        [0.0, 0.5, _DEADLINE_SECONDS],
        "b'abcd'",
    ),
    "past the deadline": (
        [b"ab", b"cd"],
        [0.0, 0.5, _DEADLINE_SECONDS + _JUST],
        "TimeoutError: response body still arriving after 1.0 seconds",
    ),
}

#: The largest chunk each reader asks `read1` for: a ceiling per call, not a wait.
_READ_CHUNK_BYTES = 64 * 1024

#: Where every redirect below starts: an https URL both sides would have opened.
_ORIGIN = "https://example.com/start"

#: Hops both sides follow.
_BOTH_FOLLOW = (
    "https://example.com/x",
    "HTTPS://example.com/x",
)

#: Hops both sides refuse.
_BOTH_REFUSE = (
    "http://example.com/x",
    "ftp://example.com/x",
    "file:///x",
    "data:,x",
    "https:///x",
    "https://[::1/x",
    "//example.com/x",
    # urlsplit's host ends in `.wikipedia.org`; a browser reads `\` as `/` and goes
    # to `evil.example`.
    "https://evil.example\\.wikipedia.org/x",
    # The same backslash after the port, and at the very start of the host.
    "https://evil.example:443\\.wikipedia.org/x",
    "https://\\evil.example/x",
)

#: The known differences: hops the copy follows and bounded_fetch refuses. Each
#: group is one rule the copy deliberately does not carry, named in
#: `src/artwork_redirect.py`'s docstring.
_USERINFO = (
    "https://listener@example.com/x",
    "https://listener:placeholder@example.com/x",
)
_UNREADABLE_PORT = (
    "https://example.com:port/x",
    "https://example.com:99999/x",
)
#: Any whitespace, and any character whose Unicode category starts with "C": control
#: (NUL, DEL), format (a zero-width space) and private use, each written as an escape.
_RAW_WHITESPACE_OR_UNICODE_C = (
    "https://example.com/a b",
    "https://example.com/a\tb",
    "https://example.com/a\nb",
    "https://example.com/\x00",
    "https://example.com/\x7f",
    "https://example.com/\u200b",
    "https://example.com/\ue000",
)

_ReadBody = Callable[..., bytes]
_READERS: dict[str, _ReadBody] = {
    "cover_art": cover_art._read_body,
    "bounded_fetch": bounded_fetch.read_body,
}


class _FakeResponse:
    """A response exposing only `read1`, handing out the given chunks in order."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        """Queue `chunks`; the body ends when they run out."""
        self._chunks = list(chunks)
        self.sizes: list[int] = []

    def read1(self, size: int, /) -> bytes:
        """The next chunk, or empty bytes once the body is exhausted."""
        self.sizes.append(size)
        return self._chunks.pop(0) if self._chunks else b""


def _outcome(read: _ReadBody, chunks: list[bytes], readings: list[float]) -> str:
    """What `read` makes of one body: its bytes, or the exception it raised."""
    clock = iter(readings).__next__
    try:
        body = read(
            _FakeResponse(chunks),
            limit=_LIMIT_BYTES,
            deadline=_DEADLINE_SECONDS,
            clock=clock,
        )
    except (ValueError, TimeoutError) as error:
        return f"{type(error).__name__}: {error}"
    return repr(body)


def _chunk_sizes(read: _ReadBody, chunks: list[bytes]) -> list[int]:
    """The size `read` hands `read1` on each call, over a body that fits."""
    response = _FakeResponse(chunks)
    read(
        response,
        limit=_LIMIT_BYTES,
        deadline=_DEADLINE_SECONDS,
        clock=iter([0.0] * (len(chunks) + 1)).__next__,
    )
    return response.sizes


def _copy_handler() -> urllib.request.HTTPRedirectHandler:
    """cover_art's own redirect handler."""
    return HttpsOnlyRedirect()


def _shared_handler() -> urllib.request.HTTPRedirectHandler:
    """bounded_fetch's redirect handler, with no caller headers to carry."""
    return bounded_fetch._HttpsOnlyRedirectHandler({})


_HANDLERS: dict[str, Callable[[], urllib.request.HTTPRedirectHandler]] = {
    "cover_art": _copy_handler,
    "bounded_fetch": _shared_handler,
}


def _verdict(make: Callable[[], urllib.request.HTTPRedirectHandler], url: str) -> str:
    """`follow` or `refuse` for a 302 to `url`, with whether the body was closed."""
    # A redirect's own response, never read: a refused hop must leave it closed.
    body = io.BytesIO()
    try:
        hop = make().redirect_request(
            urllib.request.Request(_ORIGIN),
            body,
            302,
            "Found",
            http.client.HTTPMessage(),
            url,
        )
    except ValueError as error:
        return f"refuse ({error}), closed={body.closed}"
    return f"follow to {hop.full_url if hop else None}, closed={body.closed}"


class BodyReaderParityTest(unittest.TestCase):
    """The two bounded readers give the same outcome on every case."""

    def test_both_readers_give_each_case_the_same_outcome(self) -> None:
        for case, (chunks, readings, expected) in _BODY_CASES.items():
            for side, read in _READERS.items():
                with self.subTest(case=case, side=side):
                    self.assertEqual(_outcome(read, chunks, readings), expected)

    def test_both_readers_ask_read1_for_the_same_chunk_size(self) -> None:
        chunks = [b"ab", b"cd"]
        for side, read in _READERS.items():
            with self.subTest(side=side):
                self.assertEqual(
                    _chunk_sizes(read, chunks),
                    [_READ_CHUNK_BYTES] * (len(chunks) + 1),
                )


class RedirectParityTest(unittest.TestCase):
    """The two redirect handlers agree, except on three listed differences."""

    def _assert_both(
        self, urls: Iterable[str], verdict_start: str, *, closed: bool
    ) -> None:
        """Both handlers give every URL in `urls` the same verdict, of that kind.

        `closed` is the stated expectation for the hop's own body, so both sides
        dropping (or gaining) the close together still fails.
        """
        for url in urls:
            with self.subTest(url=url):
                copy, shared = (_verdict(make, url) for make in _HANDLERS.values())
                self.assertEqual(copy, shared)
                self.assertTrue(copy.startswith(verdict_start), copy)
                self.assertTrue(copy.endswith(f", closed={closed}"), copy)

    def _assert_only_the_copy_follows(self, urls: Iterable[str]) -> None:
        """A known difference: the copy follows each URL, bounded_fetch refuses it."""
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(_verdict(_copy_handler, url).startswith("follow"))
                self.assertTrue(_verdict(_shared_handler, url).startswith("refuse"))

    def test_both_follow_an_https_hop_with_a_host(self) -> None:
        self._assert_both(_BOTH_FOLLOW, "follow", closed=False)

    def test_both_refuse_every_other_hop_and_close_its_body(self) -> None:
        self._assert_both(_BOTH_REFUSE, "refuse", closed=True)

    def test_known_difference_userinfo_is_followed_only_by_the_copy(self) -> None:
        self._assert_only_the_copy_follows(_USERINFO)

    def test_known_difference_an_unreadable_port_is_followed_only_by_the_copy(
        self,
    ) -> None:
        self._assert_only_the_copy_follows(_UNREADABLE_PORT)

    def test_known_difference_raw_whitespace_or_unicode_c_is_followed_only_by_the_copy(
        self,
    ) -> None:
        self._assert_only_the_copy_follows(_RAW_WHITESPACE_OR_UNICODE_C)


#: URLs the recording rule and bounded_fetch's URL rule must judge alike, beyond the
#: redirect cases above: a backslash in the path, a percent-encoded backslash in the host,
#: and hosts with other unusual characters both rules accept.
_URL_RULE_EXTRAS = (
    "https://example.com/a\\b",
    # Both admit it, since it holds no raw backslash. A browser decodes the host to
    # `evil.example\.wikipedia.org` and refuses it: a dead link, not another host.
    "https://evil.example%5C.wikipedia.org/x",
    "https://ev%il.example/x",
    "https://ev^il.example/x",
    "https://ev|il.example/x",
)


class RecordableUrlParityTest(unittest.TestCase):
    """`_is_recordable_url` is bounded_fetch's `https_url_or_none`, case for case.

    The two rules must match character for character; this holds them to it, since
    they were written apart and one has been weaker before.
    """

    def test_both_rules_judge_every_case_alike(self) -> None:
        cases = (
            *_BOTH_FOLLOW,
            *_BOTH_REFUSE,
            *_USERINFO,
            *_UNREADABLE_PORT,
            *_RAW_WHITESPACE_OR_UNICODE_C,
            *_URL_RULE_EXTRAS,
        )
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(
                    cover_art._is_recordable_url(url),
                    bounded_fetch.https_url_or_none(url) is not None,
                )


if __name__ == "__main__":
    unittest.main()
