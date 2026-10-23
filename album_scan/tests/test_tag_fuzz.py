"""The ID3 tag reader under hostile bytes: a well-formed header over arbitrary content.

A track's tag is untrusted input: whatever wrote the file decides every byte after the
ten-byte ID3 header, and the folder source reads every `.mp3` in the library. So the
reader is held to its contract over the input space, not only over the hand-built tags
in `test_folder_facts.py`: `read_track_tags` answers a `TrackTags` whose rating is a
POPM byte or None, or raises only the `MutagenError` its docstring names, and the
wrapper `folder_facts` calls never raises at all.

Each example is written to a temporary `.mp3`, because the reader takes a path.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from mutagen import MutagenError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import album_scan  # noqa: E402, F401 - imported for its sys.path bootstrap

# album_scan's bootstrap above is what puts its private src/ on sys.path.
import folder_facts  # noqa: E402 - import follows the sys.path bootstrap above

# A REGISTERED profile loaded at import time, never an `@settings` decorator: a `@given`
# test snapshots its settings when decorated, and `fuzz_gate --explore` re-applies its
# own settings, which Hypothesis refuses on a function already carrying one.
# derandomize=True keeps the in-suite run reproducible; deadline=None and the too_slow
# suppression keep a loaded machine or a cold cache from reading as a flaky property.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: The ID3v2 major versions mutagen reads: 2.2, 2.3 and 2.4.
_MAJOR_VERSIONS = (2, 3, 4)

#: The largest body drawn: enough for a run of frames, small enough to stay fast.
_MAX_BODY = 512

#: The highest value a POPM rating byte can hold.
_MAX_RATING = 255


def _syncsafe(size: int) -> bytes:
    """`size` as the ID3 header's four 7-bit "syncsafe" bytes."""
    return bytes((size >> shift) & 0x7F for shift in (21, 14, 7, 0))


@st.composite
def _tagged_files(draw: st.DrawFn) -> bytes:
    """A file opening with a well-formed ID3 header, then arbitrary bytes.

    The header's version and flag byte are drawn too, since the flags select the
    unsynchronization and extended-header paths. Its size field is either the true
    body length or an arbitrary one, so a header that overstates or understates its
    tag is in the input space as well.
    """
    body = draw(st.binary(max_size=_MAX_BODY))
    size = draw(st.one_of(st.just(len(body)), st.integers(0, 2**28 - 1)))
    header = (
        b"ID3"
        + bytes((draw(st.sampled_from(_MAJOR_VERSIONS)), 0))
        + bytes((draw(st.integers(0, 0xFF)),))
        + _syncsafe(size)
    )
    return header + body


class TagReaderFuzzTest(unittest.TestCase):
    """The tag reader's contract over every byte string behind an ID3 header."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.track = Path(self._tmp.name) / "Invented Track.mp3"

    def _assert_well_formed(self, tags: folder_facts.TrackTags) -> None:
        self.assertIsInstance(tags, folder_facts.TrackTags)
        if tags.rating is not None:
            self.assertTrue(0 <= tags.rating <= _MAX_RATING, tags.rating)
        if tags.year is not None:
            self.assertIsInstance(tags.year, int)
            self.assertTrue(
                folder_facts.EARLIEST_YEAR <= tags.year <= folder_facts.LATEST_YEAR,
                tags.year,
            )

    @given(_tagged_files())
    def test_fuzz_read_track_tags(self, content: bytes) -> None:
        self.track.write_bytes(content)
        try:
            tags = folder_facts.read_track_tags(self.track)
        except MutagenError:
            pass  # the documented refusal; the wrapper below must absorb it
        else:
            self._assert_well_formed(tags)
        reported: list[str] = []
        self._assert_well_formed(
            folder_facts._read_or_report(self.track, reported.append)
        )


if __name__ == "__main__":  # pragma: no cover - direct-run entry point
    unittest.main()
