"""One live call against the real `osascript` / Music.app boundary, for the counts.

`album_stats/test_album_stats.py` fakes the `run` seam, which proves the
PARSING and the generated script's TEXT but not the CONTRACT: `favorited` being renamed
again -- as it already was, from `loved` -- or the `whose` filter changing shape would
leave every faked test green. This file is the only thing in the suite that would
notice, which requires it to FAIL, not skip, on a broken contract.

It mirrors `test_live_music.py`'s structure deliberately, including why reachability is
decided by a separate probe (the one `_music_probe.py` holds for both live files):
`album_facts` never raises, so Music.app being absent, not running, not scriptable or
automation-denied all come back as a plain `None`, indistinguishable from "reachable,
but that album is not in the library". Skipping on `None` would defeat the whole point
of this file.

The album under test is not hardcoded. It is whichever album Music.app itself reports
as most recently added, which is by construction an album that EXISTS in this library --
so a `None` from `album_facts` after that lookup succeeded means the counts contract
moved, not that a made-up album was missing. No track title is read at any point; the
script asks for counts only.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# This directory too, for the shared probe beside this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _music_probe import (  # noqa: E402 - import follows the sys.path bootstrap above
    SCANNING_TIMEOUT_SECONDS,
    live_toolbox,
    music_is_reachable,
)

OFFLINE = os.environ.get("ALBUM_SCAN_OFFLINE") == "1"


@unittest.skipIf(OFFLINE, "ALBUM_SCAN_OFFLINE=1 - live Music.app lookup skipped")
class LiveFavoritedCountsTest(unittest.TestCase):
    """Exercise the real counts boundary once, so a changed contract is caught."""

    def setUp(self) -> None:
        """Skip only for genuine unreachability, decided independently of the subject.

        `album_facts` swallows every failure by contract, so reachability has to be
        established by something that does not -- otherwise this file could never tell
        "Music.app is missing" from "the contract moved", which is the one distinction
        it exists to make.
        """
        toolbox = live_toolbox()
        if toolbox is None:
            self.skipTest("no apple-music-toolbox is installed beside this project")
        self.toolbox = toolbox
        if not music_is_reachable(run=toolbox.recently_added._real_run):
            self.skipTest("Music.app is not reachable on this host")

    def test_the_newest_album_resolves_to_real_counts(self) -> None:
        # Resolved here rather than in setUp so the narrowing below is a plain local:
        # an attribute keeps its declared list across the method boundary no matter
        # what setUp proved about it.
        refs = self.toolbox.recently_added.newest_albums_from_music(
            1, timeout=SCANNING_TIMEOUT_SECONDS
        )
        if not refs:
            self.skipTest(
                "Music.app is reachable but names no recently added album -- an "
                "empty result, which test_live_music.py is the file that judges"
            )
        ref = refs[0]
        counts = self.toolbox.album_stats.album_facts(
            artist=ref.artist,
            album=ref.album,
            timeout=SCANNING_TIMEOUT_SECONDS,
        )
        # setUp proved Music.app is reachable and the lookup above named this exact
        # album, so a None here is not "maybe unavailable" and not "no such album" --
        # it means the counts contract has moved: `favorited` renamed the way `loved`
        # was, the `album artist`/`album` filter changed shape, or the reply format
        # changed.
        self.assertIsNotNone(
            counts,
            "Music.app named an album but album_facts returned None for it -- the "
            "AppleScript contract (the `favorited` property, the `whose` filter, or "
            "the reply format) has moved",
        )
        assert counts is not None  # narrows for mypy; assertIsNotNone does not
        # An album Music.app named must have at least one track, and the favorited
        # count is a subset of it. album_stats' `_parse_facts` already rejects a
        # reply that breaks either, so a transposed pair of `whose` clauses fails the
        # assertIsNotNone above; these hold the invariant should that check loosen.
        self.assertGreater(counts.tracks, 0)
        self.assertGreaterEqual(counts.favorited, 0)
        self.assertLessEqual(counts.favorited, counts.tracks)


if __name__ == "__main__":
    unittest.main()
