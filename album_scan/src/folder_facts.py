"""The folder source's facts about one album: read from its own folder, never Music.app.

`tracks` is the album folder's audio-file count, `year` the first ID3 year tag found,
and the favorites count comes from each track's ID3 `POPM` (popularimeter) frames. The
only module in this project that imports mutagen, the tag reader the folder source
needs; nothing here spawns a process or opens a connection.

The star scale is the one POPM writers share: a rating byte of 1, 64, 128, 196, or 255
starts one to five stars, and 0 is unrated. A track's rating is the highest byte across
all of its POPM frames, whichever player wrote each one, since a frame per player is
how the format records several opinions of one track. An album on which no track
carries a POPM frame has no favorites count at all, which is a different fact from a
counted zero, and the common case for a library Music.app manages, since Music.app
keeps its ratings in its own library rather than in the files.

The walk below each album folder is recursive, so a multi-disc album's disc folders
count as one album's tracks, with any name starting with a dot passed over, as
`library_walk` passes them over at the artist and album levels.

Importing this module puts `capped_zlib`'s bounded `zlib` in mutagen's ID3 frame
module, so a compressed frame inflating past `MAX_FRAME_INFLATE_BYTES`, or a tag whose
compressed frames together inflate past `MAX_TAG_INFLATE_BYTES`, is refused rather
than held, and its track is read as an unreadable tag. Each tag is read through
`capped_read`'s view of the file, so a tag, or a v2.3 extended header, declaring more
than `MAX_TAG_READ_BYTES` is refused before any of it is read, however little the file
holds; a refusal of either kind is `TagOverCap`, which `folder_facts` reports by cause.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Final, NamedTuple

import mutagen.id3._frames as id3_frames
from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError

import capped_read
import capped_zlib
from record_builder import AlbumFacts

#: The file suffixes counted as a track, compared case-insensitively. The common audio
#: containers a music library holds; anything else in an album folder (cover art, a
#: cue sheet, notes) is not a track.
AUDIO_SUFFIXES = frozenset(
    {".mp3", ".m4a", ".aac", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".opus", ".wma"}
)

#: The one container whose tags are read: ID3 is MP3's tag format. Another format's
#: track still counts toward `tracks`, and contributes no rating and no year.
_ID3_SUFFIX = ".mp3"

#: The lowest rating byte of each star, one to five; a byte reaching one counts as
#: that many stars. Shared by the POPM writers that use a five-star scale.
STAR_THRESHOLDS = (1, 64, 128, 196, 255)

#: The threshold when the operator names none: any rated track is a favorite.
DEFAULT_MIN_STARS = 1

#: The frame holding a track's year, once mutagen has upgraded a v2.3 or v2.2 tag.
_YEAR_FRAME = "TDRC"

#: The span of years read as a year: anything outside it is a corrupt or placeholder
#: tag (0000, 9999), which the album card would otherwise print as the album's year.
EARLIEST_YEAR = 1000
LATEST_YEAR = 2999

#: The most one compressed ID3 frame may inflate to: 1 MiB. Only the rating and year
#: frames are kept, each a few dozen bytes, so this is far past any frame read here;
#: it sits that high so a compressed frame this reader never keeps (lyrics, a
#: comment) still loads rather than costing the track its rating, while a frame
#: inflating to hundreds of megabytes from a few kilobytes on disk is refused.
MAX_FRAME_INFLATE_BYTES: Final = 1024 * 1024

#: The most all of one tag's compressed frames may inflate to together: 4 MiB, four
#: frames at the cap. The cap alone bounds one frame, not a tag: mutagen keeps every
#: frame whose key differs (a private frame's key holds its data), so a tag of tens of
#: kilobytes can hold dozens of frames each just under the cap. Frames that fail to
#: inflate are charged too, since mutagen drops them as junk and reads on. Four frames
#: at the cap is still far past any real tag's compressed frames.
MAX_TAG_INFLATE_BYTES: Final = 4 * MAX_FRAME_INFLATE_BYTES

#: The most one read of a track's tag may ask for: 16 MiB. mutagen reads a tag's body
#: whole, at the size its header declares (up to 256 MiB), before parsing any frame, so
#: this bounds each read a track can make the reader allocate. It sits that high so a
#: real tag holding several large embedded covers still reads. A v2.3 extended header
#: and the body are separate reads, about 32 MiB together at worst, and parsing costs
#: two to three times the body again, so one track peaks at about 80 MiB.
MAX_TAG_READ_BYTES: Final = 16 * 1024 * 1024

#: The shim, put in place of the `zlib` name mutagen's ID3 frame module inflates with.
BOUNDED_ZLIB: Final = capped_zlib.BoundedZlib(
    MAX_FRAME_INFLATE_BYTES, MAX_TAG_INFLATE_BYTES
)
# setattr, because the module attribute is typed as a module and the shim stands in for it.
setattr(id3_frames, "zlib", BOUNDED_ZLIB)

#: What `folder_facts` calls with the file name of each track whose tags it could not
#: read: a corrupt tag, a file it may not open, or (absent a reporter of its own) a tag
#: over a size cap.
UnreadableReporter = Callable[[str], None]


class TagOverCap(MutagenError):
    """The tag is over a size cap: its read, or its compressed frames' inflate."""


class TagMalformed(MutagenError):
    """The tag is malformed in a way mutagen raises as a `ValueError`, not its own error.

    An extended header claiming more than its whole tag is one: mutagen then asks for
    the tag's remainder at a negative size.
    """


class TrackTags(NamedTuple):
    """What one track's tags say: its highest POPM byte (None: no frame) and its year."""

    rating: int | None
    year: int | None


def stars_for(rating_byte: int) -> int:
    """How many stars a POPM rating byte is, from 0 (unrated) to 5."""
    return sum(1 for threshold in STAR_THRESHOLDS if rating_byte >= threshold)


def audio_files(album_dir: Path) -> list[Path]:
    """Every track under `album_dir`, at any depth, in one sorted order.

    Raises ValueError for a path no filesystem call accepts (a NUL in it); a folder
    that is missing or cannot be listed yields no tracks, as `os.walk` reports it.
    """
    found: list[Path] = []
    for folder, subfolders, files in os.walk(album_dir):
        # Pruned in place, so the walk never enters a hidden folder.
        subfolders[:] = sorted(name for name in subfolders if not name.startswith("."))
        found.extend(
            Path(folder) / name
            for name in files
            if not name.startswith(".") and Path(name).suffix.lower() in AUDIO_SUFFIXES
        )
    return sorted(found)


def _year_of(tags: ID3) -> int | None:
    """The year of the track's year tag, or None when it holds none.

    TDRC holds a timestamp ("2001-05-03") whose year mutagen parses; a v2.3 tag's
    TYER, a bare year, arrives as TDRC because mutagen upgrades it on load. A year
    outside `EARLIEST_YEAR`..`LATEST_YEAR` (0 being an untagged year written as a
    number) reads as none.
    """
    frame = tags.get(_YEAR_FRAME)
    if frame is None:
        return None
    year: int | None = frame.text[0].year
    if year is None or not EARLIEST_YEAR <= year <= LATEST_YEAR:
        return None
    return year


def _open_track(track: Path) -> BinaryIO:
    """`track` opened for reading: the file the capped view reads the tag from."""
    return track.open("rb")


def _parse_capped(reader: capped_read.CappedReader) -> ID3:
    """The ID3 tag mutagen reads through `reader`, with each refusal as its own error.

    A refusal of the read or the inflate is `TagOverCap`, and so is any mutagen error
    or `ValueError` raised once the reader has refused, since the refusal is what the
    read failed on, whatever it surfaced as. Otherwise a `ValueError` is `TagMalformed`:
    mutagen raises one, unconverted, for an extended header claiming more than its tag.
    """
    try:
        return ID3(reader)
    except (capped_read.ReadOverCap, capped_zlib.InflateOverCap) as error:
        raise TagOverCap(str(error)) from error
    except (MutagenError, ValueError) as error:
        if reader.refused:
            raise TagOverCap("a refused read was followed by another error") from error
        # Several of mutagen's own errors (no ID3 header among them) subclass
        # ValueError, and those keep their type.
        if isinstance(error, MutagenError):
            raise
        raise TagMalformed(str(error)) from error


def _load_id3(track: Path) -> ID3:
    """`track`'s ID3 tag, read within `MAX_TAG_READ_BYTES` and the inflate budget.

    Each tag is its own read, so one track's frames never spend the next one's budget.
    Refusals are noted as well as caught: mutagen 1.48.1 lets both refusals through,
    but a release catching one would load the tag without the frame, or the part, it
    refused, and a partial tag must not pass for the whole one. `MAX_TAG_READ_BYTES`
    is read at call time.
    """
    BOUNDED_ZLIB.begin_read()
    refusals = BOUNDED_ZLIB.refusals
    with _open_track(track) as handle:
        reader = capped_read.CappedReader(handle, MAX_TAG_READ_BYTES, track.name)
        tags = _parse_capped(reader)
    if reader.refused:
        raise TagOverCap("a refused read was dropped from the tag")
    if BOUNDED_ZLIB.refusals != refusals:
        raise TagOverCap(
            "a compressed frame past the cap or the budget was dropped from the tag"
        )
    return tags


# fuzz-target
def read_track_tags(track: Path) -> TrackTags:
    """One track's highest POPM rating and year; empty for a file with no ID3 tag.

    Raises `MutagenError` for a tag that cannot be parsed (`TagOverCap` among them,
    for a tag over the read cap or compressed frames inflating past the cap or the
    budget, and `TagMalformed`) and `OSError` for a file that cannot be opened: the caller counts the track either way. Nothing else
    escapes, whatever the file holds, and a rating is always a POPM byte, 0 to 255;
    tests/test_tag_fuzz.py holds it to both over arbitrary bytes behind an ID3 header.
    """
    if track.suffix.lower() != _ID3_SUFFIX:
        return TrackTags(rating=None, year=None)
    try:
        tags = _load_id3(track)
    except ID3NoHeaderError:
        return TrackTags(rating=None, year=None)
    ratings = [frame.rating for frame in tags.getall("POPM")]
    if ratings:
        rating: int | None = max(ratings)
    else:
        rating = None
    return TrackTags(rating=rating, year=_year_of(tags))


def _read_or_report(
    track: Path,
    on_unreadable: UnreadableReporter,
    on_over_cap: UnreadableReporter | None = None,
) -> TrackTags:
    """`read_track_tags`, with a failure reported by file name and read as no tags.

    A tag over a size cap goes to `on_over_cap`, so its cause can be named; without
    one, to `on_unreadable` like any other failure.
    """
    try:
        return read_track_tags(track)
    except TagOverCap:
        (on_over_cap or on_unreadable)(track.name)
    except (MutagenError, OSError):
        on_unreadable(track.name)
    return TrackTags(rating=None, year=None)


def _favorites_count(tags: list[TrackTags], min_stars: int) -> int | None:
    """How many tracks reach `min_stars`, or None when no track carries a POPM frame."""
    rated = [entry.rating for entry in tags if entry.rating is not None]
    if not rated:
        return None
    return sum(1 for rating in rated if stars_for(rating) >= min_stars)


def folder_facts(
    album_dir: Path,
    *,
    min_stars: int,
    favorites: bool,
    on_unreadable: UnreadableReporter,
    on_over_cap: UnreadableReporter | None = None,
) -> AlbumFacts | None:
    """This album folder's facts, or None when it holds no track at all.

    Never raises: a folder that is missing, cannot be listed, or has a name no
    filesystem call accepts holds no tracks as far as a refresh is concerned, and a
    track whose tags cannot be read still counts, with no rating and no year, after
    `on_unreadable` hears its file name, or `on_over_cap` when one is given and the
    tag is over a size cap. `favorites=False` reads the tracks and the
    year and no count, so the record says null for the count and its signal.
    """
    try:
        tracks = audio_files(album_dir)
    except ValueError:
        return None
    if not tracks:
        return None
    tags = [_read_or_report(track, on_unreadable, on_over_cap) for track in tracks]
    year = next((entry.year for entry in tags if entry.year is not None), None)
    favorited = None
    if favorites:
        favorited = _favorites_count(tags, min_stars)
    signal = None
    if favorited is not None:
        signal = f"id3.popm>={min_stars}"
    return AlbumFacts(
        tracks=len(tracks), favorited=favorited, year=year, favorite_signal=signal
    )
