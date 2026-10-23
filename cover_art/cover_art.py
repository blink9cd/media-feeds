"""Resolve an album's cover-art URL at Apple's CDN, at a caller-chosen size.

A self-contained VENDOR ADAPTER: it owns the iTunes Search API traffic -- TWO queries
per cover resolved, pooled before ranking, for the reason `artwork_url` records -- and
the CDN's own URL-sizing scheme, and knows nothing about picking which album is newest.
`album_scan.py` is its caller.

The size is an ARGUMENT, never a constant chosen in here: the display size a cover is
rendered at is the consumer's concern, so the caller decides and this module only
knows how to say it in Apple's URL scheme. See `cover_art.md` for the measurement
behind the default, and for what the CDN does and does not honor.

**This module verifies that the album it found is the album it was asked for.** Taking
`results[0]` from a single-result query is the rejected alternative: any album the
store cannot find comes back as some OTHER album's cover, asserted as fact -- *Fictionals
(Gold)* would come back wearing the *Teal Album*'s art. Choosing between candidates is
`src/artwork_match.py`'s job and is kept pure there; this module owns the I/O half:
building the queries, pooling their results, and turning the winner into a cover URL
at the requested size and format.

Every network call goes through the injected `Fetcher` seam, so no unit test reaches
the host. The real seam is proved twice: a loopback server under `tests/` shows that
the body it reads is bounded in size and in time, and the live boundary test in the
same folder shows that the endpoint still answers in the shape this module expects.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import NamedTuple, Protocol

# This tool's own pure matching half (src/artwork_match.py), put on sys.path from this
# file's own folder. Nothing else: `AlbumRef` is defined below rather than imported, so
# this module -- and album_scan, which takes the type from here -- reaches no other
# project.
sys.path[:0] = [str(Path(__file__).resolve().parent / "src")]

from artwork_match import (  # noqa: E402 - follows the sys.path bootstrap
    pick_album,
    release_year,
)


class AlbumRef(NamedTuple):
    """One album, identified by artist, album, and the date it was added.

    media_feeds' own copy, and the only one here: album_scan's sources produce it and
    this module resolves its cover. It lives in this module because both of them
    import it and this one imports neither, so no cycle can form. The
    apple-music-toolbox defines a type of the same shape, which album_scan converts
    at its Apple boundary; a test in the monorepo keeps the two in step, and neither
    export needs the other.
    """

    artist: str
    album: str
    added: dt.date


# (url, timeout_seconds) -> response body. Injected so both the found and not-found
# branches stay reachable without a network, and so no unit test can reach the host.
Fetcher = Callable[[str, float], bytes]

_SEARCH_ENDPOINT = "https://itunes.apple.com/search"

# The API returns a 100x100 thumbnail URL; every other size and format is the SAME URL
# with its final segment rewritten, and Apple honors both exactly. See
# `cover_art.md` for the measurements behind these two defaults.
DEFAULT_ARTWORK_PX = 400
DEFAULT_ARTWORK_FORMAT = "webp"

# Only the formats actually measured against the CDN. `webp` is 59% smaller than `jpg`
# at identical dimensions; a caller wanting the widest possible decoder support asks
# for `jpg`. Anything else is unverified, so the CLI (`--artwork-format`) refuses it
# rather than emitting a URL nobody has confirmed the service serves.
ARTWORK_FORMATS = ("webp", "jpg")

# Rewrites the FINAL path segment only, which is the whole subtlety. A real artwork URL
# carries the source file's own extension mid-path
# (".../075679854247.jpg/100x100bb.jpg"), so a plain `.jpg` replace would corrupt the
# path and 404; the pattern is anchored to the end of the URL. It captures Apple's own
# rendition suffix (the "bb") and re-emits it, since that is not ours to invent, and a
# URL whose last segment is not `/<W>x<H><suffix>.<ext>` matches nothing and comes back
# unchanged -- the original, still-valid URL -- rather than mangled.
_FINAL_SEGMENT = re.compile(r"/\d+x\d+(?P<suffix>[a-z-]*)\.[A-Za-z0-9]+$")

# The only scheme a recorded cover URL may carry. The value is destined for an
# `<img src>` on a page, so anything else -- `javascript:`, `data:`, `file:`, or plain
# `http:` -- is a string the page must never be handed, whatever the store said.
_RECORDABLE_SCHEME = "https"

# Unicode general categories starting with this letter are the control, format,
# surrogate, private-use and unassigned characters: none belongs in a URL, and a
# genuine artwork URL never contains one.
_UNSAFE_CATEGORY_PREFIX = "C"

_LOOKUP_TIMEOUT_SECONDS = 10.0

# The most a search reply may grow to before the read is abandoned. A reply holding
# the full `_SEARCH_LIMIT` of results is tens of kilobytes, so this is two orders of
# magnitude of headroom: a body past it is not a search reply, and a server that never
# stops sending has to be cut off somewhere short of memory.
_BODY_LIMIT_BYTES = 4 * 1024 * 1024

# How long the whole body may take to arrive, checked between chunks. The socket
# timeout alone cannot end a server that sends one byte every few seconds, because
# every byte restarts it. A genuine reply finishes in well under a second even on a
# slow link; this bounds a drip while leaving that reply a wide margin. The longest a
# fetch can then wait is this plus ONE socket timeout (see `_read_body`).
_BODY_DEADLINE_SECONDS = 15.0

# The ceiling on what one `read1` call may return, not an amount waited for: `read1`
# hands back whatever a single socket read produced, so this only decides how often
# the cap and the deadline above are re-checked.
_READ_CHUNK_BYTES = 64 * 1024

# Not 1: with a single result there is nothing to choose BETWEEN, so the lookup could
# only accept whatever arrived -- the wrong-cover defect. A wider window is what makes
# verification possible at all.
_SEARCH_LIMIT = 25


class ArtworkMatch(NamedTuple):
    """The chosen album's cover URL and its release year, either of which may be None.

    Both come from the same chosen candidate, so they are returned together rather than
    resolved by two searches that could disagree with each other.

    `year` is the STORE's release year and is only ever a fallback for an album
    Music.app has no year for. It must never be fed back in as a matching signal --
    ranking an iTunes candidate by a year taken from iTunes confirms whatever it picked.
    """

    url: str | None
    year: int | None


def _search(term: str, fetch: Fetcher, timeout: float) -> list[dict]:
    """One album search, or an empty list on any failure. Never raises.

    A cover that cannot be resolved is a normal outcome, not an error: a consumer must
    already read correctly with a null artwork URL, so it has nothing useful to do with
    an exception. The query is built INSIDE the try, not before it: `urlencode` raises
    `UnicodeEncodeError` -- a `ValueError` subclass already caught here -- on a lone
    surrogate in `ref.artist`/`ref.album`. Unreachable via APFS, which rejects such
    names in a real folder scan, but reachable when the folder scan runs off macOS,
    where a lossy filename decode (surrogateescape) can hand this function exactly that.
    """
    try:
        query = urllib.parse.urlencode(
            {"term": term, "entity": "album", "limit": _SEARCH_LIMIT}
        )
        payload = json.loads(fetch(f"{_SEARCH_ENDPOINT}?{query}", timeout))
        results = payload["results"]
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        http.client.HTTPException,
        RecursionError,
    ):
        return []
    if not isinstance(results, list):
        return []
    return [result for result in results if isinstance(result, dict)]


def _is_recordable_url(url: str) -> bool:
    """Whether a cover URL may be recorded for a page to use as an image source.

    Admitted: an `https` URL with a non-empty host and no userinfo. Refused: every
    other scheme, a scheme-relative `//host`, an empty host, a `user:password@`
    prefix, and any string carrying whitespace or a Unicode control or format
    character. There is deliberately no allowlist of vendor hosts: the rule is about
    what a page can safely be handed, not about which CDN is currently in use.

    The character scan runs on the raw string and comes BEFORE the parse, because the
    parser is not a filter: `urlsplit` silently strips tab, carriage return and line
    feed before it parses, so a URL smuggling a header break parses clean, and it
    never looks at a NUL, an escape or a zero-width mark at all. The parser's own
    `ValueError` (an unbalanced IPv6 bracket) is the one shape it does refuse.
    """
    if any(
        char.isspace() or unicodedata.category(char).startswith(_UNSAFE_CATEGORY_PREFIX)
        for char in url
    ):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (
        parts.scheme == _RECORDABLE_SCHEME
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
    )


class _BodyReader(Protocol):
    """The one method of an HTTP response the body reader relies on.

    An `http.client.HTTPResponse` provides it, and so does `io.BytesIO`, which is what
    lets the reader be proved against an in-memory body.
    """

    def read1(self, size: int = ..., /) -> bytes:
        """Up to `size` bytes from at most one underlying read; empty at the end."""


def _read_body(
    response: _BodyReader,
    *,
    limit: int,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """The whole body of `response`, bounded in size and in time.

    Read with `read1`, never `read(n)`: `read(n)` blocks until `n` bytes have arrived
    or the body ends, so a byte-at-a-time server would hold it exactly as long as it
    holds a bare `read()`. `read1(n)` returns whatever ONE underlying read produced,
    which is what lets the deadline be checked between chunks at all. The socket
    timeout still bounds each of those reads, so the longest a fetch can wait is
    `deadline` plus one socket timeout: the read in flight when the deadline passes
    is ended only by the socket's own timer.

    Raises `ValueError` once more than `limit` bytes have arrived -- the body is not a
    search reply -- and `TimeoutError` once a chunk lands `deadline` seconds or more
    after the read began. Both are already in the family `_search` swallows
    (`TimeoutError` is an `OSError`), so an oversized or dripping reply is the same
    "no cover" outcome as any other failed fetch. `clock` is injectable so the
    deadline is proved by readings rather than by waiting it out; the bounds are
    required rather than defaulted so their one source of truth is the module
    constant the caller names, which a test can patch and the real path will see.
    """
    started = clock()
    chunks: list[bytes] = []
    received = 0
    while chunk := response.read1(_READ_CHUNK_BYTES):
        received += len(chunk)
        if received > limit:
            raise ValueError(f"response body exceeds {limit} bytes")
        if clock() - started >= deadline:
            raise TimeoutError(f"response body still arriving after {deadline} seconds")
        chunks.append(chunk)
    return b"".join(chunks)


def _real_fetch(url: str, timeout: float) -> bytes:
    """Fetch a URL: `timeout` bounds each socket operation, `_read_body` the body.

    The timeout is on the call itself, not on an enclosing loop: an outer deadline can
    only fire BETWEEN calls and cannot interrupt one already in flight. It bounds each
    socket read, though, not the body as a whole -- that is `_read_body`'s job. The
    bounds are passed by name from the module constants, read at call time, so a test
    can patch them small and exercise this real path against a loopback server.

    An HTTP error is still a RESPONSE: `urlopen` raises it BEFORE the `with` below can
    bind, carrying the error page's open body, and `_search` then drops it as one more
    `OSError`. Left open it leaks the connection and surfaces later as a
    ResourceWarning nowhere near its cause, so it is closed here -- the one place that
    knows the exception owns a stream -- and re-raised unchanged.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed https endpoint
            return _read_body(
                response, limit=_BODY_LIMIT_BYTES, deadline=_BODY_DEADLINE_SECONDS
            )
    except urllib.error.HTTPError as error:
        error.close()
        raise


# fuzz-target
def artwork_url(
    ref: AlbumRef,
    *,
    fetch: Fetcher = _real_fetch,
    timeout: float = _LOOKUP_TIMEOUT_SECONDS,
    width: int = DEFAULT_ARTWORK_PX,
    image_format: str = DEFAULT_ARTWORK_FORMAT,
    year: int | None = None,
    tracks: int | None = None,
) -> ArtworkMatch:
    """The album's cover URL and release year, or Nones when no candidate IS it.

    Never raises. A cover that cannot be found is a normal outcome, not an error: a
    consumer must already read correctly with a null artwork URL, so the caller has
    nothing useful to do with an exception. Search failures -- a lone surrogate in
    `ref.artist`/`ref.album` included -- are absorbed inside `_search`.

    `width` is the square edge length to request and `image_format` the file type.
    Neither is validated here: Apple's thumbnailer honored every width AND both formats
    measured, so there is no invalid-request failure mode to guard -- a CDN serving
    pre-generated sizes would need one. The CLI still restricts `image_format` to
    `ARTWORK_FORMATS`, because an unmeasured format is a claim about the service nobody
    has checked. See `cover_art.md`.

    WHY TWO QUERIES, POOLED BEFORE RANKING. The store does not always use the library's
    name for an album: it calls *Fictionals (Gold)* plain `Fictionals`, so the `(Gold)` token
    matches nothing and the right record is absent from the album-and-artist query
    entirely. The artist-alone query does contain it. Returning as soon as the FIRST
    query produced any name match is the rejected alternative: it kept choosing
    *Fictionals (Teal Album)* -- both names reduce to `fictionals`, so the narrow query
    "succeeded" and the query holding the right answer never ran. Both queries always
    run and are pooled before ranking, which is why this is not a loop that returns
    early.

    `year` and `tracks` are what Music.app reports for THIS library's copy. `pick_album`
    ranks by them after the name, and admits a same-artist candidate whose name does
    not match only when BOTH agree. They are never required: an unreachable Music.app
    degrades the match to name-only, which is weaker and honestly so.
    """
    pool: list[dict] = []
    seen: set[object] = set()
    for term in (f"{ref.artist} {ref.album}", ref.artist):
        for candidate in _search(term, fetch, timeout):
            key = candidate.get("collectionId")
            if not isinstance(key, Hashable):
                # Remote input, and what the set below needs is a HASHABLE key, not
                # merely a non-None one -- the same screen-vs-consumer mismatch
                # `release_year` documents. A JSON array or object is legal JSON,
                # decodes to an unhashable list/dict, and would raise TypeError from the
                # membership test. `isinstance` is sound here because these values
                # arrive from `json.loads`, which cannot produce the ABC's one false
                # positive -- a tuple holding an unhashable member. An id that cannot be
                # hashed is no identity at all, so it is treated as a MISSING one: the
                # candidate is kept, and simply never deduped against.
                key = None
            if key is not None and key in seen:
                continue
            seen.add(key)
            pool.append(candidate)
    chosen = pick_album(
        pool, artist=ref.artist, album=ref.album, year=year, tracks=tracks
    )
    if chosen is None:
        return ArtworkMatch(url=None, year=None)
    chosen_year = release_year(chosen)
    thumb = chosen.get("artworkUrl100")
    if not isinstance(thumb, str):
        # The right album, with no usable art. Its year is still worth keeping.
        return ArtworkMatch(url=None, year=chosen_year)
    upsized = _FINAL_SEGMENT.sub(
        lambda match: f"/{width}x{width}{match['suffix']}.{image_format}", thumb
    )
    if not _is_recordable_url(upsized):
        # Judged AFTER the rewrite, because the rewritten string is the value actually
        # recorded, and the rewrite can only ADD what the caller passed as
        # `image_format`: a thumbnail that passed before the rewrite could still come
        # out hostile. Refusing it is the same outcome as unusable art -- the right
        # album, whose year is still worth keeping.
        return ArtworkMatch(url=None, year=chosen_year)
    return ArtworkMatch(url=upsized, year=chosen_year)
