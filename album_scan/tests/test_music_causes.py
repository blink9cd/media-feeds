"""`src/music_causes.py`: the recording runner, and the cause it reads off a record.

The toolbox answers every Music.app failure with the same `[]` or `None`, so the
recorder is what keeps the difference: it sits between the toolbox and the real `run`,
notes what came back, and hands the toolbox exactly that. These cases prove the
pass-through (the same object returned, the same exception re-raised) and each cause
the classifiers name, against records built by hand, with no toolbox and no process.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import album_scan  # noqa: E402, F401 - imported for its bootstrap, which puts src/ on sys.path
import music_causes  # noqa: E402 - import follows the sys.path bootstrap above

from music_causes import (  # noqa: E402 - import follows the sys.path bootstrap above
    STDERR_ECHO_LIMIT,
    RecordingRunner,
    RunRecord,
    facts_cause,
    newest_cause,
)

#: An invented bound, distinct from any default, so a message naming it proves it
#: came from the record rather than from a constant.
_BOUND = 42.5

#: An invented lookback window, for the same reason.
_WINDOW = 17


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    """A fake `osascript` result, built without spawning a process."""
    return subprocess.CompletedProcess(
        args=["osascript"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _answered(result: subprocess.CompletedProcess[str]) -> RunRecord:
    """The record of a call that returned `result`."""
    return RunRecord(timeout=_BOUND, error=None, result=result)


def _raised(error: Exception) -> RunRecord:
    """The record of a call that raised `error`."""
    return RunRecord(timeout=_BOUND, error=error, result=None)


_TIMED_OUT = _raised(subprocess.TimeoutExpired(["osascript"], _BOUND))
_NOT_FOUND = _raised(FileNotFoundError("osascript"))
_DENIED = _answered(
    _completed(
        1,
        stderr="0:42: execution error: Not authorized to send Apple events to "
        "Music. (-1743)\nsecond line\n",
    )
)


class RecordingRunnerTest(unittest.TestCase):
    """The recorder changes nothing the wrapped run does; it only remembers it."""

    def test_nothing_is_recorded_before_a_call(self) -> None:
        recorder = RecordingRunner(lambda _args, _timeout: _completed(0))
        self.assertIsNone(recorder.last)
        self.assertEqual(recorder.records, [])

    def test_a_result_is_returned_unchanged_and_recorded(self) -> None:
        answer = _completed(3, stdout="out", stderr="err")
        recorder = RecordingRunner(lambda _args, _timeout: answer)
        self.assertIs(recorder(["osascript"], _BOUND), answer)
        self.assertEqual(recorder.last, RunRecord(_BOUND, None, answer))

    def test_each_failure_the_toolbox_absorbs_is_re_raised_and_recorded(self) -> None:
        failures: list[Exception] = [
            subprocess.TimeoutExpired(["osascript"], _BOUND),
            FileNotFoundError("osascript"),
            ValueError("embedded null byte"),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):

                def run(
                    _args: list[str], _timeout: float, error: Exception = failure
                ) -> subprocess.CompletedProcess[str]:
                    raise error

                recorder = RecordingRunner(run)
                with self.assertRaises(type(failure)) as caught:
                    recorder(["osascript"], _BOUND)
                self.assertIs(caught.exception, failure)
                self.assertEqual(recorder.last, RunRecord(_BOUND, failure, None))

    def test_an_error_outside_the_toolbox_contract_is_not_recorded(self) -> None:
        # Not one the toolbox absorbs, so it escapes the toolbox anyway: recording
        # it would claim a cause for an answer that never comes back.
        def run(_args: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            raise KeyError("a bug in a fake")

        recorder = RecordingRunner(run)
        with self.assertRaises(KeyError):
            recorder(["osascript"], _BOUND)
        self.assertIsNone(recorder.last)

    def test_the_last_call_is_the_one_read(self) -> None:
        answers = iter([_completed(1), _completed(0, stdout="x")])
        recorder = RecordingRunner(lambda _args, _timeout: next(answers))
        recorder(["osascript"], _BOUND)
        recorder(["osascript"], _BOUND)
        self.assertEqual(len(recorder.records), 2)
        last = recorder.last
        assert last is not None  # narrows for mypy
        assert last.result is not None
        self.assertEqual(last.result.stdout, "x")


class NewestCauseTest(unittest.TestCase):
    """Why the newest-albums lookup came back empty, read off the last record."""

    def test_nothing_recorded_names_no_cause(self) -> None:
        self.assertIsNone(newest_cause(None, lookback_days=_WINDOW))

    def test_a_timeout_names_its_bound(self) -> None:
        self.assertEqual(
            newest_cause(_TIMED_OUT, lookback_days=_WINDOW),
            "Music.app timed out after 42.5s",
        )

    def test_an_osascript_that_cannot_run_says_so(self) -> None:
        for record in (_NOT_FOUND, _raised(ValueError("embedded null byte"))):
            with self.subTest(error=type(record.error).__name__):
                self.assertEqual(
                    newest_cause(record, lookback_days=_WINDOW),
                    "osascript could not be run",
                )

    def test_a_nonzero_exit_echoes_the_first_stderr_line(self) -> None:
        record = _answered(_completed(1, stderr="\n  boom (-2700)  \nmore\n"))
        self.assertEqual(
            newest_cause(record, lookback_days=_WINDOW),
            "the Music.app query failed (osascript exit 1: boom (-2700))",
        )

    def test_the_echoed_line_has_its_control_characters_escaped(self) -> None:
        record = _answered(_completed(1, stderr="bad\x1b]0;title\x07 tag"))
        cause = newest_cause(record, lookback_days=_WINDOW)
        assert cause is not None  # narrows for mypy
        self.assertIn("bad\\x1b]0;title\\x07 tag", cause)
        self.assertNotIn("\x1b", cause)

    def test_a_long_stderr_line_is_cut_at_the_limit(self) -> None:
        record = _answered(_completed(1, stderr="e" * (STDERR_ECHO_LIMIT + 50)))
        cause = newest_cause(record, lookback_days=_WINDOW)
        assert cause is not None  # narrows for mypy
        self.assertIn("e" * STDERR_ECHO_LIMIT + "...)", cause)
        self.assertNotIn("e" * (STDERR_ECHO_LIMIT + 1), cause)

    def test_the_cap_applies_after_escaping(self) -> None:
        # Each BEL escapes to four characters, so a cap taken before escaping would
        # echo four times the limit; the escaped text is what reaches the terminal.
        record = _answered(_completed(1, stderr="\x07" * (STDERR_ECHO_LIMIT + 50)))
        cause = newest_cause(record, lookback_days=_WINDOW)
        assert cause is not None  # narrows for mypy
        echoed = cause.split("osascript exit 1: ", 1)[1][: -len(")")]
        self.assertEqual(len(echoed), STDERR_ECHO_LIMIT + len("..."))
        self.assertTrue(echoed.startswith("\\x07\\x07"))
        self.assertTrue(echoed.endswith("..."))
        self.assertNotIn("\x07", cause)

    def test_a_nonzero_exit_with_no_stderr_names_the_exit_alone(self) -> None:
        self.assertEqual(
            newest_cause(_answered(_completed(2)), lookback_days=_WINDOW),
            "the Music.app query failed (osascript exit 2)",
        )

    def test_error_1743_reads_as_a_denied_automation_permission(self) -> None:
        self.assertEqual(
            newest_cause(_DENIED, lookback_days=_WINDOW),
            "automation permission denied (osascript exit 1: 0:42: execution "
            "error: Not authorized to send Apple events to Music. (-1743))",
        )

    def test_a_clean_empty_answer_names_the_window(self) -> None:
        record = _answered(_completed(0, stdout="\n"))
        self.assertEqual(
            newest_cause(record, lookback_days=_WINDOW),
            "Music.app has nothing added within the last 17 days",
        )

    def test_a_clean_empty_answer_with_no_known_window_says_so_generically(
        self,
    ) -> None:
        record = _answered(_completed(0, stdout=""))
        self.assertEqual(
            newest_cause(record, lookback_days=None),
            "Music.app has nothing added within its lookback window",
        )

    def test_an_answer_with_no_usable_row_says_so(self) -> None:
        record = _answered(_completed(0, stdout="not\ta row at all\n"))
        self.assertEqual(
            newest_cause(record, lookback_days=_WINDOW),
            "Music.app's answer held no usable album row",
        )


class FactsCauseTest(unittest.TestCase):
    """Why an album's facts came back None, read off the last record."""

    def test_nothing_recorded_names_no_cause(self) -> None:
        self.assertIsNone(facts_cause(None))

    def test_the_failures_read_as_they_do_for_the_newest_lookup(self) -> None:
        self.assertEqual(facts_cause(_TIMED_OUT), "Music.app timed out after 42.5s")
        self.assertEqual(facts_cause(_NOT_FOUND), "osascript could not be run")
        cause = facts_cause(_DENIED)
        assert cause is not None  # narrows for mypy
        self.assertTrue(cause.startswith("automation permission denied ("))

    def test_a_clean_empty_answer_says_so(self) -> None:
        self.assertEqual(
            facts_cause(_answered(_completed(0, stdout="\n"))),
            "Music.app's answer was empty",
        )

    def test_a_clean_answer_without_usable_counts_names_both_readings(self) -> None:
        # A zero track count (the album is not in the library) and a reply that does
        # not parse both come back None from the toolbox; telling them apart would
        # mean re-reading its wire format here, so the line names both.
        for stdout in ("0\t0\t0", "garbage"):
            with self.subTest(stdout=stdout):
                self.assertEqual(
                    facts_cause(_answered(_completed(0, stdout=stdout))),
                    "not in Music.app's library, or its answer held no usable counts",
                )


class PermissionDeniedTest(unittest.TestCase):
    """`is_permission_denied`: the cause a refused Apple event yields, and no other."""

    def test_a_denial_cause_is_recognized(self) -> None:
        result = subprocess.CompletedProcess(
            ["osascript"],
            1,
            "",
            "execution error: Not authorized to send Apple events (-1743)\n",
        )
        self.assertTrue(
            music_causes.is_permission_denied(music_causes._query_failed(result))
        )

    def test_a_consent_refusal_is_recognized_as_a_denial(self) -> None:
        # -1744: the event would need user consent, which an unattended run cannot
        # ask for; it is as much a refusal as -1743, and named with the same label.
        result = subprocess.CompletedProcess(
            ["osascript"],
            1,
            "",
            "execution error: Sending Apple events needs user consent. (-1744)\n",
        )
        cause = music_causes._query_failed(result)
        self.assertTrue(cause.startswith("automation permission denied ("))
        self.assertTrue(music_causes.is_permission_denied(cause))

    def test_other_causes_are_not_denials(self) -> None:
        self.assertFalse(
            music_causes.is_permission_denied(
                "the Music.app query failed (osascript exit 1)"
            )
        )
        self.assertFalse(music_causes.is_permission_denied(None))


if __name__ == "__main__":
    unittest.main()
