"""Look up a book's cover and year on its public Goodreads page: URL, parse, rule.

Private to `book_scan/`: the sibling `book_scan.py` primary script is this module's
only importer, and re-exports everything `tests/` needs as `book_scan.<name>`.

Why the book page: a shelf item can arrive with no usable cover (no large-image
variant, or Goodreads' "nophoto" placeholder), and a hand-exported Goodreads library
carries a Book Id but no cover at all. The public page `/book/show/<book_id>` names
its cover in an `og:image` meta tag, the Open Graph field a link preview reads, and
`robots.txt` allows the path (measured 2026-10-28, with this project's honest
User-Agent; the same day the tag was on all three public ids probed). The same page
states the publication year in one `<p data-testid="publicationInfo">` element, which
a shelf widget's book lacks, so one fetch fills both fields.

No network here: the fetch is injected (`goodreads_page_lookup`'s `fetch`), and
`book_scan.main` hands over `feed_read.real_fetch` on a live run only. Every failure
(a refused request, a 404, a sign-in or challenge page, a page with no tag) is
"no page" for that book alone, never an error for the run, and is never retried.
"""

from __future__ import annotations

import http.client
import re
import sys
import urllib.error
import urllib.parse
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

# Put the sibling feed_read tool folder on sys.path from this repository's root, two
# folders above this file (book_scan/src/), so this module imports alone as well. One
# fixed candidate rather than a search up the ancestors, for book_scan.py's reason.
sys.path += [
    str(Path(__file__).resolve().parents[2] / "feed_read"),
]

import feed_read


class BookPage(NamedTuple):
    """What one book page gives: its usable cover and its publication year.

    Either is None when the page does not state it, or the page was never read.
    """

    cover: str | None
    year: str | None


#: How a recorded book's missing cover (and year) is resolved from its Book Id.
PageLookup = Callable[[str], BookPage]

#: The public book page. The id is checked by `book_page_url` before it is put here,
#: so nothing but digits ever reaches the path.
BOOK_PAGE_URL = "https://www.goodreads.com/book/show/{book_id}"

#: The one meta property that names the cover. Matched exactly: `og:image:width` and
#: its siblings share the prefix and are not the cover.
_OG_IMAGE = "og:image"

#: The path segment Goodreads files its no-cover placeholder under
#: (`/assets/nophoto/book/...`). A placeholder is a picture of "no cover", so the
#: card's text fallback says it better than the picture would.
_NOPHOTO_SEGMENT = "nophoto"

#: A Goodreads Book Id is all ASCII digits. `str.isdigit` is not this test: it accepts
#: other scripts' digits and superscripts, which are not ids.
_BOOK_ID = re.compile(r"[0-9]+", re.ASCII)

#: The `data-testid` of the one element holding the page's publication line.
#: Measured 2026-10-28: exactly one per page, right after the pages-and-format line.
#: The year is read inside it only: the description and reviews above and below it
#: say "First published in 1959 as a short story" and the like.
_PUBLICATION_INFO = "publicationInfo"

#: The full English month names, the only words the publication line puts before a
#: day or a year; "in" or "as" in that slot is prose, not a date.
_MONTH = (
    "(?:January|February|March|April|May|June|July|August|September|October"
    "|November|December)"
)

#: The publication line, matched at the start of a text node only.
#: Measured 2026-10-28: "First published September 16, 1987".
_PUBLISHED = re.compile(
    r"(First published|Published) (?:" + _MONTH + r" (?:[0-9]{1,2}, )?)?([0-9]{4})\b",
    re.ASCII,
)

#: The prefix `_PUBLISHED` captures for the original edition's line, which wins over
#: the plain "Published" line naming the edition the page shows.
_FIRST_PUBLISHED = "First published"

#: The answer for a book whose page was not, or could not be, read.
_NO_PAGE = BookPage(cover=None, year=None)


def no_page(_book_id: str) -> BookPage:
    """Answer "no page" without asking anything.

    The default lookup for `book_scan.newest_books`, so a bare call makes no network
    request; `main` substitutes the live lookup on a live run.
    """
    return _NO_PAGE


def usable_cover(url: str) -> str | None:
    """`url` when it is a cover a card can show, else None.

    An https URL with a host (`feed_read.https_url_or_none`: the string is a third
    party's, and the record publishes it for the page to load an image from), and not
    Goodreads' no-cover placeholder. The placeholder test is on a whole path segment,
    so a cover file merely named like it still passes.
    """
    checked = feed_read.https_url_or_none(url)
    if checked is None:
        return None
    if _NOPHOTO_SEGMENT in urllib.parse.urlsplit(checked).path.split("/"):
        return None
    return checked


def book_page_url(book_id: str) -> str | None:
    """The public page URL for `book_id`, or None unless the id is all ASCII digits."""
    if _BOOK_ID.fullmatch(book_id):
        return BOOK_PAGE_URL.format(book_id=book_id)
    return None


class _BookPageParser(HTMLParser):
    """Collects the first `<meta property="og:image">` tag's `content`, and the year.

    The stdlib's tolerant HTML tokenizer, never a regular expression over the page:
    it already handles either attribute order, either quote, a self-closed or unclosed
    tag, comments, and character references, each a way a regex misreads a tag. Only
    a text node inside the publication-info element is matched by `_PUBLISHED`.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.content: str | None = None
        self.first_published: str | None = None
        self.published: str | None = None
        # The tag name of the open publication-info element, None outside it. Its
        # end tag closes it; an element left unclosed keeps it open to the end.
        self.info_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Note entry into publication info; keep the first og:image tag's content."""
        values = dict(attrs)
        if values.get("data-testid") == _PUBLICATION_INFO:
            self.info_tag = tag
        if tag != "meta" or self.content is not None:
            return
        if values.get("property") == _OG_IMAGE:
            self.content = values.get("content") or ""

    def handle_endtag(self, tag: str) -> None:
        """Note leaving the publication-info element."""
        if tag == self.info_tag:
            self.info_tag = None

    def handle_data(self, data: str) -> None:
        """Inside publication info, keep the first year of each line kind."""
        if self.info_tag is None:
            return
        match = _PUBLISHED.match(data.strip())
        if match is None:
            return
        label, year = match.groups()
        if label == _FIRST_PUBLISHED:
            self.first_published = self.first_published or year
        else:
            self.published = self.published or year


# fuzz-target
def book_page_from_page(body: bytes) -> BookPage:
    """The usable cover and the publication year a book page states.

    Decoded as UTF-8 with replacement: the page declares UTF-8, and a stray byte must
    cost at most the character it sits in, not the parse. `HTMLParser` is tolerant,
    but not on every patch release: see the handler below. The property test holds
    that this function never raises over arbitrary bytes.
    """
    parser = _BookPageParser()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
        parser.close()
    except AssertionError:
        # The stdlib's `_markupbase` raises AssertionError on a malformed marked
        # section such as `<![x[` (3.10 to 3.12 releases before the
        # backported fix); the floor names no patch level. The rest of the page is
        # lost, but a cover or year already read is kept.
        pass
    cover = None
    if parser.content is not None:
        cover = usable_cover(parser.content.strip())
    return BookPage(cover=cover, year=parser.first_published or parser.published)


# fuzz-target
def og_image_from_page(body: bytes) -> str | None:
    """The usable cover a book page's `og:image` names, or None for any other body."""
    return book_page_from_page(body).cover


def goodreads_page_lookup(fetch: feed_read.Fetcher) -> PageLookup:
    """A `PageLookup` that asks each book's public page, through `fetch`.

    One attempt per book, with no retry: a missing cover or year costs the card a
    picture or a line, not the book, so it is not worth a scheduled run's time. No
    failure is reported from here; an exception's text never leaves this function.
    """

    def lookup(book_id: str) -> BookPage:
        """The cover and year the page for `book_id` states; "no page" on failure."""
        url = book_page_url(book_id)
        if url is None:
            return _NO_PAGE
        try:
            body = fetch(url, feed_read.FEED_TIMEOUT_SECONDS)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            # An HTTPError is the refused response (a 403, a 404 for a dropped id):
            # close it, or CPython warns at garbage collection that the file was
            # implicitly cleaned up.
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            return _NO_PAGE
        return book_page_from_page(body)

    return lookup
