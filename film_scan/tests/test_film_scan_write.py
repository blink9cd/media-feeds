"""The record write: its exact bytes, the parents it creates, and how it fails.

Everything here is about `--output` itself (the bytes that land there, the directory
created above it, and what a write that cannot complete leaves behind) rather than
about which film the CLI picks. Every test injects `fetch`, passes `--no-director` so
the lookup seam is never reached, and points `--output` under a
`tempfile.TemporaryDirectory()`; nothing here touches the network.

The `test_film_scan_` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above
import film_scan  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE = (_TOOL_ROOT / "fixtures" / "diary.xml").read_bytes()

# Same reasoning as test_film_scan_cli.py's own _A_USERNAME: an obvious placeholder,
# chosen to look nothing like a real Letterboxd username.
_A_USERNAME = "a-placeholder-username"


def _fetch_returning(body: bytes) -> feed_read.Fetcher:
    """A fake `fetch` that ignores its arguments and returns a fixed body."""

    def fetch(url: str, timeout: float) -> bytes:
        del url, timeout
        return body

    return fetch


def _tmdb_with_poster(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
    """A fake TMDB fetch: every film has the same placeholder poster, no request made."""
    del url, timeout, headers
    return b'{"poster_path": "/a-placeholder-poster.jpg"}'


class RecordWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, out: Path) -> tuple[int, str, str]:
        """Run `main` against the fixture, writing to `out`: (code, stdout, stderr)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        # A placeholder token, pinned so a real one in the shell running the suite
        # can change nothing; the TMDB seam above answers without a request.
        with (
            patch.dict(os.environ, {film_scan.TMDB_TOKEN_ENV: "a-placeholder-token"}),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = film_scan.main(
                ["--username", _A_USERNAME, "--no-director", "--output", str(out)],
                fetch=_fetch_returning(_FIXTURE),
                poster_fetch=_tmdb_with_poster,
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_the_record_is_two_space_indented_json_ending_in_one_lf(self) -> None:
        # Pinned byte for byte, not merely as parseable JSON: the record is a
        # committed file, so a changed indent or line ending would surface as a diff
        # on every refresh. Re-dumping what was read pins the format without
        # restating the data.
        out = self.root / "films.json"
        self.assertEqual(self._run(out)[0], 0)
        raw = out.read_bytes()
        records = json.loads(raw)
        self.assertEqual(raw, (json.dumps(records, indent=2) + "\n").encode("utf-8"))
        self.assertNotIn(b"\r\n", raw)

    def test_missing_parent_directories_are_created(self) -> None:
        out = self.root / "src" / "data" / "films.json"
        self.assertEqual(self._run(out)[0], 0)
        self.assertTrue(out.is_file())

    def test_an_output_that_is_a_directory_exits_2_and_leaves_no_temp_file(
        self,
    ) -> None:
        # The OS refuses the rename onto a directory. That must come back as the
        # documented exit 2 with one line on stderr (not a traceback, and not the
        # exit 1 that means "no film found"), and the temp file must not survive it.
        out = self.root / "films.json"
        out.mkdir()
        code, _stdout, stderr = self._run(out)
        self.assertEqual(code, 2)
        self.assertIn("could not write the record file", stderr)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_a_failed_rename_keeps_the_previous_record_byte_for_byte(self) -> None:
        # The atomic rename is what guarantees a reader never sees a half-written
        # record; when the rename itself fails, the old file must be untouched (not
        # truncated, not partly rewritten) and the temp file removed.
        out = self.root / "films.json"
        old_content = '[{"title": "Old Film", "watched": "2020-01-01"}]'
        out.write_text(old_content, encoding="utf-8", newline="\n")
        with patch.object(os, "replace", side_effect=OSError("simulated rename")):
            code, _stdout, stderr = self._run(out)
        self.assertEqual(code, 2)
        self.assertIn("simulated rename", stderr)
        self.assertEqual(out.read_text(encoding="utf-8"), old_content)
        self.assertEqual(list(self.root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
