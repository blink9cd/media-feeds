# media-feeds

Feed adapters that turn public media feeds, and a local music library, into the small JSON
records a static site renders. Each adapter is run by hand when its record should refresh,
never as a build step and never at page-view time, so the page reads only a committed file,
and a failed refresh leaves the last good record in place.

| Tool | Reads | Produces |
| --- | --- | --- |
| `film_scan` | a Letterboxd member's public diary RSS | `src/data/films.json`: the most recently watched films, with title, year, rating, watched date, rewatch flag, poster, and director |
| `book_scan` | a Goodreads member's public shelf widget, a library CSV exported by hand, or the shelf RSS | `src/data/books.json`: the most recently read books, with title, author, year, rating, read date, and cover |
| `wikidata_director` | a TMDB movie id | the film's director(s) on stdout, resolved through Wikidata with no API key |
| `album_scan` | a local music library's `Artist/Album` folders and their ID3 tags, or optionally Apple Music's own "date added" | the most recently added albums, with artist, album, added date, year, track and favorites counts (and which favorites were counted), and cover art |

`cover_art` is the library `album_scan` resolves a cover-art URL with, through the iTunes
Search API. By default `album_scan` reads a local library folder: it ranks album folders
by when they appeared, and reads each album's track count, year, and favorites (ID3 `POPM`
star ratings) from its own files, so it needs no music app at all. Its optional Apple
Music sources (`--source music` and `auto`) come from the separate apple-music-toolbox
project; see "Using the Apple Music source" below.

`feed_read` holds what the adapters share: fetching, walking a feed's items, reading
vendor dates, and the atomic record write every adapter, `album_scan` included, writes its
file through. Small libraries sit beside the tools: `stdio_utf8` (UTF-8 on each tool's own
output streams), `help_formatter` (live defaults in `--help`), `bounded_fetch` (the HTTPS
fetch `feed_read` uses: which URLs may be requested, and a response body bounded in size
and in time), `library_walk` (the walk over a library's `Artist/Album` folders), and
`bounded_run` with `live_pid_guard` (a subprocess timeout that survives grandchildren,
and the cleanup its live tests use).

## Install

Needs Python 3.12 or newer. Install into a virtual
environment, because Homebrew and Debian Python refuse a
system-wide `pip install`:

```bash
python3 -m venv .venv
# shellcheck source=/dev/null # the activate script exists only once the line above has run
source .venv/bin/activate
pip install .
```

The commands below assume the venv is active, so `python`
is the venv's interpreter.

That installs the one runtime package, `mutagen`, which `album_scan` reads ID3 tags with,
at the version pinned under `[project].dependencies` in `pyproject.toml`; the feed adapters
use the standard library only. No tool is installed as a command: each is run as a script
from this folder, as below.

## Using the Apple Music source

`album_scan --source music` (and `auto`, which prefers Music.app and falls back to the
folder) asks macOS's Music app, through AppleScript, for its own "date added" and its
favorites. The AppleScript lives in a separate project, apple-music-toolbox, which is not
a package: the `apple-music` extra in `pyproject.toml` installs nothing and names this
requirement. To meet it, clone apple-music-toolbox beside this repository, as sibling
folders, which `album_scan` finds on its own, or point `album_scan --toolbox` or the
`APPLE_MUSIC_TOOLBOX` environment variable at a clone elsewhere. Without it,
`--source music` and `auto` exit `2` with a message saying so, and the default folder
source is unaffected.

One trap before choosing: Music.app keeps its ratings and favorites in its own library and
never writes them into the files, so on a library Music.app manages, the folder source
finds no rating and records the favorites count as `null`. Use `--source music` for that
library's favorites. `album_scan/album_scan.md` has the details.

## Refreshing a site's records

`film_scan` and `book_scan` write a file whose default `--output` is relative to the
working directory, so run them from the site's root, the directory holding `src/data/`.
`album_scan` prints its record to stdout unless `--output` names a file, so give it the
path the site reads:

```bash
python path/to/film_scan/film_scan.py --username YOUR_LETTERBOXD_USERNAME
python path/to/book_scan/book_scan.py --source widget --user-id YOUR_GOODREADS_USER_ID
python path/to/album_scan/album_scan.py --library ~/Music/Albums --output src/data/albums.json
```

`film_scan` takes each film's poster from TMDB, so a poster needs a TMDB API read token
in `FILM_SCAN_TMDB_TOKEN`. Without it every film is recorded with no poster; Letterboxd's
own poster images refuse scripted requests, so the feed cannot supply one
(`film_scan/film_scan.md`, "Why the poster comes from TMDB").

`book_scan` has four sources, chosen by `--source` or `BOOK_SCAN_SOURCE`:

- `widget` reads the shelf widget Goodreads offers for embedding in a blog: public, with no
  account or key, and fixed at the shelf's five most recently read books.
- `csv` reads the newest `.csv` in a folder (`--csv-dir` or `BOOK_SCAN_CSV_DIR`) holding
  library exports made by hand (My Books, then Import and export).
- `rss` reads the shelf RSS. It is still the default, but since 2026-10-28 Goodreads has
  answered 404 for that feed for every account tried, and `book_scan` then exits `3`, so
  choose `widget` or `csv`.
- `both` merges the RSS with the library export. When the record already in `--output`
  holds a later read date than anything the two sources gave, it is kept rather than
  replaced by an older one.

Each identity can come from the environment instead, as `FILM_SCAN_USERNAME` and
`BOOK_SCAN_USER_ID`, so it never needs to be typed or stored anywhere the records are.
Every CLI states its options, defaults, prerequisites, and exit codes in `--help`, and each
tool folder has a companion `<tool>.md` recording why it works the way it does.

## Running the tests

The tests also need `hypothesis`, pinned in the `test` extra, so install that extra:

```bash
pip install '.[test]'
```

Each folder has one test file at its top that runs its whole suite. This loop runs every
one, names each that fails, and exits 1 if any did, so a script or CI step running it
fails too; the parentheses keep that `exit` from closing your own shell:

```bash
(
  status=0
  for suite in */test_*.py; do
    python "$suite" || { echo "FAILED: $suite" >&2; status=1; }
  done
  exit "$status"
)
```

Several live-boundary test classes reach the real services. The Letterboxd and Goodreads
ones run only when `FILM_SCAN_USERNAME` or `BOOK_SCAN_USER_ID` is set, and use that
identity. The Wikidata one asks about a fixed, public film id. To run everything else
without touching the network, set:

```bash
export FILM_SCAN_OFFLINE=1 BOOK_SCAN_OFFLINE=1 WIKIDATA_DIRECTOR_OFFLINE=1
export ALBUM_SCAN_OFFLINE=1 COVER_ART_OFFLINE=1
```

`ALBUM_SCAN_OFFLINE` skips `album_scan`'s live Music.app classes, and `COVER_ART_OFFLINE`
skips `cover_art`'s live iTunes Search class. The tests that drive the Apple Music source
through the real apple-music-toolbox skip themselves when no clone is found, so a checkout
without one still runs every other test.

## Samples

`album_scan/samples/` holds a tiny invented library and the record written for it,
regenerated offline. `film_scan` and `book_scan` each ship a fixture feed in `fixtures/`
and, in `samples/`, the record the tool must write for it, regenerated fully offline.
`wikidata_director/samples/` holds captured live runs instead, since its output is a third
party's answer. The README in each `samples/` folder has the command.

## License

GNU Affero General Public License v3.0 or later; see `LICENSE`.
