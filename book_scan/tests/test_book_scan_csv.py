"""The Goodreads library export: the CSV parse, the newest-export pick, and the read.

A hand-exported library (My Books, Import and export) is the second source `book_scan`
reads. These tests drive the parser on the committed fixture, whose every row is
invented and reproduces only the export's measured structure, on variants of it built
in memory, and on arbitrary text (the property test). The folder tests build their own
exports under a temporary directory. No test here reaches the network or reads a file
outside this tool's fixtures and its own temporary directory.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above

_FIXTURE_PATH = _TOOL_ROOT / "fixtures" / "library_export.csv"
_FIXTURE = _FIXTURE_PATH.read_text(encoding="utf-8")
_HEADER, _, _BODY = _FIXTURE.partition("\n")
_COLUMNS = _HEADER.split(",")

_RECORD_KEYS = {"title", "author", "year", "rating", "read", "cover"}
_DIGITS = re.compile(r"[0-9]+", re.ASCII)


def _row(**values: str) -> str:
    """One export row in the fixture's column order, every column not named empty.

    Keys are column names with spaces as underscores (`Book_Id`), so a test states
    only the fields it is about.
    """
    named = {name.replace("_", " "): value for name, value in values.items()}
    return ",".join(named.get(column, "") for column in _COLUMNS)


def _export(*rows: str) -> str:
    """An export holding the fixture's header and `rows`."""
    return "\n".join([_HEADER, *rows]) + "\n"


def _by_id(text: str, shelf: str = "read") -> dict[str, book_scan.BookRecord]:
    """The parsed export's records, keyed by Book Id."""
    return {
        book_id: record
        for record, book_id in book_scan.parse_library_export(text, shelf)
    }


class ParseFixtureTest(unittest.TestCase):
    """The committed fixture, parsed on the default shelf."""

    def setUp(self) -> None:
        self.records = _by_id(_FIXTURE)

    def test_only_dateable_rows_on_the_read_shelf_are_kept_in_file_order(self) -> None:
        ids = [
            book_id for _, book_id in book_scan.parse_library_export(_FIXTURE, "read")
        ]
        self.assertEqual(ids, ["8100001", "8100002", "7000003", "8100004", "8100007"])

    def test_a_full_row_becomes_exactly_the_record(self) -> None:
        self.assertEqual(
            self.records["8100001"],
            {
                "title": "A Lantern for Wren",
                "author": "Odile Marchetti",
                "year": "2017",
                "rating": 4,
                "read": "2026-09-20",
                "cover": None,
            },
        )

    def test_every_record_has_exactly_the_bookrecord_keys(self) -> None:
        for record in self.records.values():
            self.assertEqual(set(record), _RECORD_KEYS)

    def test_a_quoted_field_holding_a_comma_stays_one_field(self) -> None:
        self.assertEqual(self.records["8100002"]["title"], "Harbors, Bridges and Tolls")
        self.assertEqual(self.records["8100002"]["author"], "Ferran Quist")

    def test_a_decimal_rating_reads_as_the_same_whole_star(self) -> None:
        self.assertEqual(self.records["8100002"]["rating"], 4)

    def test_a_zero_rating_in_either_form_is_unrated(self) -> None:
        self.assertIsNone(self.records["8100004"]["rating"])
        self.assertIsNone(self.records["8100007"]["rating"])

    def test_year_is_the_original_publication_year_first(self) -> None:
        self.assertEqual(self.records["8100001"]["year"], "2017")

    def test_an_empty_original_year_falls_back_to_year_published(self) -> None:
        self.assertEqual(self.records["8100002"]["year"], "2022")

    def test_an_empty_read_date_falls_back_to_the_added_date(self) -> None:
        self.assertEqual(self.records["8100007"]["read"], "2026-03-03")

    def test_a_row_with_neither_date_is_dropped(self) -> None:
        self.assertNotIn("8100008", self.records)

    def test_another_shelf_is_read_by_its_name(self) -> None:
        self.assertEqual(list(_by_id(_FIXTURE, "to-read")), ["8100005"])
        reading = _by_id(_FIXTURE, "currently-reading")
        self.assertEqual(reading["8100006"]["read"], "2026-09-22")

    def test_a_shelf_no_row_names_gives_no_record(self) -> None:
        self.assertEqual(book_scan.parse_library_export(_FIXTURE, "favorites"), [])


class ParseFieldTest(unittest.TestCase):
    """One field at a time, on rows built for the case."""

    def _one(self, **values: str) -> book_scan.BookRecord:
        """The single record a one-row export parses to."""
        values.setdefault("Book_Id", "8200001")
        values.setdefault("Date_Read", "2026/01/02")
        values.setdefault("Exclusive_Shelf", "read")
        records = book_scan.parse_library_export(_export(_row(**values)), "read")
        self.assertEqual(len(records), 1)
        return records[0][0]

    def test_every_whole_star_rating_in_both_forms(self) -> None:
        for stars in range(1, 6):
            for form in (f"{stars}", f"{stars}.0", f"{stars}.00", f" {stars} "):
                with self.subTest(form=form):
                    self.assertEqual(self._one(My_Rating=form)["rating"], stars)

    def test_any_other_rating_is_unrated(self) -> None:
        for form in ("", "0", "0.0", "4.5", "7", "-3", "junk", "4.", ".4", "1e0", "٤"):
            with self.subTest(form=form):
                self.assertIsNone(self._one(My_Rating=form)["rating"])

    def test_no_year_in_either_column_is_null_never_empty(self) -> None:
        self.assertIsNone(self._one()["year"])

    def test_a_year_that_is_not_a_bare_number_is_ignored(self) -> None:
        record = self._one(
            Original_Publication_Year="circa 1900", Year_Published="1901"
        )
        self.assertEqual(record["year"], "1901")
        self.assertIsNone(self._one(Original_Publication_Year="19O0")["year"])

    def test_a_year_is_carried_through_stripped(self) -> None:
        self.assertEqual(self._one(Original_Publication_Year=" 1847 ")["year"], "1847")

    def test_an_impossible_read_date_falls_back_to_the_added_date(self) -> None:
        record = self._one(Date_Read="2026/13/01", Date_Added="2026/02/03")
        self.assertEqual(record["read"], "2026-02-03")

    def test_a_date_in_another_shape_is_not_a_date(self) -> None:
        record = self._one(Date_Read="2026-01-02", Date_Added="2026/02/03")
        self.assertEqual(record["read"], "2026-02-03")

    def test_title_and_author_are_stripped(self) -> None:
        record = self._one(Title="  Spaced Out  ", Author=" An Author ")
        self.assertEqual(
            (record["title"], record["author"]), ("Spaced Out", "An Author")
        )

    def test_a_book_id_that_is_not_ascii_digits_is_carried_as_empty(self) -> None:
        for book_id in ("", "12a", "١٢٣", "-5", " 7 x"):
            with self.subTest(book_id=book_id):
                text = _export(
                    _row(
                        Book_Id=book_id, Date_Read="2026/01/02", Exclusive_Shelf="read"
                    )
                )
                self.assertEqual(book_scan.parse_library_export(text, "read")[0][1], "")

    def test_a_book_id_is_stripped(self) -> None:
        text = _export(
            _row(Book_Id=" 8200002 ", Date_Read="2026/01/02", Exclusive_Shelf="read")
        )
        self.assertEqual(book_scan.parse_library_export(text, "read")[0][1], "8200002")

    def test_the_shelf_is_matched_stripped(self) -> None:
        self.assertEqual(self._one(Exclusive_Shelf=" read ")["read"], "2026-01-02")


class ParseShapeTest(unittest.TestCase):
    """What the parser tolerates in the file's shape, and what it refuses."""

    def test_columns_are_found_by_name_in_any_order(self) -> None:
        rows = [
            line.split(",")
            for line in _export(
                _row(
                    Book_Id="8200003",
                    Title="T",
                    Date_Read="2026/01/02",
                    Exclusive_Shelf="read",
                )
            ).splitlines()
        ]
        reversed_text = "\n".join(",".join(reversed(row)) for row in rows) + "\n"
        records = book_scan.parse_library_export(reversed_text, "read")
        self.assertEqual([(r["title"], i) for r, i in records], [("T", "8200003")])

    def test_a_byte_order_mark_and_crlf_line_ends_are_tolerated(self) -> None:
        text = "﻿" + _FIXTURE.replace("\n", "\r\n")
        self.assertEqual(_by_id(text), _by_id(_FIXTURE))

    def test_a_short_row_reads_its_missing_fields_as_empty(self) -> None:
        text = _export("8200004,Short Row,An Author")
        self.assertEqual(book_scan.parse_library_export(text, "read"), [])

    def test_a_long_row_ignores_its_extra_fields(self) -> None:
        row = _row(Book_Id="8200005", Date_Read="2026/01/02", Exclusive_Shelf="read")
        records = book_scan.parse_library_export(_export(row + ",extra,fields"), "read")
        self.assertEqual(records[0][1], "8200005")

    def test_a_header_only_export_gives_no_record(self) -> None:
        self.assertEqual(book_scan.parse_library_export(_HEADER + "\n", "read"), [])

    def test_blank_lines_are_skipped(self) -> None:
        row = _row(Book_Id="8200006", Date_Read="2026/01/02", Exclusive_Shelf="read")
        records = book_scan.parse_library_export(_export("", row, ""), "read")
        self.assertEqual([book_id for _, book_id in records], ["8200006"])

    def test_an_empty_file_is_refused(self) -> None:
        for text in ("", "﻿", "\n\n"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "no header row"):
                    book_scan.parse_library_export(text, "read")

    def test_a_file_missing_a_required_column_is_refused_naming_it(self) -> None:
        for column in book_scan.REQUIRED_EXPORT_COLUMNS:
            with self.subTest(column=column):
                header = ",".join(c for c in _COLUMNS if c != column)
                with self.assertRaisesRegex(ValueError, re.escape(column)):
                    book_scan.parse_library_export(header + "\n", "read")

    def test_the_required_columns_are_the_measured_ones(self) -> None:
        self.assertEqual(
            set(book_scan.REQUIRED_EXPORT_COLUMNS),
            {
                "Book Id",
                "Title",
                "Author",
                "My Rating",
                "Year Published",
                "Original Publication Year",
                "Date Read",
                "Date Added",
                "Exclusive Shelf",
            },
        )

    def test_a_csv_module_error_is_refused_as_a_value_error(self) -> None:
        # A field past the csv module's size limit raises csv.Error, which is not a
        # ValueError; the caller catches only ValueError, so it must be converted.
        text = _export('"' + "x" * 200_000 + '"')
        with self.assertRaisesRegex(ValueError, "not a readable CSV"):
            book_scan.parse_library_export(text, "read")

    def test_a_refusal_never_echoes_row_content(self) -> None:
        private_title = "A Private Title Nobody Should See"
        text = _export('"' + private_title + "x" * 200_000 + '"')
        with self.assertRaises(ValueError) as caught:
            book_scan.parse_library_export(text, "read")
        self.assertNotIn(private_title, str(caught.exception))


_HEADER_PREFIXED = st.text(max_size=300).map(lambda body: _HEADER + "\n" + body)
_ROWISH = st.lists(
    st.lists(
        st.sampled_from(
            ["", "read", "8100001", "2026/01/02", "4.0", "x,y", '"', "2019"]
        ),
        max_size=25,
    ).map(",".join),
    max_size=6,
).map(lambda rows: _export(*rows))


class ParsePropertyTest(unittest.TestCase):
    """The parser over arbitrary text: a documented refusal or well-formed records."""

    @settings(deadline=None)
    @given(
        st.one_of(st.text(max_size=300), _HEADER_PREFIXED, _ROWISH),
        st.sampled_from(["read", "to-read", ""]),
    )
    def test_fuzz_parse_library_export(self, text: str, shelf: str) -> None:
        try:
            records = book_scan.parse_library_export(text, shelf)
        except ValueError:
            return
        for record, book_id in records:
            self.assertEqual(set(record), _RECORD_KEYS)
            self.assertIsInstance(record["title"], str)
            self.assertIsInstance(record["author"], str)
            read = record["read"]
            # The export dates every row it keeps; only a widget record is undated.
            assert read is not None
            self.assertEqual(dt.date.fromisoformat(read).isoformat(), read)
            self.assertIn(record["rating"], {None, 1, 2, 3, 4, 5})
            self.assertTrue(
                record["year"] is None or record["year"].strip() == record["year"] != ""
            )
            self.assertIsNone(record["cover"])
            self.assertTrue(book_id == "" or _DIGITS.fullmatch(book_id))


class NewestExportTest(unittest.TestCase):
    """Which file in the export folder is read."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def _write(self, name: str, mtime: float, text: str = _FIXTURE) -> Path:
        """An export named `name`, last modified at `mtime`."""
        path = self.folder / name
        path.write_text(text, encoding="utf-8", newline="\n")
        os.utime(path, (mtime, mtime))
        return path

    def test_the_most_recently_modified_export_is_chosen(self) -> None:
        self._write("goodreads_library_export.csv", 1_000)
        newer = self._write("an_older_name.csv", 2_000)
        self.assertEqual(book_scan.newest_export(self.folder), newer)

    def test_equal_times_are_broken_by_the_later_name(self) -> None:
        self._write("a.csv", 1_000)
        later = self._write("b.csv", 1_000)
        self.assertEqual(book_scan.newest_export(self.folder), later)

    def test_only_csv_files_count_whatever_the_suffix_case(self) -> None:
        self._write("notes.txt", 9_000)
        (self.folder / "folder.csv").mkdir()
        upper = self._write("EXPORT.CSV", 1_000)
        self.assertEqual(book_scan.newest_export(self.folder), upper)

    def test_a_folder_with_no_export_is_refused(self) -> None:
        self._write("notes.txt", 1_000)
        with self.assertRaisesRegex(ValueError, "no .csv export"):
            book_scan.newest_export(self.folder)

    def test_a_missing_folder_is_refused_without_its_path(self) -> None:
        missing = self.folder / "a-private-folder-name"
        with self.assertRaises(ValueError) as caught:
            book_scan.newest_export(missing)
        self.assertIn("the export folder", str(caught.exception))
        self.assertNotIn("a-private-folder-name", str(caught.exception))

    def test_a_file_named_as_the_folder_is_refused(self) -> None:
        path = self._write("not-a-folder.csv", 1_000)
        with self.assertRaisesRegex(ValueError, "the export folder"):
            book_scan.newest_export(path)


class ReadLibraryExportTest(unittest.TestCase):
    """The newest export, read and parsed; every failure a ValueError."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def test_the_newest_export_is_parsed(self) -> None:
        (self.folder / "export.csv").write_bytes(_FIXTURE_PATH.read_bytes())
        records = book_scan.read_library_export(self.folder, "read")
        self.assertEqual(records, book_scan.parse_library_export(_FIXTURE, "read"))

    def test_a_byte_order_mark_on_disk_is_tolerated(self) -> None:
        (self.folder / "export.csv").write_bytes(
            b"\xef\xbb\xbf" + _FIXTURE_PATH.read_bytes()
        )
        self.assertEqual(len(book_scan.read_library_export(self.folder, "read")), 5)

    def test_bytes_that_are_not_utf8_are_refused_naming_the_file_only(self) -> None:
        (self.folder / "export.csv").write_bytes(_HEADER.encode() + b"\n\xff\xfe,\n")
        with self.assertRaises(ValueError) as caught:
            book_scan.read_library_export(self.folder, "read")
        message = str(caught.exception)
        self.assertIn("export.csv", message)
        self.assertIn("UTF-8", message)
        self.assertNotIn(str(self.folder), message)

    def test_a_bad_header_is_refused_naming_the_file(self) -> None:
        (self.folder / "export.csv").write_text(
            "Name,Value\n", encoding="utf-8", newline="\n"
        )
        with self.assertRaisesRegex(ValueError, r"export\.csv.*Book Id"):
            book_scan.read_library_export(self.folder, "read")

    def test_an_unreadable_export_is_refused_naming_the_file_only(self) -> None:
        path = self.folder / "export.csv"
        path.write_text(_FIXTURE, encoding="utf-8", newline="\n")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        if os.access(path, os.R_OK):  # pragma: no cover - as root, or on Windows
            self.skipTest("root, and Windows, read a mode-0 file")
        with self.assertRaises(ValueError) as caught:
            book_scan.read_library_export(self.folder, "read")
        self.assertIn("export.csv", str(caught.exception))
        self.assertNotIn(str(self.folder), str(caught.exception))

    def test_a_read_refused_on_any_host_is_refused_naming_the_file_only(self) -> None:
        # The portable twin of the case above, which skips on Windows: a mode-0 file
        # is only read-only there, so the read itself is made to refuse, with the
        # whole path in its error as the real one carries.
        path = self.folder / "export.csv"
        path.write_text(_FIXTURE, encoding="utf-8", newline="\n")
        refusal = PermissionError(13, "Permission denied", str(path))
        with mock.patch.object(Path, "read_bytes", side_effect=refusal):
            with self.assertRaises(ValueError) as caught:
                book_scan.read_library_export(self.folder, "read")
        self.assertEqual(str(caught.exception), "export.csv: Permission denied")
        self.assertNotIn(str(self.folder), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
