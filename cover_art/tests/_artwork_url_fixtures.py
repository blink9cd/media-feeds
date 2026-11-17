"""The fixtures cover_art's grouped suites are built on.

Shared across the grouped suites, by import or by inheritance alike. They live here
rather than inside one suite because a fixture reached ACROSS suites is what makes
the decomposition honest: the alternative is near-copies that drift, and a drifted
copy fails in whichever suite touches it first, pointing at the wrong subject.

Every helper is PUBLIC. A leading underscore would claim these are private to one
module, which stopped being true the moment a second suite imported them.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cover_art import AlbumRef  # noqa: E402 - position only; keep below the bootstrap above


REF = AlbumRef(artist="An Artist", album="A Record", added=dt.date(2026, 8, 30))


def fake_body(payload: object) -> bytes:
    """Encode a JSON-able payload the way an HTTP response body would arrive."""
    return json.dumps(payload).encode("utf-8")


def matching_result(**overrides: object) -> dict[str, object]:
    """A search result that verifies as REF's album.

    A result is only used when it is demonstrably the album that was asked for, so a
    fixture meaning "the store found it" has to carry the artist and title as well as
    an artwork URL. A bare `{"artworkUrl100": ...}` means "some other album"; accepting
    that payload is the defect this suite guards.
    """
    result: dict[str, object] = {
        "artistName": "An Artist",
        "collectionName": "A Record",
        "collectionId": 1,
        "releaseDate": "1994-01-01T00:00:00Z",
        "trackCount": 10,
        "artworkUrl100": "https://example.com/a/100x100bb.jpg",
    }
    result.update(overrides)
    return result
