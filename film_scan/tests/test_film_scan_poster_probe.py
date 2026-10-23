"""The poster probe: a resized poster URL is verified before it is recorded.

MEASURED 2026-09-04: Letterboxd's pre-generated poster sizes are PER POSTER, not one
universal set. One real film's 400-wide poster answered HTTP 403 while its 300, 500
and 600 all answered 200, and the other recorded films' 400s served fine. The page
drops a poster that fails to load, so the unverified rewrite surfaced as a text-only
card. `main` therefore GETs each rewritten URL through the same injected `fetch` seam
the feed uses, and falls back to the feed's own URL -- the one guaranteed to exist --
when the rewrite is refused.

The `test_film_scan_` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.

Every run passes `--no-director`, so the only network seam any test reaches is the
one it injects.
"""

from __future__ import annotations

import email.message
import io
import json
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import film_scan  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE_PATH = _TOOL_ROOT / "fixtures" / "diary.xml"
_FIXTURE = _FIXTURE_PATH.read_bytes()

# Same reasoning as test_film_scan_cli.py's own _A_USERNAME: an obvious placeholder,
# chosen to look nothing like a real Letterboxd username.
_A_USERNAME = "a-placeholder-username"
_FEED_URL = film_scan.feed_url(_A_USERNAME)

#: The fixture's newest film's poster, exactly as the feed hands it out (600x900).
_VOYAGE_FEED_POSTER = "https://example.invalid/poster/voyage-0-600-0-900-crop.jpg"
#: The same poster after `--poster-width`'s default (400) rewrites its suffix.
_VOYAGE_RESIZED_POSTER = "https://example.invalid/poster/voyage-0-400-0-600-crop.jpg"
#: The second-newest film's poster, resized the same way.
_UNRATED_RESIZED_POSTER = "https://example.invalid/upload/unrated-0-400-0-600-crop.jpg"
_UNRATED_FEED_POSTER = "https://example.invalid/upload/unrated-0-600-0-900-crop.jpg"


class _RecordingFetch:
    """A `fetch` fake that serves the feed, refuses chosen posters, and logs every URL.

    Returns `body` for the diary feed URL and a stand-in image body for any other
    URL, except that a URL containing any of `refused` raises the `HTTPError` 403
    Letterboxd really answers for a size it did not pre-generate -- the exception
    type the probe must catch, not a stand-in OSError.
    """

    def __init__(self, body: bytes = _FIXTURE, refused: tuple[str, ...] = ()) -> None:
        self.body = body
        self.refused = refused
        self.urls: list[str] = []
        # Every HTTPError raised, kept so a test can check the probe CLOSED it: an
        # HTTPError is also the refused response (addinfourl, socket and all), and
        # CPython warns at garbage collection when one is discarded unclosed.
        self.refusals: list[urllib.error.HTTPError] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        del timeout
        self.urls.append(url)
        if url == _FEED_URL:
            return self.body
        for marker in self.refused:
            if marker in url:
                error = urllib.error.HTTPError(
                    url, 403, "Forbidden", email.message.Message(), None
                )
                self.refusals.append(error)
                raise error
        return b"a stand-in poster body"


def _timing_out_fetch(body: bytes) -> film_scan.feed_read.Fetcher:
    """A `fetch` fake that serves the feed and times out on every other URL."""

    def fetch(url: str, timeout: float) -> bytes:
        del timeout
        if url == _FEED_URL:
            return body
        raise TimeoutError("timed out")

    return fetch


def _explode_fetch(url: str, timeout: float) -> bytes:
    """A `fetch` fake that fails the test if it is ever called."""
    del url, timeout
    raise AssertionError("fetch must not be called on a --feed-file run")


class PosterProbeTest(unittest.TestCase):
    """The rewritten poster URL is fetched once, and kept only when it is served."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.out = Path(self._tmp.name) / "films.json"
        self.addCleanup(self._tmp.cleanup)

    def _run(
        self, fetch: film_scan.feed_read.Fetcher, extra_args: list[str]
    ) -> list[dict]:
        """Run `main` to completion and return the records it wrote."""
        with redirect_stdout(io.StringIO()):
            code = film_scan.main(
                ["--username", _A_USERNAME, "--no-director", "--output", str(self.out)]
                + extra_args,
                fetch=fetch,
            )
        self.assertEqual(code, 0)
        records: list[dict] = json.loads(self.out.read_text(encoding="utf-8"))
        return records

    def test_a_refused_resized_poster_falls_back_to_the_feeds_own_url(self) -> None:
        fetch = _RecordingFetch(refused=("-0-400-0-600-crop",))
        records = self._run(fetch, [])
        self.assertEqual(records[0]["poster"], _VOYAGE_FEED_POSTER)

    def test_a_refused_response_is_closed_not_leaked(self) -> None:
        fetch = _RecordingFetch(refused=("-0-400-0-600-crop",))
        self._run(fetch, [])
        self.assertEqual(len(fetch.refusals), 1)
        self.assertTrue(fetch.refusals[0].closed)

    def test_a_probe_that_times_out_falls_back_too(self) -> None:
        # A slow CDN must not fail the whole refresh: the timeout is an OSError,
        # not an HTTPError, so there is no response to close -- the other arm of
        # the probe's cleanup.
        records = self._run(_timing_out_fetch(_FIXTURE), [])
        self.assertEqual(records[0]["poster"], _VOYAGE_FEED_POSTER)

    def test_a_served_resized_poster_is_recorded_resized(self) -> None:
        # The refusal is PER POSTER, which is the whole defect: in one run the
        # newest film's 400-wide poster is served and stays resized, while the
        # second film's is refused and falls back -- neither outcome leaks into
        # the other record.
        fetch = _RecordingFetch(refused=("unrated-0-400-0-600-crop",))
        records = self._run(fetch, ["--count", "2"])
        self.assertEqual(records[0]["poster"], _VOYAGE_RESIZED_POSTER)
        self.assertEqual(records[1]["poster"], _UNRATED_FEED_POSTER)

    def test_the_probe_fetches_the_resized_url_and_never_the_feeds_own(self) -> None:
        fetch = _RecordingFetch()
        self._run(fetch, [])
        # Exact list equality: one feed fetch, one probe of the REWRITTEN URL, and
        # nothing else -- no probe of the feed's own URL, and no second probe.
        self.assertEqual(fetch.urls, [_FEED_URL, _VOYAGE_RESIZED_POSTER])

    def test_a_feed_file_run_never_probes_and_keeps_the_unverified_rewrite(
        self,
    ) -> None:
        # The offline path must stay fully offline: samples/ regenerates through it
        # with no network at all. The price is that its rewrite goes unverified,
        # which film_scan.md documents.
        with redirect_stdout(io.StringIO()):
            code = film_scan.main(
                [
                    "--username",
                    _A_USERNAME,
                    "--no-director",
                    "--feed-file",
                    str(_FIXTURE_PATH),
                    "--output",
                    str(self.out),
                ],
                fetch=_explode_fetch,
            )
        self.assertEqual(code, 0)
        records = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(records[0]["poster"], _VOYAGE_RESIZED_POSTER)

    def test_a_poster_that_is_not_https_is_recorded_null_and_never_probed(
        self,
    ) -> None:
        # A description's first <img> is a third party's string. Before the https
        # rule, a file: URL here was rewritten to a size, fetched through urlopen
        # (which speaks file:) and written into the record. It is no poster: the
        # record says null, and the only request made is the feed's own.
        hostile = "file:///srv/private-0-600-0-900-crop.jpg"
        body = _FIXTURE.replace(_VOYAGE_FEED_POSTER.encode(), hostile.encode())
        self.assertNotEqual(body, _FIXTURE)
        fetch = _RecordingFetch(body=body)
        records = self._run(fetch, [])
        self.assertIsNone(records[0]["poster"])
        self.assertIn('"poster": null', self.out.read_text(encoding="utf-8"))
        self.assertEqual(fetch.urls, [_FEED_URL])

    def test_a_poster_with_no_size_suffix_is_not_probed(self) -> None:
        # Nothing was rewritten, so there is nothing to verify: the only fetch is
        # the feed's, and the record carries the feed's URL untouched.
        bare = "https://example.invalid/poster/voyage.jpg"
        body = _FIXTURE.replace(_VOYAGE_FEED_POSTER.encode(), bare.encode())
        self.assertNotEqual(body, _FIXTURE)
        fetch = _RecordingFetch(body=body)
        records = self._run(fetch, [])
        self.assertEqual(fetch.urls, [_FEED_URL])
        self.assertEqual(records[0]["poster"], bare)


if __name__ == "__main__":
    unittest.main()
