"""`--help`: what it says, and that its epilog keeps its paragraphs.

These read the parser's rendered help, so no `main` call and no boundary seam is
involved. The epilog is the one place an operator learns the exit codes and what a
run spends without opening the source, which is why its wording is pinned here
rather than left to drift.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from album_scan import build_parser  # noqa: E402 - import follows the sys.path bootstrap above

# AFTER the line above, deliberately, and not sorted with it: help_formatter reaches
# sys.path only when album_scan's own bootstrap runs, which importing it does.
import help_formatter  # noqa: E402 - reachable only via album_scan's bootstrap


def _help_for(option: str) -> str:
    """The `help=` text of one named option, as the parser holds it."""
    parser = build_parser()
    action = next(a for a in parser._actions if option in a.option_strings)
    assert action.help is not None
    return action.help


class HelpTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()
        self.help = self.parser.format_help()

    def test_the_epilog_keeps_its_paragraphs(self) -> None:
        # ArgumentDefaultsHelpFormatter alone re-wraps the epilog into one block, so
        # "Exit codes:" lands mid-line. Each heading must start its own line, after
        # a blank one.
        for heading in (
            "Prerequisites:",
            "What a run spends:",
            "Output:",
            "Exit codes:",
        ):
            with self.subTest(heading=heading):
                self.assertIn(f"\n\n{heading}\n", self.help)

    def test_the_formatter_still_shows_every_default(self) -> None:
        # The two bases compose: the raw-description base keeps the epilog's lines
        # and the defaults base still appends each option's default.
        self.assertIsInstance(
            self.parser.formatter_class(prog="x"),
            argparse.ArgumentDefaultsHelpFormatter,
        )
        self.assertIn("(default: -)", self.help)

    def test_the_formatter_is_built_on_the_shared_one(self) -> None:
        # Pinned as a BINDING, not a rendering: on 3.13 and later argparse already
        # drops the bogus "(default: None)" from a required option, so a --help
        # assertion here would pass just as well with the shared base removed
        # ("The version trap" in help_formatter.md). The floor is 3.12,
        # below that fix. This tool has no required option TODAY, so the shim changes
        # nothing it renders; the binding is what keeps that true of the option added
        # next, instead of leaving one CLI in this project on the stock formatter.
        #
        # An INSTANCE check, not assertIs: the local formatter MIXES the shared base
        # with RawDescriptionHelpFormatter, which is what keeps the epilog's line
        # breaks, so the assertion is "built on", not "is". Instantiated the way
        # argparse itself does because `formatter_class` is typed as a callable
        # (`_FormatterClass`) rather than a `type`, which `issubclass` rejects --
        # the same shape the defaults assertion above uses.
        self.assertIsInstance(
            self.parser.formatter_class(prog="x"),
            help_formatter.ReqAwareDefaultsFormatter,
        )

    def test_exit_2_covers_a_usage_error_the_library_and_the_output(self) -> None:
        self.assertIn("a usage error", self.help)
        self.assertIn("--library not a directory or not readable", self.help)
        self.assertIn("--output cannot be written", self.help)

    def test_the_description_counts_the_albums(self) -> None:
        # Up to --count albums are written, so the description is plural.
        assert self.parser.description is not None
        self.assertIn("albums", self.parser.description)
        self.assertNotIn("the most recently added album,", self.parser.description)

    def test_output_help_says_missing_parents_are_created(self) -> None:
        self.assertIn("parent directories", _help_for("--output"))

    def test_no_favorites_help_says_it_drops_the_music_year_too(self) -> None:
        self.assertIn("year", _help_for("--no-favorites"))

    def test_source_help_spells_music_app(self) -> None:
        self.assertIn("Music.app", _help_for("--source"))
        self.assertNotIn("music.app", _help_for("--source"))

    def test_the_epilog_counts_osascript_per_source(self) -> None:
        self.assertIn("--source folder runs none", self.help)

    def test_the_epilog_names_the_notices(self) -> None:
        for phrase in ("fallback notice", "counts", "cover art", "could not read"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.help)


if __name__ == "__main__":
    unittest.main()
