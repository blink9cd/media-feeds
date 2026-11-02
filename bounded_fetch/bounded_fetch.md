# bounded_fetch

A hardened HTTPS GET for tools that read a third party's server: one rule for which URLs
may be requested, one place a request is built (refusing any header that would break the
request's framing), and one response-body read bounded in size and in time. Beside it, a
HEAD that reports a redirect instead of following it, for a caller that needs only to
learn where an address leads. A pure library
(no CLI, no `__main__`, standard library only). Each caller supplies its own headers and
timeout; this module decides only what is safe to send and how much may come back.

## API

```python
from bounded_fetch import (
    BODY_DEADLINE_SECONDS,
    BODY_LIMIT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    ChunkReader,
    HeadAnswer,
    answer_head,
    build_no_follow_opener,
    build_opener,
    build_request,
    fetch_url,
    head_url,
    https_url_or_none,
    read_body,
)

https_url_or_none(url)  # url verbatim, or None when it may not be requested
build_request(url, headers)  # a urllib Request; ValueError for a refused URL or header
build_request(url, headers, method="HEAD")  # the same, as a HEAD; no other method
read_body(response)  # the whole body; ValueError past the cap, TimeoutError late
build_opener(headers)  # urllib's opener; redirects only to https, headers in-origin
fetch_url(url, timeout, headers)  # build_request -> build_opener().open -> read_body
build_no_follow_opener()  # urllib's opener, following no redirect at all
answer_head(open_response)  # a HeadAnswer from a 2xx or 3xx; HTTPError otherwise
head_url(url, timeout, headers)  # a HEAD through the no-follow opener -> HeadAnswer
HeadAnswer(status, location)  # frozen: the status, and a 3xx's Location as sent
DEFAULT_TIMEOUT_SECONDS  # 15.0: a default for callers, per socket operation
BODY_LIMIT_BYTES  # 4 MiB
BODY_DEADLINE_SECONDS  # 60.0
```

`fetch_url` lets an HTTP error status through as `urllib.error.HTTPError`, unchanged, so a
caller can read the status code and decide what a 404 or a 429 means to it. The refusals
are a `ValueError` (a URL or header refused before any connection is opened, a redirect
to http, ftp or a refused https URL, a body over the cap) or an `OSError` (the socket
timeout, or the body deadline's `TimeoutError`). One redirect refusal is urllib's own:
a redirect to a scheme it will not follow at all (`file:`, `data:`) raises its
`HTTPError` before this module's handler is asked, and that message quotes the target
URL. `HTTPError` is an `OSError`, so a caller's `except (OSError, ValueError)` around a
fetch catches every one of them.

## A HEAD that does not follow a redirect

`head_url(url, timeout, headers)` sends a HEAD for `url` and returns a `HeadAnswer`
without following any redirect. It is for a caller whose answer IS the redirect: a site
that sends a known address on to the page it stands for, where the target says what the
caller wants to know and the page itself is never needed.

- **A 2xx** comes back as `HeadAnswer(status, None)`.
- **A 3xx** comes back as `HeadAnswer(status, location)`, `location` being the
  `Location` header exactly as the server sent it (often a path relative to the
  request), or None when it sent none. Resolving it against the request URL, and
  deciding whether to trust where it points, is the caller's.
- **A 4xx or 5xx** raises `urllib.error.HTTPError` unchanged, as `fetch_url` does, so a
  caller reads the status and headers (a 429's `Retry-After`) and decides what each means.
- **A refused URL or header** raises `build_request`'s `ValueError` before any
  connection is opened; a transport failure raises its `OSError`.

`build_request(url, headers, method="HEAD")` builds the request, under every rule below
for the URL and the headers; it accepts `"GET"` (the default) and `"HEAD"` and raises
`ValueError` for any other method. `build_no_follow_opener()` is urllib's default opener
with its redirect handler replaced by one whose redirect status methods (`http_error_301`
to `http_error_308`) return None, as does its `redirect_request`: the opener's default
error handler then raises the 3xx as an `HTTPError`, and no second request is sent
anywhere. The status methods are overridden, not only `redirect_request`, because the
stock `http_error_302` parses the `Location` header before it asks `redirect_request`: a
malformed header would raise a `ValueError` quoting it, and a target scheme urllib will
not follow an `HTTPError` quoting it. Here the header is never parsed, so the caller gets
it exactly as sent. The default opener holds no
cookie handler, so nothing a server sets is kept or sent back. `answer_head(open_response)`
turns what the opener returns or raises into the answer above, closing every response
and every error first (a HEAD answer has no body, and its parsed headers stay readable
after the close). It is unit-tested with fakes in place of `opener.open`, which is the
one line of `head_url` excluded from coverage, for the reason the network-seam section
below gives.

## Only an https URL with a host is requested

`https_url_or_none(url)` returns `url` verbatim when it carries no whitespace and no Unicode
control or format character AND `urllib.parse.urlsplit` then gives it the scheme `https`, a
non-empty hostname and no userinfo. Everything else is `None`: `http:`, `file:`, `ftp:`,
`data:`, `javascript:`, an `https:///x` with no host, a scheme-relative `//host/x`, an empty
string, a `user:password@` prefix, and a string `urlsplit` itself refuses (an unclosed IPv6
bracket). That last one answers `None` rather than letting a `ValueError` out of a parser.
The uppercase `HTTPS://` is accepted as it is: urllib lowercases the scheme itself when it
picks a handler.

The character scan runs on the raw string, before the parse, and that order is the
point. Parsing is not filtering: `urlsplit` silently strips tab, carriage return and line
feed, so a URL smuggling a header break (or one with a tab spliced into the scheme) parses
perfectly clean, and it never looks at a NUL, an escape or a zero-width mark at all.
Scanning afterwards would therefore accept all of them. It scans the characters that are
actually present, so a percent-ESCAPED space or control character is ordinary URL text and
survives, because refusing that would reject legitimate URLs.

A caller applies the rule wherever it decides a URL, so a rejected one becomes "no URL" in
the caller's own terms (a record with no image, say). `build_request` applies it again at the
sink, so no URL the rule rejects can reach the opener by any route, whatever a caller was
handed. Its refusal message names the rule, never the URL, so a refusal cannot echo a URL
that carries an identity.

Why https-only, with no host allowlist, is enough: urllib's opener speaks `file:`, `ftp:` and
`data:` as readily as https, and the rule keeps every one of those from being opened at all.
An https request to an internal address (a loopback, a link-local metadata service, a
private range) fails at the TLS handshake, on certificate verification, before any request
line is sent: no public authority issues a certificate for a private address. That covers
the FIRST hop only: redirects are followed, and where a server sends one is the server's to
say, within the next section's limits. That is an accepted risk, not a guarantee that
nothing internal is ever reached.

## A redirect goes only to https, and carries the caller's headers only in-origin

urllib's stock redirect handler copies every ordinary request header onto the next hop,
whichever host and scheme the server names, and the https rule above sees only the first
hop. Left alone, a server could redirect a fetch onto plain http, or hand a caller's
`Authorization` header to a host the caller never named. Two measures close that:

- `build_request` attaches the caller's headers with `Request.add_unredirected_header`,
  which the stock handler never copies to a redirect.
- `build_opener(headers)`, which `fetch_url` opens through, replaces the stock handler with
  one that refuses, with `ValueError`, any redirect whose target `https_url_or_none`
  rejects (plain http and ftp included), or whose port cannot be read, closing the
  redirect's own response first; and that re-attaches the caller's headers only when the
  target's scheme, host and port are the redirecting request's. A path moved on the same
  server (a trailing slash, a renamed feed) still gets the User-Agent it may require;
  another host, or another port on the same host, gets none of them.

Which redirects are followed at all is still urllib's: a 302 answering a `PUT`, say, is
raised as `HTTPError` exactly as before, and so is a redirect to a scheme urllib will not
follow (`file:`, `data:`), which its own `http_error_302` refuses before this handler is
asked, with a message that quotes the target URL.

## Every header is printable ASCII

`build_request(url, headers)` sends exactly the caller's headers, and raises `ValueError`
when any header name or value holds anything but printable ASCII (the space through the
tilde). Two different hazards sit outside that range:

- **A carriage return, a line feed or a NUL.** A line break ends the header early and
  starts another of the sender's choosing (a header injection), and a NUL truncates the
  header in whatever reads it next. Tab, DEL and the other controls go with them: no
  header a caller needs carries one.
- **Any non-ASCII character.** `http.client` encodes a header value as latin-1 when it
  sends it, and for a character outside latin-1 (a typographic apostrophe, say) it raises
  `UnicodeEncodeError`, whose `args` and `repr` carry the whole value. A token with one
  stray character would then be printed by any caller that logs the exception. Refusing
  every non-ASCII character first, rather than only the non-latin-1 ones, also keeps a
  value from reaching the server as bytes a reader would decode differently.

The message names neither the header nor its value, in `str` or in `repr`, because a value
may be a credential (an `Authorization: Bearer` token, a contact address inside a
User-Agent).

The check covers what a caller passes, not the URL: the URL's own characters were already
refused by the scan above.

## The three bounds on a fetch

`DEFAULT_TIMEOUT_SECONDS = 15.0` is sized for a whole feed body (a real RSS shelf measured
roughly 187 KB). `fetch_url` passes the caller's `timeout` to the opener's `open`, where it
bounds each blocking socket operation: the connect, and every read of the body. That is the
only place a bound can interrupt a call already in flight (an outer deadline can only
fire _between_ calls), but it is also all it bounds. A server that sends one byte every
few seconds restarts it on every byte, so under that timeout alone a fetch stays open for
as long as the server cares to drip, and a body is read into memory whole with no ceiling
at all.

`read_body(response)` closes both gaps. It consumes the response in chunks with `read1`,
never `read(n)`. `read(n)` blocks until `n` bytes have arrived or the body ends, so a
byte-at-a-time server would hold it as long as it holds a bare `read()`, whereas `read1(n)`
returns whatever one underlying read produced and so hands control back between chunks.
Between chunks it applies the other two bounds:

- `BODY_LIMIT_BYTES` (4 MiB): past this the read stops with `ValueError`. This is generous against
  the largest real feed measured, more than twenty times over, so a body this size is not
  what was asked for.
- `BODY_DEADLINE_SECONDS` (60): a body still arriving this long after the read began stops
  with `TimeoutError`. Four socket timeouts: a full-size body still arrives over a slow link
  (a little under 70 KB/s), while a drip-feed is cut off within the minute. Checked between
  chunks, so the longest a fetch can wait is this deadline plus one socket timeout.

`TimeoutError` is an `OSError`, like the socket timeout it backs up, so both refusals ride a
caller's existing `except (OSError, ValueError)`. `read_body` takes `limit`, `deadline` and
an injectable monotonic `clock` as keywords, so the deadline is proven by handing it readings
rather than by waiting one out. `ChunkReader` is the `read1` protocol it needs, which an HTTP
response and `io.BytesIO` both provide.

## The network seam, and why it is not mocked

`fetch_url` is one of the two functions here that open a connection (`head_url`'s
`opener.open` is the other, excluded the same way), and it is excluded from
coverage (`# pragma: no cover`, with the reason beside it) rather than mocked: a mocked
`opener.open` proves only that the mock was called correctly, never that a genuine HTTPS
response comes back readable. The halves either side of it are unit-tested on their own:
`build_request`, which builds what is sent, and `read_body`, which consumes what comes back.
A caller proves the whole chain with a live end-to-end test at its own boundary, where
the real server is. A caller's own unit tests inject a fake in place of `fetch_url`.

## Importing it

This folder sits at the repository root, beside the tools that use it. Each tool puts it
on `sys.path` from the root, the folder above the tool's own, as one fixed candidate:

```python
sys.path += [str(Path(__file__).resolve().parents[1] / "bounded_fetch")]

import bounded_fetch
```

The folder joins at the end of `sys.path`, so it can never shadow the standard library,
and no search up the ancestors can pick up a same-named folder planted above the
checkout. A tree that leaves the folder out fails at the first run with a plain
`ImportError` naming the module.

## Testing

The suites live under `tests/`, one per concern, behind the `test_bounded_fetch.py`
aggregator, which folds them into one run and floors each file's test count, so a suite lost
to a failed import fails the run instead of passing as "no tests ran".
`tests/test_bounded_fetch_url.py` exercises `https_url_or_none` against each accepted and
rejected shape and `build_request`'s headers and refusals, without a request being sent;
`tests/test_bounded_fetch_url_properties.py` holds `https_url_or_none` (a `# fuzz-target`)
to its whole contract over arbitrary and URL-shaped text with a Hypothesis property,
`test_fuzz_https_url_or_none`;
`tests/test_bounded_fetch_redirect.py` calls the redirect handler `build_opener` installs
directly, for a hop to another host, another port, the same origin and plain http;
`tests/test_bounded_fetch_body.py` exercises `read_body` against `io.BytesIO` bodies and a
clock made of readings; `tests/test_bounded_fetch_head.py` covers the HEAD method, drives
the no-follow opener's own error chain to show a 302 is raised rather than followed and
that no cookie handler is installed, and hands `answer_head` fakes for each status class.
None of them touches the network.
