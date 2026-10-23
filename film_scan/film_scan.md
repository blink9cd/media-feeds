# film_scan.py

Fetch a member's public Letterboxd diary RSS feed, pick the `--count` most recently
WATCHED films, and write `src/data/films.json`. Run by hand whenever the record
should be refreshed -- **never** as a build step and never at page-view time: the
page reads only the committed record, so a failed refresh keeps the last good
record. **This tool writes records and nothing else** -- no card rendering lives
here -- the page reads `films.json` for itself.

The parsing this doc describes below (`diary_entries`, `poster_from_description`,
`poster_at_width`, `newest_films`, and everything each of those calls) lives in the
private sibling `src/film_parse.py`, not in `film_scan.py` itself. `film_scan.py`
re-exports every one of those names, so `film_scan.<name>` works for each of them.

## The refresh command

```bash
python path/to/film_scan/film_scan.py --username YOUR_LETTERBOXD_USERNAME
```

Or, with `FILM_SCAN_USERNAME` exported in the shell, the same with no `--username`
at all -- see "Where the username comes from" below for why that form is the one to
prefer on the operator's machine.

Run from the site's root, the directory holding `src/data/` -- `--output`'s default
is relative to the working directory. This fetches
`https://letterboxd.com/<username>/rss/`, picks the `--count` films (default `1`)
with the newest `watched` dates, and writes them to `--output` as a JSON array --
always an array, even at the default count of one (see "`--count`: how many films to
record" below). On exit `0` it also prints a one-line confirmation naming how many
films were written, the newest one's title, and its watched date
(`film_scan: wrote <n> film(s), newest <title> (<watched>)`).

## `--count`: how many films to record

`--count` (default `1`) is the number of most-recently-watched films `newest_films`
returns, newest first. Asking for more than the diary holds is not an error -- a
short diary (the site's 1-or-2-film case) simply returns everything it has, and the
record file still comes out as a well-formed array. The record file is a JSON array
at **every** count, including the default of one, so a reader never has to branch on
whether it received an object or an array (`feed_read.write_records`).

**`--count` and the director lookup's cost are coupled.** The director is resolved
AFTER the `--count` films are chosen, never inside `_record` -- see "The network
boundaries" below for why that ordering is load-bearing rather than a nicety, and how
it keeps a short diary cheap by construction.

## Why half a feed is not films

A real Letterboxd diary feed mixes two kinds of `<item>`: diary entries (a film
watched and logged) and lists (a curated list of films, published to the same feed).
**Measured on a real public feed: 50 of 100 items were lists, not films.** Treating
"the first N items" or "an item with a `letterboxd:` namespace at all" as a film would
be wrong on real data from day one.

The discriminator `diary_entries` uses is **structural**, not positional: a genuine
diary entry carries a `letterboxd:filmTitle` element; a list item carries no film
elements at all. This is the one property that cannot be spoofed by an unusual title,
a reordered feed, or a list whose name happens to mention a real film
(`fixtures/diary.xml`'s "A Made-Up List of Films" item exists specifically to catch a
naive filter that trusted the title instead).

## Why the poster comes from the description HTML

Letterboxd's RSS gives no dedicated poster-URL element. The poster is embedded as an
`<img>` inside the CDATA-wrapped `description` field, alongside a variable number of
review paragraphs. Two libraries were rejected before `HTMLParser`:

- **`xml.etree.ElementTree`** cannot parse the description at all -- it is an HTML
  **fragment** with no single root element (`<p>..</p> <p>..</p>`), which is not
  well-formed XML.
- **A regex over the raw bytes** would have to separately account for the CDATA
  wrapper, HTML entity variance, and attribute-quoting differences -- exactly the
  class of fragile, hard-to-audit parsing this project avoids elsewhere.

`HTMLParser` (`_FirstImageSrc` in `src/film_parse.py`) handles fragments by design, needs
no CDATA-specific handling (the CDATA wrapper is already stripped by the time
`ElementTree` hands over the `description` text), and is standard library -- keeping
`[project].dependencies` empty. It returns the **first** `<img>`'s `src`, verbatim:
measured over 150 diary items, the poster image is reliably the first element, but a
variable number of `<p>`/`<i>` review paragraphs may follow it (19 of 150 items), and
the `src` itself is returned by `poster_from_description` unmodified,
because poster paths vary (`/resized/film-poster/...` and `/resized/sm/upload/...`
both occur in the wild) -- nothing here may pattern-match the path. `poster_at_width`
(below) later rewrites the **dimension suffix** of that URL, and only that: it matches
`-0-<w>-0-<h>-crop`, which both path shapes carry, so the path rule above still holds.

Unmodified, but not unconditionally: only an https URL with a host is a poster.
`poster_from_description` runs the `src` through `feed_read.https_url_or_none` and
answers `None` for anything else -- `http:`, `file:`, `ftp:`, `data:`, a `javascript:`
string, a host-less or scheme-relative URL. The string is a third party's, the poster
probe fetches what is recorded, and the record is published for the page to load an
image from, so a `src` that fails the rule is treated exactly like no `<img>` at all
(next section). The refusal lives in the parser rather than at the probe so that a
live run and a `--feed-file` run write the same `null`, and so the probe never sees a
rejected URL in the first place. `feed_read.md`, "Only an https URL with a host is
fetched, or recorded", records why https-only is enough without a host allowlist, and
that `bounded_fetch.build_request`, inside `fetch_url`, refuses every other scheme at
the sink besides.

## `--poster-width`: asking for a poster the card can actually use

The feed hands out a **600x900** poster. MEASURED 2026-09-01 in a real browser, the
card renders its cover in a box that maxes out at **168.7 CSS pixels** wide -- the
`.shell` container caps at 704px, so no monitor makes it bigger -- which a 2x display
covers with 338 physical pixels. The 600 was therefore about three times the pixels
any visitor could see, and cost 56,020 bytes of a page whose entire text payload is
about 56 KB gzipped.

The default is now **400** (`400x600`, 29,840 bytes): the smallest pre-generated size
that still covers a 2x display, with headroom so a layout change does not immediately
under-serve the card. That is a 26,180-byte saving with no visible difference at the
rendered size -- compared at the display resolution, the poster's title wordmark is
indistinguishable from the 600x900 original.

### Letterboxd serves PRE-GENERATED sizes, so the option is a fixed set

This is why `--poster-width` takes `choices` rather than any integer. Measured by
fetching each candidate against one poster, Letterboxd served exactly these widths
and answered **403 Forbidden** -- not 404, and never a live resize -- for anything
else (`345` and `568` were both refused). MEASURED AGAIN 2026-09-04: the set is what
Letterboxd pre-generates for **some** posters, not a universal one. One real film's
400-wide poster answered 403 while its 300, 500 and 600 all answered 200 with
`image/jpeg`, and the other two recorded films' 400s served fine. So a width in this
table is a candidate, and the refresh verifies it (next section):

| Width x height        | Bytes      |
| --------------------- | ---------- |
| 70x105                | 5,035      |
| 125x187               | 12,904     |
| 150x225               | 17,004     |
| 200x300               | 27,235     |
| 230x345               | 34,015     |
| 250x375               | 14,625     |
| 300x450               | 19,547     |
| **400x600** (default) | **29,840** |
| 460x690               | 37,483     |
| 500x750               | 42,166     |
| 600x900               | 56,020     |
| 1000x1500             | 121,311    |

Two things that table is worth reading for:

- **The heights are Letterboxd's own pairings, not a computed 2:3 ratio.** 125x187
  would be 187.5 on the ratio. A calculated height would emit a URL that 403s.
- **Bytes are NOT monotonic in dimensions.** 230x345 is 34,015 bytes -- _larger_ than
  400x600's 29,840 -- because each pre-generated size is independently encoded at its
  own JPEG quality. Asking for a smaller poster does not reliably get a smaller file,
  which is why the choice is made from measurements rather than from arithmetic.

An unlisted width is refused by the argument parser, offline, before any fetch
happens. That placement is the point: a width that 403s would otherwise fail at
**page-view time**, in a visitor's browser, where no test of this tool could see it.

### The rewrite is verified at refresh time, and an offline run's is not

A listed width can still be missing for one particular poster (the 2026-09-04
measurement above), and that is exactly how this defect surfaced: the page drops a
poster whose image fails to load, so the film whose 400 was refused rendered as a
text-only card. So after rewriting a poster URL to `--poster-width`, `main` GETs the
rewritten URL once, through the same `fetch` seam the feed uses, and **falls back to
the feed's own URL** -- the 600x900 that is guaranteed to exist, since the feed just
handed it out -- when the rewrite is refused. One GET per recorded film at a by-hand
refresh is negligible next to the director lookup's three, and reusing the feed's seam
means every test's single injection point is unchanged. The probe catches the same
network and HTTP exceptions the feed fetch does, so a slow or refusing CDN costs the
resize, never the refresh. A poster with no size suffix is not rewritten and so is not
probed.

**A `--feed-file` run does NOT probe.** That path must stay fully offline -- it is how
`samples/` regenerates, with a fetch that fails the test if it is ever called -- so an
offline run keeps the **unverified** rewrite. A record refreshed offline is therefore
the one place a refused size could still reach the page; refresh from the live feed
before committing a record that will be served.

### If a pre-generated size is withdrawn after a refresh

The probe verifies a size at refresh time, not at page-view time. A size withdrawn
later 403s and the cover fails to load -- which is the degrade path the card already
has: the page removes a cover that fails to load rather than showing a broken image,
and the card reads completely from its own text. So a withdrawn size costs the
picture, never the card, and the next refresh repairs it.

### Letterboxd serves no WebP, and this was measured -- do not re-probe

Measured 2026-09-01: `.webp`, `.avif`, `.png` and even `.jpeg` **all return 403** on a
poster URL whose `.jpg` form works, and the response carries no `Vary: Accept`, so there
is no content negotiation either. The format is not a parameter here.

The reason is the same one behind the fixed size set above: these are **pre-rendered
files** in object storage, and the dimensions in the URL are part of a filename generated
in advance. There is no transform pipeline to ask for a different output.

## The no-poster degrade path

1 item in 150 (measured) had no `<img>` in its description at all. `poster_from_description`
returns `None` for that case rather than raising, and `FilmRecord.poster` is typed
`str | None` to carry that through. A first `<img>` whose `src` is not an https URL
with a host (previous section) is the same case, recorded the same way: `poster: null`.
Nothing in this tool treats a missing poster as an error -- a card with no poster is a
normal, correctly-shaped record, and the page renders it as a text-only card, the
degrade path it already has for a cover that fails to load ("If a pre-generated size
is withdrawn after a refresh", above); degrading is the renderer's job, not this tool's.

## The network boundaries, and why the director lookup is cheap

Every call happens on the operator's machine at refresh time -- never in a visitor's
browser -- and each has its own opt-out, so any combination is runnable offline:

| Boundary         | Call                                                                        | Skipped by      |
| ---------------- | --------------------------------------------------------------------------- | --------------- |
| The diary feed   | one fetch of `feed_url(--username)`                                         | `--feed-file`   |
| The poster probe | one GET of each RECORDED film's rewritten poster URL (see `--poster-width`) | `--feed-file`   |
| The director     | about three Wikidata API calls PER FILM RECORDED, via `wikidata_director`   | `--no-director` |

Every URL the poster probe GETs has passed the https rule twice: once in
`poster_from_description`, which is the only way a poster reaches a record, and again
in `bounded_fetch.build_request` inside `fetch_url`, which refuses to build a request
for anything else. The probe catches that refusal like any other failed GET and falls
back to the feed's own poster, which passed the same rule.

**The director lookup runs once per FILM RECORDED, not once per candidate diary
entry.** `newest_films` ranks every candidate first, slices to the `--count`
newest, and resolves the director only for those. This is load-bearing rather than
incidental: a real diary feed carries around fifty entries, `directors_for` spends
three API calls each, so resolving inside `_record` (before the slice) would aim
roughly a hundred and fifty requests at Wikimedia every refresh regardless of
`--count`. Slicing first also keeps a SHORT diary cheap: asking for `--count 3`
against a one-film diary costs one lookup, not three, because there is only one
record to slice to. The record never holds the TMDB id, so the item is carried
alongside its record through the ranking to keep the id reachable at the moment
each winner is known.

The library default is inert: `newest_films(items, count)` with no `lookup`
resolves nothing and touches no network, which is what lets
`tests/test_live_feed.py` exercise the Letterboxd boundary without silently
acquiring a Wikidata one. Only `main` defaults to the real lookup, because a bare
command-line run is exactly the case that wants it.

## Why the record carries no filesystem path and no username

`FilmRecord` holds only the film's own facts (`title`, `year`, `rating`, `watched`,
`rewatch`, `poster`, `director` -- every one from the feed but `director`, which is
resolved from the feed's TMDB id) -- never a path, and never the `--username` that built the fetch
URL. The record is written into the site's tree, so the site's PII scan governs it,
and it must stay inside that scan: a published record is where a leaked machine path
would land. A bare username is not a machine path or a credential,
so no keyword-based scanner would flag it either way -- keeping it out of every
record, message, and log line is a structural guarantee (`feed_url`'s docstring, and
`main`'s error-handling comment on why `exc` can never carry it), not something left
to the scanner to catch after the fact.

## Exit codes

- `0` -- wrote the record(s): between one and `--count` films, whichever is fewer
  than the diary actually holds a diary entry for.
- `1` -- the feed holds **zero** diary entries: every item was a list, or the feed
  was genuinely empty. No file is written. Finding fewer than `--count` but at
  least one is success (exit `0`), not this code -- a short diary is the site's
  1-or-2-film case, not an error.
- `2` -- the feed could not be read: a network failure fetching it, an unreadable
  `--feed-file`, or a feed that is not parseable XML (including one carrying a
  `<!DOCTYPE`, which `feed_read.feed_items`'s entity-expansion guard refuses
  outright). Also `2` when the record could not be written: `--output` names a
  directory, or a path that cannot be created or renamed onto. A failure at either
  stage leaves any pre-existing `--output` file completely untouched -- a failed
  refresh keeps the last good record, since `feed_read.write_records` is either
  never reached or fails before its atomic rename, removing its own temp file on the
  way out. Every failure line goes to stderr; stdout carries only the success
  summary.

## `--feed-file`: reading a local feed instead of fetching one

```bash
python path/to/film_scan/film_scan.py --username YOUR_LETTERBOXD_USERNAME \
  --feed-file path/to/diary.xml
```

Reads the diary feed from a local file instead of fetching it over the network.
A username is still required, from `--username` or from `FILM_SCAN_USERNAME` (there
is no separate "offline mode" flag), but its value is never read on this path --
`feed_url` is only ever called in the fetch branch. This exists so the samples pair
(`samples/README.md`) is hand-runnable with no network, and so reproducing a record
never depends on the live feed being reachable -- treat it as a reproducibility
feature, not a debugging flag.

## Where the username comes from

`--username` when given; otherwise the `FILM_SCAN_USERNAME` environment variable;
otherwise a usage error (exit `2`, naming both). `--username` always wins over the
variable, and an empty variable counts as unset -- a half-filled identities file that
exports `FILM_SCAN_USERNAME=` is refused rather than fetched as an empty path.

The fallback exists because the username is a personal identifier, and a value
passed on the command line lands in the shell's history and in any scheduled command
that carries it. Exporting it once from a file kept outside the repository keeps it
out of both. The variable name is the one `tests/test_live_feed.py` already reads, so
one exported environment serves the refresh and the live suite alike. The check
happens before the feed branch, which is why a `--feed-file` run needs it too
(above).

## Run order

1. Run the refresh command above, with the real Letterboxd username exported as
   `FILM_SCAN_USERNAME` or passed as `--username`.
2. On exit `0`, review the written `--output` records by eye (a bare username
   matches no keyword-based scanner, so `pii_scan` cannot be the only check -- see
   "Why the record carries no filesystem path and no username").
3. Commit the record file. There is no render/paste step: no card rendering lives
   here -- the page reads `films.json` directly.

On exit `1`, the feed held no diary entries at all this refresh -- there is nothing
to fix; no film means no card, not an error. On exit `2`, fix the reported problem
(network access, the `--feed-file` path, or the feed's own shape) and re-run.

## Usage

```text
python film_scan.py [--username USERNAME] [--count COUNT] [--output OUTPUT]
                    [--no-director] [--poster-width POSTER_WIDTH]
                    [--feed-file FEED_FILE]
```

A username is required, from `--username` or `FILM_SCAN_USERNAME`; run `--help` for
the current default of every optional argument, and for `--poster-width`'s current
choices (this doc does not restate them, so it cannot drift from the parser):

| Option           | Required | Description                                                           |
| ---------------- | -------- | --------------------------------------------------------------------- |
| `--username`     | no*      | Letterboxd username; falls back to `FILM_SCAN_USERNAME`.              |
| `--count`        | no       | How many most-recently-watched films to record.                       |
| `--output`       | no       | Where to write the film record(s), relative to the working directory. |
| `--poster-width` | no       | Poster width to record, verified per poster at refresh; see above.    |
| `--no-director`  | no       | Skip the Wikidata director lookup and record a null director.         |
| `--feed-file`    | no       | Read the diary feed from this local file instead of fetching it.      |

\* Optional at the parser only: one of `--username` and `FILM_SCAN_USERNAME` must be
set, and `main` refuses with a usage error when neither is.

## Worked example

`samples/README.md` regenerates `samples/expected_films.json` from the committed
`fixtures/diary.xml` with `--feed-file --no-director`, which proves the
feed-to-record path with no network call at any boundary -- see that file for the real, captured command and output.
`tests/test_parse.py` covers the parsing functions directly (`diary_entries`,
`poster_from_description`, `newest_films`); `tests/test_film_scan_cli.py` covers the
CLI's exit codes, atomic write, and the samples pair's own drift guard;
`tests/test_film_scan_count.py` covers `--count`'s surface end to end (what it
accepts, what it refuses, and how many records it writes).

## Running the test suite on a host with no network

Set `FILM_SCAN_OFFLINE=1` to skip the ONE live-boundary test class
`tests/test_live_feed.py` carries (the real Letterboxd diary feed), for a CI runner or
any host with no network access, where a live test could never pass regardless of
whether the code is correct. That test class also skips on its own -- independently of
`FILM_SCAN_OFFLINE` -- whenever `FILM_SCAN_USERNAME` is not set in the environment: the
username is a personal identifier that must never be written into a committed file
(see "Why the record carries no filesystem path and no username" above), so the live
test reads it from the environment only, with no literal username anywhere in that
file. Set `FILM_SCAN_USERNAME` to a real Letterboxd username to actually exercise that
boundary; leave it unset (the default on any shared or sandboxed machine) to skip it.
The CLI reads the same variable as its `--username` fallback ("Where the username
comes from" above), and a real value in the operator's shell cannot change a unit
test's outcome: the option always wins where one is passed, and the tests that
exercise its absence clear the variable first.

This is a convenience for running the rest of the suite quickly, **not** a way to
satisfy `python_gate` on this folder: skipping the live class also means
`feed_read.real_fetch`'s only caller in this tool's own tests never runs. The normal
way to gate this folder is WITHOUT either variable set to skip it, on a machine with
network access and a real `FILM_SCAN_USERNAME`.
