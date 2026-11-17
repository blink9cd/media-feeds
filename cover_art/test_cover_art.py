"""Aggregator: imports every tests/ suite so a coverage run measures one unit.

This file holds no tests of its own beyond the guards below. Every test of `cover_art`
and of the modules under `src/` lives in a tests/test_*.py file, and `load_tests` folds
them all into this module's run, so running this one file runs every suite.

The floors below are deliberately restated, not derived. A floor computed from
discovery compares discovery against itself and passes for an empty suite, which is the
defect this guard exists to catch. What is derived is the file set: `_tests_by_module`
flattens the suite `load_tests` folds into the real run into a per-file count, and
`GroupedSuiteDiscoveryTest.test_grouped_suite_meets_its_own_per_file_floor` checks
that count's key set against `_MIN_TESTS_BY_FILE`'s, so a new tests/test_*.py arrives
unfloored loudly, not silently absorbed into a combined total. That table, not this
docstring, is the list of suites.

``_SUITE_DIR`` is the discovery root for ``_grouped_suite()``, with
``top_level_dir=str(_SUITE_DIR)`` so a grouped module's ``__module__`` is its bare
filename (``test_artwork_url``, not ``tests.test_artwork_url``). That avoids the mypy
"Source file found twice under different module names" conflict a package-style
``from tests import test_artwork_url`` import produces the moment ``discover()`` and an
explicit import disagree on one file's qualified name.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"
sys.path.insert(0, str(_SUITE_DIR))

from _offline_guard import setUpModule, tearDownModule  # noqa: E402, F401 - the bootstrap above; unittest calls the hooks by name

# Restated, not derived (see module docstring): a floor computed from discovery would
# compare discovery against itself and pass for an empty suite.
_MIN_TESTS_BY_FILE: dict[str, int] = {
    # The catalog fallback: asked only on a miss, only for an artistId a same-artist
    # candidate supplied, at most twice, and ranked by the same matcher.
    "test_artist_catalog.py": 9,
    # The pure matcher: name normalization, `pick_album`'s choice over a captured
    # reply's candidate set, that reply end to end through `artwork_url`, and the
    # name-length cap ahead of the qualifier strip.
    "test_artwork_match.py": 23,
    # NFKC again after casefold and on the flattened words: each letter casefold
    # splits, an accent a deleted apostrophe leaves, caseless spellings picked as one
    # album, normalize idempotent as a property, and why the own text needs neither.
    "test_artwork_match_casefold.py": 14,
    # The raw-length refusal ahead of the normalized cap: an over-long name refused
    # without normalizing it, a fully decomposed name at the cap still matched, and
    # the derivation's longest decomposition checked over every code point.
    "test_artwork_match_raw_bound.py": 3,
    # The matcher over every script, not ASCII alone: each script's letters kept,
    # full-width qualifiers, non-Latin albums told apart, variation selectors, and the
    # flatten's invariants as properties.
    "test_artwork_match_scripts.py": 38,
    # A title with no letter, digit or mark compared by its own text: symbol titles by
    # one artist told apart, "&" kept apart from "and", and its spellings and bound;
    # each qualifier group by the same rule, as a property too, and blank names refused.
    "test_artwork_match_symbols.py": 37,
    # A redirect off https is never followed, through the real opener with urllib's
    # protocol handlers scripted.
    "test_artwork_redirect.py": 3,
    # `artwork_url` against a fake fetcher, one branch of the parse per test: the
    # wrong-album guard, the URL rewrite, every malformed field and body, and every
    # failure that must come back as no cover rather than an exception.
    "test_artwork_url.py": 23,
    # `artwork_url`'s never-raise contract as two properties: over hostile response
    # bodies and over arbitrary AlbumRef text.
    "test_artwork_url_properties.py": 2,
    # The candidate pool: the search window asked for, and an unusable collectionId
    # kept rather than deduped against.
    "test_candidate_pool.py": 2,
    # cover_art's copies of the bounded read and the redirect rule held to
    # bounded_fetch's originals, case by case.
    "test_fetch_parity.py": 7,
    # Always 4: the live class is loaded and counted whether or not COVER_ART_OFFLINE
    # later skips it at run time, beside the reachability probe's own classification.
    "test_live_artwork.py": 4,
    # Why a cover came back null: each miss names its cause, a found cover none.
    "test_miss_reason.py": 9,
    # Each property module's profile registered and loaded before its first class of
    # @given tests, read off its syntax tree, the checker that reads it, and each
    # profile's missing deadline.
    "test_hypothesis_profile_order.py": 17,
    # The offline tripwire: what it refuses and lets through, and which modules carry
    # it, read off each file's syntax tree.
    "test_offline_guard.py": 10,
    # The bounded body read: its cap and deadline on an in-memory response, and the
    # real fetch against a loopback server.
    "test_read_body.py": 12,
    # What `_real_fetch` does around its opener's `open`, faked.
    "test_real_fetch.py": 1,
    # Only an https URL with a host is recorded: the predicate, and `artwork_url`
    # consulting it.
    "test_recordable_url.py": 7,
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
_OWN_GUARD_TESTS = 3


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
    changes. Each leaf test case's ``__module__`` (e.g. ``"test_artwork_url"``) becomes
    a ``"test_artwork_url.py"`` key, matching ``_MIN_TESTS_BY_FILE``'s keys.

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
    loadTestsFromModule` is what `python test_cover_art.py` and a coverage run
    use to build the real suite, and it honors the `load_tests` protocol (calling the
    function below if present, and returning only this module's own tests
    otherwise), so this is what actually depends on that wiring.
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


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """unittest's hook: fold the grouped suite in beside this file's own guards.

    Running this file is how the tests/ suites run: without this hook none of them
    would run under it, and the modules under `src/` would be measured without their
    own tests.
    """
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
