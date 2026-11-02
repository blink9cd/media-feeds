"""Grouped suite: how `read_body` bounds a response in size and in time, against
in-memory bodies and a clock made of readings.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bounded_fetch  # noqa: E402 - import follows the sys.path bootstrap above


class TestReadBody(unittest.TestCase):
    """The bounded body read on its own, against in-memory responses.

    `io.BytesIO` provides the `read1` the reader needs, exactly as an HTTP response
    does, and the clock is injected: the deadline is proven by readings, never by
    waiting it out.
    """

    def test_a_body_spanning_several_chunks_is_returned_whole(self) -> None:
        chunk = bounded_fetch._READ_CHUNK_BYTES
        body = bytes(range(256)) * (chunk // 256 * 2 + 1)
        self.assertGreater(len(body), 2 * chunk, "the premise: more than two reads")
        self.assertEqual(bounded_fetch.read_body(io.BytesIO(body)), body)

    def test_an_empty_body_is_empty_bytes(self) -> None:
        self.assertEqual(bounded_fetch.read_body(io.BytesIO(b"")), b"")

    def test_a_body_of_exactly_the_cap_is_accepted(self) -> None:
        body = b"x" * 10
        self.assertEqual(bounded_fetch.read_body(io.BytesIO(body), limit=10), body)

    def test_a_body_over_the_cap_is_refused_before_it_is_read_to_the_end(self) -> None:
        # Three chunks' worth, capped at one: the second read pushes the count over
        # and the third is never made, so a server that never stops sending never
        # gets to fill memory first.
        chunk = bounded_fetch._READ_CHUNK_BYTES
        response = io.BytesIO(b"x" * (3 * chunk))
        with self.assertRaises(ValueError):
            bounded_fetch.read_body(response, limit=chunk)
        self.assertEqual(response.tell(), 2 * chunk)

    def test_a_body_still_arriving_past_the_deadline_is_a_timeout(self) -> None:
        # Readings, not sleeps: the clock is read once at the start and once after
        # each chunk, and the second chunk lands past the deadline. Iterating the
        # readings also pins that count: a spare call would raise StopIteration.
        readings = iter([0.0, 10.0, 61.0])
        response = io.BytesIO(b"x" * (3 * bounded_fetch._READ_CHUNK_BYTES))
        with self.assertRaises(TimeoutError) as caught:
            bounded_fetch.read_body(
                response, deadline=60.0, clock=lambda: next(readings)
            )
        # An OSError: every caller's existing except clause already catches a socket
        # timeout, and this rides the same clause.
        self.assertIsInstance(caught.exception, OSError)

    def test_a_body_that_completes_at_the_deadline_is_returned(self) -> None:
        # The boundary: a read finishing exactly at the deadline is not late.
        readings = iter([0.0, 60.0])
        body = b"x" * 5
        self.assertEqual(
            bounded_fetch.read_body(
                io.BytesIO(body), deadline=60.0, clock=lambda: next(readings)
            ),
            body,
        )


if __name__ == "__main__":
    unittest.main()
