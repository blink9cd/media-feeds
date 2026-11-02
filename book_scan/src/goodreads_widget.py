"""Read Goodreads' custom shelf widget: the URL, the JavaScript string, the book parse.

Private to `book_scan/`: the sibling `book_scan.py` primary script is this module's
only importer, and re-exports everything `tests/` needs as `book_scan.<name>`.

Why the widget: it is a public, credential-free page that lists the read shelf in
the account holder's finishing order, so the card needs no hand export. Measured
2026-10-28, with honest requests: `/review/custom_widget/<user_id>` answers `text/javascript`
that sets one single-quoted string, `var widget_code = '...'`, to server-rendered
HTML (escaping `'`, `"`, `/`, and newline with a backslash). The HTML holds one
`gr_custom_each_container_` div per book, with a cover `<img>` whose file name is the
Book Id, a rating `<span>` whose `title` is Goodreads' rating word, and title and
author links. Every query parameter is ignored (book count, sort, order, and shelf
all returned byte-identical bodies of five books), so none is sent.

The widget carries no read date and no publication year, and only a 75px cover
thumbnail, so each record leaves `year`, `read`, and `cover` None for later steps.
`widget_candidates` parses a body it is handed; `read_widget` reads that body through
the fetch seam its caller injects, or from a local file.
"""

from __future__ import annotations

import http.client
import re
import sys
import urllib.error
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

# Put the sibling feed_read tool folder on sys.path from this repository's root, two
# folders above this file (book_scan/src/), so this module imports alone as well. One
# fixed candidate rather than a search up the ancestors, for book_scan.py's reason.
sys.path += [
    str(Path(__file__).resolve().parents[2] / "feed_read"),
]

import feed_read  # noqa: E402 - follows the sys.path bootstrap above
from book_select import EXIT_BAD_INPUT, SourceResult  # noqa: E402 - same reason
from book_sources import BookRecord, Candidate  # noqa: E402 - same reason

#: The widget page. The user id is percent-encoded by `widget_url` before it is put
#: here, so it cannot add a path segment or a query.
WIDGET_URL = "https://www.goodreads.com/review/custom_widget/{user_id}"

#: The one JavaScript string the widget sets. Single-quoted; a backslash escapes the
#: next character (measured: \' \" \/ and \n).
_WIDGET_CODE = re.compile(r"var widget_code = '((?:[^'\\]|\\.)*)'", re.DOTALL)
_JS_ESCAPE = re.compile(r"\\(.)", re.DOTALL)

#: Goodreads' five rating words, each the `title` of the rating span.
_RATING_WORDS = {
    "did not like it": 1,
    "it was ok": 2,
    "liked it": 3,
    "really liked it": 4,
    "it was amazing": 5,
}

#: The Book Id is a cover's file name: `/books/<stamp><letter>/<book_id>.<rest>`.
_COVER_BOOK_ID = re.compile(r"/books/[0-9]+[a-z]/([0-9]+)[._]", re.ASCII)

#: The div class opening one book's block, and the field each inner div class holds.
_EACH = "gr_custom_each_container_"
_FIELDS = {
    "gr_custom_book_container_": "cover",
    "gr_custom_rating_": "rating",
    "gr_custom_title_": "title",
    "gr_custom_author_": "author",
}


def widget_url(user_id: str) -> str:
    """The widget URL for `user_id`, percent-encoded so it cannot inject a query."""
    return WIDGET_URL.format(user_id=urllib.parse.quote(user_id, safe=""))


def widget_html(body: bytes) -> str | None:
    """The HTML inside the widget's `widget_code` string, unescaped; None if absent."""
    match = _WIDGET_CODE.search(body.decode("utf-8", errors="replace"))
    if match is None:
        return None
    return _JS_ESCAPE.sub(
        lambda m: "\n" if m.group(1) == "n" else m.group(1), match.group(1)
    )


class _WidgetParser(HTMLParser):
    """Collects each book block's raw fields, keyed by the nearest gr_custom_ div.

    The stdlib's tolerant HTML tokenizer, never a regular expression over the markup:
    it already handles attribute order, quoting, and character references.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.books: list[dict[str, str]] = []
        self._divs: list[str] = []
        self._in_link = False

    def _field(self) -> str | None:
        """The field the innermost known div names, inside a book block only."""
        if _EACH not in self._divs:
            return None
        for name in reversed(self._divs):
            if name in _FIELDS:
                return _FIELDS[name]
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track divs and links; take the cover src and the rating title."""
        values = dict(attrs)
        if tag == "div":
            classes = (values.get("class") or "").split()
            known = [c for c in classes if c == _EACH or c in _FIELDS]
            # Every div is pushed, known or not, so each `</div>` pops its own.
            self._divs.append(known[0] if known else "")
            if known[:1] == [_EACH]:
                self.books.append({})
            return
        field = self._field()
        if tag == "a":
            self._in_link = True
        elif tag == "img" and field == "cover":
            self.books[-1].setdefault("cover", values.get("src") or "")
        elif tag == "span" and field == "rating" and values.get("title"):
            self.books[-1].setdefault("rating", values.get("title") or "")

    def handle_endtag(self, tag: str) -> None:
        """Close a div or a link."""
        if tag == "div" and self._divs:
            self._divs.pop()
        elif tag == "a":
            self._in_link = False

    def handle_data(self, data: str) -> None:
        """Append link text to the title or author of the current book."""
        field = self._field()
        if self._in_link and field in ("title", "author"):
            self.books[-1][field] = self.books[-1].get(field, "") + data


def _candidate(raw: dict[str, str]) -> Candidate | None:
    """One book's record and id, or None without a title and an author."""
    title = " ".join(raw.get("title", "").split())
    author = " ".join(raw.get("author", "").split())
    if not title or not author:
        return None
    match = _COVER_BOOK_ID.search(raw.get("cover", ""))
    record: BookRecord = {
        "title": title,
        "author": author,
        "year": None,
        "rating": _RATING_WORDS.get(raw.get("rating", "").strip()),
        "read": None,
        "cover": None,
    }
    return record, match.group(1) if match else ""


# fuzz-target
def widget_candidates(body: bytes) -> list[Candidate]:
    """Every readable book in a widget body, in the widget's (finishing) order.

    Decoded as UTF-8 with replacement, so a stray byte costs at most its character.
    `HTMLParser` is tolerant, but not on every patch release: see the handler below.
    The property test holds that this function never raises over arbitrary bytes.
    """
    html = widget_html(body)
    if html is None:
        return []
    parser = _WidgetParser()
    try:
        parser.feed(html)
        parser.close()
    except AssertionError:
        # The stdlib's `_markupbase` raises AssertionError on a malformed marked
        # section such as `<![x[` (3.10 to 3.12 releases before the
        # backported fix); the floor names no patch level. The rest of the body is
        # lost, but every book block already read is kept.
        pass
    return [pair for raw in parser.books if (pair := _candidate(raw)) is not None]


def read_widget(
    user_id: str, fetch: feed_read.Fetcher, widget_file: Path | None
) -> SourceResult:
    """The widget's candidates, or its failure's exit code after one stderr line.

    A readable body with no book is exit 2, not 1: a scheduled caller may treat 1 as
    a quiet no-data day, and an empty parse of this undocumented format most likely means its
    markup changed.
    """
    try:
        if widget_file is not None:
            body = widget_file.read_bytes()
        else:
            body = fetch(widget_url(user_id), feed_read.FEED_TIMEOUT_SECONDS)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # urllib's exceptions carry a reason or a status, never the request URL, so
        # the line cannot name the user id.
        print(f"book_scan: could not read the shelf widget: {exc}", file=sys.stderr)
        # An HTTPError is the refused response: close it rather than leak it.
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        # A 404 or 410 is final: its own exit, so a scheduled refresh does not retry.
        return feed_read.EXIT_FEED_GONE if feed_read.feed_gone(exc) else EXIT_BAD_INPUT
    candidates = widget_candidates(body)
    if not candidates:
        print(
            "book_scan: the shelf widget held no book this tool can read; "
            "its markup may have changed",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT
    return candidates
