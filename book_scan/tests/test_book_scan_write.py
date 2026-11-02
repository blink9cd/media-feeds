"""The record write: its exact bytes, the parents it creates, and how it fails.

Everything here is about `--output` itself (the bytes that land there, the directory
created above it, and what a write that cannot complete leaves behind) rather than
about which book the CLI picks. Every test injects `fetch` and points `--output` under
a `tempfile.TemporaryDirectory()`; no unit test reaches the network.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

from _book_scan_offline import offline_main  # noqa: E402 - follows the sys.path bootstrap above
import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
import feed_read  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "shelf.xml").read_bytes()

# Same reasoning as test_book_scan_cli.py's own _A_USER_ID: an obvious placeholder,
# chosen to look nothing like a real Goodreads user id.
_A_USER_ID = "a-placeholder-user-id"


def _fetch_returning(body: bytes) -> feed_read.Fetcher:
    """A fake `fetch` that ignores its arguments and returns a fixed body."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        return body

    return fetch


class RecordWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, out: Path) -> tuple[int, str, str]:
        """Run `main` against the fixture, writing to `out`: (code, stdout, stderr)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = offline_main(
                ["--user-id", _A_USER_ID, "--output", str(out)],
                fetch=_fetch_returning(_FIXTURE),
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_the_record_is_two_space_indented_json_ending_in_one_lf(self) -> None:
        # Pinned byte for byte, not merely as parseable JSON: the record is a
        # committed file, so a changed indent or line ending would surface as a diff
        # on every refresh. Re-dumping what was read pins the format without
        # restating the data.
        out = self.root / "books.json"
        self.assertEqual(self._run(out)[0], 0)
        raw = out.read_bytes()
        records = json.loads(raw)
        self.assertEqual(raw, (json.dumps(records, indent=2) + "\n").encode("utf-8"))
        self.assertNotIn(b"\r\n", raw)

    def test_missing_parent_directories_are_created(self) -> None:
        out = self.root / "src" / "data" / "books.json"
        self.assertEqual(self._run(out)[0], 0)
        self.assertTrue(out.is_file())

    def test_an_output_that_is_a_directory_exits_2_and_leaves_no_temp_file(
        self,
    ) -> None:
        # The OS refuses the rename onto a directory. That must come back as the
        # documented exit 2 with one line on stderr (not a traceback, and not the
        # exit 1 that means "no book found"), and the temp file must not survive it.
        out = self.root / "books.json"
        out.mkdir()
        code, _stdout, stderr = self._run(out)
        self.assertEqual(code, 2)
        self.assertIn("could not write the record file", stderr)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_a_failed_rename_keeps_the_previous_record_byte_for_byte(self) -> None:
        # The atomic rename is what guarantees a reader never sees a half-written
        # record; when the rename itself fails, the old file must be untouched (not
        # truncated, not partly rewritten) and the temp file removed.
        out = self.root / "books.json"
        old_content = '[{"title": "Old Book", "read": "2020-01-01"}]'
        out.write_text(old_content, encoding="utf-8", newline="\n")
        with patch.object(os, "replace", side_effect=OSError("simulated rename")):
            code, _stdout, stderr = self._run(out)
        self.assertEqual(code, 2)
        self.assertIn("simulated rename", stderr)
        self.assertEqual(out.read_text(encoding="utf-8"), old_content)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_an_undated_newest_record_prints_no_read_date(self) -> None:
        # A widget record carries no read date, so the success line must
        # end at the title rather than print "(None)".
        undated = book_scan.BookRecord(
            title="An Undated Book",
            author="An Author",
            year=None,
            rating=None,
            read=None,
            cover=None,
        )
        with patch.object(book_scan, "fill_from_pages", return_value=[undated]):
            code, stdout, _stderr = self._run(self.root / "books.json")
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "book_scan: wrote 1 book(s), newest An Undated Book\n")


if __name__ == "__main__":
    unittest.main()
