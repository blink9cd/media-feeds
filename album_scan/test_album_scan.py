"""Aggregator: imports every tests/ suite so a coverage run measures one unit.

The floors below are deliberately restated, not derived. A floor computed from
discovery compares discovery against itself and passes for an empty suite, which is the
defect this guard exists to catch. What is derived is the file set: `_tests_by_module`
flattens the suite `load_tests` folds into the real run into a per-file count, and
`GroupedSuiteDiscoveryTest.test_grouped_suite_meets_its_own_per_file_floor` checks
that count's key set against `_MIN_TESTS_BY_FILE`'s, so a new tests/test_*.py arrives
unfloored loudly, not silently absorbed into a combined total. A third guard reads every
`main(` call under tests/ off the AST and fails one that leaves either boundary seam on
its real default (`_main_calls_missing_a_seam`).

``_SUITE_DIR`` is the discovery root for ``_grouped_suite()``, with
``top_level_dir=str(_SUITE_DIR)`` so a grouped module's ``__module__`` is its bare
filename (``test_discovery``, not ``tests.test_discovery``). That avoids the mypy
"Source file found twice under different module names" conflict a package-style
``from tests import test_discovery`` import produces the moment ``discover()`` and an
explicit import disagree on one file's qualified name.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"

# Restated, not derived (see module docstring): a floor computed from discovery would
# compare discovery against itself and pass for an empty suite.
_MIN_TESTS_BY_FILE: dict[str, int] = {
    # Folder-scan discovery at a count of one (among them two albums added the same
    # day ranking by time of day, an unreadable artist folder both skipped and
    # reported by name, and an unreadable root named without its path), plus
    # `--count`: a count of two, asking past the end, an empty library at a count
    # above one, and the decorate-sort-slice stat-count guard.
    "test_discovery.py": 19,
    "test_record.py": 5,
    # The producer half of the record contract: the record's consumer reads it from a
    # file and never imports album_scan, so no type checker sees the two together.
    # This suite pins the exact key set, verified non-vacuous by renaming `added` to
    # `date_added` and watching it fail, and `favorite_signal` as text or null.
    "test_record_contract.py": 9,
    # The CLI surface: exit codes (a missing and an unreadable --library root each
    # named without its path), --output's stdout default and "-" spelling, nothing
    # but the record reaching stdout, every message going to stderr even when writing
    # a file (so a redirect cannot bury the source-fallback notice), newline
    # translation pinned on a real non-StringIO stdout, --artwork-format reaching the
    # written record rather than merely the parser and refusing an unmeasured value at
    # parse time, and the arm where Music.app's facts are handed to the lookup as
    # tiebreakers. The --source dispatch tests are in test_source_selection.py.
    "test_album_scan_cli.py": 18,
    # --source dispatch (folder by default), including --source music at --count above
    # one driven end to
    # end through main with an injected `run` returning three distinct albums' worth
    # of rows: the only test that threads count through resolve_albums's music branch
    # (recently_added's own suite covers the collapse-then-rank logic, and
    # test_album_scan_count.py the folder path's --count wiring). Plus the
    # date-added seam raising ValueError (a NUL in argv) reading as "nothing to
    # report", never as a traceback out of main.
    "test_source_selection.py": 11,
    # The live smoke test at the Music.app/osascript process boundary, plus
    # deterministic tests of its own recent-count probe's classification
    # (_any_recent_track_exists), which run regardless of ALBUM_SCAN_OFFLINE. The
    # reachability probe both live files share is tested in test_music_probe.py.
    "test_live_music.py": 6,
    # The counts and year flowing from the toolbox's album_stats into the record: over
    # album_record's counts arm directly (supplied, absent, and a genuine zero, which
    # must not collapse into absent), over its year arm (Music's year, Music's year
    # beating the store fallback, the fallback filling an absence, neither giving
    # null), and driving main end to end under --source music: counts reaching the
    # written file,
    # --no-favorites skipping the query rather than discarding its answer, an
    # unreachable Music.app still producing a valid record, and an album Music.app
    # does not know writing null counts, and a name the subprocess seam refuses
    # outright (a NUL in argv) degrading to null counts, not a traceback.
    "test_favorites_in_record.py": 12,
    # The live counts boundary. Deliberately thin: album_stats' own suite already
    # proves the parsing and the generated script text against a fake seam, so the
    # only thing left for a live file to add is the contract: that Music.app still
    # answers this query at all. It picks its album from Music.app's own newest-album
    # answer rather than hardcoding one, so a None can only mean the contract moved.
    "test_live_favorites.py": 1,
    # The reachability probe both live files gate their skip with, classified
    # against a faked run: a clean exit reachable; a non-zero exit, an OSError, and a
    # timeout each unreachable. Process-free, so it runs regardless of
    # ALBUM_SCAN_OFFLINE.
    "test_music_probe.py": 4,
    # --count's CLI surface on the folder path: a below-one and a non-integer value
    # each refused as a usage error with nothing written, the record file staying a
    # JSON array at the default count, an explicit count selecting that many albums
    # newest-first, an explicit count of exactly one accepted, a count below the
    # library's size writing only that many, asking past the end returning what
    # exists, and --count's own default.
    "test_album_scan_count.py": 8,
    # --output's file branch, which is feed_read.write_records: the exact bytes
    # written, missing parents created, a leading ~ read as the home directory, a
    # directory target and a read-only directory each reported by name at exit 2 with
    # the previous record byte-identical, an injected replace failure leaving the old
    # record and no staging file, the staging file living in the target's own
    # directory, a symlink planted at the target replaced by a regular file with
    # the link's destination untouched, and the written file 0644 under a restrictive
    # umask and over a file of another mode. Plus the stdout branch: a detached
    # stream not failing the newline pin, and UTF-8 forced onto a real non-UTF-8
    # stream.
    "test_record_write.py": 13,
    # --cover-report: one word per cover result (found, each null cause, not asked,
    # unknown) and the entry's shape, then main writing it for a found cover, a real
    # miss, a failed request, and --no-artwork, none on no album or without the option,
    # and an unwritable report said by name while the run still succeeds.
    "test_cover_report.py": 12,
    # The stderr notices beyond the success line: --source auto's fallback notice
    # (printed ahead of an empty folder scan, ahead of a --library that is no
    # directory or cannot be listed, and worded on what is known about the scan),
    # the per-album degradation lines (counts unavailable, cover art not resolved,
    # and each one suppressed by the flag that declines the lookup, save the folder
    # source's counts line, which --no-favorites leaves standing), and an unreadable
    # artist folder reported by name only, with the scan going on to the album behind
    # it.
    "test_narration.py": 12,
    # --artwork-px's floor: a zero or negative edge length refused at parse time with
    # nothing written and neither seam reached, and an edge length of one accepted.
    "test_artwork_px.py": 2,
    # --help: the epilog keeping its four paragraph headings, the formatter still
    # showing defaults, exit 2's three meanings, the plural description, --output's
    # parent directories, --no-favorites dropping the year, --source spelling
    # Music.app, the osascript count per source, and the notices named.
    "test_help_text.py": 9,
    # The folder source's facts: the POPM star scale at every byte, tracks counted
    # on every disc with hidden names and non-audio files passed over, the first
    # year found (a v2.3 TYER included, and a tag that holds none), favorites as the
    # highest rating across every POPM frame against the threshold, null and not
    # zero when no frame exists, a present-but-zero rating a real zero, declined
    # favorites keeping tracks and year, a non-mp3 track, a tagless mp3, a corrupt
    # tag, and an unopenable file each counted, and no facts for a folder holding no
    # track, one that is missing, or one whose name no filesystem call accepts.
    "test_folder_facts.py": 19,
    # Finding the apple-music-toolbox: --toolbox over the environment over the
    # upward search, a ~ in --toolbox, an empty variable read as unset, the search's
    # two spellings and its nearest-ancestor rule, a half toolbox refused, the
    # not-found and named-but-wrong messages, an import failure read as not found,
    # the Music.app sources refusing to run with none, exit 2 from main for both,
    # and the folder source never looking. Plus the toolbox's age: a star threshold
    # refused by a toolbox whose album_facts takes no min_stars (and exit 2 from main
    # naming the fix), accepted by one that does, and not asked for with no threshold
    # or with favorites declined.
    "test_toolbox_lookup.py": 19,
    # The Music.app arms a standalone clone, which has no toolbox to drive, must still
    # run, through a stand-in toolbox written to a temporary folder: Music.app
    # reporting no album (exit 1, the library unnamed), Music.app's year and track
    # count admitting a store result by another name and, as the control, refused
    # when they disagree, and one toolbox imported twice appending each folder once.
    "test_fake_toolbox.py": 4,
    # The ID3 tag reader's property test: arbitrary bytes behind a well-formed
    # header give a POPM-byte rating or the documented MutagenError, and the wrapper
    # folder_facts calls never raises.
    "test_tag_fuzz.py": 1,
    # favorite_signal: the boundary conversion's three outcomes and the toolbox's
    # refs becoming ours, --favorite-min-stars' range, and the written record's
    # signal for the folder default, a folder threshold, no POPM, Music.app's flag
    # and rating, a Music.app that cannot answer, --no-favorites under either
    # source, and auto taking it from whichever source answered.
    "test_favorite_signal.py": 13,
}
# This module's own tests: the guards below, which are the only reason the file exists.
# Kept apart from the grouped floors rather than summed into one total. A combined number
# carries the grouped suites' growth as slack, and these guards are small enough to vanish
# inside it: two added grouped tests would buy the silent deletion of both. Counted
# apart, no amount of grouped growth pays for a deleted guard.
#
# Restated, not counted off the class below: a count taken from that class falls by one
# when a guard is deleted, so it would pass for the very deletion it exists to catch.
# Exact rather than a floor: under ``>=`` an added guard becomes room a later deletion
# can hide in.
_OWN_GUARD_TESTS = 4


def _is_this_tools_main(func: ast.expr) -> bool:
    """`main` imported bare, or `album_scan.main`; never `unittest.main`."""
    if isinstance(func, ast.Name):
        return func.id == "main"
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "main"
        and isinstance(func.value, ast.Name)
        and func.value.id == "album_scan"
    )


def _main_calls_missing_a_seam(path: Path) -> list[int]:
    """Line numbers of `main(...)` calls in `path` that pass neither or only one of
    `run=` and `fetch=`.

    `main`'s defaults are the two real boundaries: `_real_run` spawns `osascript` and
    `_real_fetch` opens an HTTPS connection. A flag such as `--source folder`,
    `--no-favorites`, or `--no-artwork` keeps a call off one of them only while the
    code honoring that flag is correct, which is what a regression, or a
    mutant, breaks. Measured: a flipped `--source` dispatch routed the folder-scan
    tests into the real `osascript`, and a bypassed `--no-favorites` routed the one
    call with no seams at all into it. So a flag is no exemption: every call passes
    both keywords, and `tests/_seams.py` supplies raising fakes for a case that needs
    neither boundary. Read off the AST, not by grep: the keywords are structure, a
    call split over several lines still counts once, and `unittest.main()` is not a
    call of this tool's `main`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_this_tools_main(node.func)):
            continue
        passed = {keyword.arg for keyword in node.keywords}
        if not {"run", "fetch"} <= passed:
            missing.append(node.lineno)
    return missing


def _grouped_suite() -> unittest.TestSuite:
    """Every test in tests/, discovered by walking the directory."""
    loader = unittest.TestLoader()
    return loader.discover(
        str(_SUITE_DIR), pattern="test_*.py", top_level_dir=str(_SUITE_DIR)
    )


class _SuiteImportError(RuntimeError):
    """A tests/ module did not import, so no census of that suite can be trusted."""


def _import_failure_message(test: unittest.TestCase) -> str | None:
    """Why `discover` could not import this leaf's module, or None for a real test.

    `unittest.TestLoader.discover` does not raise on a module it cannot import: it
    substitutes a placeholder whose class lives in the loader's own module, so the
    broken module drops out of the census entirely and a loader-owned key appears in
    its place. Both guards below would then fail for the wrong stated reason: the key
    set names the loader, and the total falls short by the broken file's whole test
    count, which reads as a stale floor and invites lowering the number. That trades a
    hard failure for a permanent silent gap, so the census refuses instead.

    Matched on the placeholder's defining module, never on a spelled-out module name and
    never on unittest's private placeholder class. Running the placeholder re-raises the
    original error with its message intact, which is the whole of the recovery.
    """
    if type(test).__module__ != unittest.loader.__name__:
        return None
    outcome = unittest.TestResult()
    test.run(outcome)
    # A placeholder always errors (raising the stored import error is the only thing
    # it does), so an empty `errors` would be unittest itself changing shape, and an
    # IndexError reports that more honestly than a fallback string pretending to explain.
    _, traceback_text = outcome.errors[0]
    detail = traceback_text.strip().splitlines()[-1]
    module = test.id().rsplit(".", 1)[-1]
    return (
        f"tests/{module}.py failed to import, so it is absent from this census and "
        f"every count is measuring its absence: {detail}. Fix the import; do not "
        "lower a floor to match the shortfall."
    )


def _tests_by_module(suite: unittest.TestSuite) -> dict[str, int]:
    """Flatten a (possibly nested) test suite into a per-module test count.

    ``unittest.TestLoader.discover`` returns a suite of suites (one layer per
    directory/module it walked), so this recurses through the nesting rather than
    assuming a fixed depth; it keeps working if discovery's internal structure ever
    changes. Each leaf test case's ``__module__`` (e.g. ``"test_discovery"``) becomes a
    ``"test_discovery.py"`` key, matching ``_MIN_TESTS_BY_FILE``'s keys.

    Raises ``_SuiteImportError`` when any leaf is a discovery placeholder: a module that
    failed to import is absent from the counts rather than reported by them, so a census
    over such a suite would be measuring the wrong thing. See
    ``_import_failure_message``.
    """
    counts: dict[str, int] = {}
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for module, count in _tests_by_module(item).items():
                counts[module] = counts.get(module, 0) + count
        else:
            # Ahead of the count, and here rather than in each guard: every census runs
            # through this branch, so there is no call site left to omit.
            if (message := _import_failure_message(item)) is not None:
                raise _SuiteImportError(message)
            filename = f"{type(item).__module__}.py"
            counts[filename] = counts.get(filename, 0) + 1
    return counts


class GroupedSuiteDiscoveryTest(unittest.TestCase):
    """Fail loudly when this module's real test load is missing a grouped suite,
    not merely when `tests/`, read directly off disk, is missing one.

    A test that instead called `_grouped_suite()` directly shares its subject's own
    failure mode: `load_tests` and such a guard would both call the same helper, so
    deleting or renaming `load_tests` (which silently stops every suite under `tests/`
    from running as part of the real invocation) would leave the guard re-deriving the
    same count fresh off disk and staying green regardless. `unittest.TestLoader().
    loadTestsFromModule` is what `python test_album_scan.py` and a coverage run
    use to build the real suite, and it honors the `load_tests` protocol (calling the
    function below if present, and returning only this module's own tests
    otherwise), so this is what actually depends on that wiring. Measured on a suite
    of the same shape: deleting `load_tests` dropped 27 tests to 1 while a
    discovery-based guard stayed green.
    """

    def test_module_load_includes_the_grouped_suites(self) -> None:
        loaded = _tests_by_module(
            unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        )
        own_found = loaded.pop(f"{__name__}.py", 0)
        self.assertEqual(
            own_found,
            _OWN_GUARD_TESTS,
            f"this module contributed {own_found} of its own tests, expected exactly "
            f"{_OWN_GUARD_TESTS}: a guard above may have been deleted, or a new one "
            f"added without raising _OWN_GUARD_TESTS",
        )
        # Derived from the per-file floors rather than restated as a second total: that
        # dict is a written-down literal, not a count taken from discovery, so summing it
        # still measures the suite against a number from outside it.
        grouped_found = sum(loaded.values())
        grouped_floor = sum(_MIN_TESTS_BY_FILE.values())
        self.assertGreaterEqual(
            grouped_found,
            grouped_floor,
            f"loading this module yielded {grouped_found} grouped tests, expected at "
            f"least {grouped_floor}: load_tests may have been removed or renamed, "
            f"silently dropping a tests/ suite from the real run",
        )

    def test_grouped_suite_meets_its_own_per_file_floor(self) -> None:
        """Fail loudly when one grouped file has lost tests, and when a new
        tests/test_*.py arrives with no floor entry in `_MIN_TESTS_BY_FILE` at all.
        """
        found_by_file = _tests_by_module(_grouped_suite())
        # The full key set is compared both ways. Without that, a new tests/test_*.py
        # with no entry here would be unfloored, its whole-file loss caught only by the
        # grouped sum in the guard above, which a compensating gain elsewhere can mask.
        # With it, an unmapped new file (or a mapped file that no longer exists) fails
        # loudly, here, before any test count is compared.
        self.assertEqual(
            found_by_file.keys(),
            _MIN_TESTS_BY_FILE.keys(),
            "tests/ discovery and _MIN_TESTS_BY_FILE disagree on which files exist: "
            f"found {sorted(found_by_file)}, mapped {sorted(_MIN_TESTS_BY_FILE)}",
        )
        for filename, floor in _MIN_TESTS_BY_FILE.items():
            found = found_by_file.get(filename, 0)
            self.assertGreaterEqual(
                found,
                floor,
                f"tests/{filename} yielded {found} tests, expected at least "
                f"{floor}: a test may have been removed from it",
            )

    def test_a_module_that_fails_to_import_is_named_with_its_own_error(self) -> None:
        """A census over a suite holding a discovery placeholder refuses, by name.

        Built on a throwaway directory, because proving this against the real `tests/`
        would mean breaking one of them. The broken module imports a missing name from a
        sibling rather than a missing module outright: that is the shape the real defect
        takes, a re-export that stopped being re-exported.
        """
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "sibling_of_the_broken.py").write_text(
                "EXPORTED = 1\n", encoding="utf-8", newline="\n"
            )
            (root / "test_sound_one.py").write_text(
                "import unittest\n"
                "class Sound(unittest.TestCase):\n"
                "    def test_passes(self) -> None:\n"
                "        pass\n",
                encoding="utf-8",
                newline="\n",
            )
            (root / "test_broken_one.py").write_text(
                "from sibling_of_the_broken import never_exported\n",
                encoding="utf-8",
                newline="\n",
            )
            # `discover` inserts its top_level_dir and never removes it, and this
            # directory is about to be deleted, so put sys.path back rather than leave a
            # dead entry in the process every other suite here shares.
            self.addCleanup(sys.path.__setitem__, slice(None), list(sys.path))
            suite = unittest.TestLoader().discover(
                str(root), pattern="test_*.py", top_level_dir=str(root)
            )
            with self.assertRaises(_SuiteImportError) as raised:
                _tests_by_module(suite)
        message = str(raised.exception)
        self.assertIn("test_broken_one", message)
        self.assertIn("never_exported", message)

    def test_every_main_call_injects_both_boundary_seams(self) -> None:
        """Fail loudly when any file here calls `main` without both `run=` and
        `fetch=`: that call runs on a real boundary the moment the flag it relies on
        stops being honored. Every `.py` under tests/ is read, helpers included, and
        this file with them.
        """
        offenders = {
            path.name: lines
            for path in sorted([Path(__file__).resolve(), *_SUITE_DIR.glob("*.py")])
            if (lines := _main_calls_missing_a_seam(path))
        }
        self.assertEqual(
            offenders,
            {},
            "these main calls leave a real boundary on its default (file: line "
            f"numbers): {offenders}; pass both run= and fetch=, from "
            "tests/_seams.py where the case needs neither boundary",
        )


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """unittest's hook: fold the grouped suite in beside this file's own guards."""
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
