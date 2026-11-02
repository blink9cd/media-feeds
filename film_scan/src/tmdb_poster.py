"""Look up a film's poster on TMDB: the request, the answer's parse, and the URL rule.

Private to `film_scan/`: the sibling `film_scan.py` primary script is this module's
only importer, and re-exports everything `tests/` needs as `film_scan.<name>`.

Why TMDB: Letterboxd's own poster host answers every scripted request with HTTP 403
and Cloudflare's `error code: 1010`, a refusal by browser signature that no honest
client clears, and this project never imitates a browser. Letterboxd's posters come
from TMDB, the diary feed already names each film's TMDB id, and TMDB's API is meant
for scripted use. One `GET /3/movie/{id}` per recorded film answers with a
`poster_path`, and the poster is TMDB's `w500` rendition of it: the smallest TMDB
size at least as wide as the 400px poster the card used before.

No network here: the fetch is injected (`tmdb_poster_lookup`'s `fetch`), and
`film_scan.main` hands over `feed_read.real_fetch_with_headers` on a live run only.
Every failure is "no poster" for that film alone, never an error for the run.
"""

from __future__ import annotations

import http.client
import json
import re
import sys
import urllib.error
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType

# Put the sibling feed_read tool folder on sys.path from this repository's root, two
# folders above this file (film_scan/src/), so this module imports alone as well. One
# fixed candidate rather than a search up the ancestors, for film_scan.py's reason.
sys.path += [
    str(Path(__file__).resolve().parents[2] / "feed_read"),
]

import feed_read

#: TMDB's movie-details endpoint. The id is checked by `tmdb_movie_url` before it is
#: put here, so nothing but digits ever reaches the path.
TMDB_MOVIE_URL = "https://api.themoviedb.org/3/movie/{tmdb_id}"

#: TMDB's image host at the `w500` size: a poster URL is this plus a `poster_path`.
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

#: The one shape a `poster_path` may take: a single file name of ASCII letters, digits,
#: `_`, and `-` under the root, with an image extension in either case. ASCII as well as
#: case-insensitive, because a Unicode case-insensitive `[A-Za-z]` also matches the
#: Kelvin sign and the long s. Anything else (a second segment, a `..`, a query, a
#: whole URL) is a third party's string this tool will not publish.
_POSTER_PATH = re.compile(
    r"/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png)", re.ASCII | re.IGNORECASE
)

#: A TMDB id is all ASCII digits. `str.isdigit` is not this test: it accepts other
#: scripts' digits and superscripts, which are not ids and would reshape nothing useful.
_TMDB_ID = re.compile(r"[0-9]+", re.ASCII)


# fuzz-target
def poster_url_or_none(poster_path: object) -> str | None:
    """The `w500` poster URL for `poster_path`, or None unless it is a plain file name.

    Takes `object`, not `str`: the value is whatever TMDB's JSON held under the key,
    and a null (a film with no poster) or a number must come back None, not raise.
    """
    if isinstance(poster_path, str) and _POSTER_PATH.fullmatch(poster_path):
        return TMDB_IMAGE_BASE + poster_path
    return None


# fuzz-target
def poster_from_movie_json(body: bytes) -> str | None:
    """The poster URL in a movie-details answer, or None for any other body.

    `RecursionError` is caught beside `ValueError` (which covers both a malformed
    document and undecodable bytes): the body is bounded in size, not in depth, and a
    few hundred kilobytes of `[` exhausts the parser's recursion limit.
    """
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    return poster_url_or_none(payload.get("poster_path"))


def tmdb_movie_url(tmdb_id: str) -> str | None:
    """The movie-details URL for `tmdb_id`, or None unless the id is all ASCII digits."""
    if _TMDB_ID.fullmatch(tmdb_id):
        return TMDB_MOVIE_URL.format(tmdb_id=tmdb_id)
    return None


def tmdb_poster_lookup(
    token: str, fetch: feed_read.HeaderFetcher
) -> Callable[[str], str | None]:
    """A `film_parse.PosterLookup` that asks TMDB with `token`, through `fetch`.

    The token travels only in the `Authorization: Bearer` header, which
    `bounded_fetch` re-attaches on a redirect only within the same origin. No failure
    is reported from here: an exception's text never leaves this function, so no
    message can carry the token, and the caller counts the films left without a
    poster instead. A token that cannot be a header value is refused by
    `bounded_fetch.build_request` with a `ValueError` naming neither, which lands in
    the same arm. The header set is read-only because every film's request shares it.
    """
    headers = MappingProxyType(
        {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )

    def lookup(tmdb_id: str) -> str | None:
        """The poster URL TMDB gives for `tmdb_id`, or None on any failure."""
        url = tmdb_movie_url(tmdb_id)
        if url is None:
            return None
        try:
            body = fetch(url, feed_read.FEED_TIMEOUT_SECONDS, headers)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            # An HTTPError is the refused response (a 401 for a bad token, a 404
            # for an id TMDB dropped): close it, or CPython warns at garbage
            # collection that the file was implicitly cleaned up.
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            return None
        return poster_from_movie_json(body)

    return lookup
