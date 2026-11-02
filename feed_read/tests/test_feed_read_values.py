"""The small value parsers: RFC-822 and ISO dates as the vendors write them, and the
floor-of-one integer option type as argparse applies it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above


class TestTerminalSafeText(unittest.TestCase):
    """What a vendor's title may do to the terminal that prints the summary."""

    def test_a_control_character_becomes_a_visible_escape(self) -> None:
        # Escaped rather than dropped: the operator should see that the title held
        # something odd, not read a silently shortened title as the real one.
        self.assertEqual(feed_read.terminal_safe_text("a\x1bb"), "a\\x1bb")

    def test_the_c1_block_a_feed_can_actually_carry_is_escaped(self) -> None:
        # Measured 2026-09-22: expat refuses a raw C0 character outright, and
        # refuses `&#27;` too ("reference to invalid character number"), so ESC
        # cannot reach a title through the feed at all. C1 can: XML 1.0 admits
        # U+0080-U+009F unescaped, and both the raw character and `&#155;` parse.
        # U+009B is the control sequence introducer and U+0085 a line break, so
        # these are the two this half of the rule actually removes.
        self.assertEqual(feed_read.terminal_safe_text("a\u009bb"), "a\\x9bb")
        self.assertEqual(feed_read.terminal_safe_text("a\u0085b"), "a\\x85b")

    def test_a_tab_and_a_newline_are_escaped_so_the_summary_stays_one_line(
        self,
    ) -> None:
        self.assertEqual(feed_read.terminal_safe_text("a\tb\nc"), "a\\x09b\\x0ac")

    def test_ordinary_text_including_non_ascii_is_untouched(self) -> None:
        # The rule is about control characters, not about non-ASCII: a title with
        # accents, CJK, or an emoji is ordinary text and must print as itself.
        for value in ("Amélie", "東京物語", "A Film \U0001f3ac"):
            with self.subTest(value=value):
                self.assertEqual(feed_read.terminal_safe_text(value), value)

    def test_an_empty_title_survives(self) -> None:
        self.assertEqual(feed_read.terminal_safe_text(""), "")


class TestRfc822Date(unittest.TestCase):
    """RFC-822 dates as Goodreads actually writes them."""

    def test_a_two_digit_day_parses(self) -> None:
        self.assertEqual(
            feed_read.rfc822_date("Fri, 28 Aug 2026 18:11:20 -0700"),
            dt.date(2026, 8, 28),
        )

    def test_a_SINGLE_digit_day_parses(self) -> None:
        # Measured on a real feed, 2026-08-31: Goodreads writes a single-digit day
        # with no leading zero (4 of 49 items). This pins the real feed's shape; it is
        # not why parsedate_to_datetime is used over strptime, whose `%d` accepts an
        # unpadded day too. test_a_date_with_no_weekday_parses below is a form that
        # genuinely differentiates the two.
        self.assertEqual(
            feed_read.rfc822_date("Sat, 1 Aug 2026 00:00:00 +0000"),
            dt.date(2026, 8, 1),
        )

    def test_a_date_with_no_weekday_parses(self) -> None:
        # RFC-822/2822 makes the leading day-of-week optional. strptime("%a, %d %b %Y
        # %H:%M:%S %z") cannot handle this (its literal "%a, " prefix has nothing to
        # match), while parsedate_to_datetime, which implements the grammar rather
        # than one fixed template, accepts it.
        self.assertEqual(
            feed_read.rfc822_date("1 Aug 2026 00:00:00 +0000"),
            dt.date(2026, 8, 1),
        )

    def test_an_unparseable_date_is_none_not_an_exception(self) -> None:
        self.assertIsNone(feed_read.rfc822_date("not a date"))

    def test_an_empty_date_is_none(self) -> None:
        self.assertIsNone(feed_read.rfc822_date(""))


class TestIsoDate(unittest.TestCase):
    """Letterboxd's `watchedDate` is already ISO."""

    def test_an_iso_date_parses(self) -> None:
        self.assertEqual(feed_read.iso_date("2026-08-31"), dt.date(2026, 8, 31))

    def test_a_bad_iso_date_is_none(self) -> None:
        self.assertIsNone(feed_read.iso_date("31/08/2026"))


class TestPositiveInt(unittest.TestCase):
    """The shared floor of one for an integer option, as argparse applies it.

    Each adapter's own CLI suite proves the option wiring (a refused `--count 0`,
    an accepted `--count 1`); these pin the function itself, once.
    """

    def test_one_is_the_smallest_value_accepted(self) -> None:
        self.assertEqual(feed_read.positive_int("1"), 1)

    def test_a_larger_value_is_returned_as_an_int(self) -> None:
        self.assertEqual(feed_read.positive_int("50"), 50)

    def test_zero_and_below_are_refused_as_argparse_type_errors(self) -> None:
        # ArgumentTypeError, not ValueError: argparse prints the former's message
        # verbatim in its usage error, where a ValueError becomes a bare "invalid
        # positive_int value" with the reason lost.
        for text in ("0", "-1"):
            with self.subTest(text=text):
                with self.assertRaises(argparse.ArgumentTypeError):
                    feed_read.positive_int(text)

    def test_a_non_integer_is_left_to_argparse_as_a_value_error(self) -> None:
        with self.assertRaises(ValueError):
            feed_read.positive_int("two")
