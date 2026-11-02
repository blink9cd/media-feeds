"""The TMDB poster lookup on its own: the URL rule, the JSON parse, and the request.

Every test injects the header-taking fetch, so nothing here reaches the network. The
CLI's use of the lookup (the token from the environment, the stderr report, the
offline path) is `tests/test_film_scan_tmdb_poster.py`'s.

The `test_film_scan_` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import email.message
import http.client
import json
import sys
import unittest
import urllib.error
from collections.abc import Mapping
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import film_scan  # noqa: E402 - import follows the sys.path bootstrap above

#: An invented token, easy to spot if any message under test ever echoed it.
_TOKEN = "tok-SENTINEL-NEVER-PRINTED"

#: What the lookup records for a film whose TMDB answer names `/abc123.jpg`.
_POSTER = "https://image.tmdb.org/t/p/w500/abc123.jpg"

#: One recorded call to the fake fetch: URL, timeout, headers.
_Call = tuple[str, float, dict[str, str]]


class _RecordingFetch:
    """A header-taking fetch fake: answers `body`, or raises `error`, and logs calls."""

    def __init__(self, body: bytes = b"", error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[_Call] = []

    def __call__(self, url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
        self.calls.append((url, timeout, dict(headers)))
        if self.error is not None:
            raise self.error
        return self.body


def _movie(poster_path: object) -> bytes:
    """A TMDB movie-details body carrying `poster_path`."""
    return json.dumps({"id": 1, "title": "x", "poster_path": poster_path}).encode()


class PosterUrlRuleTest(unittest.TestCase):
    """`poster_url_or_none`: only a single plain image file name becomes a URL."""

    def test_a_plain_jpg_path_becomes_the_w500_url(self) -> None:
        self.assertEqual(film_scan.poster_url_or_none("/abc123.jpg"), _POSTER)

    def test_the_extension_is_case_insensitive_and_jpeg_and_png_pass(self) -> None:
        for path in ("/a_B-9.JPG", "/a.jpeg", "/a.Png"):
            with self.subTest(path=path):
                self.assertEqual(
                    film_scan.poster_url_or_none(path),
                    film_scan.TMDB_IMAGE_BASE + path,
                )

    def test_every_other_shape_is_no_poster(self) -> None:
        refused: tuple[object, ...] = (
            None,
            42,
            "",
            "abc.jpg",
            "/.jpg",
            "/a.gif",
            "/a/b.jpg",
            "/../a.jpg",
            "/a.jpg\n",
            "/a.jpg?x=1",
            "/a b.jpg",
            "https://elsewhere.invalid/a.jpg",
            # The Kelvin sign case-folds to "k" under a Unicode IGNORECASE: the rule
            # is ASCII, so it must not slip through as a letter.
            "/K.jpg",
            "/a.Kpg",
        )
        for path in refused:
            with self.subTest(path=path):
                self.assertIsNone(film_scan.poster_url_or_none(path))


class MovieJsonTest(unittest.TestCase):
    """`poster_from_movie_json`: a body in, a poster URL or None out, never a raise."""

    def test_a_movie_with_a_poster_path_gives_its_url(self) -> None:
        self.assertEqual(
            film_scan.poster_from_movie_json(_movie("/abc123.jpg")), _POSTER
        )

    def test_a_null_poster_path_is_no_poster(self) -> None:
        self.assertIsNone(film_scan.poster_from_movie_json(_movie(None)))

    def test_a_missing_poster_path_is_no_poster(self) -> None:
        self.assertIsNone(film_scan.poster_from_movie_json(b'{"id": 1}'))

    def test_an_invalid_poster_path_is_no_poster(self) -> None:
        self.assertIsNone(film_scan.poster_from_movie_json(_movie("/x/../y.jpg")))

    def test_a_body_that_is_not_an_object_is_no_poster(self) -> None:
        for body in (b"[]", b'"/a.jpg"', b"null", b"3"):
            with self.subTest(body=body):
                self.assertIsNone(film_scan.poster_from_movie_json(body))

    def test_a_body_that_is_not_json_is_no_poster(self) -> None:
        for body in (b"", b"<html>", b"\xff\xfe\xfa", b'{"poster_path": '):
            with self.subTest(body=body):
                self.assertIsNone(film_scan.poster_from_movie_json(body))

    def test_a_pathologically_nested_body_is_no_poster_not_a_recursion_error(
        self,
    ) -> None:
        # A body is bounded in size, not in depth: a few hundred kilobytes of `[`
        # exhausts the parser's recursion limit, which raises RecursionError, not
        # the ValueError a malformed body raises.
        self.assertIsNone(film_scan.poster_from_movie_json(b"[" * 200_000))


class MovieUrlTest(unittest.TestCase):
    """`tmdb_movie_url`: only an all-ASCII-digit id is put into a URL."""

    def test_a_digit_id_fills_the_template(self) -> None:
        self.assertEqual(
            film_scan.tmdb_movie_url("603"), "https://api.themoviedb.org/3/movie/603"
        )

    def test_any_other_id_is_refused(self) -> None:
        # "٣" is ARABIC-INDIC DIGIT THREE: str.isdigit() accepts it, the rule
        # must not.
        for tmdb_id in ("", "12a", "-1", "1/2", "1?x=1", " 1", "1\n", "٣", "²"):
            with self.subTest(tmdb_id=tmdb_id):
                self.assertIsNone(film_scan.tmdb_movie_url(tmdb_id))


class LookupRequestTest(unittest.TestCase):
    """`tmdb_poster_lookup`: the one request per film, and every failure as None."""

    def test_the_request_carries_the_bearer_token_and_a_timeout(self) -> None:
        fetch = _RecordingFetch(_movie("/abc123.jpg"))
        lookup = film_scan.tmdb_poster_lookup(_TOKEN, fetch)
        self.assertEqual(lookup("603"), _POSTER)
        [(url, timeout, headers)] = fetch.calls
        self.assertEqual(url, "https://api.themoviedb.org/3/movie/603")
        self.assertGreater(timeout, 0)
        self.assertEqual(headers["Authorization"], f"Bearer {_TOKEN}")
        self.assertEqual(headers["Accept"], "application/json")

    def test_an_id_that_is_not_digits_is_never_asked_about(self) -> None:
        fetch = _RecordingFetch(_movie("/abc123.jpg"))
        lookup = film_scan.tmdb_poster_lookup(_TOKEN, fetch)
        self.assertIsNone(lookup("603/images"))
        self.assertEqual(fetch.calls, [])

    def test_every_fetch_failure_is_no_poster(self) -> None:
        failures: tuple[Exception, ...] = (
            OSError("network unreachable"),
            TimeoutError("timed out"),
            ValueError("refusing a request header that is not printable ASCII"),
            http.client.IncompleteRead(b""),
        )
        for error in failures:
            with self.subTest(error=type(error).__name__):
                lookup = film_scan.tmdb_poster_lookup(
                    _TOKEN, _RecordingFetch(error=error)
                )
                self.assertIsNone(lookup("603"))

    def test_a_refused_response_is_closed_and_is_no_poster(self) -> None:
        # An HTTPError is the response, body and all: left unclosed, CPython warns
        # at garbage collection, far from the cause.
        error = urllib.error.HTTPError(
            "https://api.themoviedb.org/3/movie/603",
            401,
            "Unauthorized",
            email.message.Message(),
            None,
        )
        lookup = film_scan.tmdb_poster_lookup(_TOKEN, _RecordingFetch(error=error))
        self.assertIsNone(lookup("603"))
        self.assertTrue(error.fp.closed, "the HTTP error response was left open")

    def test_a_bad_body_is_no_poster(self) -> None:
        lookup = film_scan.tmdb_poster_lookup(_TOKEN, _RecordingFetch(b"<html>"))
        self.assertIsNone(lookup("603"))

    def test_the_headers_handed_over_are_read_only(self) -> None:
        # One header set serves every film in the run; a fetch that mutated it would
        # change what the next film's request sends.
        seen: list[Mapping[str, str]] = []

        def fetch(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
            del url, timeout
            seen.append(headers)
            return _movie("/abc123.jpg")

        film_scan.tmdb_poster_lookup(_TOKEN, fetch)("603")
        with self.assertRaises(TypeError):
            seen[0]["Authorization"] = "changed"  # type: ignore[index]


class NeverRaisesPropertyTest(unittest.TestCase):
    """The URL rule and the JSON parse take a third party's data: neither may raise."""

    @given(
        st.one_of(
            st.none(),
            st.integers(),
            st.text(),
            st.from_regex(r"/[A-Za-z0-9_.-]{0,12}\.(jpg|png|gif)", fullmatch=True),
        )
    )
    def test_fuzz_poster_url_or_none(self, poster_path: object) -> None:
        result = film_scan.poster_url_or_none(poster_path)
        if result is not None:
            self.assertTrue(result.startswith(film_scan.TMDB_IMAGE_BASE + "/"))
            self.assertEqual(
                result.count("/"), film_scan.TMDB_IMAGE_BASE.count("/") + 1
            )

    @given(
        st.one_of(
            st.binary(),
            st.recursive(
                st.none() | st.booleans() | st.integers() | st.text(),
                lambda inner: st.lists(inner) | st.dictionaries(st.text(), inner),
            ).map(lambda value: json.dumps(value).encode()),
            st.dictionaries(
                st.just("poster_path"), st.one_of(st.none(), st.text(), st.integers())
            ).map(lambda value: json.dumps(value).encode()),
        )
    )
    def test_fuzz_poster_from_movie_json(self, body: bytes) -> None:
        result = film_scan.poster_from_movie_json(body)
        self.assertTrue(result is None or result.startswith(film_scan.TMDB_IMAGE_BASE))


if __name__ == "__main__":
    unittest.main()
