"""The tag reader's bound on compressed ID3 frames: mutagen inflates through a shim.

mutagen inflates a compressed ID3 frame (v2.3 and v2.4, the compression flag set) with
`zlib.decompress`, which has no output bound, so a tag of a few kilobytes can hold a
frame that inflates to any size. These prove the shim itself: the counted inflate at,
under, and over its limit; one stream refused past the cap without being held; every
stream of a read charged against the budget, a failed one too, and a spent budget
refusing without counting; and `folder_facts` putting the shim in mutagen's frame
module, whose refusal mutagen lets through. The reader's side is in
test_folder_facts_inflate_reader.
"""

from __future__ import annotations

import sys
import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import mutagen.id3._frames as id3_frames
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from mutagen.id3 import ID3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import album_scan  # noqa: E402, F401 - imported for its sys.path bootstrap

# album_scan's bootstrap above is what puts its private src/ on sys.path.
import capped_zlib  # noqa: E402 - import follows the sys.path bootstrap above
import folder_facts  # noqa: E402 - import follows the sys.path bootstrap above

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # for _inflate_frames beside this file
from _inflate_frames import (  # noqa: E402 - follows the sys.path bootstrap
    BOMB_BYTES,
    CHECKSUM_BYTES,
    COMPRESSING_VERSIONS,
    bomb_frame,
    write_tagged,
)

# The same registered profile tests/test_tag_fuzz.py loads, for the same reasons: a
# reproducible in-suite run, and no deadline or too_slow check on a loaded machine.
settings.register_profile(
    "deterministic",
    derandomize=True,
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("deterministic")

#: A small limit for the counted inflate's own tests, under one counting chunk.
SMALL_LIMIT = 100
#: A budget no shim test reaches, for the tests of the per-frame cap alone.
AMPLE_BUDGET = sys.maxsize


def ample_shim(cap: int) -> capped_zlib.BoundedZlib:
    """A shim refusing one stream past `cap`, with a budget no test here reaches."""
    return capped_zlib.BoundedZlib(cap, AMPLE_BUDGET)


class CountInflatedTest(unittest.TestCase):
    """The count before the keep: how much a stream inflates to, holding a chunk."""

    def test_a_stream_under_the_limit_counts_its_whole_length(self) -> None:
        body = b"invented text " * 10
        self.assertEqual(
            capped_zlib.count_inflated(zlib.compress(body), len(body) + 1),
            (len(body), None),
        )

    def test_a_stream_past_the_limit_stops_within_a_chunk_past_it(self) -> None:
        limit = capped_zlib.INFLATE_CHUNK_BYTES
        counted, error = capped_zlib.count_inflated(
            zlib.compress(bytes(BOMB_BYTES)), limit
        )
        self.assertIsNone(error)
        self.assertGreater(counted, limit)
        self.assertLessEqual(counted, limit + capped_zlib.INFLATE_CHUNK_BYTES)

    def test_a_malformed_stream_comes_back_with_zlibs_error(self) -> None:
        counted, error = capped_zlib.count_inflated(b"not a zlib stream", SMALL_LIMIT)
        self.assertIsInstance(error, zlib.error)
        self.assertEqual(counted, capped_zlib.INFLATE_CHUNK_BYTES)


class DecompressCapTest(unittest.TestCase):
    """One stream through the shim: whole under or at the cap, refused past it."""

    def test_under_the_cap_inflates_whole(self) -> None:
        self.assertEqual(
            ample_shim(SMALL_LIMIT).decompress(zlib.compress(b"x" * 10)), b"x" * 10
        )

    def test_exactly_at_the_cap_inflates_whole(self) -> None:
        body = b"y" * SMALL_LIMIT
        self.assertEqual(ample_shim(SMALL_LIMIT).decompress(zlib.compress(body)), body)

    def test_one_byte_past_the_cap_is_refused(self) -> None:
        body = b"z" * (SMALL_LIMIT + 1)
        with self.assertRaises(capped_zlib.InflateOverCap):
            ample_shim(SMALL_LIMIT).decompress(zlib.compress(body))

    def test_a_malformed_stream_raises_zlibs_error(self) -> None:
        with self.assertRaises(zlib.error):
            ample_shim(SMALL_LIMIT).decompress(b"not a zlib stream")

    def test_a_stream_cut_short_raises_zlibs_error(self) -> None:
        packed = zlib.compress(b"invented text")[:-CHECKSUM_BYTES]
        with self.assertRaises(zlib.error):
            ample_shim(SMALL_LIMIT).decompress(packed)

    def test_a_stream_past_the_cap_is_never_inflated_whole(self) -> None:
        # The count is chunked and dropped, so only the whole-stream inflate could hold
        # the bomb: refusing it must never reach that call at all.
        packed = zlib.compress(bytes(BOMB_BYTES))
        with mock.patch.object(zlib, "decompress", wraps=zlib.decompress) as whole:
            with self.assertRaises(capped_zlib.InflateOverCap):
                ample_shim(SMALL_LIMIT).decompress(packed)
        whole.assert_not_called()

    def test_output_the_count_missed_is_checked_after_the_inflate(self) -> None:
        # zlib can hold back a few bytes of output the chunked count did not see, so
        # the whole inflate's length is checked again; an undercount stands in for it.
        shim = ample_shim(SMALL_LIMIT)
        with mock.patch.object(capped_zlib, "count_inflated", return_value=(0, None)):
            with self.assertRaises(capped_zlib.InflateOverCap):
                shim.decompress(zlib.compress(bytes(SMALL_LIMIT + 1)))
        self.assertEqual(shim.charged, SMALL_LIMIT + 1)

    @given(st.binary(max_size=256), st.integers(0, 512), st.integers(0, 1024))
    def test_fuzz_decompress(self, data: bytes, cap: int, budget: int) -> None:
        # Arbitrary bytes, as a hostile frame holds: the answer is zlib's own inflate
        # when it fits both bounds, a refusal, or zlib's error, and nothing else.
        try:
            inflated = capped_zlib.BoundedZlib(cap, budget).decompress(data)
        except (zlib.error, capped_zlib.InflateOverCap):
            return
        self.assertLessEqual(len(inflated), min(cap, budget))
        self.assertEqual(inflated, zlib.decompress(data))

    @given(st.binary(max_size=2048), st.integers(0, 2048))
    def test_a_well_formed_stream_inflates_exactly_when_it_fits(
        self, body: bytes, cap: int
    ) -> None:
        try:
            inflated: bytes | None = ample_shim(cap).decompress(zlib.compress(body))
        except capped_zlib.InflateOverCap:
            inflated = None
        self.assertEqual(inflated, body if len(body) <= cap else None)


class DecompressBudgetTest(unittest.TestCase):
    """Every stream of one read, charged against the shim's budget."""

    def test_the_budget_charges_every_stream_of_a_read(self) -> None:
        shim = capped_zlib.BoundedZlib(SMALL_LIMIT, SMALL_LIMIT + SMALL_LIMIT // 2)
        shim.begin_read()
        body = b"a" * SMALL_LIMIT
        self.assertEqual(shim.decompress(zlib.compress(body)), body)
        with self.assertRaises(capped_zlib.InflateOverCap):
            shim.decompress(zlib.compress(body))
        self.assertEqual(shim.refusals, 1)

    def test_a_failed_stream_is_charged_what_it_counted(self) -> None:
        # mutagen drops a failed frame as junk and reads on, so a failure must spend
        # its count, or a tag of junk frames would inflate without end.
        shim = ample_shim(SMALL_LIMIT)
        shim.begin_read()
        with self.assertRaises(zlib.error):
            shim.decompress(zlib.compress(b"c" * SMALL_LIMIT)[:-CHECKSUM_BYTES])
        self.assertEqual(shim.charged, SMALL_LIMIT)
        with self.assertRaises(zlib.error):
            shim.decompress(b"not a zlib stream")
        self.assertEqual(shim.charged, SMALL_LIMIT + capped_zlib.INFLATE_CHUNK_BYTES)

    def test_a_spent_budget_refuses_without_counting(self) -> None:
        shim = capped_zlib.BoundedZlib(SMALL_LIMIT, SMALL_LIMIT // 2)
        shim.begin_read()
        with self.assertRaises(capped_zlib.InflateOverCap):
            shim.decompress(zlib.compress(b"d" * SMALL_LIMIT))
        with mock.patch.object(capped_zlib, "count_inflated") as count:
            with self.assertRaises(capped_zlib.InflateOverCap):
                shim.decompress(zlib.compress(b"e"))
        count.assert_not_called()
        self.assertEqual(shim.refusals, 2)

    def test_a_budget_spent_exactly_refuses_without_counting(self) -> None:
        # At a charge equal to the budget nothing is left, so the next stream is
        # refused before a chunk of it is counted.
        shim = capped_zlib.BoundedZlib(SMALL_LIMIT, SMALL_LIMIT)
        shim.begin_read()
        body = b"g" * SMALL_LIMIT
        self.assertEqual(shim.decompress(zlib.compress(body)), body)
        self.assertEqual(shim.charged, shim.budget)
        with mock.patch.object(
            capped_zlib, "count_inflated", wraps=capped_zlib.count_inflated
        ) as count:
            with self.assertRaises(capped_zlib.InflateOverCap):
                shim.decompress(zlib.compress(b"h"))
        count.assert_not_called()
        self.assertEqual(shim.refusals, 1)

    def test_begin_read_starts_a_fresh_budget(self) -> None:
        # Outside a read the budget runs from the shim's making, so a caller that
        # never begins one is held to a single budget rather than to none.
        shim = capped_zlib.BoundedZlib(SMALL_LIMIT, SMALL_LIMIT)
        body = b"f" * SMALL_LIMIT
        self.assertEqual(shim.decompress(zlib.compress(body)), body)
        with self.assertRaises(capped_zlib.InflateOverCap):
            shim.decompress(zlib.compress(body))
        shim.begin_read()
        self.assertEqual(shim.charged, 0)
        self.assertEqual(shim.decompress(zlib.compress(body)), body)


class BoundedZlibTest(unittest.TestCase):
    """The stand-in for zlib in mutagen's frame module."""

    def test_mutagens_frame_module_inflates_through_the_shim(self) -> None:
        self.assertIs(id3_frames.zlib, folder_facts.BOUNDED_ZLIB)

    def test_a_refusal_raises_and_is_counted(self) -> None:
        shim = ample_shim(SMALL_LIMIT)
        with self.assertRaises(capped_zlib.InflateOverCap):
            shim.decompress(zlib.compress(bytes(SMALL_LIMIT + 1)))
        self.assertEqual(shim.refusals, 1)
        self.assertEqual(shim.decompress(zlib.compress(b"ok")), b"ok")
        self.assertEqual(shim.refusals, 1)

    def test_every_other_name_is_zlibs_own(self) -> None:
        shim = ample_shim(SMALL_LIMIT)
        self.assertIs(shim.error, zlib.error)
        self.assertIs(shim.compress, zlib.compress)

    def test_mutagen_lets_the_refusal_through_unchanged(self) -> None:
        # What mutagen 1.48.1 does with it: its frame loop swallows only a junk or
        # unsupported frame, and its IOError conversion leaves this class alone, so
        # the shim's own refusal is what `ID3()` raises, in either version.
        with TemporaryDirectory() as folder:
            for version in COMPRESSING_VERSIONS:
                with self.subTest(version=version):
                    path = write_tagged(
                        Path(folder) / f"v2{version}.mp3", bomb_frame(version), version
                    )
                    # A fresh budget, so the frame cap refuses it, not a budget
                    # an earlier read left spent.
                    folder_facts.BOUNDED_ZLIB.begin_read()
                    with self.assertRaises(capped_zlib.InflateOverCap):
                        ID3(path)


if __name__ == "__main__":  # pragma: no cover - direct-run entry point
    unittest.main()
