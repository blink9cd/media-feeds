"""A `zlib` whose inflate is capped, for the tag reader to put in mutagen's frame module.

mutagen inflates a compressed ID3 frame (v2.3 and v2.4) whole with `zlib.decompress`,
which has no output bound, so a tag of a few kilobytes can hold a frame that inflates
to any size, or many frames that each stay under a per-frame bound and together do not.
`BoundedZlib` stands in for the `zlib` name in that module: its `decompress` counts
what a stream inflates to before keeping any of it, charges the count against the
current read's budget, refuses a stream past its cap or a read past its budget with
`InflateOverCap`, and passes every other name through to zlib itself.

The refusal is deliberately neither a `zlib.error` nor an `OSError`: mutagen answers a
`zlib.error` by retrying the frame and then dropping it as junk, keeping the rest of
the tag, and converts an `OSError` into its own error type. A plain exception passes
through mutagen unchanged, and `refusals` counts each one besides, so the caller can
tell a refusal happened even if a mutagen release ever catches it.

Entry point assumptions: imported only, with `src/` on `sys.path`; no side effects.
Installing the shim is the importer's job, and nothing here imports mutagen.
"""

from __future__ import annotations

import zlib
from typing import Any, Final, NoReturn

#: How much inflated output is counted at a time before a frame is inflated for keeps:
#: the most of a refused stream ever held at once.
INFLATE_CHUNK_BYTES: Final = 64 * 1024


class InflateOverCap(Exception):
    """A compressed stream would inflate past the cap or the budget; none of it was kept."""


def count_inflated(data: bytes, limit: int) -> tuple[int, zlib.error | None]:
    """How many bytes the zlib stream `data` inflates to, counted up to just past `limit`.

    The output is counted `INFLATE_CHUNK_BYTES` at a time and dropped, so a stream past
    `limit` is told apart holding no more than one chunk of it, and the count stops
    within one chunk past `limit`. A malformed stream gives back zlib's error with the
    count so far plus the chunk the failing step may have inflated and dropped: that
    is what the stream is charged, so a failure is never free.
    """
    inflater = zlib.decompressobj()
    total = 0
    try:
        total = len(inflater.decompress(data, INFLATE_CHUNK_BYTES))
        while inflater.unconsumed_tail and total <= limit:
            total += len(
                inflater.decompress(inflater.unconsumed_tail, INFLATE_CHUNK_BYTES)
            )
    except zlib.error as error:
        return total + INFLATE_CHUNK_BYTES, error
    return total, None


class BoundedZlib:
    """A `zlib` whose `decompress` keeps to a per-stream cap and a per-read budget.

    One stream may inflate to at most `cap` bytes, and every stream of one read
    together to at most `budget`: `begin_read` starts a read, zeroing `charged`.
    Outside any read, `charged` runs from the shim's making, so a caller that never
    begins one is held to a single budget for the shim's life rather than to none.
    `refusals` counts every refusal this shim has raised, so a caller can compare it
    across a read and learn of a refusal that did not reach it as an exception.
    """

    def __init__(self, cap: int, budget: int) -> None:
        """Refuse a stream inflating past `cap` bytes, or a read past `budget` in all."""
        self.cap = cap
        self.budget = budget
        # Both counts are process-global, not per thread: right only because album_scan
        # reads its tags on one thread. A caller reading tags on several threads at
        # once would need them kept per reader instead.
        self.charged = 0
        self.refusals = 0

    def begin_read(self) -> None:
        """Start a read: every stream from here is charged against a fresh budget."""
        self.charged = 0

    def _refuse(self, reason: str) -> NoReturn:
        """Count the refusal and raise it."""
        self.refusals += 1
        raise InflateOverCap(reason)

    # fuzz-target
    def decompress(self, data: bytes) -> bytes:
        """`data` inflated; `InflateOverCap` past the cap or budget, `zlib.error` if bad.

        The count is taken only up to the cap or the budget left, whichever is smaller,
        and charged before anything else, so a stream that then fails (cut short, or a
        bad checksum) still spends its share: mutagen drops a failed frame as junk and
        reads on, and a tag of them would otherwise inflate without end. A failure is
        refused when its charge passes the budget, else raised as zlib's own error, so
        mutagen's handling of a malformed frame still runs. A spent budget, one charged
        up to its last byte or past it, refuses at once, without counting.
        """
        if self.charged >= self.budget:
            self._refuse(f"compressed frames past the {self.budget}-byte budget")
        limit = min(self.cap, self.budget - self.charged)
        counted, error = count_inflated(data, limit)
        self.charged += counted
        if self.charged > self.budget or (error is None and counted > limit):
            self._refuse(f"a compressed stream past the {limit}-byte limit left")
        if error is not None:
            raise error
        inflated = zlib.decompress(data)  # a stream cut short fails here, charged above
        # Once the input is all taken, zlib can still hold back output the count did
        # not see, bounded by its internal buffers (up to several KiB): charge it, and
        # check the whole length again.
        self.charged += len(inflated) - counted
        if len(inflated) > limit:
            self._refuse(f"a compressed stream past the {limit}-byte limit left")
        return inflated

    def __getattr__(self, name: str) -> Any:
        """The attribute `name` of zlib itself (`error` and the rest), unchanged."""
        return getattr(zlib, name)
