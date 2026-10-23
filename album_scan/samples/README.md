# album_scan samples

Hand-verification pair for `album_scan`: a tiny fake library holding one album
folder, and the record the tool writes for it.
`library/The Invented Quartet/Placeholder Album/` is an empty folder (a `.gitkeep` keeps the otherwise-empty <!-- claim-check: ignore -->
tree under version control) rather than the real music library, which lives outside this
repository and is never committed (see `album_scan.py`'s module docstring on why nothing
this tool writes may contain a filesystem path). The marker above suppresses that path's
citations: its spaces split it into bogus path tokens (`library`, `The`, `Invented`,
and so on) that would otherwise fail resolution. The artist and album are invented, so
the sample is nobody's actual listening history and no real catalog entry stands behind
it.

## Regenerate and compare

From this project's root directory:

```bash
python album_scan/album_scan.py \
  --library album_scan/samples/library \
  --output album_scan/samples/expected_album.json \
  --no-artwork
```

`--no-artwork` keeps this sample off the network -- it proves the discovery and
record-shaping path, not the network lookup, which `cover_art/test_cover_art.py`
and `cover_art/tests/test_live_artwork.py` already cover.

Nothing else is needed to keep it off the operator's real library: the default
`--source folder` reads only `--library`, and takes each album's track count, year
and favorites from the files in its folder, so it never asks Music.app anything and
never imports the apple-music-toolbox. Do NOT add `--source music` or `auto`: either
would regenerate this file from the operator's REAL Music.app library instead --
exactly what this sample must never do.

Real run, captured verbatim:

```text
album_scan: counts unavailable for The Invented Quartet - Placeholder Album (no audio file in its folder, or the folder could not be read)
album_scan: wrote 1 album(s), newest The Invented Quartet - Placeholder Album (source: folder)
```

The first line is expected: the sample album folder holds no audio file, so the facts
the run asked for (favorites were not declined) could not be read, and the tool says
so rather than writing a record that only looks complete.

Exit code `0`. The command writes `expected_album.json`, always a JSON array -- even
at the default `--count` of one, which this sample uses -- so a reader never has to
branch on whether it received an object or an array:

```json
[
  {
    "artist": "The Invented Quartet",
    "album": "Placeholder Album",
    "added": "2026-10-23",
    "artwork": null,
    "tracks": null,
    "favorited": null,
    "year": null,
    "favorite_signal": null
  }
]
```

## What you should see

- `artist` is `"The Invented Quartet"` and `album` is `"Placeholder Album"` -- the two path segments
  under `library/`.
- `artwork` is `null` because `--no-artwork` was passed.
- `tracks`, `favorited`, `year` and `favorite_signal` are `null` because the sample
  album folder holds no audio file -- only the `.gitkeep` -- so there are no tracks to
  count and no tags to read; and `--no-artwork` rules out the store's release date,
  the year's only fallback. `null` here means "no facts could be read", which is
  deliberately distinct from `favorited: 0`, which would mean the tracks WERE rated and
  none reached the threshold.
- `added` is an ISO 8601 date (`YYYY-MM-DD`).

## A verification note

`added` is **not** stable across a re-run: it is the folder's filesystem birth date
(`_birth_time` in `src/folder_scan.py`), which is whenever `library/The Invented Quartet/Placeholder Album/` <!-- claim-check: ignore -->
was last checked out onto disk -- a fresh `git clone` or `git checkout` resets it to that
moment. (Same reason as the marker above: that path's spaces split it into bogus
path-citation tokens.) Comparing the regenerated file to the committed
`expected_album.json` byte-for-byte would therefore fail on every machine except the
one it was generated on for a reason that has nothing to do with the tool being wrong.

Verify on the keys that ARE stable instead: `artist` and `album` (fixed by the sample
folder names), `artwork` (fixed by `--no-artwork`), `tracks`, `favorited` and
`favorite_signal` (fixed by the empty album folder), and `year` (fixed by that folder
and `--no-artwork` together). `added` merely needs to be
present and to parse as an ISO 8601 date -- it is not compared to the committed value.

## Optional: a live run with real artwork

A live variant of the same command shows the cover-art lookup at work. Because the
sample's names are invented, run against `album_scan/samples/library` it finds no
verified match and records `artwork: null`; to see a genuine cover resolve, point
`--library` at a folder of your own, outside this repository, holding one
`<artist>/<album>/` folder named after an album the store carries:

```bash
python album_scan/album_scan.py \
  --library album_scan/samples/library
```

Dropping `--no-artwork` makes this variant behave differently in three ways that
matter: it makes **two real network calls per album recorded** -- both to the iTunes
Search API, via `artwork_url`, one querying `"The Invented Quartet Placeholder Album"` and one
querying `"The Invented Quartet"` alone, sent unconditionally and pooled before ranking rather
than stopping at whichever matches first (`cover_art.md`, "One lookup is two
searches", records the mis-match that forced the second) -- and, for an album the store
carries, resolves a genuine cover-art URL instead of `null`, so this sample library,
holding the one album, costs exactly two; it is **not part of any gate** -- no gate runs either
command in this file; and its output is **not committed** -- it prints to stdout, so
run it by hand to see the real lookup work, but do not paste its `artwork` URL into
`expected_album.json`.
