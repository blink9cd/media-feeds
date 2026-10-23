"""A hardened HTTPS GET, bounded in what it may open, send, and read back.

One rule for which URLs may be requested at all (`https_url_or_none`), one place a
request is built (`build_request`, which refuses everything that rule refuses and any
header that would break the request's framing), and one bounded body read
(`read_body`, capped in size and in time). `fetch_url` chains the three around an
opener from `build_opener`, whose redirect handler follows only https and keeps the
caller's headers in their origin; it is the ONE network seam, which every unit test
leaves alone -- the pieces either side of it are tested on their own.

A pure library: no CLI, no `__main__`, standard library only. Each caller decides its
own headers (a User-Agent a vendor requires, a bearer token) and its own timeout; this
module decides only what is safe to send and how much may come back.
"""

from __future__ import annotations

import http.client
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import IO, NoReturn, Protocol

#: Sized for a whole feed body (a real Goodreads read shelf measured ~187 KB at
#: per_page=50). It bounds each blocking socket operation -- the connect, and every
#: read of the body -- because that is the only place a bound can interrupt a call
#: already in flight; the two constants below bound what a sequence of such calls may
#: add up to. A default for callers, not a rule: `fetch_url` takes its timeout from
#: the caller.
DEFAULT_TIMEOUT_SECONDS = 15.0

#: The most a response body may grow to before the read is abandoned. Generous
#: against the largest real feed measured (the ~187 KB body above, more than twenty
#: times over): a body past this is not a feed or an API answer, and a server that
#: never stops sending has to be cut off somewhere short of memory.
BODY_LIMIT_BYTES = 4 * 1024 * 1024

#: How long the whole body may take to arrive, checked between chunks. The socket
#: timeout alone cannot end a server that sends one byte every few seconds: each
#: byte restarts it, and a fetch stays open for as long as the server cares to drip.
#: Four socket timeouts: a full-size body still arrives over a slow link (a little
#: under 70 KB/s), while a drip-feed is cut off within the minute.
BODY_DEADLINE_SECONDS = 60.0

#: `read1` returns at most this much per underlying socket read, so the cap and the
#: deadline are re-checked at least this often.
_READ_CHUNK_BYTES = 64 * 1024

#: Unicode general categories starting with this letter are the control, format,
#: surrogate, private-use and unassigned characters. None belongs unescaped in a URL,
#: and no genuine vendor URL carries one, so `https_url_or_none` refuses the whole
#: class rather than naming the few that are known to do damage. The same constant,
#: for the same reason, is `cover_art._UNSAFE_CATEGORY_PREFIX` in media_feeds.
_UNSAFE_CATEGORY_PREFIX = "C"

#: Why a header is refused, and all the refusal says: never the name, never the value,
#: because a value may be a credential.
_HEADER_REFUSAL = "refusing a request header that is not printable ASCII"

#: Why a redirect is refused, and all the refusal says: never the target URL.
_REDIRECT_REFUSAL = "refusing a redirect to anything but an https URL with a host"


# fuzz-target
def https_url_or_none(url: str) -> str | None:
    """`url` when it is an https URL with a host, else None.

    The one rule for a URL a record may carry or a request may be built from. A
    caller applies it wherever it decides a URL (so a rejected one becomes "no URL"
    in its own terms), and `build_request` applies it again at the sink, so none can
    reach the opener by any route.

    Admitted: an https URL with a non-empty host, no userinfo, and no whitespace or
    Unicode control or format character. Refused: every other scheme, a
    scheme-relative `//host/x`, an empty host, a `user:password@` prefix, and a
    string `urlsplit` itself refuses (an unclosed IPv6 bracket), which comes back as
    "no URL" rather than as a `ValueError` out of a parser. There is deliberately NO
    allowlist of hosts: the rule is about what a page can safely be handed and what
    this module may open, not about which CDN a vendor is using this year.

    The character scan runs on the RAW string and comes BEFORE the parse, because
    parsing is not filtering: `urlsplit` silently strips tab, carriage return and
    line feed, so a URL smuggling a header break parses clean, and it never looks at
    a NUL, an escape or a zero-width mark at all. It scans the characters that are
    actually there, so a percent-ESCAPED space or control character is ordinary URL
    text and survives. This is `cover_art._is_recordable_url`'s rule in
    media_feeds, character for character; the two were written apart and the weaker
    one let all of the above through.

    urllib's opener speaks `file:`, `ftp:` and `data:` as readily as https, and none of
    those passes here, so none is ever opened. An https request to an internal
    address fails at the TLS handshake, on certificate verification, before any
    request line is sent: no public authority issues a certificate for a private
    address. That holds for the FIRST hop only -- redirects are followed, and where
    they go is the server's to say (an accepted risk, recorded 2026-09-21) -- so it
    is not a guarantee that nothing internal is ever reached.

    Accepted verbatim, uppercase scheme included: urllib lowercases the scheme
    itself when it picks a handler.
    """
    if any(
        char.isspace() or unicodedata.category(char).startswith(_UNSAFE_CATEGORY_PREFIX)
        for char in url
    ):
        return None
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if (
        parts.scheme == "https"
        and parts.hostname
        and parts.username is None
        and parts.password is None
    ):
        return url
    return None


def _is_printable_ascii(text: str) -> bool:
    """True when every character of `text` is ASCII from the space to the tilde.

    That excludes CR, LF and NUL, which would end a header early and start another
    of the sender's choosing (a header injection) or truncate it in whatever reads it
    next; tab, DEL and the other controls; and every non-ASCII character, which
    `http.client` would try to encode as latin-1 -- raising, for anything outside it,
    a `UnicodeEncodeError` whose args and repr carry the whole value.
    """
    return text.isascii() and text.isprintable()


def build_request(url: str, headers: Mapping[str, str]) -> urllib.request.Request:
    """A GET request for `url`, carrying exactly the caller's `headers`.

    Raises `ValueError` unless `https_url_or_none` accepts `url`. This is the one
    place a request is built for `fetch_url`, so no caller can send anything but
    https, whatever it was handed; the message names the rule, never the URL, so no
    refusal can echo a URL that carries an identity.

    Raises `ValueError` too when a header name or value is not printable ASCII
    (`_is_printable_ascii` gives the reasons). That message names neither the header
    nor its value, because a value may be a credential.

    The headers are attached as UNREDIRECTED headers, which urllib's redirect
    handler never copies to the next hop. Its stock handler copies every ordinary
    header to whichever host and scheme the server names, so a credential would
    follow a redirect anywhere; `build_opener`'s handler re-attaches them only within
    the same origin.

    Kept apart from `fetch_url`'s `opener.open` (a network seam excluded from coverage)
    so the headers and the refusals are all unit-testable: a dropped header fails a
    test instead of showing up as a live refusal from the server.
    """
    if https_url_or_none(url) is None:
        raise ValueError("refusing to request anything but an https URL with a host")
    if not all(
        _is_printable_ascii(name) and _is_printable_ascii(value)
        for name, value in headers.items()
    ):
        raise ValueError(_HEADER_REFUSAL)
    request = urllib.request.Request(url)
    for name, value in headers.items():
        request.add_unredirected_header(name, value)
    return request


def _origin(url: str) -> tuple[str, str, int | None]:
    """`url`'s scheme, lowercased host and explicit port: what a redirect must keep.

    Raises `urlsplit`'s own `ValueError` for a port it cannot read, whose message may
    quote the port text; the redirect handler turns that into its own refusal.
    """
    parts = urllib.parse.urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to https, carrying the caller's headers only in-origin.

    The https rule at `build_request` sees the first hop alone; every later hop is
    decided here, so a server cannot redirect a fetch onto plain http, `file:` or
    `ftp:`. The caller's headers ride the next hop only when its scheme, host and
    port are the redirecting request's: a path moved on the same server (a trailing
    slash) still gets the User-Agent it may require, while a credential or a
    contact address never reaches a host the caller did not name.
    """

    def __init__(self, headers: Mapping[str, str]) -> None:
        """Hold the caller's headers, to re-attach on a same-origin hop."""
        super().__init__()
        self._headers = dict(headers)

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """The next hop for `newurl`; `ValueError` when it is not an https URL.

        Also `ValueError` for an https target whose port cannot be read. The message
        names the rule, never the URL, as `build_request`'s does. Only targets
        urllib's own `http_error_302` lets through arrive here: it refuses a scheme
        other than http, https or ftp (`file:`, `data:`) itself, earlier, with an
        `HTTPError` that quotes the target.
        """
        if https_url_or_none(newurl) is None:
            _refuse_redirect(fp)
        try:
            target = _origin(newurl)
        except ValueError:
            _refuse_redirect(fp)
        hop = super().redirect_request(req, fp, code, msg, headers, newurl)
        if hop is not None and target == _origin(req.full_url):
            for name, value in self._headers.items():
                hop.add_unredirected_header(name, value)
        return hop


def _refuse_redirect(fp: IO[bytes]) -> NoReturn:
    """Close the redirect's own response, which is never read, and refuse the hop."""
    fp.close()
    # from None: the suppressed context may be urlsplit's error, quoting the port.
    raise ValueError(_REDIRECT_REFUSAL) from None


def build_opener(headers: Mapping[str, str]) -> urllib.request.OpenerDirector:
    """The default urllib opener, with the https-only redirect handler in its place.

    `headers` are the caller's, for the handler to re-attach on a same-origin hop.
    """
    return urllib.request.build_opener(_HttpsOnlyRedirectHandler(headers))


class ChunkReader(Protocol):
    """What `read_body` needs of a response: `read1`, as an HTTP response provides it.

    `io.BytesIO` provides the same method, which is what lets the reader be tested
    against an in-memory body.
    """

    def read1(self, size: int, /) -> bytes:
        """Up to `size` bytes from at most one underlying read; empty at the end."""


def read_body(
    response: ChunkReader,
    *,
    limit: int = BODY_LIMIT_BYTES,
    deadline: float = BODY_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """The whole body of `response`, bounded in size and in time.

    Read with `read1`, never `read(n)`: `read(n)` blocks until `n` bytes have arrived
    or the body ends, so a byte-at-a-time server would hold it as long as it holds a
    bare `read()`. `read1(n)` returns whatever ONE underlying read produced, which is
    what lets the deadline be checked between chunks at all. The per-call socket
    timeout still bounds each of those reads, so a fetch waits at most `deadline`
    plus one socket timeout.

    Raises `ValueError` past `limit` -- the body is not what was asked for -- and
    `TimeoutError` once the body is still arriving `deadline` seconds after the read
    began: an `OSError`, like the socket timeout it backs up, so a caller's except
    clause around a fetch catches both. `clock` is injectable so the deadline is
    proven by readings rather than by waiting it out.
    """
    started = clock()
    chunks: list[bytes] = []
    received = 0
    while chunk := response.read1(_READ_CHUNK_BYTES):
        received += len(chunk)
        if received > limit:
            raise ValueError(f"response body exceeds {limit} bytes")
        chunks.append(chunk)
        if clock() - started > deadline:
            raise TimeoutError(f"response body still arriving after {deadline} seconds")
    return b"".join(chunks)


def fetch_url(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
    """Fetch `url` with `headers`, each socket operation bounded by `timeout`.

    `read_body` bounds the body, in size and in time.

    An HTTP error status raises `urllib.error.HTTPError` unchanged, so a caller can
    read its status code; a refused URL or header raises `build_request`'s
    `ValueError` before any connection is opened. A redirect to http or ftp, or to
    an https URL the rule refuses, raises `ValueError` before it is followed; one to
    a scheme urllib itself will not follow (`file:`, `data:`) raises urllib's own
    `HTTPError` first, whose message quotes the target URL.
    """
    # Not covered: the real network seam. Every unit test injects a fake in place of
    # this function, so it runs only in a caller's live end-to-end test.
    # `build_request`, which builds the request, and `read_body`, which consumes the
    # response, ARE unit-tested; only opening the connection is excluded.
    request = build_request(url, headers)  # pragma: no cover
    opener = build_opener(headers)  # pragma: no cover
    with opener.open(request, timeout=timeout) as response:  # pragma: no cover
        return read_body(response)  # pragma: no cover
