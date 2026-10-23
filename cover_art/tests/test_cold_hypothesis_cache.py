"""The property tests pass on a FRESH CLONE, where Hypothesis has no warm cache.

The failure this pins was invisible from a warm checkout and present on every cold
one. The first draw of a text strategy builds Hypothesis's unicode tables -- once per
storage directory, then cached under `.hypothesis/unicode_data/` -- and that build
(0.8 s measured on an idle machine, 1.6 s on a loaded one) lands inside the ~1 s the
`too_slow` health check allows the first examples. So whichever property drew text
first failed with `FailedHealthCheck: Input generation is slow`, and passed on the next
run once the cache existed. `test_cover_art.py` registers a profile that suppresses
exactly that check; this test proves the profile is loaded before the properties are
decorated, which is the only order in which it does anything.

A SUBPROCESS, deliberately: Hypothesis keeps the loaded tables in module globals, so
once any test in this process has drawn text the cold path is unreachable in-process no
matter where the storage directory points. The child gets a fresh temporary storage
directory through `HYPOTHESIS_STORAGE_DIRECTORY`, and the cache it builds is deleted
with it.

The same profile sets `deadline=None`, and `ProfileDeadlineTest` pins that in-process:
under Hypothesis's default 200 ms per-example deadline, a loaded machine that slows a
passing property's first run but not its retry fails it with `FlakyFailure: Unreliable
test timings`.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import settings

_TOOL_ROOT = Path(__file__).resolve().parent.parent

# The shared bounded-subprocess runner, vendored at one fixed place: directly under the
# repository root. Not a captured `subprocess.run(timeout=...)`:
# on expiry CPython kills only the direct child and then re-reads pipes a surviving
# grandchild still holds open, so that bound does not hold (reliability_lint RL005).
# `run_bounded` kills the tree before reading.
sys.path.insert(0, str(_TOOL_ROOT.parent / "bounded_run"))

import bounded_run  # noqa: E402 - import follows the sys.path bootstrap above

# Bounds a wedged child, not a slow one: the cold run measures 1-5 s.
_CHILD_TIMEOUT_SECONDS = 60.0

# Named CLASSES, never the module: the aggregator's `load_tests` discovers THIS file,
# so a child running the whole module would spawn a grandchild, without end. Both
# property classes, because which one pays for the table build is whichever draws
# text first, and that is an ordering detail the profile must not depend on.
_PROPERTY_SUITES = (
    "test_cover_art.ArtworkUrlAlbumRefPropertyTest",
    "test_cover_art.ArtworkUrlPropertyTest",
)


class ColdHypothesisCacheTest(unittest.TestCase):
    """Both property classes, in a child that starts with no Hypothesis cache at all."""

    def test_the_property_tests_pass_with_no_warm_unicode_cache(self) -> None:
        with TemporaryDirectory() as fresh:
            env = {
                **os.environ,
                "HYPOTHESIS_STORAGE_DIRECTORY": fresh,
                # Set, not merely inherited: every process that runs any of this
                # project's suites carries the offline switch, and a child is no
                # exception -- whatever this process was launched with.
                "COVER_ART_OFFLINE": "1",
            }
            result = bounded_run.run_bounded(
                [sys.executable, "-m", "unittest", *_PROPERTY_SUITES],
                timeout=_CHILD_TIMEOUT_SECONDS,
                cwd=_TOOL_ROOT,
                env=env,
            )
        self.assertEqual(
            result.returncode,
            0,
            f"the property tests failed on a cold Hypothesis cache:\n{result.stderr}",
        )


class ProfileDeadlineTest(unittest.TestCase):
    """The profile the property classes load sets no per-example wall-clock deadline."""

    def test_the_property_profile_carries_no_deadline(self) -> None:
        # Read from Hypothesis's process-wide registry, which test_cover_art.py
        # fills at import, before its `load_tests` reaches this file. Sound only while
        # that NAME is registered nowhere else: the registry keeps the LAST registration
        # of a name, so a shared name would read whichever module registered it last.
        profile = settings.get_profile("cover_art_properties")
        self.assertIsNone(profile.deadline)


if __name__ == "__main__":
    unittest.main()
