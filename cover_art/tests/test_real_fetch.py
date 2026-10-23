"""`_real_fetch`, the one real network seam, with `urlopen` faked.

The live suite (tests/test_live_artwork.py) proves this seam against the real endpoint
and skips offline; what it cannot prove deterministically is what the seam does AROUND
the call. This file holds that, network-free, so it runs everywhere the unit tests do.
"""

from __future__ import annotations

import email.message
import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

_TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TOOL_ROOT)]

from cover_art import (  # noqa: E402 - import follows the sys.path bootstrap above
    _real_fetch,
)

_URL = "https://example.com/search"


class RealFetchTest(unittest.TestCase):
    """What `_real_fetch` does around `urlopen`, faked so no test reaches the host."""

    def test_an_http_error_response_is_closed_before_it_is_re_raised(self) -> None:
        # An HTTP error is still a RESPONSE: `urlopen` raises it BEFORE the `with` in
        # `_real_fetch` can bind it, carrying the error page's open body, and
        # `_search` then drops it as one more OSError -- so nothing downstream ever
        # closed it, and every real 4xx/5xx leaked its connection as a
        # ResourceWarning raised nowhere near its cause. Asserted on the stream
        # directly rather than by catching that warning, which would depend on when
        # the collector happens to run.
        body = io.BytesIO(b"<html>Not Found</html>")
        error = urllib.error.HTTPError(
            _URL, 404, "Not Found", email.message.Message(), body
        )
        with (
            mock.patch("urllib.request.urlopen", side_effect=error),
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            _real_fetch(_URL, 1.0)
        self.assertIs(caught.exception, error, "re-raised as itself, not wrapped")
        self.assertTrue(body.closed, "the HTTP error response was left open")


if __name__ == "__main__":
    unittest.main()
