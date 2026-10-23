"""The record's shape, and the guarantee that it carries no filesystem path.

The path test is the most important one in this project. The record `album_record` builds
is usually committed and published (see "Why the record carries no filesystem path" in
`album_scan.md`), and this tool is the one program in the project that handles
absolute paths at all, which makes `album_record` the one function whose output could
carry a machine path into a published file.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from album_scan import AlbumRef, album_record, newest_albums  # noqa: E402 - import follows the sys.path bootstrap above

REF = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2026, 8, 30))


class AlbumRecordTest(unittest.TestCase):
    def test_the_record_carries_names_a_date_and_an_artwork_url(self) -> None:
        record = album_record(REF, "https://example.com/a/600x600bb.jpg")
        self.assertEqual(
            record,
            {
                "artist": "An Artist",
                "album": "A Record",
                "added": "2026-08-30",
                "artwork": "https://example.com/a/600x600bb.jpg",
                # Null because no counts were supplied, which is a different fact
                # from a zero count -- see tests/test_favorites_in_record.py.
                "tracks": None,
                "favorited": None,
                "year": None,
                "favorite_signal": None,
            },
        )

    def test_a_missing_cover_serializes_as_null_not_an_empty_string(self) -> None:
        # An empty string would render as a src="" image, which browsers resolve to the
        # page itself; null says plainly that there is no cover.
        self.assertIsNone(album_record(REF, None)["artwork"])

    def test_the_date_is_iso_8601(self) -> None:
        self.assertEqual(album_record(REF, None)["added"], "2026-08-30")

    def test_the_record_contains_no_filesystem_path_from_a_deep_library(self) -> None:
        """The guarantee the module docstring calls the most important in this
        project, so it must hold on every platform the gate type-checks (win32,
        linux, darwin), not just the one it happened to run on. A plain
        `assertNotIn(tmp, serialized)` misses a leaked Windows path: `json.dumps`
        doubles a Windows path's backslashes, so the raw `tmp` (single backslashes)
        never appears in the doubled-backslash JSON text even when the path leaked in
        full -- checked against BOTH the raw and the JSON-escaped form of `tmp` for
        that reason. And checking only "/" misses a Windows path entirely (it has
        none); both slash directions are rejected, regardless of which one this
        host's `tmp` actually used, because the record must carry neither on ANY
        platform.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "An Artist" / "A Record").mkdir(parents=True)
            ref = newest_albums(root, 1)[0]
            serialized = json.dumps(album_record(ref, None))
        # The temp root is a real absolute path on this machine. None of it may
        # survive, in either its raw or its JSON-escaped form.
        self.assertNotIn(tmp, serialized)
        escaped_tmp = json.dumps(tmp)[1:-1]  # strip the quotes json.dumps wraps it in
        self.assertNotIn(escaped_tmp, serialized)
        # No path separator survives either -- forward slash, backslash (a JSON-
        # escaped backslash is a PAIR of backslash characters, so a single-char
        # search for "\\" already catches it), and this host's own os.sep, which is
        # redundant with the two above but stated because the guarantee is "no
        # separator of any kind", not "no separator this host happens to use".
        # https:// is the one legitimate "/" carrier (an artwork URL) and is stripped
        # before the check.
        sanitized = serialized.replace("https://", "")
        for separator in ("/", "\\", os.sep):
            self.assertNotIn(separator, sanitized)

    def test_the_record_keys_are_exactly_the_documented_set(self) -> None:
        # Deliberately a literal, not derived from AlbumRecord.__annotations__: a set
        # derived from the subject compares the subject against itself and would pass
        # after any rename. tests/test_record_contract.py holds the other copy, which
        # is the one the CONSUMER's contract is stated in; this one guards the
        # producer in isolation, so both are meant to exist.
        self.assertEqual(
            set(album_record(REF, None)),
            {
                "artist",
                "album",
                "added",
                "artwork",
                "tracks",
                "favorited",
                "year",
                "favorite_signal",
            },
        )


if __name__ == "__main__":
    unittest.main()
