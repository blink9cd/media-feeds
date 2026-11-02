"""Shared scaffolding for the `--source` suites: fixtures, fakes, and a run helper.

Not a `test_*.py` file, so discovery never collects it as a suite of its own. Every
fake here answers from memory: no helper reaches the network, and every export folder
is a temporary directory a test builds.
"""

from __future__ import annotations

import email.message
import io
import json
import os
import shutil
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import book_scan
import feed_read

TOOL_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = TOOL_ROOT / "fixtures"
SHELF_FEED = FIXTURES / "shelf.xml"
EXPORT = FIXTURES / "library_export.csv"
PAGE = (FIXTURES / "book_page.html").read_bytes()
PAGE_COVER = "https://images.example.invalid/books/1700000000i/7000004.jpg"
PAGE_BASE = "https://www.goodreads.com/book/show/"

#: Same reasoning as test_book_scan_cli.py's own _A_USER_ID: an obvious placeholder.
A_USER_ID = "a-placeholder-user-id"

#: Every variable `book_scan` reads, cleared for each test so the operator's own
#: shell cannot change an outcome.
_ENV_NAMES = (
    "BOOK_SCAN_SOURCE",
    "BOOK_SCAN_CSV_DIR",
    "BOOK_SCAN_USER_ID",
    "BOOK_SCAN_KEY",
)

_NO_HEADERS = email.message.Message()


def gone_error() -> urllib.error.HTTPError:
    """The HTTP 404 Goodreads now answers for the shelf feed."""
    return urllib.error.HTTPError(
        "https://example.invalid/", 404, "gone", _NO_HEADERS, None
    )


def fetch_returning(body: bytes) -> feed_read.Fetcher:
    """A fake `fetch` that ignores its arguments and returns a fixed body."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        return body

    return fetch


def fetch_raising(error: Exception) -> feed_read.Fetcher:
    """A fake `fetch` that raises `error` instead of answering."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        raise error

    return fetch


def explode(url: str, timeout: float) -> bytes:
    """A `fetch` fake that fails the test if it is ever called."""
    raise AssertionError(f"no fetch was expected, got {url} ({timeout})")


class Pages:
    """A page-fetch fake recording each Book Id asked, answering with the fixture page."""

    def __init__(self) -> None:
        self.ids: list[str] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        del timeout
        self.ids.append(url.removeprefix(PAGE_BASE))
        return PAGE


class SourceCase(unittest.TestCase):
    """A clean environment, an export folder holding the fixture, and an output path."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.exports = self.root / "exports"
        self.exports.mkdir()
        shutil.copyfile(EXPORT, self.exports / "goodreads_library_export.csv")
        self.out = self.root / "books.json"
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in _ENV_NAMES:
            os.environ.pop(name, None)

    def run_main(
        self,
        *argv: str,
        fetch: feed_read.Fetcher = explode,
        cover_fetch: feed_read.Fetcher = explode,
    ) -> tuple[int, str, str]:
        """`book_scan.main` on `argv` plus `--output`; its code, stdout, and stderr."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = book_scan.main(
                [*argv, "--output", str(self.out)], fetch=fetch, cover_fetch=cover_fetch
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def usage_error(self, *argv: str) -> str:
        """The stderr of a run `argv` refuses as a usage error (exit 2)."""
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            book_scan.main(
                [*argv, "--output", str(self.out)], fetch=explode, cover_fetch=explode
            )
        self.assertEqual(caught.exception.code, 2)
        return stderr.getvalue()

    def written(self) -> list[dict[str, object]]:
        """The records the run wrote."""
        records: list[dict[str, object]] = json.loads(
            self.out.read_text(encoding="utf-8")
        )
        return records
