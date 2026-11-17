"""A read-only file view for mutagen that refuses one read over a cap.

mutagen reads an ID3v2 tag's body with a single read at the size its header declares,
a 28-bit integer of up to 256 MiB, and a v2.3 extended header at a plain 32-bit size of
up to 4 GiB, so a file of a hundred bytes could make it allocate either. `CappedReader`
is the file the tag reader hands mutagen instead of a path: it refuses a read over its
cap before reading anything, and asks the file for no more than the file holds past the
current position, so a size the file merely declares is never allocated.

The refusal, `ReadOverCap`, is deliberately neither an `OSError` nor a mutagen error:
mutagen's loaders convert an `OSError` into their own error type, which would hide what
happened, while a plain exception passes through them unchanged (the same reasoning as
`capped_zlib`'s refusal). The reader's `refused` flag is set besides, so the caller can
tell a refusal happened even if a mutagen release ever catches it.

The read cap is separate from `capped_zlib`'s inflate budget: neither charges the other.

Entry point assumptions: imported only, with `src/` on `sys.path`; no side effects,
and nothing here imports mutagen.
"""

from __future__ import annotations

import os
from typing import NoReturn, Protocol


class ReadOverCap(Exception):
    """A read asked for more bytes than one read may take; nothing was read."""


class ReadableFile(Protocol):
    """The open binary file a `CappedReader` wraps: what it reads, moves and measures."""

    def read(self, size: int = -1, /) -> bytes:
        """Up to `size` bytes from the current position."""

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        """Move to `offset` from `whence`."""

    def tell(self) -> int:
        """The current position."""

    def fileno(self) -> int:
        """The file's descriptor, for its size."""


class CappedReader:
    """A read-only view of an open file for mutagen, refusing one read over `cap` bytes.

    mutagen's ID3 loader calls only `read`, `seek` and `tell` on a file it is handed,
    and reads `name` for its messages. A refusal raises `ReadOverCap` before anything
    is read and sets `refused`.
    """

    def __init__(self, handle: ReadableFile, cap: int, name: str) -> None:
        """Wrap `handle`, refusing any one read over `cap` bytes."""
        self._handle = handle
        self._cap = cap
        self.name = name
        self.refused = False

    def _refuse(self) -> NoReturn:
        """Record the refusal and raise it."""
        self.refused = True
        raise ReadOverCap(f"a read over the {self._cap}-byte cap")

    # fuzz-target
    def read(self, size: int | None = -1) -> bytes:
        """At most `size` bytes, or the rest of the file when `size` is None or negative.

        Refused when `size` is over the cap, before anything is read. The rest of the
        file is read with a bound one past the cap, so a rest over the cap is told
        apart, and refused, without reading more than that.
        """
        if size is None or size < 0:
            data = self._read_upto(self._cap + 1)
            if len(data) > self._cap:
                self._refuse()
            return data
        if size > self._cap:
            self._refuse()
        return self._read_upto(size)

    def _read_upto(self, want: int) -> bytes:
        """Up to `want` bytes, fewer at the end of the file, in one read of what is there.

        A file object asked for `n` bytes may allocate `n` before it reads, so a read
        at a size the file only declares would allocate it however short the file is.
        Asking for no more than the file holds past here allocates what is there. A
        file that grows meanwhile reads short, as at its end.
        """
        remaining = os.fstat(self._handle.fileno()).st_size - self._handle.tell()
        return self._handle.read(max(0, min(want, remaining)))

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        """Move to `offset` from `whence`, as the file's own `seek` does."""
        return self._handle.seek(offset, whence)

    def tell(self) -> int:
        """The current position in the file."""
        return self._handle.tell()
