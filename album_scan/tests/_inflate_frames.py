"""Byte-built ID3 tags for the inflate suites: compressed frames, bombs and junk.

Every file is built byte by byte in a temporary folder: an ID3 header and its frames,
with no audio behind them, which is all an ID3 reader needs. Every name is invented.

Not discovered (no `test_` prefix). The inflate suites import it from their own
directory, which each puts on `sys.path` beside the tool folder.
"""

from __future__ import annotations

import sys
import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import album_scan  # noqa: E402, F401 - imported for its sys.path bootstrap

# album_scan's bootstrap above is what puts its private src/ on sys.path.
import folder_facts  # noqa: E402 - import follows the sys.path bootstrap above

#: Frame format flags: v2.4 compression with its data-length indicator, and v2.3's.
V24_COMPRESSED = 0x08 | 0x01
V23_COMPRESSED = 0x80
#: What a bomb frame inflates to: eight times the cap, and still a few kilobytes packed.
BOMB_BYTES = 8 * folder_facts.MAX_FRAME_INFLATE_BYTES
#: The most a bomb file may weigh for the test to show a tiny file doing it: 64 KiB.
TINY_FILE_BYTES = 64 * 1024
#: An invented player's address, the key a POPM frame carries.
EMAIL = b"player@example.com"
#: The ID3 versions that compress a frame: 2.3 and 2.4.
COMPRESSING_VERSIONS = (3, 4)
#: The bytes cut from a stream's end to make it fail: its checksum.
CHECKSUM_BYTES = 4
#: A deflate block header zlib rejects: final, of the reserved block type.
INVALID_BLOCK = b"\xff"
#: What each frame of a budget tag inflates to: just under the per-frame cap, so no
#: frame alone is refused and only the per-tag budget, which many such frames
#: together exceed, can refuse the tag.
UNDER_CAP_BYTES = folder_facts.MAX_FRAME_INFLATE_BYTES - 64
#: How many such frames the per-tag budget holds in one read.
FRAMES_IN_BUDGET = folder_facts.MAX_TAG_INFLATE_BYTES // UNDER_CAP_BYTES
#: How many a tag past the budget holds: twice what fits.
FRAMES_PAST_BUDGET = 2 * FRAMES_IN_BUDGET


def synchsafe(value: int) -> bytes:
    """`value` as ID3's four-byte synchsafe integer: seven bits per byte."""
    return bytes((value >> shift) & 0x7F for shift in (21, 14, 7, 0))


def corrupted_mid_body(body: bytes) -> bytes:
    """`body` zlib-compressed, with the block that starts its second half spoiled.

    The first half is flushed to a byte boundary, so the byte after it is a block
    header, and an invalid one: zlib inflates the first half, then fails mid-stream.
    """
    packer = zlib.compressobj(9)
    half = len(body) // 2
    head = packer.compress(body[:half]) + packer.flush(zlib.Z_FULL_FLUSH)
    tail = packer.compress(body[half:]) + packer.flush()
    return head + INVALID_BLOCK + tail[len(INVALID_BLOCK) :]


def compressed_frame(
    frame_id: bytes,
    body: bytes,
    version: int,
    *,
    cut: int = 0,
    corrupt: bool = False,
) -> bytes:
    """One `frame_id` frame holding `body`, zlib-compressed, for ID3v2.`version`.

    `cut` drops that many bytes from the stream's end, which makes a frame zlib counts
    whole and then fails: one mutagen drops as junk. `corrupt` spoils a byte mid-body
    instead, so the count itself fails after inflating half the frame.
    """
    packed = corrupted_mid_body(body) if corrupt else zlib.compress(body, 9)
    packed = packed[: len(packed) - cut]
    if version == 4:
        data = synchsafe(len(body)) + packed
        return frame_id + synchsafe(len(data)) + bytes((0, V24_COMPRESSED)) + data
    data = len(body).to_bytes(4, "big") + packed
    return frame_id + len(data).to_bytes(4, "big") + bytes((0, V23_COMPRESSED)) + data


def plain_frame(frame_id: bytes, body: bytes, version: int) -> bytes:
    """One uncompressed `frame_id` frame holding `body`, for ID3v2.`version`."""
    size = synchsafe(len(body)) if version == 4 else len(body).to_bytes(4, "big")
    return frame_id + size + b"\x00\x00" + body


def popm_body(rating: int) -> bytes:
    """A POPM frame's body: the address, the rating byte, and a zero play count."""
    return EMAIL + b"\x00" + bytes((rating,)) + b"\x00\x00\x00\x00"


def bomb_frame(version: int) -> bytes:
    """A private frame that inflates to `BOMB_BYTES`, past the cap, from a few KiB."""
    return compressed_frame(b"PRIV", b"example.com\x00" + bytes(BOMB_BYTES), version)


def under_cap_frame(
    index: int, version: int, *, cut: int = 0, corrupt: bool = False
) -> bytes:
    """A private frame inflating to `UNDER_CAP_BYTES`, its label numbered by `index`.

    mutagen keys a private frame by its label and data, so every numbered frame is
    kept: a tag of them holds the sum of what they inflate to.
    """
    label = f"example.com/{index}".encode() + b"\x00"
    body = label + bytes(UNDER_CAP_BYTES - len(label))
    return compressed_frame(b"PRIV", body, version, cut=cut, corrupt=corrupt)


def write_tagged(path: Path, frames: bytes, version: int) -> Path:
    """A file at `path` holding only an ID3v2.`version` tag of `frames`."""
    header = b"ID3" + bytes((version, 0, 0)) + synchsafe(len(frames))
    path.write_bytes(header + frames)
    return path


class TaggedFileCase(unittest.TestCase):
    """A temporary album folder to write byte-built tracks into."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.album = Path(self._tmp.name) / "The Invented Quartet" / "Placeholder Album"
        self.album.mkdir(parents=True)

    def bomb_track(self, version: int, name: str = "Bomb.mp3") -> Path:
        """A track whose bomb frame comes before a plain five-star POPM frame.

        The POPM frame after the bomb is what a swallowed refusal would keep as a
        partial tag, so a test that reads no rating proves the whole tag was refused.
        """
        frames = bomb_frame(version) + plain_frame(b"POPM", popm_body(255), version)
        return write_tagged(self.album / name, frames, version)

    def budget_track(
        self,
        version: int,
        count: int,
        name: str,
        *,
        cut: int = 0,
        corrupt: bool = False,
    ) -> Path:
        """A track of `count` under-cap private frames, then a plain five-star POPM.

        As in `bomb_track`, the POPM frame after them is what a partial tag would keep.
        """
        frames = b"".join(
            under_cap_frame(i, version, cut=cut, corrupt=corrupt) for i in range(count)
        )
        frames += plain_frame(b"POPM", popm_body(255), version)
        return write_tagged(self.album / name, frames, version)
