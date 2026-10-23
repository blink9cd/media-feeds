"""The response body is read with a size cap and a deadline, through the REAL seam.

`_read_body` is proved on an in-memory fake with an injected clock, so the deadline is
a matter of readings rather than waiting. That alone cannot show `_real_fetch` uses
it -- a bounded reader nobody calls passes every test of its own -- so the loopback
class below stands up a real HTTP server on the loopback interface and calls the real
`_real_fetch` against it: a body larger than the cap and a body that drips slower than
the deadline must each be refused, quickly, and a normal body must come back whole.
"""

from __future__ import annotations

import http.server
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TOOL_ROOT)]

import cover_art  # noqa: E402 - import follows the sys.path bootstrap above
from cover_art import (  # noqa: E402 - import follows the sys.path bootstrap above
    _read_body,
    _real_fetch,
)

#: The interface the loopback server binds. Never a routable address: the whole point
#: of this file is proving the seam without a network.
_LOOPBACK_HOST = "127.0.0.1"

#: Sized so the fake-response tests read as arithmetic: two chunks of two bytes.
_LIMIT_BYTES = 4
_DEADLINE_SECONDS = 1.0

#: The cap the loopback test patches in, and a body four times larger than it.
_SMALL_LIMIT_BYTES = 1024
_BIG_BODY = b"x" * (4 * _SMALL_LIMIT_BYTES)

#: The drip: this many single bytes, this far apart, against a deadline that passes
#: well before the body completes. The margin is what keeps the outcome independent
#: of scheduling -- however late the client reads, the body cannot have finished.
_DRIP_BYTES = 20
_DRIP_INTERVAL_SECONDS = 0.05
_SMALL_DEADLINE_SECONDS = 0.2

#: Far longer than the drip's whole duration, so a socket timeout can never be the
#: reason a `TimeoutError` surfaces here: only the deadline can be. It bounds a
#: runaway test all the same.
_SOCKET_TIMEOUT_SECONDS = 10.0

_OK_BODY = b'{"resultCount": 0, "results": []}'


class _FakeResponse:
    """A response exposing only `read1`, handing out the given chunks in order.

    Deliberately WITHOUT a `read` method: a reader that fell back to `read()` or
    `read(n)` -- the calls a dripping server can hold for a socket timeout per byte --
    fails these tests with an `AttributeError` instead of passing by accident.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        """Queue `chunks`; the body ends when they run out."""
        self._chunks = list(chunks)
        self.sizes: list[int] = []

    def read1(self, size: int = -1, /) -> bytes:
        """The next chunk, or empty bytes once the body is exhausted."""
        self.sizes.append(size)
        return self._chunks.pop(0) if self._chunks else b""


def _clock(readings: list[float]) -> mock.Mock:
    """A monotonic clock that answers with `readings` in order, the first at entry."""
    return mock.Mock(side_effect=readings)


class ReadBodyTest(unittest.TestCase):
    """`_read_body` on an in-memory response, with the clock under test control."""

    def test_a_body_under_the_limit_is_returned_whole(self) -> None:
        response = _FakeResponse([b"ab", b"cd"])
        body = _read_body(
            response,
            limit=_LIMIT_BYTES,
            deadline=_DEADLINE_SECONDS,
            clock=_clock([0.0, 0.1, 0.2]),
        )
        self.assertEqual(body, b"abcd")

    def test_an_empty_body_is_empty_bytes(self) -> None:
        response = _FakeResponse([])
        body = _read_body(
            response,
            limit=_LIMIT_BYTES,
            deadline=_DEADLINE_SECONDS,
            clock=_clock([0.0]),
        )
        self.assertEqual(body, b"")

    def test_each_read_asks_for_at_most_the_chunk_size(self) -> None:
        # The size handed to `read1` is a ceiling per call, not an amount waited for;
        # pinning it keeps a future `read(n)` -- which DOES wait for n -- from
        # inheriting a size that would then block for a whole chunk.
        response = _FakeResponse([b"ab", b"cd"])
        _read_body(
            response,
            limit=_LIMIT_BYTES,
            deadline=_DEADLINE_SECONDS,
            clock=_clock([0.0, 0.1, 0.2]),
        )
        self.assertEqual(response.sizes, [cover_art._READ_CHUNK_BYTES] * 3)

    def test_a_body_exactly_at_the_limit_is_still_accepted(self) -> None:
        response = _FakeResponse([b"ab", b"cd"])
        body = _read_body(
            response,
            limit=_LIMIT_BYTES,
            deadline=_DEADLINE_SECONDS,
            clock=_clock([0.0] * 3),
        )
        self.assertEqual(len(body), _LIMIT_BYTES)

    def test_a_body_over_the_limit_raises_value_error(self) -> None:
        response = _FakeResponse([b"ab", b"cd", b"e"])
        with self.assertRaisesRegex(ValueError, "exceeds"):
            _read_body(
                response,
                limit=_LIMIT_BYTES,
                deadline=_DEADLINE_SECONDS,
                clock=_clock([0.0] * 4),
            )

    def test_a_body_still_arriving_at_the_deadline_raises_timeout_error(self) -> None:
        # Entry at 0.0; the first chunk lands at 0.5 (fine), the second at exactly the
        # deadline, which is where "still arriving `deadline` seconds after the read
        # began" first holds.
        response = _FakeResponse([b"ab", b"cd", b"ef"])
        with self.assertRaisesRegex(TimeoutError, "still arriving"):
            _read_body(
                response,
                limit=_SMALL_LIMIT_BYTES,
                deadline=_DEADLINE_SECONDS,
                clock=_clock([0.0, 0.5, _DEADLINE_SECONDS]),
            )

    def test_a_body_that_finishes_just_before_the_deadline_is_returned(self) -> None:
        response = _FakeResponse([b"ab", b"cd"])
        body = _read_body(
            response,
            limit=_SMALL_LIMIT_BYTES,
            deadline=_DEADLINE_SECONDS,
            clock=_clock([0.0, 0.5, _DEADLINE_SECONDS - 0.01]),
        )
        self.assertEqual(body, b"abcd")


class _Handler(http.server.BaseHTTPRequestHandler):
    """Three bodies: a normal one, one past the patched cap, and one that drips."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the base class's own parameter name
        """Silence the request log: the suite's output is the assertions', not the server's."""

    def do_GET(self) -> None:
        """Answer by path; any other path is a normal small body."""
        if self.path == "/big":
            self._send(_BIG_BODY)
        elif self.path == "/drip":
            self._drip()
        else:
            self._send(_OK_BODY)

    def _send(self, body: bytes) -> None:
        """One complete response with a declared length."""
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drip(self) -> None:
        """Declare `_DRIP_BYTES`, then send them one at a time with a pause between."""
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-Length", str(_DRIP_BYTES))
        self.end_headers()
        for _ in range(_DRIP_BYTES):
            try:
                self.wfile.write(b"x")
            except OSError:
                # The client gave up mid-body, which is exactly what the deadline test
                # expects it to do; nothing is owed to a peer that has gone.
                return
            time.sleep(_DRIP_INTERVAL_SECONDS)


class _LoopbackServer(http.server.ThreadingHTTPServer):
    """A threaded server whose handler threads are joined when it closes.

    Handler threads are non-daemon on purpose: `server_close` then waits for the
    drip handler to notice its client has gone, so no test leaves a thread writing
    to a socket after the case that owned it has finished.
    """

    daemon_threads = False


class RealFetchLoopbackTest(unittest.TestCase):
    """`_real_fetch`, unfaked, against a server on the loopback interface.

    Both bounds are patched small on the module, and `_real_fetch` reads them at
    call time, so the patch reaches the real path. The proxy environment is pinned
    for the duration: `urllib` decides per request whether a host bypasses the
    proxy, from the environment when any proxy variable is set and from the
    operating system's settings when none is, so naming the loopback address in
    `no_proxy` makes the request direct under either regime without touching the
    production code path at all.
    """

    def setUp(self) -> None:
        """Start the server on an ephemeral port and pin the proxy environment."""
        self.server = _LoopbackServer((_LOOPBACK_HOST, 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.enterContext(
            mock.patch.dict(
                os.environ, {"NO_PROXY": _LOOPBACK_HOST, "no_proxy": _LOOPBACK_HOST}
            )
        )
        self.enterContext(
            mock.patch.object(cover_art, "_BODY_LIMIT_BYTES", _SMALL_LIMIT_BYTES)
        )
        self.enterContext(
            mock.patch.object(
                cover_art, "_BODY_DEADLINE_SECONDS", _SMALL_DEADLINE_SECONDS
            )
        )

    def _stop(self) -> None:
        """Stop serving, close the listening socket, and wait for every thread."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=_SOCKET_TIMEOUT_SECONDS)

    def _url(self, path: str) -> str:
        """The server's URL for `path`, on whatever port it was given."""
        return f"http://{_LOOPBACK_HOST}:{self.server.server_address[1]}{path}"

    def test_a_normal_body_comes_back_whole(self) -> None:
        self.assertEqual(
            _real_fetch(self._url("/ok"), _SOCKET_TIMEOUT_SECONDS), _OK_BODY
        )

    def test_a_body_larger_than_the_cap_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds"):
            _real_fetch(self._url("/big"), _SOCKET_TIMEOUT_SECONDS)

    def test_a_body_dripping_past_the_deadline_is_refused(self) -> None:
        # The message is asserted, not just the type: since Python 3.10 a socket
        # timeout IS a `TimeoutError`, and only the deadline's own wording proves it
        # was the deadline -- not the socket -- that ended the read.
        with self.assertRaisesRegex(TimeoutError, "still arriving"):
            _real_fetch(self._url("/drip"), _SOCKET_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
