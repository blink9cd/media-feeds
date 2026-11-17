"""Grouped suite: `artwork_url` against a fake fetcher, one branch of the parse per test.

No test here touches the network: every fetch is a lambda returning a canned body.
"""

from __future__ import annotations

import datetime as dt
import http.client
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cover_art import artwork_url  # noqa: E402 - position only; keep below the bootstrap above
from cover_art import AlbumRef  # noqa: E402 - position only; keep below the bootstrap above
from _offline_guard import setUpModule, tearDownModule  # noqa: E402, F401 - the bootstrap above; unittest calls the hooks by name
from _artwork_url_fixtures import REF, fake_body, matching_result  # noqa: E402 - beside this module, via the loader's top_level_dir


class ArtworkUrlTest(unittest.TestCase):
    """`artwork_url` against a fake fetcher covering every branch of the parse."""

    def test_a_hit_returns_an_upsized_artwork_url(self) -> None:
        body = fake_body({"resultCount": 1, "results": [matching_result()]})
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertEqual(match.url, "https://example.com/a/400x400bb.webp")

    def test_a_result_for_a_DIFFERENT_album_yields_no_url(self) -> None:
        # The wrong-cover defect, as a unit test: taking the first result would return
        # this cover, and it belongs to a different record entirely.
        body = fake_body(
            {
                "resultCount": 1,
                "results": [
                    matching_result(collectionName="Some Other Record", trackCount=7)
                ],
            }
        )
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: body).url)

    def test_the_default_format_is_webp(self) -> None:
        # Measured 2026-09-01: the same 400x400 art is 14,192 bytes as WebP against
        # 34,757 as JPEG, 59% smaller at identical dimensions, indistinguishable at
        # the size it is rendered at.
        body = fake_body({"resultCount": 1, "results": [matching_result()]})
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertEqual(match.url, "https://example.com/a/400x400bb.webp")

    def test_the_format_is_the_callers_choice(self) -> None:
        body = fake_body({"resultCount": 1, "results": [matching_result()]})
        match = artwork_url(REF, fetch=lambda _u, _t: body, image_format="jpg")
        self.assertEqual(match.url, "https://example.com/a/400x400bb.jpg")

    def test_only_the_FINAL_extension_is_rewritten(self) -> None:
        # The trap. A real artwork URL carries the source file's own ".jpg" mid-path
        # (".../075679854247.jpg/100x100bb.jpg"), so a naive replace of ".jpg" would
        # corrupt the path and 404. Only the last segment's extension may change.
        body = fake_body(
            {
                "resultCount": 1,
                "results": [
                    matching_result(
                        artworkUrl100="https://example.com/a/075679854247.jpg/100x100bb.jpg"
                    )
                ],
            }
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertEqual(
            match.url, "https://example.com/a/075679854247.jpg/400x400bb.webp"
        )

    def test_the_requested_width_is_what_gets_substituted(self) -> None:
        # Measured 2026-09-01: Apple's resizer honored every width tried exactly, so
        # the size is the caller's to choose rather than a constant fixed in here.
        body = fake_body({"resultCount": 1, "results": [matching_result()]})
        match = artwork_url(REF, fetch=lambda _u, _t: body, width=250)
        self.assertEqual(match.url, "https://example.com/a/250x250bb.webp")

    def test_an_empty_result_set_yields_no_url(self) -> None:
        match = artwork_url(
            REF, fetch=lambda _u, _t: fake_body({"resultCount": 0, "results": []})
        )
        self.assertIsNone(match.url)

    def test_a_result_with_no_artwork_field_yields_no_url(self) -> None:
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(artworkUrl100=None)]}
        )
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: body).url)

    def test_a_non_string_artwork_field_yields_no_url(self) -> None:
        # The field exists but is the wrong type (e.g. a malformed upstream record),
        # a distinct branch from the field being absent entirely.
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(artworkUrl100=12345)]}
        )
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: body).url)

    def test_a_results_field_that_is_not_a_list_yields_no_url(self) -> None:
        # A 2xx body of the wrong shape: valid JSON, wrong contract. Distinct from
        # unparseable, and reachable from a proxy or an API change.
        body = fake_body({"resultCount": 1, "results": {"not": "a list"}})
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: body).url)

    def test_the_right_album_with_no_release_date_still_yields_its_url(self) -> None:
        # The album is identified by name; the year is simply unavailable. The cover
        # must still be used: the year is a bonus, not a precondition.
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(releaseDate=None)]}
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertEqual(match.url, "https://example.com/a/400x400bb.webp")
        self.assertIsNone(match.year)

    def test_an_unparseable_release_date_yields_a_url_with_no_year(self) -> None:
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(releaseDate="not-a-date")]}
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertIsNotNone(match.url)
        self.assertIsNone(match.year)

    def test_a_release_date_in_SUPERSCRIPT_digits_yields_a_url_with_no_year(
        self,
    ) -> None:
        # `releaseDate` is remote input, and "²⁰²⁵" satisfies str.isdigit() while int()
        # rejects it, so an isdigit() year guard admits a value the conversion then
        # refuses, raising ValueError out of the year parse at a call site outside the try
        # that guards the search. Regression for that uncaught raise: the never-raises
        # contract has to hold for every digit-ish year, not only for text that is
        # obviously not a date like the case above.
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(releaseDate="²⁰²⁵-01-01")]}
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertIsNotNone(match.url)
        self.assertIsNone(match.year)

    def test_a_release_date_in_NON_ASCII_decimal_digits_still_yields_its_year(
        self,
    ) -> None:
        # The other half of why that guard is isdecimal() rather than the stricter
        # `isascii() and isdigit()`: Arabic-Indic "٢٠٢٥" is a real year and int()
        # parses it to 2025, so clamping to ASCII would stop the raise above by
        # discarding a good answer instead. This pins the accepted half, so
        # over-tightening the guard fails here rather than passing silently.
        body = fake_body(
            {"resultCount": 1, "results": [matching_result(releaseDate="٢٠٢٥-01-01")]}
        )
        match = artwork_url(REF, fetch=lambda _u, _t: body)
        self.assertIsNotNone(match.url)
        self.assertEqual(match.year, 2025)

    def test_an_UNHASHABLE_collection_id_yields_a_url_rather_than_raising(
        self,
    ) -> None:
        # `collectionId` is remote input and is used as the pool's dedup key, so it
        # lands in a set. A JSON array or object there is legal JSON that decodes to
        # an unhashable list/dict, and an unguarded `key in seen` raises TypeError out
        # of `artwork_url`, from a line outside the try that guards `_search`, so the
        # never-raises contract breaks just as it would for the release year. An id
        # that cannot be hashed carries no usable identity, so it is treated as a
        # missing one: the candidate is kept and simply never deduped against.
        for unusable in ([1, 2], {"a": 1}):
            with self.subTest(collection_id=unusable):
                body = fake_body(
                    {
                        "resultCount": 1,
                        "results": [matching_result(collectionId=unusable)],
                    }
                )
                match = artwork_url(REF, fetch=lambda _u, _t: body)
                self.assertEqual(match.url, "https://example.com/a/400x400bb.webp")

    def test_a_non_json_body_yields_no_url(self) -> None:
        # A proxy can return a non-JSON body with a 2xx status; json.loads must be guarded.
        self.assertIsNone(
            artwork_url(REF, fetch=lambda _u, _t: b"<html>nope</html>").url
        )

    def test_a_network_failure_yields_no_url_rather_than_raising(self) -> None:
        def boom(_url: str, _timeout: float) -> bytes:
            raise OSError("no route to host")

        self.assertIsNone(artwork_url(REF, fetch=boom).url)

    def test_a_truncated_http_response_yields_no_url_rather_than_raising(self) -> None:
        # http.client.HTTPException (e.g. IncompleteRead, raised out of a real
        # response.read() on a truncated chunked body) is neither OSError nor
        # ValueError, so it needs its own arm in `_search`'s except clause, or it
        # propagates straight out of artwork_url.
        def truncated(_url: str, _timeout: float) -> bytes:
            raise http.client.IncompleteRead(b"partial")

        self.assertIsNone(artwork_url(REF, fetch=truncated).url)

    def test_a_pathologically_nested_body_yields_no_url_rather_than_raising(
        self,
    ) -> None:
        # json.loads' recursive-descent parser raises RecursionError (a RuntimeError,
        # not a ValueError) once nesting exceeds the interpreter's stack budget.
        # Real network bodies are not bounded the way the property test's
        # st.binary(max_size=2048) samples are, so this is reachable in production
        # even though it never surfaces from the capped fuzz strategy.
        deeply_nested = b"[" * 2_000_000
        self.assertIsNone(artwork_url(REF, fetch=lambda _u, _t: deeply_nested).url)

    def test_the_query_carries_both_artist_and_album(self) -> None:
        seen: list[str] = []

        def record(url: str, _timeout: float) -> bytes:
            seen.append(url)
            return fake_body({"resultCount": 0, "results": []})

        artwork_url(REF, fetch=record)
        self.assertIn("An+Artist", seen[0])
        self.assertIn("A+Record", seen[0])
        # both queries run and are pooled before ranking. Returning after the first
        # lets the wrong album win: the store does not always use the library's name,
        # so the right record can appear only in the second.
        self.assertEqual(len(seen), 2)
        self.assertNotIn("A+Record", seen[1])

    def test_the_timeout_is_passed_through_to_the_fetcher(self) -> None:
        seen: list[float] = []

        def record(_url: str, timeout: float) -> bytes:
            seen.append(timeout)
            return fake_body({"resultCount": 0, "results": []})

        artwork_url(REF, fetch=record, timeout=2.5)
        self.assertEqual(seen, [2.5, 2.5])

    def test_a_surrogate_in_the_artist_yields_no_url_rather_than_raising(self) -> None:
        # Regression guard: with urllib.parse.urlencode outside `_search`'s try
        # block, a lone surrogate in ref.artist (or ref.album) raises
        # UnicodeEncodeError straight out of this "never raises" function.
        # Unreachable via a real macOS folder scan (APFS rejects such names), but
        # reachable when the folder scan runs off macOS, where a lossy filename decode
        # can hand this exact input. UnicodeEncodeError is a ValueError subclass,
        # already in `_search`'s except tuple, so building the query inside the try is
        # the entire fix: this test fails without that and passes with it.
        bad_ref = AlbumRef(
            artist="An Artist \udcff", album="A Record", added=dt.date(2026, 8, 30)
        )
        body = fake_body({"resultCount": 0, "results": []})
        self.assertIsNone(artwork_url(bad_ref, fetch=lambda _u, _t: body).url)

    def test_a_surrogate_in_the_album_yields_no_url_rather_than_raising(self) -> None:
        bad_ref = AlbumRef(
            artist="An Artist", album="A Record \udcff", added=dt.date(2026, 8, 30)
        )
        body = fake_body({"resultCount": 0, "results": []})
        self.assertIsNone(artwork_url(bad_ref, fetch=lambda _u, _t: body).url)
