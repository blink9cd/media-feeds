# library_walk

The albums of a music library, found the one way every tool here finds them: the
non-hidden `Artist/Album` folders under the library root. A library module with no CLI
of its own, shared rather than private to `album_scan`'s folder scan because more than
one tool visits a library album by album.

## What counts as an album

`album_dirs(library_root, on_skip)` returns every folder exactly two levels under the
root, an artist folder and then an album folder, sorted by artist and then by album:

- A folder whose name starts with a dot is passed over at either level, so a run folder
  such as `.lyricsmith`, the older `.album_lyrics`, or a system folder is never taken
  for an artist or an album.
- Nothing deeper is an album of its own. A multi-disc album keeps its `Disc 1` and
  `Disc 2` folders inside one album folder, and the walk names that album once.
- A file at either level is never an album.

`DEFAULT_LIBRARY` is the library a tool reads when it is given none, `~/Music/Albums`,
expanded by the caller at run time.

## Why one shared walk

More than one tool visits a library album by album. Were each to keep its own walk, two would
drift: one might start splitting multi-disc albums or entering hidden folders while the
other did not, and the albums one tool reports would stop matching the albums the other
visits. One module keeps a single answer.

Any-depth discovery, treating every folder that holds an MP3 as an album, was rejected
for the same multi-disc reason: it would split one album into one per disc.

## An artist folder that cannot be listed

An artist folder with no read permission is reported to `on_skip` by its NAME and passed
over, so one unreadable folder costs the walk one artist rather than the whole library.
The name alone, never the path: the error's own text carries the full path, and a tool
here may print what it is handed. The default `on_skip`, `ignore_skipped`, reports
nothing.

The root's own listing is not caught. A root that cannot be listed raises as
`Path.iterdir` raises, and the caller decides how to name it: a caller may name the
root by its final component only, for the reason above.

## Tests

`test_library_walk.py` builds every library in a temporary folder: the sort order, the
hidden folders at both levels, files at the wrong depth, a multi-disc album, and an
unreadable artist folder, with and without a reporter. The unreadable-folder tests skip
under root, which can list a folder of mode 000.
