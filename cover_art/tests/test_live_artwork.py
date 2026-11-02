"""One live call against the real endpoint.

Unit tests fake the fetcher, which proves the parsing but not the contract: a renamed
field or a moved endpoint would leave every faked test green. This test is the only
thing in the suite that would notice, which requires it to fail, not skip, on a
broken contract.

`artwork_url` never raises, so a renamed `artworkUrl100` or a reshaped response would
come back as a plain `None`: indistinguishable, from inside `artwork_url` alone,
from being offline. Skipping in that case would defeat the whole point of this file,
so reachability is decided separately, by a probe that calls the module's own
`_real_fetch` directly (the one thing here that does not swallow network errors): a
genuine `urllib.error.URLError` (DNS/connect/TLS failure: the network layer itself
did not work) counts as "offline" and skips, but `urllib.error.HTTPError` (a non-2xx
HTTP response, including a moved endpoint returning 404) counts as reachable, because
the request plainly reached a real server and got a real answer, the very case
this file exists to catch as a loud failure, not a silent skip. Once the probe decides
"reachable", the assertions below run unconditionally, so a contract change fails this
test instead of blending into "maybe offline".
"""

from __future__ import annotations

import datetime as dt
import email.message
import os
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

_TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TOOL_ROOT)]

import cover_art  # noqa: E402 - import follows the sys.path bootstrap above
from cover_art import (  # noqa: E402 - import follows the sys.path bootstrap above
    _real_fetch,
    artwork_url,
)
from cover_art import AlbumRef  # noqa: E402 - import follows the sys.path bootstrap above

OFFLINE = os.environ.get("COVER_ART_OFFLINE") == "1"

_PROBE_TIMEOUT_SECONDS = 15.0
_PROBE_URL = "https://itunes.apple.com/search?term=probe&entity=album&limit=1"


def _webp_size(body: bytes) -> tuple[int, int] | None:
    """(width, height) from a WebP's RIFF header, or None if it is not parseable.

    Three chunk layouts exist and they store the dimensions differently, so all three
    are handled rather than assuming the lossy one: VP8 (lossy) keeps 14-bit values
    after the sync marker, VP8L (lossless) packs width-1 and height-1 into 28 bits,
    and VP8X (extended) stores 24-bit canvas dimensions minus one.
    """
    if len(body) < 30 or body[:4] != b"RIFF" or body[8:12] != b"WEBP":
        return None
    chunk = body[12:16]
    if chunk == b"VP8 ":
        return (
            int.from_bytes(body[26:28], "little") & 0x3FFF,
            int.from_bytes(body[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L":
        bits = int.from_bytes(body[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":
        return (
            int.from_bytes(body[24:27], "little") + 1,
            int.from_bytes(body[27:30], "little") + 1,
        )
    return None


def _jpeg_size(body: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG's SOF marker, or None if it is not parseable.

    Stdlib only, deliberately: this file's whole job is proving a real network
    boundary, and reaching for an imaging dependency to read two numbers out of a
    header would put a new package in the install path of a tool that has none.
    Walks the marker chain rather than assuming a fixed offset, because the encoder
    is free to put any number of APPn/DQT segments before the frame header.
    """
    if not body.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 < len(body):
        if body[i] != 0xFF:
            return None
        marker = body[i + 1]
        length = int.from_bytes(body[i + 2 : i + 4], "big")
        # The SOFn frame headers carry the dimensions; C4/C8/CC are not frames.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(body[i + 5 : i + 7], "big")
            width = int.from_bytes(body[i + 7 : i + 9], "big")
            return width, height
        i += 2 + length
    return None


def _image_size(body: bytes) -> tuple[int, int] | None:
    """(width, height) for either format this adapter can request.

    Dispatches on the file's own magic bytes rather than on the URL's extension or the
    server's Content-Type: both are claims, and the point of this check is to catch a
    CDN that answers a `.webp` request with something else.
    """
    return _webp_size(body) if body[:4] == b"RIFF" else _jpeg_size(body)


def _endpoint_is_reachable() -> bool:
    """True unless reaching the search endpoint itself fails at the network layer.

    Deliberately calls `_real_fetch`, not `artwork_url`: `artwork_url` swallows every
    `OSError` (that is its contract), so probing through it could never distinguish
    "offline" from "reachable but the response no longer parses". `HTTPError` is
    caught first and treated as reachable: it is an `OSError` subclass, so the bare
    `except OSError` below would otherwise swallow it identically to a real network
    failure and let a moved endpoint skip instead of fail. Any body that comes back
    (even a malformed one) also counts as reachable and lets the real test run, and
    fail loudly if the contract moved.
    """
    try:
        _real_fetch(_PROBE_URL, _PROBE_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        # `exc` carries the real (or, under test, synthetic) response body as
        # `exc.fp`; close it so a real 404's underlying connection, or the
        # implicit BytesIO Python fills in for a fp=None construction, does not
        # linger to GC time (surfaces as a ResourceWarning otherwise).
        exc.close()
        return True
    except OSError:
        return False
    return True


class EndpointReachableTest(unittest.TestCase):
    """`_endpoint_is_reachable`'s own classification, against a faked `_real_fetch`.

    Deterministic and network-free (unlike `LiveLookupTest` below), so it runs
    regardless of `COVER_ART_OFFLINE` and proves the HTTPError-vs-URLError split
    without depending on a real 404 existing to trigger it.
    """

    def test_an_http_error_response_counts_as_reachable(self) -> None:
        def raise_http_error(_url: str, _timeout: float) -> bytes:
            # fp=None: `_endpoint_is_reachable` closes whatever Python fills in for
            # it (see that function's docstring); nothing left to clean up here.
            # hdrs must be an email.message.Message (mypy: HTTPError's 4th
            # positional arg), not a bare dict: urllib's own real callers always
            # pass the real HTTPMessage the response parsed.
            raise urllib.error.HTTPError(
                _PROBE_URL, 404, "Not Found", email.message.Message(), None
            )

        with mock.patch(f"{__name__}._real_fetch", raise_http_error):
            self.assertTrue(_endpoint_is_reachable())

    def test_a_url_error_counts_as_unreachable(self) -> None:
        def raise_url_error(_url: str, _timeout: float) -> bytes:
            raise urllib.error.URLError("no route to host")

        with mock.patch(f"{__name__}._real_fetch", raise_url_error):
            self.assertFalse(_endpoint_is_reachable())

    def test_a_successful_response_counts_as_reachable(self) -> None:
        with mock.patch(f"{__name__}._real_fetch", lambda _u, _t: b"{}"):
            self.assertTrue(_endpoint_is_reachable())


@unittest.skipIf(OFFLINE, "COVER_ART_OFFLINE=1 - live lookup skipped")
class LiveLookupTest(unittest.TestCase):
    """Exercise the real boundary once, so a moved endpoint or renamed field is caught."""

    def setUp(self) -> None:
        """Skip only for genuine unreachability, decided independently of `artwork_url`."""
        if not _endpoint_is_reachable():
            self.skipTest("no network: the search endpoint itself is unreachable")

    def test_a_well_known_album_resolves_to_an_https_artwork_url(self) -> None:
        # A widely-cataloged release, chosen so the assertion is about the API contract
        # rather than about this library's contents.
        ref = AlbumRef(
            artist="Radiohead", album="OK Computer", added=dt.date(2026, 1, 1)
        )
        try:
            url = artwork_url(ref, timeout=_PROBE_TIMEOUT_SECONDS).url
        except (
            Exception
        ) as exc:  # pragma: no cover - defensive; artwork_url must not raise
            self.fail(f"artwork_url raised instead of returning None: {exc!r}")
        # setUp already proved the endpoint is reachable, so a None here is not "maybe
        # offline": it means the response no longer matches what artwork_url expects
        # (a renamed field, an empty result set for a well-known album, ...), and this
        # assertion is what turns that into a loud failure instead of a silent skip.
        self.assertIsNotNone(
            url,
            "endpoint is reachable but artwork_url returned None; "
            "the API contract (a field name, the response shape) may have moved",
        )
        assert url is not None  # narrows the type for mypy; assertIsNotNone doesn't
        self.assertTrue(url.startswith("https://"), url)
        self.assertIn(f"{cover_art.DEFAULT_ARTWORK_PX}x", url)
        self.assertTrue(url.endswith(cover_art.DEFAULT_ARTWORK_FORMAT), url)
        # The URL naming a size is not the same fact as the CDN honoring it: a
        # service can ignore a size token and hand back the original, which would
        # ship a "smaller" URL that saves nothing and pass every offline test. So
        # fetch it and read the real dimensions out of the image's own header (WebP
        # or JPEG, chosen by its magic bytes).
        with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_SECONDS) as response:
            body = response.read()
        self.assertEqual(
            _image_size(body),
            (cover_art.DEFAULT_ARTWORK_PX, cover_art.DEFAULT_ARTWORK_PX),
            "the CDN returned a different size than the URL asked for: it has "
            "stopped honoring the size segment, and the recorded URLs now lie",
        )
        # Same argument for the format, and it is the half a size check cannot make:
        # answering a .webp request with a JPEG would keep every dimension correct
        # while silently giving back the bytes the format was chosen to save.
        self.assertEqual(
            body[:4] == b"RIFF",
            cover_art.DEFAULT_ARTWORK_FORMAT == "webp",
            "the CDN did not return the format the URL asked for",
        )


if __name__ == "__main__":
    unittest.main()
