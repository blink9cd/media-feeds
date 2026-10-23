"""Aggregator: imports every tests/ suite so python_gate measures one coverage unit.

The floor below is deliberately RESTATED, not derived. A floor computed from discovery
compares discovery against itself and passes for an empty suite, which is the defect
this guard exists to catch. What IS derived is the file SET: `_tests_by_module`
flattens the suite `load_tests` actually folds into the real run into a per-file count,
and `GroupedSuiteDiscoveryTest.test_grouped_suite_meets_its_own_per_file_floor` checks
that count's key set against `_MIN_TESTS_BY_FILE`'s -- so a new tests/test_*.py arrives
unfloored loudly, not silently absorbed into a combined total.

``_SUITE_DIR`` is the discovery root for ``_grouped_suite()``, with
``top_level_dir=str(_SUITE_DIR)`` so a grouped module's ``__module__`` is its bare
filename (``test_parse``, not ``tests.test_parse``). That avoids the mypy "Source file
found twice under different module names" conflict a package-style ``from tests import
test_parse`` import produces the moment ``discover()`` and an explicit import disagree
on one file's qualified name.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"

# Restated, not derived (see module docstring): a floor computed from discovery would
# compare discovery against itself and pass for an empty suite.
_MIN_TESTS_BY_FILE: dict[str, int] = {
    "test_parse.py": 32,
    "test_film_scan_cli.py": 24,
    "test_film_scan_count.py": 5,
    "test_film_scan_failures.py": 5,
    "test_film_scan_identity.py": 7,
    "test_film_scan_poster_probe.py": 8,
    "test_film_scan_write.py": 4,
    # Always at least 1: the live-boundary class is loaded (and counted) regardless
    # of whether FILM_SCAN_OFFLINE or a missing FILM_SCAN_USERNAME later skips it at
    # RUN time -- discovery/collection happens before either skip decision.
    "test_live_feed.py": 2,
}
# This module's OWN tests: the guards below, which are the only reason the file exists.
# Kept apart from the grouped floors rather than summed into one total. A combined number
# carries the grouped suites' growth as slack, and these guards are small enough to vanish
# inside it -- two added grouped tests would buy the silent deletion of both. Counted
# apart, no amount of grouped growth pays for a deleted guard.
#
# Restated, not counted off the class below: a count taken from that class falls by one
# when a guard is deleted, so it would pass for exactly the deletion it exists to catch.
# Exact rather than a floor: under ``>=`` an added guard becomes room a later deletion
# can hide in.
_OWN_GUARD_TESTS = 3


def _grouped_suite() -> unittest.TestSuite:
    """Every test in tests/, discovered by walking the directory."""
    loader = unittest.TestLoader()
    return loader.discover(
        str(_SUITE_DIR), pattern="test_*.py", top_level_dir=str(_SUITE_DIR)
    )


def _tests_by_module(suite: unittest.TestSuite) -> dict[str, int]:
    """Flatten a (possibly nested) test suite into a per-module test count.

    ``unittest.TestLoader.discover`` returns a suite of suites (one layer per
    directory/module it walked), so this recurses through the nesting rather than
    assuming a fixed depth -- it keeps working if discovery's internal structure ever
    changes. Each leaf test case's ``__module__`` (e.g. ``"test_parse"``) becomes a
    ``"test_parse.py"`` key, matching ``_MIN_TESTS_BY_FILE``'s keys.
    """
    counts: dict[str, int] = {}
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for module, count in _tests_by_module(item).items():
                counts[module] = counts.get(module, 0) + count
        else:
            filename = f"{type(item).__module__}.py"
            counts[filename] = counts.get(filename, 0) + 1
    return counts


def _main_calls_on_the_real_director_client(path: Path) -> list[int]:
    """Line numbers of `film_scan.main(...)` calls in `path` left on main's own `lookup`.

    That default is the real Wikidata client, so a unit test that neither injects
    `lookup=` nor passes the literal `--no-director` makes real requests. Read off the
    AST, not by grep: the argv list and the keyword are both structure, and a call
    split over several lines still counts once.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    unsafe: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "main"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "film_scan"
        ):
            continue
        injects = any(keyword.arg == "lookup" for keyword in node.keywords)
        opts_out = any(
            isinstance(leaf, ast.Constant) and leaf.value == "--no-director"
            for leaf in ast.walk(node)
        )
        if not (injects or opts_out):
            unsafe.append(node.lineno)
    return unsafe


class GroupedSuiteDiscoveryTest(unittest.TestCase):
    """Fail loudly when THIS MODULE's real test load is missing a grouped suite --
    not merely when `tests/`, read directly off disk, is missing one.

    A test that instead called `_grouped_suite()` directly shares its subject's own
    failure mode: `load_tests` and such a guard would both call the same helper, so
    deleting or renaming `load_tests` (which silently stops every suite under `tests/`
    from running as part of the real invocation) would leave the guard re-deriving the
    same count fresh off disk and staying green regardless. `unittest.TestLoader().
    loadTestsFromModule` is exactly what `python test_film_scan.py` and python_gate's
    coverage run use to build the real suite, and it honors the `load_tests` protocol --
    calling the function below IF PRESENT, and returning only this module's own tests
    otherwise -- so this is what actually depends on that wiring (measured: deleting
    `load_tests` from an aggregator of this shape dropped a large suite to 1 while a
    discovery-based guard stayed green).
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
            f"{_OWN_GUARD_TESTS} -- a guard above may have been deleted, or a new one "
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
            f"least {grouped_floor} -- load_tests may have been removed or renamed, "
            f"silently dropping a tests/ suite from the real run",
        )

    def test_grouped_suite_meets_its_own_per_file_floor(self) -> None:
        """Fail loudly when ONE grouped file has lost tests, and when a new
        tests/test_*.py arrives with no floor entry in `_MIN_TESTS_BY_FILE` at all.
        """
        found_by_file = _tests_by_module(_grouped_suite())
        # The comparison below checks the FULL key set both ways, so a new
        # tests/test_*.py added without a matching entry here would be silently
        # unfloored -- its whole-file loss would then only be caught by
        # the grouped SUM in the guard above, which a compensating gain elsewhere can mask.
        # Comparing key sets closes that hole: an unmapped new file (or a mapped file
        # that no longer exists) fails loudly, here, before any test count is compared.
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
                f"{floor} -- a test may have been removed from it",
            )

    def test_no_grouped_test_drives_main_on_the_real_director_client(self) -> None:
        """Fail loudly when a tests/ file calls `film_scan.main` with neither a
        `lookup=` fake nor `--no-director`: that is a real Wikidata request from a
        unit test. The live suite is under the same glob and calls no `main`.
        """
        offenders = {
            path.name: lines
            for path in sorted(_SUITE_DIR.glob("test_*.py"))
            if (lines := _main_calls_on_the_real_director_client(path))
        }
        self.assertEqual(
            offenders,
            {},
            "these film_scan.main calls run on the real director client "
            f"(file: line numbers): {offenders} -- inject lookup= or pass --no-director",
        )


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """unittest's hook: fold the grouped suite in beside this file's own guards."""
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
