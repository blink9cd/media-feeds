"""Byte-built ID3 tags for the read-cap suites, and a file handle that records its reads.

Every tag declares a size, or an extended header size, the file does not hold, which
is the case the read cap exists for. Every name is invented.

Not discovered (no `test_` prefix). The read-cap suites import it from their own
directory, which each puts on `sys.path` beside the tool folder.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _inflate_frames
from _inflate_frames import (  # noqa: E402 - follows the sys.path bootstrap
    plain_frame,
    popm_body,
    synchsafe,
)

#: The largest size an ID3v2 header can declare: 28 bits, all set (256 MiB less one).
DECLARED_MAX = 2**28 - 1
#: The header's four size bytes declaring `DECLARED_MAX`.
DECLARED_MAX_FIELD = b"\x7f\x7f\x7f\x7f"
#: A v2.3 extended header size near the 4 GiB its plain 32-bit field allows.
EXTENDED_HUGE = 0xFFFF_FFF0
#: The most a file built here weighs: the point is a tiny file claiming a huge tag.
TINY_FILE_BYTES = 1024
#: The extended header flag, in the ID3 header's flag byte.
EXTENDED_FLAG = 0x40


def rated_frame(version: int, rating: int = 255) -> bytes:
    """A plain POPM frame, which a tag read whole would hand back as the rating."""
    return plain_frame(b"POPM", popm_body(rating), version)


def huge_declared_tag(path: Path, version: int = 4) -> Path:
    """A tiny file whose header declares a 256 MiB tag, then one real POPM frame."""
    header = b"ID3" + bytes((version, 0, 0)) + DECLARED_MAX_FIELD
    path.write_bytes(header + rated_frame(version))
    return path


def huge_extended_header(path: Path) -> Path:
    """A tiny v2.3 file whose extended header claims nearly 4 GiB, then a POPM frame.

    The tag's own declared size is honest; only the extended header's plain 32-bit
    size field, which mutagen reads whole before the frames, claims the huge read.
    """
    body = EXTENDED_HUGE.to_bytes(4, "big") + rated_frame(3)
    header = b"ID3" + bytes((3, 0, EXTENDED_FLAG)) + synchsafe(len(body))
    path.write_bytes(header + body)
    return path


def extended_header_past_tag(path: Path, version: int) -> Path:
    """A file whose extended header claims more than the whole tag it sits in.

    The header declares a 20-byte tag; the extended header claims 100 bytes, which the
    file does hold. mutagen reads those, then asks for the tag's remainder at a
    negative size.
    """
    claimed = 100
    if version == 4:
        field = synchsafe(claimed + 4)  # v2.4 counts the size field itself
    else:
        field = claimed.to_bytes(4, "big")
    header = b"ID3" + bytes((version, 0, EXTENDED_FLAG)) + synchsafe(20)
    path.write_bytes(header + field + bytes(claimed))
    return path


def padded_tag(path: Path, body_bytes: int) -> Path:
    """A v2.4 tag of exactly `body_bytes` past its header: a POPM frame, then padding."""
    frames = rated_frame(4)
    body = frames + bytes(body_bytes - len(frames))
    path.write_bytes(b"ID3" + bytes((4, 0, 0)) + synchsafe(len(body)) + body)
    return path


class RecordingHandle:
    """An open file that records the size of every read asked of it.

    Stands in for the file the tag reader opens, so a test sees every size the reader
    asked the file itself for, rather than what mutagen asked the reader for.
    """

    def __init__(self, handle: BinaryIO) -> None:
        """Wrap the open binary file `handle`."""
        self._handle = handle
        self.sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        """`size` bytes of the file, recorded first."""
        self.sizes.append(size)
        return self._handle.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        """The file's own `seek`."""
        return self._handle.seek(offset, whence)

    def tell(self) -> int:
        """The file's own `tell`."""
        return self._handle.tell()

    def fileno(self) -> int:
        """The file's own descriptor."""
        return self._handle.fileno()

    def __enter__(self) -> RecordingHandle:
        """This handle, as `open` gives back its file."""
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """Close the file."""
        self._handle.close()
