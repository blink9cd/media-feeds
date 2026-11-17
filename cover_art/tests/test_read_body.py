"""The response body is read with a size cap and a deadline, through the real seam.

`_read_body` is proved on an in-memory fake with an injected clock, so the deadline is
a matter of readings rather than waiting. That alone cannot show `_real_fetch` uses
it (a bounded reader nobody calls passes every test of its own), so the loopback
class below stands up a real HTTP server on the loopback interface and calls the real
`_real_fetch` against it: a body larger than the cap and a body that drips slower than
the deadline must each be refused, quickly, and a normal body must come back whole.
"""

from __future__ import annotations

import http.server
import itertools
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

_TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TOOL_ROOT), str(Path(__file__).resolve().parent)]

import cover_art  # noqa: E402 - import follows the sys.path bootstrap above
from cover_art import (  # noqa: E402 - import follows the sys.path bootstrap above
    _read_body,
    _real_fetch,
)
from _offline_guard import setUpModule, tearDownModule  # noqa: E402, F401 - the bootstrap above; unittest calls the hooks by name

#: The interface the loopback server binds. Never a routable address: the whole point
#: of this file is proving the seam without a network.
_LOOPBACK_HOST = "127.0.0.1"

#: Sized so the fake-response tests read as arithmetic: two chunks of two bytes.
_LIMIT_BYTES = 4
_DEADLINE_SECONDS = 1.0

#: The cap the loopback test patches in, and a body four times larger than it.
_SMALL_LIMIT_BYTES = 1024
_BIG_BODY = b"x" * (4 * _SMALL_LIMIT_BYTES)

#: The drip: single bytes this far apart, against a deadline only the drip tests
#: patch in. The server keeps dripping until the client hangs up, so however late
#: the client reads, more of the body is still to come and the deadline must fire;
#: a fixed-length drip could finish while a stalled client was not looking. The
#: burst is when the server signals that a client held back has missed a stretch.
#: The cap bounds a broken test, stays under `_SMALL_LIMIT_BYTES` so the size check
#: can never be what ends the read, and at the interval lasts about ten seconds.
_DRIP_BURST_BYTES = 20
_DRIP_MAX_BYTES = 200
_DRIP_INTERVAL_SECONDS = 0.05
_SMALL_DEADLINE_SECONDS = 0.2

#: Far longer than the drip's whole duration, so a socket timeout can never be the
#: reason a `TimeoutError` surfaces here: only the deadline can be. It bounds a
#: runaway test all the same.
_SOCKET_TIMEOUT_SECONDS = 10.0

_OK_BODY = b'{"resultCount": 0, "results": []}'

#: How far the stalled clock jumps per reading: well past the small deadline, well
#: inside the real one, which is the gap a normal body must be allowed to cross.
_STALL_SECONDS = 1.0


class _FakeResponse:
    """A response exposing only `read1`, handing out the given chunks in order.

    Deliberately without a `read` method: a reader that fell back to `read()` or
    `read(n)` (the calls a dripping server can hold for a socket timeout per byte)
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
        # pinning it keeps a future `read(n)` (which does wait for n) from
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

    def test_a_chunk_at_the_deadline_is_accepted_and_one_past_it_refused(
        self,
    ) -> None:
        # Entry at 0.0; the first chunk lands at 0.5, the second at exactly the
        # deadline, which is not yet "after" it, so both are kept. The third lands
        # just past the deadline, the first reading of which "still arriving after
        # `deadline` seconds" holds, and is refused. bounded_fetch draws the line in
        # the same place, which tests/test_fetch_parity.py holds the two to.
        at_deadline = _FakeResponse([b"ab", b"cd"])
        body = _read_body(
            at_deadline,
            limit=_SMALL_LIMIT_BYTES,
            deadline=_DEADLINE_SECONDS,
            clock=_clock([0.0, 0.5, _DEADLINE_SECONDS]),
        )
        self.assertEqual(body, b"abcd")
        past_deadline = _FakeResponse([b"ab", b"cd", b"ef"])
        with self.assertRaisesRegex(TimeoutError, "still arriving"):
            _read_body(
                past_deadline,
                limit=_SMALL_LIMIT_BYTES,
                deadline=_DEADLINE_SECONDS,
                clock=_clock([0.0, 0.5, _DEADLINE_SECONDS, _DEADLINE_SECONDS + 0.01]),
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
        """Declare `_DRIP_MAX_BYTES`, then send one at a time until the client goes."""
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-Length", str(_DRIP_MAX_BYTES))
        self.end_headers()
        burst_sent = cast("_LoopbackServer", self.server).burst_sent
        for sent in range(1, _DRIP_MAX_BYTES + 1):
            try:
                self.wfile.write(b"x")
            except OSError:
                # The client gave up mid-body, which is what the deadline tests
                # expect it to do; nothing is owed to a peer that has gone.
                return
            if sent == _DRIP_BURST_BYTES:
                burst_sent.set()
            time.sleep(_DRIP_INTERVAL_SECONDS)


class _LoopbackServer(http.server.ThreadingHTTPServer):
    """A threaded server whose handler threads are joined when it closes.

    Handler threads are non-daemon on purpose: `server_close` then waits for the
    drip handler to notice its client has gone, so no test leaves a thread writing
    to a socket after the case that owned it has finished. `burst_sent` is set once
    the drip has written its first `_DRIP_BURST_BYTES`, so a test can hold the client back
    until then: the stall a loaded machine produces, on cue.
    """

    daemon_threads = False

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[http.server.BaseHTTPRequestHandler],
    ) -> None:
        """Bind `address` with `handler`, and start with no burst sent."""
        super().__init__(address, handler)
        self.burst_sent = threading.Event()


class RealFetchLoopbackTest(unittest.TestCase):
    """`_real_fetch`, unfaked, against a server on the loopback interface.

    Both bounds are patched small on the module, and `_real_fetch` reads them at
    call time, so the patch reaches the real path. The size cap is patched for every
    test, since it costs nothing in time; the deadline only for the drip tests,
    because a sub-second deadline on a normal body fails it whenever a loaded machine
    stalls the client that long. The proxy environment is pinned
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

    def _patch_small_deadline(self) -> None:
        """Shrink the body deadline for this test only: the drip tests need it."""
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
        # The message is asserted as well as the type: since Python 3.10 a socket
        # timeout is a `TimeoutError`, and only the deadline's own wording proves it
        # was the deadline, not the socket, that ended the read.
        self._patch_small_deadline()
        with self.assertRaisesRegex(TimeoutError, "still arriving"):
            _real_fetch(self._url("/drip"), _SOCKET_TIMEOUT_SECONDS)

    def test_a_normal_body_survives_a_stalled_client(self) -> None:
        # A loaded machine can hold the client between the headers and each chunk;
        # the clock below jumps a whole second per reading to stand in for that, so
        # a deadline small enough to trip on it is caught here deterministically.
        real_read_body = cover_art._read_body
        stalled = itertools.count(0.0, _STALL_SECONDS)

        def read_with_stalls(
            response: cover_art._BodyReader, *, limit: int, deadline: float
        ) -> bytes:
            return real_read_body(
                response, limit=limit, deadline=deadline, clock=lambda: next(stalled)
            )

        with mock.patch.object(cover_art, "_read_body", read_with_stalls):
            body = _real_fetch(self._url("/ok"), _SOCKET_TIMEOUT_SECONDS)
        self.assertEqual(body, _OK_BODY)

    def test_a_client_that_falls_behind_the_drip_is_still_refused(self) -> None:
        # Hold the client back until the server has sent a full burst, so the
        # first read takes all of it at once: the deadline must still fire on the
        # bytes that keep arriving after, not depend on the client keeping up.
        self._patch_small_deadline()
        real_read_body = cover_art._read_body
        burst_sent = self.server.burst_sent

        def read_after_burst(
            response: cover_art._BodyReader, *, limit: int, deadline: float
        ) -> bytes:
            self.assertTrue(burst_sent.wait(_SOCKET_TIMEOUT_SECONDS))
            return real_read_body(response, limit=limit, deadline=deadline)

        with mock.patch.object(cover_art, "_read_body", read_after_burst):
            with self.assertRaisesRegex(TimeoutError, "still arriving"):
                _real_fetch(self._url("/drip"), _SOCKET_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
