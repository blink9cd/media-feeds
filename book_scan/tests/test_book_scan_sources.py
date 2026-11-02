"""`--source`, `--csv-dir` and `--cover-lookup`: which source a run reads, and csv runs.

The settings resolve option first, then environment, then default, like `--user-id`
(tests/test_book_scan_identity.py). A csv run reads the newest export in a folder the
test builds; the shelf fetch is a fake that fails the test if called, and the page
fetch is a fake recording each Book Id asked. No test here reaches the network.

The ``test_book_scan_`` prefix is load-bearing: one mypy pass covers every tool folder
in the project, so test basenames must be unique across it.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT.parent / "feed_read"))

import book_scan  # noqa: E402 - import follows the sys.path bootstrap above
from _book_scan_sources_kit import (  # noqa: E402 - follows the sys.path bootstrap above
    A_USER_ID,
    PAGE_COVER,
    SHELF_FEED,
    Pages,
    SourceCase,
    fetch_returning,
)

# Pinned as literals here, not read back from the module: the feeds env and the
# operator docs both spell them out, so a rename in the code alone must fail.
_SOURCE_ENV = "BOOK_SCAN_SOURCE"
_CSV_DIR_ENV = "BOOK_SCAN_CSV_DIR"

_FEED = SHELF_FEED.read_bytes()


class SourceSettingTest(SourceCase):
    """Where `--source` comes from, and what each value requires."""

    def test_the_default_is_the_shelf_feed(self) -> None:
        code, out, _ = self.run_main(
            "--user-id", A_USER_ID, fetch=fetch_returning(_FEED)
        )
        self.assertEqual(code, 0)
        self.assertIn("The Later Read", out)

    def test_an_empty_variable_counts_as_unset(self) -> None:
        os.environ[_SOURCE_ENV] = ""
        code, _, _ = self.run_main("--user-id", A_USER_ID, fetch=fetch_returning(_FEED))
        self.assertEqual(code, 0)

    def test_the_variable_selects_the_export(self) -> None:
        os.environ[_SOURCE_ENV] = "csv"
        code, out, _ = self.run_main(
            "--csv-dir", str(self.exports), "--no-cover-lookup"
        )
        self.assertEqual(code, 0)
        self.assertIn("A Lantern for Wren", out)

    def test_the_option_wins_over_the_variable(self) -> None:
        os.environ[_SOURCE_ENV] = "rss"
        code, out, _ = self.run_main(
            "--source", "csv", "--csv-dir", str(self.exports), "--no-cover-lookup"
        )
        self.assertEqual(code, 0)
        self.assertIn("A Lantern for Wren", out)

    def test_an_invalid_variable_is_a_usage_error_naming_the_variable(self) -> None:
        os.environ[_SOURCE_ENV] = "carrier-pigeon"
        stderr = self.usage_error("--user-id", A_USER_ID)
        self.assertIn(_SOURCE_ENV, stderr)
        self.assertNotIn("carrier-pigeon", stderr)

    def test_an_invalid_option_is_a_usage_error(self) -> None:
        self.usage_error("--source", "carrier-pigeon", "--user-id", A_USER_ID)

    def test_a_csv_run_needs_no_user_id(self) -> None:
        code, _, _ = self.run_main(
            "--source", "csv", "--csv-dir", str(self.exports), "--no-cover-lookup"
        )
        self.assertEqual(code, 0)

    def test_a_merged_run_still_needs_a_user_id(self) -> None:
        stderr = self.usage_error("--source", "both", "--csv-dir", str(self.exports))
        self.assertIn("--user-id is required", stderr)

    def test_reading_the_export_needs_a_folder(self) -> None:
        for source in ("csv", "both"):
            with self.subTest(source=source):
                stderr = self.usage_error("--source", source, "--user-id", A_USER_ID)
                self.assertIn("--csv-dir is required", stderr)
                self.assertIn(_CSV_DIR_ENV, stderr)


class CsvDirSettingTest(SourceCase):
    """Where `--csv-dir` comes from."""

    def test_the_variable_names_the_folder(self) -> None:
        os.environ[_CSV_DIR_ENV] = str(self.exports)
        code, _, _ = self.run_main("--source", "csv", "--no-cover-lookup")
        self.assertEqual(code, 0)

    def test_an_empty_variable_counts_as_unset(self) -> None:
        os.environ[_CSV_DIR_ENV] = ""
        self.usage_error("--source", "csv")

    def test_the_option_wins_over_the_variable(self) -> None:
        os.environ[_CSV_DIR_ENV] = str(self.root / "no-such-folder")
        code, _, _ = self.run_main(
            "--source", "csv", "--csv-dir", str(self.exports), "--no-cover-lookup"
        )
        self.assertEqual(code, 0)

    def test_a_leading_tilde_is_the_home_folder(self) -> None:
        # A feeds env value never passes through a shell, so nothing else expands it.
        # Both spellings: `expanduser` reads HOME on POSIX and USERPROFILE on Windows.
        os.environ["HOME"] = str(self.root)
        os.environ["USERPROFILE"] = str(self.root)
        os.environ[_CSV_DIR_ENV] = "~/exports"
        code, _, _ = self.run_main("--source", "csv", "--no-cover-lookup")
        self.assertEqual(code, 0)

    def test_an_rss_run_ignores_the_folder(self) -> None:
        os.environ[_CSV_DIR_ENV] = str(self.root / "no-such-folder")
        code, out, _ = self.run_main(
            "--user-id", A_USER_ID, fetch=fetch_returning(_FEED)
        )
        self.assertEqual(code, 0)
        self.assertIn("The Later Read", out)


class CsvRunTest(SourceCase):
    """`--source csv`: the newest export, read with no shelf fetch."""

    def _csv(
        self, *argv: str, cover_fetch: Pages | None = None
    ) -> tuple[int, str, str]:
        """A csv run on the test's export folder."""
        argv = ("--source", "csv", "--csv-dir", str(self.exports), *argv)
        if cover_fetch is None:
            return self.run_main(*argv)
        return self.run_main(*argv, cover_fetch=cover_fetch)

    def test_it_writes_the_newest_read_books(self) -> None:
        code, out, err = self._csv("--count", "3", "--no-cover-lookup")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            [record["title"] for record in self.written()],
            ["A Lantern for Wren", "The Later Read", "Harbors, Bridges and Tolls"],
        )
        self.assertEqual(
            out, "book_scan: wrote 3 book(s), newest A Lantern for Wren (2026-09-20)\n"
        )

    def test_the_committed_sample_matches_a_fresh_run(self) -> None:
        samples = _TOOL_ROOT / "samples"
        code, _, _ = self.run_main(
            "--source",
            "csv",
            "--csv-dir",
            str(samples / "library_export"),
            "--no-cover-lookup",
            "--count",
            "3",
        )
        self.assertEqual(code, 0)
        expected = (samples / "expected_books_from_export.json").read_text(
            encoding="utf-8"
        )
        self.assertEqual(self.out.read_text(encoding="utf-8"), expected)

    def test_the_sample_export_is_the_fixture(self) -> None:
        sample = _TOOL_ROOT / "samples" / "library_export" / "library_export.csv"
        self.assertEqual(
            sample.read_bytes(),
            (_TOOL_ROOT / "fixtures" / "library_export.csv").read_bytes(),
        )

    def test_each_chosen_book_asks_its_page_once_by_book_id(self) -> None:
        pages = Pages()
        code, _, _ = self._csv("--count", "2", cover_fetch=pages)
        self.assertEqual(code, 0)
        self.assertEqual(pages.ids, ["8100001", "7000003"])
        self.assertEqual(
            [record["cover"] for record in self.written()], [PAGE_COVER] * 2
        )

    def test_no_cover_lookup_asks_no_page(self) -> None:
        code, _, _ = self._csv("--no-cover-lookup")
        self.assertEqual(code, 0)
        self.assertIsNone(self.written()[0]["cover"])

    def test_cover_lookup_is_on_unless_turned_off(self) -> None:
        pages = Pages()
        code, _, _ = self._csv("--cover-lookup", cover_fetch=pages)
        self.assertEqual((code, pages.ids), (0, ["8100001"]))

    def test_a_feed_file_run_asks_no_page_whatever_the_source(self) -> None:
        code, _, _ = self._csv("--feed-file", str(SHELF_FEED))
        self.assertEqual(code, 0)
        self.assertEqual(self.written()[0]["title"], "A Lantern for Wren")

    def test_the_shelf_option_picks_the_export_shelf(self) -> None:
        code, _, _ = self._csv("--shelf", "to-read", "--no-cover-lookup")
        self.assertEqual(code, 0)
        self.assertEqual(self.written()[0]["title"], "Someday Shelf")

    def test_a_shelf_with_no_book_exits_1_and_writes_nothing(self) -> None:
        code, out, err = self._csv("--shelf", "favorites", "--no-cover-lookup")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "book_scan: no book found in the library export\n")
        self.assertFalse(self.out.exists())

    def test_a_missing_folder_exits_2_naming_no_path(self) -> None:
        self.out.write_text("[]", encoding="utf-8", newline="\n")
        missing = self.root / "a-private-folder-name"
        code, _, err = self.run_main("--source", "csv", "--csv-dir", str(missing))
        self.assertEqual(code, 2)
        self.assertIn("book_scan: could not read the library export:", err)
        self.assertNotIn("a-private-folder-name", err)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "[]")

    def test_a_folder_with_no_export_exits_2(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        code, _, err = self.run_main("--source", "csv", "--csv-dir", str(empty))
        self.assertEqual(code, 2)
        self.assertIn("no .csv export", err)

    def test_a_file_that_is_not_an_export_exits_2_without_its_rows(self) -> None:
        (self.exports / "goodreads_library_export.csv").write_text(
            "Name,Notes\nA Row Nobody Should See,x\n", encoding="utf-8", newline="\n"
        )
        code, _, err = self._csv()
        self.assertEqual(code, 2)
        self.assertIn("not a Goodreads library export", err)
        self.assertNotIn("A Row Nobody Should See", err)


class HelpTest(unittest.TestCase):
    """The new options are documented in --help, defaults and fallbacks included."""

    def setUp(self) -> None:
        self.text = " ".join(book_scan.build_parser().format_help().split())

    def test_each_option_and_its_variable_is_named(self) -> None:
        for name in ("--source", "--csv-dir", "--cover-lookup", "--no-cover-lookup"):
            self.assertIn(name, self.text)
        self.assertIn(_SOURCE_ENV, self.text)
        self.assertIn(_CSV_DIR_ENV, self.text)

    def test_the_choices_and_the_default_source_are_shown(self) -> None:
        self.assertIn("{rss,csv,both,widget}", self.text)
        self.assertIn(f"then to {book_scan.DEFAULT_SOURCE}", self.text)

    def test_the_epilog_covers_the_export(self) -> None:
        self.assertIn("library export", self.text)
        self.assertIn("Under both", self.text)


if __name__ == "__main__":
    unittest.main()
