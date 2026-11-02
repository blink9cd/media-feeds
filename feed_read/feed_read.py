"""Shared helpers for the Letterboxd and Goodreads feed adapters.

Both adapters fetch a public RSS document over HTTPS, walk its `<item>` elements, turn
vendor date strings into dates, take the same "at least one" integer options, and
write their records the same way: atomically, as a JSON array. Only the field
extraction differs, so only that lives in each adapter. Pure apart from `real_fetch`,
the one network seam, which every unit test replaces with a fake, and `write_records`,
the one place either adapter touches the filesystem. The fetch itself (the URL rule,
the request, and the bounded body read) is the shared `bounded_fetch` library's, and
is re-exported here under the names the adapters already use.

No CLI: nothing here can be run by hand against a sample input, which is why it takes
a `[tool.samples_presence].exempt` entry rather than a `samples/` dir.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import unicodedata
import urllib.error
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from email.utils import parsedate_to_datetime
from pathlib import Path

# Put the vendored bounded_fetch library on sys.path from this repository's root, the
# folder above this file's own. It is done here rather than in each importer, so every
# importer of feed_read reaches it with no bootstrap of its own. One fixed candidate,
# deliberately: a search up the ancestors would also take any same-named folder above
# the checkout and put it ahead of the standard library, where a module planted in it
# would be imported in place of the real one (stdio_utf8/tests/test_bootstrap.py
# plants such a folder).
sys.path += [str(Path(__file__).resolve().parents[1] / "bounded_fetch")]

import bounded_fetch
from bounded_fetch import BODY_DEADLINE_SECONDS as BODY_DEADLINE_SECONDS
from bounded_fetch import BODY_LIMIT_BYTES as BODY_LIMIT_BYTES
from bounded_fetch import ChunkReader as ChunkReader
from bounded_fetch import https_url_or_none as https_url_or_none
from bounded_fetch import read_body as read_body

#: `(url, timeout_seconds) -> body`: the injection seam every adapter defaults to
#: `real_fetch` and every unit test replaces with a fake.
Fetcher = Callable[[str, float], bytes]

#: `(url, timeout_seconds, headers) -> body`: the seam for a request that sends more
#: than the User-Agent. Defaulted to `real_fetch_with_headers`, replaced in unit tests.
HeaderFetcher = Callable[[str, float, Mapping[str, str]], bytes]

#: Sized for a whole feed body; `bounded_fetch.DEFAULT_TIMEOUT_SECONDS` carries the
#: measurement and the reason. Kept under this name because the adapters' fetch
#: timeouts read it.
FEED_TIMEOUT_SECONDS = bounded_fetch.DEFAULT_TIMEOUT_SECONDS

#: Sent literally on every request. Measured 2026-09-01 against the real Letterboxd
#: and Goodreads endpoints, not inferred, with the strings as sent that day:
#:   Letterboxd + urllib's own default ("Python-urllib/3.14")   -> HTTP 403 Forbidden
#:   Letterboxd + "personal-site-feed-adapter/1.0"              -> HTTP 200
#:   Letterboxd + "feed-adapter"                                -> HTTP 200
#:   Goodreads  + either of the above                           -> HTTP 200 (indifferent)
#: Letterboxd blocks the literal urllib default string, not "non-browser-ness" in
#: general, so an honest product UA is sufficient. Do not "fix" a future regression
#: by impersonating a browser: a plain product identifier already clears the real
#: block. The value below is not one of the measured strings; it rests on the bare
#: "feed-adapter" row, which shows the block is that one literal and nothing broader.
#: Do not remove this: without it, every Letterboxd fetch 403s.
_USER_AGENT = "media-feeds-feed-adapter/1.0"

#: The mode every record gets on a POSIX host (a Windows file has no mode bits; see
#: `_give_record_mode`). `tempfile.mkstemp` creates its file readable by its creator
#: alone, and a plain open would take the caller's umask, so the mode is set explicitly
#: and depends on neither: the record is public data a web server must read, and
#: whether it can must not turn on the shell the refresh happened to run in.
_RECORD_MODE = 0o644

#: The Unicode general category of the C0 and C1 control characters, which
#: `terminal_safe_text` escapes before any vendor text is echoed to a terminal. Not
#: the wider `C` prefix `https_url_or_none` refuses in a URL: a format character (a
#: soft hyphen, a bidi mark) is legitimate in a title, and bidi in titles is a risk
#: this project accepted on 2026-09-21 rather than one to re-open here.
_CONTROL_CATEGORY = "Cc"


#: HTTP statuses that are a server's final answer that a feed is not there: Not Found
#: and Gone. Every other refusal may pass on a later try (a 403 block that lifts, a
#: 429 rate limit, a 5xx outage), so only these two earn `EXIT_FEED_GONE`.
GONE_STATUSES: frozenset[int] = frozenset({404, 410})

#: The adapters' exit when `feed_gone` holds. Distinct from their exit 2 (unreadable,
#: worth a retry) so a scheduled refresh can skip its retry waits: a dead Goodreads
#: shelf feed cost each daily run about 15 minutes of them.
EXIT_FEED_GONE = 3


def real_fetch(url: str, timeout: float) -> bytes:
    """Fetch a URL: `timeout` bounds each socket operation, `read_body` the body.

    `bounded_fetch.fetch_url` with this project's one header, the User-Agent; see
    `_USER_AGENT` for why it is required, not optional. `fetch_url` builds its request
    through `bounded_fetch.build_request`, which raises `ValueError` unless
    `https_url_or_none` accepts `url`; every caller's except clause around its fetch
    already catches `ValueError`, and the message names the rule, never the URL, so no
    refusal can echo a feed URL that carries an identity.
    """
    return real_fetch_with_headers(url, timeout, {})


def real_fetch_with_headers(
    url: str, timeout: float, headers: Mapping[str, str]
) -> bytes:
    """`real_fetch` with the caller's own `headers` sent beside the User-Agent.

    For a request that needs more than the User-Agent, such as an API's
    `Authorization` header. `_USER_AGENT` is applied last, so a caller's header set
    can add to it but never replace it: one User-Agent for every request this
    project sends. `bounded_fetch` re-attaches the headers on a redirect only within
    the same origin, and its refusals never name a header's value, so a credential
    passed here appears in no error message.
    """
    return bounded_fetch.fetch_url(url, timeout, {**headers, "User-Agent": _USER_AGENT})


def feed_gone(exc: BaseException) -> bool:
    """Whether a fetch failure is the server saying the feed is gone, not a hiccup.

    Only an `HTTPError` carries a status; a timeout, a refused connection, or an
    unparseable body may all succeed next time.
    """
    return isinstance(exc, urllib.error.HTTPError) and exc.code in GONE_STATUSES


class _DoctypeRefusingBuilder(ET.TreeBuilder):
    """A tree builder that refuses the document at its DOCTYPE declaration.

    The entity-expansion guard. Stock ElementTree is vulnerable to internal entity
    expansion ("billion laughs" / "quadratic blowup"), and this body is a third
    party's. Entities require a DTD and a DTD requires a DOCTYPE, so refusing every
    DOCTYPE is the whole guard; no separate entity check is needed.

    Refused from the parser's own event rather than by searching the raw bytes for
    `<!DOCTYPE`. A byte search sees the keyword only in a one-byte encoding: expat
    auto-detects UTF-16, from a byte-order mark or from the NUL beside the leading
    `<`, and in UTF-16 every letter of the keyword is interleaved with a NUL, so such
    a body passed a byte search and expanded anyway. The parser fires this method from
    expat's start-of-DOCTYPE handler, after it has settled the encoding and before it
    reads the internal subset where the entities live, so nothing is expanded, in
    any encoding. Both properties are pinned by test rather than assumed: the C
    accelerator's dispatch was measured on this interpreter, and the tests hold
    whichever `XMLParser` implementation is loaded.

    It rejects nothing real: four real feeds across both vendors and four accounts
    hold zero `<!DOCTYPE` and zero `<!ENTITY`.
    """

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        """Refuse the document; the arguments are what the parser passes, unread."""
        raise ValueError("refusing to parse XML with a DOCTYPE declaration")


def feed_items(body: bytes) -> list[ET.Element]:
    """Every `<item>` in an RSS body, in document order.

    A body with no `<channel>` (a vendor error page served as XML) yields an empty list
    rather than raising: the adapter then writes nothing, so a failed refresh keeps the
    last good record. Genuinely malformed XML does raise: that is a broken fetch, not
    an empty feed, and the caller reports it. So does a DOCTYPE-bearing body, from
    `_DoctypeRefusingBuilder`, whichever encoding it arrives in.
    """
    # The builder's ValueError is not an ET.ParseError, so it passes through the
    # except below with its own message, as the guard's callers expect.
    try:
        root = ET.fromstring(
            body, parser=ET.XMLParser(target=_DoctypeRefusingBuilder())
        )
    except ET.ParseError as exc:
        raise ValueError(f"not parseable as XML: {exc}") from exc
    channel = root.find("channel")
    if channel is None:
        return []
    return list(channel.findall("item"))


def rfc822_date(value: str) -> dt.date | None:
    """An RFC-822 date string as a date, or None if it cannot be read.

    `parsedate_to_datetime`, not a hand-rolled `strptime` format. RFC-822/2822 permits
    several optional and variant forms that a single fixed `strptime` format cannot
    all match at once: no day-of-week, a named zone (`GMT`), an obsolete zone
    (`EST`), no seconds, or a trailing `(comment)`: each raises against
    `"%a, %d %b %Y %H:%M:%S %z"`. `parsedate_to_datetime` implements the grammar
    instead of pinning one shape, so it handles all of them.

    The single-digit day Goodreads writes ('Sat, 1 Aug 2026 ...', 4 of 49 items on a
    real shelf) is not one of those variants: `%d` accepts an unpadded day. It is
    tested because it is the real feed's shape.
    """
    if not value.strip():
        return None
    try:
        return parsedate_to_datetime(value).date()
    except (ValueError, TypeError):
        return None


def iso_date(value: str) -> dt.date | None:
    """An ISO `YYYY-MM-DD` string as a date, or None if it cannot be read.

    Letterboxd's `watchedDate` is already ISO, so it needs no RFC-822 handling, but it
    still needs the same never-raise contract, because a missing or reshaped field must
    degrade to "no card", not to a traceback.
    """
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def terminal_safe_text(value: str) -> str:
    r"""`value` with every control character replaced by a visible `\xNN` escape.

    For vendor text an adapter echoes to the operator's terminal: a film or book
    title in the success summary. A control character printed to a terminal is not
    text but an instruction to it: a crafted title can rewrite the window title, wrap
    the line in an OSC 8 hyperlink pointing anywhere, or erase what was printed above
    it. None of that touches the record, which is JSON a browser reads; the exposure
    is the terminal of whoever ran the refresh.

    Escaped rather than dropped, so the operator sees that the title held something
    odd instead of reading a silently shortened title as the real one. Only category
    `Cc`, the C0 and C1 controls. Ordinary text is untouched, accents, CJK, and
    emoji included: the rule is about control characters, not about non-ASCII.

    What can actually arrive here is narrower than it looks, and was measured rather
    than assumed (2026-09-22): expat refuses a raw C0 character outright and refuses
    `&#27;` too, so ESC cannot reach a title through a feed at all. XML 1.0 does admit
    U+0080-U+009F unescaped, and both the raw character and `&#155;` parse, so C1
    (U+009B, the control sequence introducer, and U+0085, a line break) is the class
    this removes. The whole category is escaped anyway rather than those two: it costs
    one comparison, and a rule written as "the controls" does not need revisiting when
    a parser somewhere grows more permissive.

    album_scan and cover_art apply the same rule at their own echo sites, in their own
    copy: they deliberately share no code with this module, as `https_url_or_none` and
    cover_art's `_is_recordable_url` already do.
    """
    return "".join(
        f"\\x{ord(char):02x}"
        if unicodedata.category(char) == _CONTROL_CATEGORY
        else char
        for char in value
    )


def positive_int(text: str) -> int:
    """An option value that must be an integer of at least one, for argparse `type=`.

    Raised as an ArgumentTypeError rather than checked in an adapter's `main`, so
    argparse renders the usage message and exits 2, the same treatment a
    non-integer already gets, rather than a second spelling of "bad argument". It
    also means the rejection happens before any network call.
    """
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def write_records(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    """Write `records` to `path` atomically: a temp file beside it, then `os.replace`.

    `path` may be read concurrently (the page, mid-refresh), so it is never truncated
    in place: a concurrent reader would see a half-written file. `os.replace` is an
    atomic rename on both POSIX and Windows, so a reader sees either the whole old
    file or the whole new one, never a seam. Always a JSON array, even for exactly
    one record: a bare object and a one-element array are different shapes for a
    reader to branch on, and no adapter writes the former.

    The temp file is `tempfile.mkstemp`'s: created in `path`'s directory, opened
    exclusively, under a name nothing can predict (`<name>.<random>.tmp`), and, on a
    POSIX host, given `_RECORD_MODE` before the rename. A fixed `<name>.tmp` was a name a symlink could
    be planted at ahead of time, and a plain open followed the link.

    Raises `OSError` when the write or the rename fails, after removing its own
    temp file: the caller reports the cause, and `path`, whatever it held
    before, is untouched either way. When creating the temp file itself fails
    there is nothing to remove, and the error propagates as it is. Raises
    `ValueError` for a record holding a non-finite float, before anything touches
    the filesystem: no directory is created, no temp file is written, and `path`
    is untouched.
    """
    # Serialized first, and with allow_nan=False: json.dumps would otherwise write a
    # float NaN or infinity as the bare tokens NaN and Infinity, which are not JSON,
    # so a reader's JSON.parse fails on the whole file and every card disappears.
    # Refused here, no adapter can write one, whatever its parser lets through.
    text = json.dumps(records, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp opens the file exclusively (O_EXCL) under a name nothing can predict,
    # in the record's own directory so the rename stays a same-filesystem rename.
    # The fixed <name>.tmp it replaces was a name a symlink could be planted at in
    # advance (one committed into the checkout the tools write into), and a plain
    # open followed the link: the records were written through it into the link's
    # target, and the rename then put the link itself in the record's place.
    # Outside the try: a failed creation leaves nothing to remove, and the error is
    # the caller's to report as it is.
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        # newline="\n" pins the line ending regardless of host: a text-mode write's
        # default newline=None translates to CRLF on Windows, so the committed record
        # would differ byte-for-byte between platforms.
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            _give_record_mode(handle.fileno())
            handle.write(text)
        os.replace(tmp, path)
    except OSError:
        # A failed write or rename must not leave the temp file beside the record;
        # it is this function's file to remove, and the cause is re-raised as is.
        tmp.unlink()
        raise


def _give_record_mode(descriptor: int) -> None:
    """Set `_RECORD_MODE` on the open temp file, where the platform has modes to set.

    On the descriptor, so the mode lands on the file `write_records` created and on
    nothing a path could be re-pointed at meanwhile. Nothing on Windows: it has no
    POSIX mode bits, `os.fchmod` only reached it in Python 3.13, and mkstemp's file is
    already writable.
    """
    if sys.platform == "win32":
        return  # pragma: only-on win32 - no POSIX mode bits, no fchmod before 3.13
    os.fchmod(descriptor, _RECORD_MODE)  # pragma: only-on posix
