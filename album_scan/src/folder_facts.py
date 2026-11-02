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
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError

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

#: What `folder_facts` calls with the file name of each track whose tags it could not
#: read: a corrupt tag, or a file it may not open.
UnreadableReporter = Callable[[str], None]


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


# fuzz-target
def read_track_tags(track: Path) -> TrackTags:
    """One track's highest POPM rating and year; empty for a file with no ID3 tag.

    Raises `MutagenError` for a tag that cannot be parsed and `OSError` for a file
    that cannot be opened: the caller counts the track either way. Nothing else
    escapes, whatever the file holds, and a rating is always a POPM byte, 0 to 255;
    tests/test_tag_fuzz.py holds it to both over arbitrary bytes behind an ID3 header.
    """
    if track.suffix.lower() != _ID3_SUFFIX:
        return TrackTags(rating=None, year=None)
    try:
        tags = ID3(track)
    except ID3NoHeaderError:
        return TrackTags(rating=None, year=None)
    ratings = [frame.rating for frame in tags.getall("POPM")]
    if ratings:
        rating: int | None = max(ratings)
    else:
        rating = None
    return TrackTags(rating=rating, year=_year_of(tags))


def _read_or_report(track: Path, on_unreadable: UnreadableReporter) -> TrackTags:
    """`read_track_tags`, with a failure reported by file name and read as no tags."""
    try:
        return read_track_tags(track)
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
) -> AlbumFacts | None:
    """This album folder's facts, or None when it holds no track at all.

    Never raises: a folder that is missing, cannot be listed, or has a name no
    filesystem call accepts holds no tracks as far as a refresh is concerned, and a
    track whose tags cannot be read still counts, with no rating and no year, after
    `on_unreadable` hears its file name. `favorites=False` reads the tracks and the
    year and no count, so the record says null for the count and its signal.
    """
    try:
        tracks = audio_files(album_dir)
    except ValueError:
        return None
    if not tracks:
        return None
    tags = [_read_or_report(track, on_unreadable) for track in tracks]
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
