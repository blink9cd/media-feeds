# album_scan.py

Find the `--count` most recently added albums, resolve each one's cover art, and write
the JSON record(s) described in "Who consumes this record" below. Run by hand whenever
a fresh record is wanted, never as a build step and never at page-view time.
"Most recently added" is resolved by default from a local music library folder, with
each album's track count, year, and favorites read from its files' own ID3 tags; Apple
Music's own "date added" is the opt-in alternative (`--source music` or `auto`); see
"`--source`: choosing the date-added source" and "Why the folder scan's date is a
heuristic" below. Network cost scales with `--count`: resolving one album's cover art
costs two iTunes Search requests, so a `--count 3` refresh spends six, plus at most
two artist-catalog lookups for an album neither search finds (see "What a
refresh actually spends, and the request that is not this tool's" below for the full
split, including the local subprocess queries and the image fetch that belongs to
whatever displays the record). Every one of them runs on the machine that runs this
tool, at refresh time, never by whatever consumes the record later.

Most of the work lives in private `src/` modules. The folder scan this doc describes
below (`newest_albums`, `_birth_time`) is `src/folder_scan.py`; the folder source's
per-album facts (the ID3 reads) are `src/folder_facts.py`, with the cap on a tag's
read in `src/capped_read.py` and the bound on a compressed frame's inflate in
`src/capped_zlib.py`; finding, importing and
calling the apple-music-toolbox is `src/apple_source.py`, the one module that touches
it; the lookups that leave the record (which albums are newest, what the source
knows about each, and which cover art the store has) are `src/album_lookups.py`;
the record's shape (`AlbumRecord`, `album_record`) is `src/record_builder.py`; the
parser's constants and help text are `src/scan_options.py` (the parser and its
options stay in `album_scan.py`, beside this doc); and stdout rendering is `src/record_writer.py`, while
a file is written through the sibling `feed_read` library's `write_records`. What
`album_scan.<name>` resolves to is `album_scan.py`'s own `__all__`, listed there rather
than restated here; `_birth_time` stays private to `src/folder_scan.py`, and the
lookups and the writers are reached only through `main`.

## The refresh command

The runnable example below (not the abstract synopsis in Usage, further down) runs from
this project's own root directory. `--output`'s default is `-`
(stdout): a general tool prints its record(s), and a caller names its own destination:

```bash
python album_scan/album_scan.py --output album.json
```

By default (`--source folder`, `--count 1`) this scans `--library` (an `Artist/Album/`
tree, ranked by folder birth time) for the newest album, reads that album's track
count, year, and favorites from its files' ID3 tags, resolves its cover art over the
network unless `--no-artwork` is passed, then emits the record(s): to stdout when
`--output` is `-` (the default), or to the named file otherwise, always as a JSON
array even at the default `--count` of one (see "`--count`: how many albums to
record" below). The default run never starts `osascript` and never imports the
apple-music-toolbox; only `--source music` or `auto` does (see "Finding the toolbox"
below).

A named `--output` file is written through `feed_read.write_records`, the same
writer every other tool in this project uses: missing parent directories are
created, `~` expands to home, and the finished text is staged in a uniquely named
temporary file beside the target and swapped into place in one step, so a write that
fails part-way leaves any previous record untouched rather than truncated. The
file's mode is a fixed `0644` on a POSIX host (Windows has no mode bits).
Before the move to the shared writer a new record took the caller's umask and an
overwritten one kept whatever mode it already had; a record that had been narrowed
to `0600` by hand is now readable by its group and others again after the next
refresh. A record is published data, so a fixed, world-readable mode is the intended
one; `feed_read.md` records why the writer sets it explicitly.

Every human-readable message, the one-line confirmation below included, goes to
stderr, whatever happens and wherever the record(s) go:
`album_scan --output - > album.json` must produce parseable JSON, not JSON with a
status line wrapped around it, and the fallback notice (see below) would otherwise be
swallowed into a redirected record at the moment it most needs to be seen. On
exit `0` (and on an `auto` exit `3` whose fallback wrote a record) that confirmation names how many albums were written, the newest one, and
which source actually answered (`album_scan: wrote <n> album(s), newest <artist> -
<album> (source: music|folder)`), so a fallback under `auto` never goes unsignaled.
Under `--source music` or `auto` it also names the toolbox folder imported, by its
final component only (`(source: music, toolbox: apple_music_toolbox)`), so a wrong
pick shows on every run.
The tool renders nothing itself: rendering is a consumer's job (see "Who consumes
this record" below). Pass `--source music` or `--source auto` to take the date from
Music.app instead; see "`--source`: choosing the date-added source" below.

## What goes to stderr

Every human-readable line, and only these. None of them changes the exit code or
what reaches stdout; each is one line, prefixed `album_scan:`.

- **The confirmation**, whenever a record was written (exit `0`, or an `auto` exit `3`): `wrote <n> album(s), newest <artist> - <album>
  (source: music|folder)`, with `, toolbox: <folder>` inside the parentheses when a
  Music.app source opened the toolbox.
- **The fallback notice**, under `--source auto` whenever Music.app had nothing to
  say, printed as the fallback fires and so ahead of whatever the folder scan then
  finds (see "`--source`: choosing the date-added source" below). It reads
  `--source auto found no album (<cause>); falling back to the folder scan, ...`,
  naming why Music.app said nothing (see "Why Music.app answered nothing" below).
- **A degraded record**, once per album recorded, for each fact that was asked for
  and not obtained. `counts unavailable for <artist> - <album> (<reason>)` when the
  counts came back `null`: under the folder source always, since `--no-favorites`
  still reads its tracks and year, and under Music.app only without `--no-favorites`,
  which skips its one query; the reason names the source that
  was asked: for Music.app, the cause its query recorded (see "Why Music.app
  answered nothing" below), or `not in Music.app's library, or Music.app could not
  answer` when none was recorded; `no audio file in its folder, or the folder could
  not be read` for the folder. `favorites unknown for <artist> - <album> (no track
  carries an ID3 POPM rating)` when the folder source read the tracks and year but
  found no rating to count. And `cover art not resolved for <artist> - <album> (<cause>)` when the cover
  came back `null` without `--no-artwork`, naming which null it is: the store answered
  and has no such album, a store request failed (so it may still have it), or the
  store has the album but no usable image (`cover_art.md`, "Why a cover is null"). A
  `null` the operator declined with either flag is not degraded and draws no line.
  The record is written either way, since `null` is a valid value for each (see "Who
  consumes this record" below), so the line is the only sign that this refresh got
  less than it asked for, unless a caller asks for "The cover report" below.
- **An unreadable tag**, `could not read the tags of <file>; counted as an unrated
  track`, once per track file under the folder source whose ID3 tag is corrupt or
  which cannot be opened. A tag refused for its size draws the same line with its
  cause named, `could not read the tags of <file> (the tag is over the size cap);
  counted as an unrated track`: one declaring more than the read cap, or holding
  compressed frames that would inflate past their cap or budget (see "The folder
  source's facts" below), so a real tag too large to read is told apart from a
  damaged one. The track still counts toward `tracks`; the name is the file's own,
  never its path.
- **A skipped folder**, `skipped unreadable folder <name>`, once per artist folder
  under `--library` that the folder scan could not list (no read permission on it).
  The scan passes over that folder and goes on, so one unreadable folder costs the
  refresh one entry rather than the whole run; the name is the folder's own, never
  its path.
- **The failure lines** behind exit `1`, `2` and `3`; see "Exit codes" below.

Every name in those lines is escaped before it is printed. A track tag and a folder
name are both arbitrary text: the AppleScript concatenates tag text unescaped, and the
row parser refuses only NUL and the row terminator, so ESC, BEL, and the C1 block all
reach a name intact (measured 2026-09-22). A control character printed to a terminal is
not text but an instruction to it: it can rewrite the window title, erase the lines
above, or wrap a name in an OSC 8 hyperlink pointing anywhere. So every name these
lines carry goes through `notices.terminal_safe_text` first, which replaces each
character of Unicode category `Cc` with a visible `\xNN` escape. Escaped rather than
dropped, so an odd name reads as odd instead of as a shorter name; ordinary text is
untouched, accents, CJK, and emoji included. The record is deliberately left alone:
it is JSON a page reads through `textContent`, where a control character is inert, and
escaping it there would change the data in order to protect a terminal.

### Why Music.app answered nothing

The apple-music-toolbox never raises: a timeout, a failed `osascript` and an empty
lookback window all come back from it as the same empty list (and from its facts
query as the same `None`). So album_scan names the cause on its own side of that
contract. Every Music.app query goes through the `run` seam, and each lookup wraps
that seam in a `RecordingRunner` (`src/music_causes.py`) that records what came back
and hands the toolbox the very same result or exception, so the toolbox behaves
exactly as before. The last record decides the cause, and each cause names
Music.app or `osascript` itself, so it reads the same alone after `no album:` as
inside the fallback notice's parentheses:

| What the query did | Cause named |
| --- | --- |
| ran past its time bound | `Music.app timed out after <n>s` |
| `osascript` could not be started | `osascript could not be run` |
| exited nonzero with error `-1743` or `-1744` | `automation permission denied (osascript exit <code>: <line>)` |
| exited nonzero otherwise | `the Music.app query failed (osascript exit <code>: <line>)` |
| answered nothing (newest albums) | `Music.app has nothing added within the last <n> days` |
| answered with no usable album row | `Music.app's answer held no usable album row` |
| answered nothing (an album's facts) | `Music.app's answer was empty` |
| answered with no usable counts | `not in Music.app's library, or its answer held no usable counts` |

`<line>` is the first nonblank line `osascript` wrote to stderr, escaped like every
name (above) and then cut at `STDERR_ECHO_LIMIT` characters of the escaped text, so a
line of control characters cannot echo four times the limit; with no stderr,
the parentheses hold the exit code alone. `<n>` days is the toolbox's own lookback
window, read from it; a toolbox that does not name one gets `Music.app has nothing
added within its lookback window`. A usable album row is one the toolbox's ranking
keeps, and it drops a row with a date it cannot read or one dated in the future as
well as an incomplete one (`recently_added.md`, in the apple_music_toolbox project).
An album's facts cannot tell an album missing from the library from a reply that
does not parse without re-reading the toolbox's reply format, so that cause names
both. A lookup that recorded nothing (a toolbox that never calls `run`) keeps the
generic wording: `no album: Music.app reported no recently added album`, the auto
notice's list of every possibility, and the counts line's generic reason.

## `--count`: how many albums to record

`--count` (default `1`) is the number of most-recently-added albums `newest_albums`
returns, newest first. Asking for more than the library holds is not an error: a
small library simply returns everything it has, and the record file still comes out as
a well-formed array. The record file is a JSON array at every count, including the
default of one, so a reader never has to branch on whether it received an object or an
array. Both paths serialize the same way (`indent=2`, `allow_nan=False`, one
trailing newline): `render_records` in `src/record_writer.py` for stdout and
`feed_read.write_records` for a file, and `tests/test_record_write.py` pins the two
byte-for-byte equal.

**`--count` widens every source, including Music.app.** `newest_albums_from_music`
(the `--source music` / `auto` arm) collapses its AppleScript's per-track rows to
per-album ones before ranking, grouping by `(artist, album)` and keeping each group's
newest track time as that album's added date, so `--source music --count 3` (and
`auto` when Music.app answers at all) can genuinely return up to three albums, exactly
like `--source folder --count 3`. `auto` does not top up a short Music.app answer from
the folder scan: naming fewer than `count` albums is still a genuine Music.app answer,
not "nothing to say", and only an empty Music.app answer triggers the fallback. See
`recently_added.md`, in the apple_music_toolbox project, for why the per-track-to-per-album collapse is a bug fix, not
an optimization: without it, a freshly added twelve-track album fills every slot with
copies of itself the moment `--count` asks for more than one. One facts lookup and
one artwork lookup run per album recorded, not once for the whole refresh.

## `--source`: choosing the date-added source

```text
--source {folder,music,auto}
```

The source that names an album also answers that album's facts (its track count,
year, and favorites), so `--source` picks both at once.

- **`folder`** (the default): rank the `--library` folder tree by each album folder's
  birth time, and read each album's facts from its own files (see "The folder
  source's facts" below). It never invokes Music.app or `osascript` and never imports
  the apple-music-toolbox, so a default run needs neither; add `--no-artwork` to stay
  off the network too, which is the pairing `samples/README.md` uses.
  `tests/test_source_selection.py` proves it with a `run` fake that raises if invoked
  at all.
- **`music`**: use Music.app only, through the apple-music-toolbox (see "Finding the
  toolbox" below). `--library` is ignored entirely, neither read nor validated, so a
  missing or invalid `--library` never causes a failure here; only Music.app having
  nothing to report does (exit `1`, `no album: <cause>`, see "Why Music.app
  answered nothing" above; `no album: Music.app reported no recently added album`
  when no cause was recorded).
- **`auto`**: prefer Music.app's own "date added"
  (`recently_added.newest_albums_from_music`, via AppleScript); fall back to the
  folder scan of `--library` only if Music.app reports nothing, for any cause (not
  running, not scriptable, timed out, automation permission denied, or genuinely
  nothing added within its lookback window; see `recently_added.md`, in the
  apple_music_toolbox project).
  A fallback caused by a denied automation permission (the `automation permission
  denied` cause) still scans the folder and, when it finds an album, writes its record,
  but the run exits `3` whether or not the folder held an album: a lost grant is never
  reported as a success (see "Exit codes" below).
  The fallback is short-circuited: a successful Music.app answer means `--library` is
  never even read, so an invalid or missing `--library` is harmless under `auto` as
  long as Music.app answers. A fallback that does fire prints a notice at the
  moment it fires: before the scan runs, so before the success line, and equally
  before a "no album found" or a "library root is not a directory" that the fallback
  then runs into: the folder scan ranks by each folder's birth time, which is not a
  reliable "date added" (see "Why the folder scan's date is a heuristic" below), and
  a silent substitution would hide that. The notice names the cause the query
  recorded. When it falls back, the facts come
  from the folder too, so the record says which favorites it counted through
  `favorite_signal`.

`--no-favorites` is orthogonal to all three: under `music` and `auto`-answered-by-
Music.app it skips the one query that returns the counts and the year, so all three
are `null`; under `folder` the tracks and year are still read from the files, which
costs nothing beyond them, and only the favorites count (and its signal) is `null`.

## Finding the toolbox

`--source music` and `auto` need the apple-music-toolbox, a separate project whose
`recently_added` and `album_stats` tools hold the AppleScript. This tool imports it
only for those two sources, and looks for it in this order, taking the first that
holds both tool folders:

1. **`--toolbox DIR`**, a folder named on the command line.
2. **The `APPLE_MUSIC_TOOLBOX` environment variable**, naming the same folder.
3. **An upward search** from this file's own location: every ancestor folder, the
   nearest first and on up to the home folder (the last one searched; for a checkout
   outside the home folder, on up to the filesystem root), is checked for a child named
   `apple_music_toolbox` or `apple-music-toolbox`, both spellings at each level
   before the next one up. The second spelling is a clone of apple-music-toolbox
   beside a clone of this one, and the first a folder renamed to a Python identifier.

The search reaches above this checkout on purpose, since that is where a sibling clone
sits, so four things bound what it can pick up. It stops at the home folder, so
nothing above it is ever imported from. A candidate counts only when it holds both tool
modules, and when it, its two tool folders and their two modules are all owned by the
current user and writable by neither group nor others, since a folder another account
can write is one it can swap code into; one failing that is passed over for the next
candidate. album_scan appends the toolbox's two folders to `sys.path`, never
prepends them, so the standard library and this project's own modules keep every name
they already resolve. Once imported, though, the toolbox's own modules run their own
bootstraps, which may insert their own folders ahead of everything. And nothing is searched
for, or imported, unless the run asked for `--source music` or `auto`.

On Windows the user and mode check is skipped: stat reports no POSIX user id and no
group/other write bits there, so it could refuse nothing, and folder access is governed
by ACLs this tool does not read.

A folder named by the option or the variable that is not a toolbox, or that fails the
user and mode check, is refused rather than passed over, since the operator asked
for that one. When nothing is found, or
the toolbox does not import, the run exits `2` with a message naming `--toolbox`,
`APPLE_MUSIC_TOOLBOX`, and the clone, and names any folder it was given by its final
component only, never its path; an import failure names the module that failed, never
the error's own message, which can carry a path. A toolbox whose `album_facts` takes no `min_stars` is
refused the same way, with `the apple-music-toolbox predates --favorite-min-stars;
update it`, when the run sets `--favorite-min-stars` and asks for favorites; without
a threshold such a toolbox still serves. The search runs only when a Music.app source is
asked for; `--source folder` never looks, so an install without the toolbox loses
nothing it uses.

The toolbox is reached through `importlib` and two `Protocol` types in
`src/apple_source.py`, the one module that touches it. Its album reference becomes
this project's own `AlbumRef` (defined in the sibling `cover_art` folder) at that
boundary, so no other module depends on the toolbox's types. Where both projects
share one checkout, a sync test pins the two copies' fields against each other; it is
not shipped with this project, which has no toolbox to compare against.

## The two favorites signals

A favorites count means nothing without saying which favorites it counted, so the
record carries `favorite_signal` beside `favorited`. Each source has its own signal,
and each has a trap:

| Source | Default signal | With `--favorite-min-stars N` |
| --- | --- | --- |
| folder | `id3.popm>=1` | `id3.popm>=N` |
| music | `music.favorited` | `music.rating>=N` |

- **`music.favorited`** is Music.app's own Favorite flag (the heart), counted by the
  toolbox's `album_stats`. `music.rating>=N` counts instead the tracks whose
  user-set star rating reaches N stars; `album_stats.md`, in the apple_music_toolbox
  project, explains why only a user rating counts: Music.app can derive a track
  rating from an album rating, which the listener never gave. The traps: the two are
  independent in Music.app (a track can be favorited and unrated, or rated five
  stars and not favorited), so switching the threshold on changes what the count
  means as well as how strict it is; and a listener who favorites rather than rates
  reads `0` on nearly every album under the rating signal, which is why it is not
  the default.
- **`id3.popm>=N`** counts the tracks whose highest ID3 `POPM` (popularimeter)
  rating reaches N stars, on the scale the POPM writers share: a rating byte from 1,
  64, 128, 196, or 255 starts one to five stars, and 0 is unrated. A track with
  several POPM frames (one per player that rated it) counts by its highest. The
  default threshold of one star counts every rated track.
- **The folder trap: Music.app does not write POPM.** It keeps its ratings and
  favorites in its own library, never in the files, so a library Music.app manages
  carries no POPM frame at all, and the folder source records `favorited: null`,
  `favorite_signal: null` for it, with a "favorites unknown" line on stderr. That
  `null` is the honest answer, since no rating was there to count, and is deliberately
  not `0`, which would claim the tracks were rated and none reached the threshold.
  Use `--source music` for a Music.app library's favorites.

`--favorite-min-stars` takes one to five; unset, each source uses its default above.

## The folder source's facts

Under `--source folder`, and under `auto` once it falls back, each album's facts come
from the files in its folder (`src/folder_facts.py`, the one module that imports the
`mutagen` tag reader):

- **`tracks`**: the count of audio files under the album folder, at any depth so a
  multi-disc album's disc folders count as one album, with hidden names passed over.
- **`year`**: the first ID3 year tag found, in track order; a v2.3 `TYER` counts the
  same as v2.4's `TDRC`.
- **`favorited`**: as "The two favorites signals" above.

Only MP3 files carry ID3 tags here; any other audio file counts toward `tracks` and
contributes no rating and no year. A track whose tag is corrupt or which cannot be
opened still counts, with a stderr line naming the file; a property test feeds the
tag reader arbitrary bytes behind a well-formed ID3 header to hold it to that. An album folder with no
audio file at all has `null` facts, the same as a Music.app that could not answer.

**Compressed frames inflate within a cap per frame and a budget per tag.** mutagen
inflates a compressed ID3 frame (v2.3 or v2.4) whole with zlib and no output bound, so
a tag of a few kilobytes on disk can hold a frame that inflates to any size, and one
hostile file in the library could exhaust memory. `folder_facts` therefore puts
`src/capped_zlib.py`'s bounded `zlib` in mutagen's ID3 frame module when it is
imported: each frame's output is counted a chunk at a time and dropped before any of it
is kept, and a frame past `MAX_FRAME_INFLATE_BYTES` is refused without being held. That
cap is 1 MiB: far past the rating and year frames, the only ones read, and high enough
that a large compressed frame this reader never keeps (lyrics, a comment) does not cost
the track its rating.

The cap alone bounds one frame, not a tag: mutagen keeps every frame whose key differs,
and a private frame's key holds its data, so a tag of about 62 KiB holding 60 frames
each just under the cap loaded with about 120 MiB held. Each tag read is therefore also
charged against `MAX_TAG_INFLATE_BYTES`, 4 MiB (four frames at the cap, still far past
any real tag's compressed frames), and a tag whose frames together pass it is refused
the same way. Frames that fail to inflate are charged too, at what their count
inflated: mutagen drops a failed frame as junk and reads on, so uncharged, a tag of
them would cost a count and a whole inflate each, without end. Either refusal fails the
whole tag rather than dropping the one frame, so the track gets the unreadable-tag line
and counts unrated.

What remains is CPU and a bounded amount of memory, not nothing: a hostile tag can
still make one read inflate up to the budget plus a counting chunk, and count and
discard that much before it is refused. A tag kept within the budget holds about twice
what its frames inflate to, or more, because mutagen keeps each private frame's data
twice, once as the frame's bytes and once in its key: four frames just under the cap
held about 8 MiB once loaded.

**The tag is read within a cap of its own.** mutagen reads a tag's body from the file
whole, at the size its header declares, before any frame is inflated: a 28-bit
syncsafe integer, up to 256 MiB, and a v2.3 extended header's plain 32-bit size, up to
4 GiB. A file of a hundred bytes could make it allocate either. `folder_facts`
therefore hands mutagen `src/capped_read.py`'s view of the file rather than its path:
one read over `MAX_TAG_READ_BYTES`, 16 MiB, is refused before anything is read, and
every read asks the file for no more than it holds past the current position, so a
size the file only declares is never allocated. 16 MiB fits a real tag holding several
large embedded covers. The cap bounds each read, not the track: a v2.3 extended header
just under the cap and a body just under it are two reads, about 32 MiB together, and
parsing the body costs about two to three times the body again, up to about 48 MiB, so
one track's worst case is about 80 MiB, before the inflate budget's own share above.
The refusal fails the whole tag, as the inflate's do, and draws the unreadable-tag line
naming the size cap. The read cap and the inflate budget are separate: neither is
charged to the other.

An extended header claiming more than the tag it sits in is unreadable too: mutagen
then asks for the tag's remainder at a negative size and raises a `ValueError` of its
own, which the reader turns into an unreadable tag rather than letting it end the
refresh.

## Why the folder scan's date is a heuristic

Ranking by the library folder's filesystem birth time (macOS `st_birthtime`) can be
wrong on a real library. Against its three most-recently-added albums, Apple Music's
own "date added" reproduced the correct order exactly, while the folder scan missed
the middle one entirely: its folder's birth time read months earlier than Music's
"date added", and Music was right. Neither filesystem timestamp is trustworthy on that
library: its music folder lived inside iCloud Drive, and an item that is evicted and
later re-materialized there does not preserve its original birth time: the earlier
stamp was a re-materialization artifact, not the date the album was added. `st_mtime`
fares no better in the other direction: `_birth_time`'s own docstring documents a
folder re-tagged after import announcing the re-tag date as "added". Do not
simplify this back to a single filesystem stamp: both were tried, in the field,
on a real library, and both were wrong in different, non-overlapping cases.

The folder source is still the default, because it is the one that needs nothing but
the files: no Music.app, no toolbox, no automation permission, and it runs on any
host. On a library whose folders are created when an album arrives and never evicted,
birth time is the date added. Where that does not hold, `--source music` (or `auto`)
asks the supported, testable API for "date added": Apple Music's own AppleScript
dictionary (`~/Music/Library.musicdb` is a large, undocumented binary format, and
building on a guessed wire contract with no way to exercise it under test is ruled
out).

## What a refresh actually spends, and the request that is not this tool's

Two different kinds of network request exist around this record, and they run on
different machines at different times, a split that is easy to blur. Count them in
requests, not in "lookups": one artwork lookup is two searches, so a lookup count
quoted as a request count under-reports this tool's egress by half:

- **The lookup** (this tool, unless `--no-artwork` is passed): two calls to the
  iTunes Search API (`https://itunes.apple.com/search`, no key required) per album
  recorded, sending a `term` query parameter (`entity=album&limit=25`), once for the
  artist and album together, once for the artist alone. Both are sent unconditionally;
  the adapter pools their results before ranking rather than stopping at the first
  match, and `cover_art.md`'s "One lookup is two searches" records the mis-match that
  forced it. So the artwork cost is twice `--count`: a `--count 3` refresh spends
  six requests. An album neither search returns adds up to two more, to the
  artist-catalog endpoint (`https://itunes.apple.com/lookup`, sending an `id` that is
  the store's own artist id, never a name), and only then; `cover_art.md`'s "When
  the search misses: the artist's catalog" records why. They run on the operator's
  machine, at refresh time, never in a visitor's browser. Pass `--no-artwork` to skip them entirely and record a null
  artwork URL; a record with no artwork is still complete, and a consumer is expected
  to render it as text alone. The calls live in the sibling `cover_art/` adapter,
  which this tool imports; `--artwork-px` and `--artwork-format` choose the size and file
  type they ask for, and `cover_art.md` explains why those default to a 400px WebP.
- **The `osascript` queries** (this tool, and not network at all): none at all under
  the default `--source folder`, which reads the files' tags instead. Under `music`,
  and `auto` when Music.app answers, one for "date added" per run plus one for the
  counts per album recorded unless `--no-favorites` is passed, so a `--count 3`
  refresh runs three of those on top of the one. They are local subprocesses talking
  to Music.app on this machine, which is why `--no-artwork` alone makes a refresh
  fully offline whatever the source. Listed here because an operator counting what a refresh costs needs them in
  the same place, not because they leave the machine.
- **The cover image fetch** (not this tool, but whatever displays the record): the
  resolved artwork URL points straight at the vendor's CDN, and this tool has already
  exited by the time anything reads it. Where it is fetched from then depends on the
  consumer. A consumer that copies each cover into its own site at deploy fetches every
  cover once per deploy, so its visitors load the site's own copy and no visitor's browser
  reaches the vendor's CDN. A page that hot-links the URL instead has every visitor's browser fetch that image, on every page
  view, from the CDN, which scales with traffic rather than with `--count`; a
  `referrerpolicy` of `no-referrer` on that page's `<img>` then stops the CDN also
  learning which page the visitor came from. Neither shape touches the searches above,
  which never run in a visitor's browser at all.

## Why the record carries no filesystem path

`album_record` writes names, a date, a year, a remote `artwork` URL, two integer
counts, and the fixed-form name of the favorites signal (the full list is in "Who
consumes this record" below), never the library path
itself, and nothing rooted in the operator's home directory (`_birth_time` reads
`path.stat()` but only ever returns a timestamp, of which the record keeps the date;
the path that produced it is discarded).
The counts are integers, from Music.app or from the files' tags, and the signal is
built from a fixed prefix and a star count, so neither can carry a path; the folder
source reads each track's tags by path and keeps only the rating and the year. The
guarantee is
that no key holds anything path-derived, which is what `tests/test_record.py`
asserts over the whole serialized record rather than over a named subset that would
need extending each time a key is added.

The record's usual destination is a committed, published data file: a web site's
build reads it as static data, which is why it is written to a path the caller names
rather than to one this tool assumes. This tool is the one program in this project that
handles absolute paths at all, which makes it the realistic source of an
absolute-machine-path leak; a path-free record is one a PII scan can read with no
exemption.

## The cover report

The cover notice above is for a person. A scheduled caller that counts persistent
misses needs the same answer as
data, without matching that text, so `--cover-report PATH` also writes a JSON array
with one entry per album recorded, in the record's order:

```json
[{ "artist": "<artist>", "album": "<album>", "cover": "no-match" }]
```

`cover` is `found`, one of the three null causes `cover_art.md` names (`no-match`,
`lookup-failed`, `no-art`), `not-asked` under `--no-artwork`, or `unknown` for a null
that names no cause (`src/cover_report.py`). Like the record, it carries names only,
never a path, and goes through the same atomic writer. It is written only after the
record is, so exit `1` (no album) and exit `2` write none, and exit `3` writes one only
when an `auto` fallback wrote a record. A report that cannot be written is said on
stderr as `cannot write <name>`, and the exit stays `0` (or `3`): the record it
describes was written.

## Exit codes

- `0`: wrote the record(s). Finding fewer albums than `--count` asked for is still
  `0`; only finding none is the failure case below.
- `1`: no album found for `--source`: under `--source music`, Music.app reported
  nothing, for any cause but the one exit `3` names, and the line names which (see
  "Why Music.app answered nothing" above); under `--source folder`, or `auto` once it
  falls back, nothing under `--library` looked like an album.
- `2`: a path argument or an option cannot be used: `--library` is not a directory,
  or is one the tool has no permission to list (only when a folder scan is
  actually attempted; see "`--source`: choosing the date-added source" above);
  `--output` cannot be written (it names a directory, a
  location the tool may not create a file in, or a disk that fills part-way), and the
  message names the file, never its path, and any previous record at that path is left
  exactly as it was, since the record is staged beside it and swapped in whole; or
  a usage error argparse reports itself, such as `--count` or `--artwork-px` below
  one or not an integer, or `--favorite-min-stars` outside one to five; or, under
  `--source music` or `auto`, no apple-music-toolbox was found, it did not import,
  or it predates a `--favorite-min-stars` the run sets (see "Finding the toolbox"
  above).
- `3`: Music.app refused the Automation permission (osascript error `-1743`, or
  `-1744` for an event that would need consent an unattended run cannot ask for; the
  `automation permission denied` cause). Under `--source music` no record is written.
  Under `auto` the folder fallback still runs: when it finds an album its record is
  written as on exit `0`, and the run still exits `3`; when it finds none, nothing is
  written. It is neither `0` nor `1` because a lost grant is neither a success nor a
  day with nothing new, and a caller must be able to tell it from both. This tool does
  nothing more about it: it does not retry, and whether to keep a record written beside
  exit `3`, try again or tell someone is the caller's to decide.

## Who consumes this record

`album_record` (see "Why the record carries no filesystem path" above) writes these
keys. The list is not restated as a count anywhere, deliberately: a count in prose
goes stale the next time a key is added:

| Key | Meaning |
| --- | --- |
| `artist` | The album artist. |
| `album` | The album title. |
| `added` | When it was added, as an ISO 8601 date string. |
| `artwork` | A cover-art URL, or `null` when none resolved or `--no-artwork` was passed. |
| `tracks` | How many tracks the album has, or `null` (see below). |
| `favorited` | How many of them are favorited, or `null` (see below). |
| `year` | The album's year (the source's tag, else the store's release date), as a string, or `null` (see below). |
| `favorite_signal` | Which favorites `favorited` counted, or `null` when `favorited` is (see below). |

`year` comes from the source's own tag (Music.app's, or the first ID3 year tag in the
album folder), falling back to the store's release date for the album the artwork
lookup verified, and is `null` when neither has one. It is published as text,
though every source reports an integer: one field name, one type, so a consumer
reading this record beside feed-sourced ones gets the year as text from all of them,
rather than an int it has to coerce. The conversion happens at the record boundary
only: inside this tool the year stays an integer, which is the type the artwork
matcher's `year` parameter takes. An absent year stays `null` and never becomes the
string `"None"`. The tag is not a release date: an album ripped from a remaster
reports the reissue year, and the fallback cannot correct that: it only fills an
absence, because nothing can tell a wrong tag from a right one.

`tracks` and `favorited` come from the source that named the album: from `album_stats`
under Music.app (see `album_stats.md`, in the apple_music_toolbox project), or from
the album folder's files under the folder source (see "The folder source's facts"
above). Both are `null` when the source could not be asked at all (Music.app absent,
not running, not scriptable, or denied automation permission; an album folder with no
audio file), or when `--no-favorites` was passed under Music.app. `favorited` alone is
also `null` under the folder source when `--no-favorites` was passed or no track
carries a POPM rating. That is deliberately not the same as `favorited: 0`, which
means the count was taken and nothing on the album reached it: a normal state for a
newly added album, and one a consumer may well render differently from having no
signal at all. A consumer that collapses `null` and `0` loses that distinction; the
record keeps it.

`favorite_signal` says which favorites were counted: `music.favorited`,
`music.rating>=N`, or `id3.popm>=N`, with N one to five (see "The two favorites
signals" above). It is `null` exactly when `favorited` is, so a consumer may read
either to learn whether a count exists. It is there because the same number means
different things under different signals (two favorited tracks and two tracks of
four stars or more are different claims), and a consumer that labels the count
("2 favorites") can choose its wording by the signal rather than guess. A consumer
that ignores it renders as before.

How a consumer validates and renders the record is its own concern, not this tool's.
`tests/test_record_contract.py` pins this exact key set and these types from the
producer side. A consumer never imports this code, so nothing else here would catch a
renamed or dropped key before a consumer broke on it. Renaming or removing a key is
breaking. Adding one is not breaking for a consumer that reads named keys and ignores
the rest, but it still fails that test on purpose, so a change to what a consumer can
render costs one considered edit rather than sliding in unnoticed.

## Run order: from a library scan to a record

1. Run the refresh command above.
2. On exit `0`, review the written `--output` record(s). This tool's job ends there;
   see "Who consumes this record" above for what happens next.

On exit `1`, either Music.app had nothing to report (`--source music`), or nothing
under `--library` looked like an album (`--source folder`, or `auto` once it falls
back; fix the library path or accept that no record is written this refresh). On exit
`2`, fix the path or option the message names and re-run; a previous `--output` record
is still intact. On exit `3`, allow the Automation permission for Music.app in System
Settings > Privacy & Security > Automation, then re-run; under `auto` any record the
fallback wrote is the folder scan's, and is replaced by Music.app's on that re-run.

## Usage

```text
python album_scan.py [--source {folder,music,auto}] [--library LIBRARY]
                     [--toolbox TOOLBOX] [--count COUNT] [--output OUTPUT]
                     [--artwork-px ARTWORK_PX] [--artwork-format {webp,jpg}]
                     [--no-artwork] [--cover-report COVER_REPORT]
                     [--favorite-min-stars {1,2,3,4,5}] [--no-favorites]
```

Every option is optional; run `--help` for the current default of each (this doc does
not restate them, so it cannot drift from the parser):

| Option                 | Required | Description                                                                          |
| ---------------------- | -------- | ------------------------------------------------------------------------------------ |
| `--source`             | no       | Where the albums and their facts come from: `folder`, `music`, or `auto`.            |
| `--library`            | no       | Music library root, laid out as `Artist/Album/`. Ignored under `--source music`.     |
| `--toolbox`            | no       | The apple-music-toolbox folder, for `music` and `auto`; see "Finding the toolbox".   |
| `--count`              | no       | How many most-recently-added albums to record; see the section above.                |
| `--output`             | no       | Where the record(s) go: `-` is stdout. Missing parents are created; `~` is home.     |
| `--artwork-px`         | no       | Cover-art edge length in pixels, at least one; `cover_art.md` has the why.           |
| `--artwork-format`     | no       | Cover-art file format; webp is much smaller than jpg at the same size.               |
| `--no-artwork`         | no       | Skip the cover-art lookup; record a null artwork URL instead of resolving one.       |
| `--cover-report`       | no       | Also write each album's cover result as JSON; see "The cover report".                |
| `--favorite-min-stars` | no       | Count tracks rated at least this many stars; see "The two favorites signals".        |
| `--no-favorites`       | no       | Skip the favorites count; under Music.app the counts and year go with it.            |

## Worked example

`samples/README.md` regenerates `samples/expected_album.json` from a tiny fake library
with `--no-artwork` under the default folder source, which proves the discovery and
record-shaping path without the network lookup or Music.app; see that file for the
real, captured command and output (a one-element JSON array, at this sample's default
`--count` of one), and `cover_art/test_cover_art.py` /
`cover_art/tests/test_live_artwork.py` for what covers the artwork lookup,
and this tool's own `tests/test_source_selection.py` / `tests/test_live_music.py`
for what covers the Music.app source (the apple_music_toolbox project's
`test_recently_added.py` covers the query itself).

## Running the test suite on a host with no network or no Music.app

Set `ALBUM_SCAN_OFFLINE=1` to skip the one live-boundary test class each of
`tests/test_live_music.py` (Music.app/`osascript`) and `tests/test_live_favorites.py`
(the counts, over the same boundary) carries, and `COVER_ART_OFFLINE=1` for the one in
`cover_art/tests/test_live_artwork.py` (the iTunes Search API, in the sibling folder,
which has its own switch). Both are for a CI runner or any host missing one or both of those,
where a live test could never pass regardless of whether the code is correct.

A host with no apple-music-toolbox skips the tests that need it, with no variable:
those live classes, and the offline tests that drive `--source music` or `auto`
through the real toolbox with a faked `osascript`, carry a skip when "Finding the
toolbox" above finds none, as an export clone with no toolbox beside it does. The
favorites-signal and toolbox-lookup suites stand fakes in for the toolbox and run
everywhere.

A host that needs the variable and does not set it still finishes quickly. Both
Music.app classes decide reachability with the one probe `tests/_music_probe.py`
holds, bounded by its `REACHABILITY_TIMEOUT_SECONDS` (10s, against the ~0.2s the
bare count actually costs), and a probe that times out counts as unreachable and
skips, as an absent `osascript` does. That matters because a boundary can *stall*
rather than answer or fail (an automation-consent prompt with nobody there to answer it
is the usual reason), and a stall is what an unattended scheduled run, which starts with
a minimal environment and so never sets the variable, actually hits.
Measured against a boundary that accepts the call and never replies: the suite lands
at ~20s, where borrowing the 60s scanning bound and reaching the real seam from nine
CLI tests as well put it at 720s, past a 600s budget for one test run.

That per-call/per-unit distinction is the thing to keep in mind when adding a live
call here: each is bounded correctly on its own, and the budget that fails is the
sum, which no single timeout constant can see.

This is a convenience for running the rest of the suite quickly, not a way to reach
full coverage: each skipped live class takes the only caller of a real IO seam
with it, so that seam's line never executes and a branch-coverage run lands below 100%.
That is expected, not a defect: full coverage is measured without the variable set, on
a machine with both network access and Music.app.

This folder's own live dependency is Music.app alone: `_real_fetch` and the iTunes call
live in the sibling `cover_art/`, and `cover_art.md` covers that network seam
and its live test.
