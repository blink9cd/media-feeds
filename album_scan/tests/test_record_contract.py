"""The exact shape of the record this tool emits -- the producer half of the contract.

THIS TEST IS LOAD-BEARING. DO NOT DELETE IT AS "asserting the obvious".

The record's consumer reads it from a file and is type-checked separately, so no type
checker ever sees the producer and the consumer together. This test stands where that
check would: rename a key here and it fails loudly, in the project that did the
renaming, instead of silently rendering a broken card somewhere else.

Renaming or removing a key is breaking, and that is what these assertions catch. ADDING
one is not breaking for a consumer that reads named keys off a plain dict and ignores
the rest -- but it still fails here, because the assertions below are exact-equality
against `_CONTRACT_KEYS`. That is deliberate: a key added to the record changes what a
consumer can render, so it should cost one considered edit to the list below rather
than sliding in unnoticed. Update `_CONTRACT_KEYS` when you mean to; do not loosen the
assertion to a subset check, which would surrender the rename guard this file exists for.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import no_music, no_store  # noqa: E402 - import follows the sys.path bootstrap above

# AlbumRef comes from album_scan, which re-exports it, NOT from cover_art directly:
# album_scan's own bootstrap is what puts cover_art on sys.path, and ruff's isort would
# sort a bare `from cover_art import ...` ABOVE the line that makes it importable.
# tests/test_record.py imports it the same way.
from album_scan import (  # noqa: E402 - follows the bootstrap
    AlbumFacts,
    AlbumRef,
    album_record,
    main,
)

#: The keys a consumer reads by name. Restated as a literal rather than derived from
#: AlbumRecord.__annotations__: a set derived from the subject compares the subject
#: against itself and would pass after any rename.
_CONTRACT_KEYS = {
    "artist",
    "album",
    "added",
    "artwork",
    "tracks",
    "favorited",
    "year",
    # Added deliberately, with the folder source: which favorites `favorited` counts.
    "favorite_signal",
}

_A_REF = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2026, 8, 30))


class RecordContractTest(unittest.TestCase):
    def test_the_record_carries_exactly_the_contract_keys(self) -> None:
        self.assertEqual(set(album_record(_A_REF, None)), _CONTRACT_KEYS)

    def test_the_three_text_fields_are_strings(self) -> None:
        record = album_record(_A_REF, "https://example.com/a/600x600bb.jpg")
        for key in ("artist", "album", "added"):
            self.assertIsInstance(record[key], str, f"{key} must be a str")

    def test_the_year_is_published_as_text_not_a_number(self) -> None:
        # One field name, one type: a consumer reading this record beside
        # feed-sourced ones gets the year as text from all of them, so this record
        # publishes text too, rather than an int a renderer has to coerce.
        record = album_record(
            _A_REF,
            None,
            facts=AlbumFacts(
                tracks=10, favorited=2, year=1994, favorite_signal="music.favorited"
            ),
        )
        self.assertEqual(record["year"], "1994")

    def test_the_favorite_signal_is_text_or_null(self) -> None:
        # One string naming the signal, so a consumer can say what a count means
        # without parsing a structure; null wherever no favorites were read.
        record = album_record(
            _A_REF,
            None,
            facts=AlbumFacts(
                tracks=10, favorited=2, year=None, favorite_signal="id3.popm>=1"
            ),
        )
        self.assertEqual(record["favorite_signal"], "id3.popm>=1")
        self.assertIsNone(album_record(_A_REF, None)["favorite_signal"])

    def test_an_absent_year_stays_null_and_never_becomes_the_text_None(self) -> None:
        # THE trap in publishing the year as text. A bare str() over the optional
        # turns a genuine absence into the four-character string "None", which is
        # truthy, would publish as "None" in the JSON, and would render as the word
        # None on the card -- a fabricated fact where the record means "not known".
        self.assertIsNone(album_record(_A_REF, None)["year"])

    def test_artwork_is_a_string_or_none_and_none_is_legitimate(self) -> None:
        # Asserting `str` alone here would wrongly reject a record for an album whose
        # cover could not be resolved -- a normal outcome, not an error, since a
        # consumer must read correctly with no image at all.
        self.assertIsNone(album_record(_A_REF, None)["artwork"])
        self.assertIsInstance(
            album_record(_A_REF, "https://example.com/x")["artwork"], str
        )

    def test_added_is_an_iso_8601_date_the_consumer_can_print_verbatim(self) -> None:
        added = album_record(_A_REF, None)["added"]
        self.assertEqual(added, "2026-08-30")
        self.assertEqual(dt.date.fromisoformat(added), _A_REF.added)

    def test_the_record_never_carries_a_filesystem_path(self) -> None:
        # This tool is the one program in this project that handles absolute paths at
        # all, and its record is usually committed and published, so a path leaking
        # into it is a PII finding on a public website, not a cosmetic flaw.
        self.assertNotIn("path", _CONTRACT_KEYS)
        for value in album_record(_A_REF, None).values():
            if isinstance(value, str):
                self.assertNotIn("/", value)

    def test_the_written_file_carries_the_same_contract_end_to_end(self) -> None:
        # The unit assertions above test album_record directly. This one drives the
        # whole CLI, so a main() that post-processed or re-shaped the record before
        # writing it could not slip past.
        #
        # The two raising seams are what keep this off the real Music.app and the
        # store; the flags only choose the arms. --source folder governs where the
        # album LIST comes from, and --no-favorites skips the counts query, which
        # mirrors the unit assertions above: they (the year test aside) pass no
        # facts, and the key set -- this test's whole subject -- is identical
        # either way.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "An Artist" / "A Record").mkdir(parents=True)
            out = root / "album.json"
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "--library",
                        str(root),
                        "--output",
                        str(out),
                        "--no-artwork",
                        "--no-favorites",
                        "--source",
                        "folder",
                    ],
                    fetch=no_store,
                    run=no_music,
                )
            self.assertEqual(code, 0)
            # Always a JSON array, even at the default --count of one -- the
            # written record is element [0], not the raw parse.
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(set(written[0]), _CONTRACT_KEYS)


if __name__ == "__main__":
    unittest.main()
