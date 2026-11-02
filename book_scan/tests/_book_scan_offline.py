"""`book_scan.main` with the book-page seam closed, for the suites that predate it.

`main` has two network seams: the shelf `fetch`, which every suite injects, and
`cover_fetch`, which asks a coverless book's public page. A suite written before the
cover fallback injects only the first, so calling `main` bare from one would reach the
real Goodreads site for any coverless book in its feed. Each such suite calls
`offline_main` instead, which answers every page request as a failed one: the lookup
records "no cover", which is what those suites already expect.

Those suites also predate `--source`, so each call clears the two source settings'
environment variables for its duration: a shell exporting `BOOK_SCAN_SOURCE=csv`
would otherwise send every one of their RSS runs to the library export instead.

Not a `test_*.py` file, so discovery never collects it as a suite of its own.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import book_scan
import feed_read


def refuse_page(url: str, timeout: float) -> bytes:
    """A page fetch that fails the way an unreachable host does."""
    del url, timeout
    raise OSError("no book page is fetched in a unit test")


def offline_main(argv: list[str] | None, *, fetch: feed_read.Fetcher) -> int:
    """`book_scan.main(argv, fetch=fetch)`, with no page ever fetched for real."""
    with patch.dict(os.environ, {}, clear=False):
        for name in (book_scan.SOURCE_ENV, book_scan.CSV_DIR_ENV):
            os.environ.pop(name, None)
        return book_scan.main(argv, fetch=fetch, cover_fetch=refuse_page)
