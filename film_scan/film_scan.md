# film_scan.py

Fetch a member's public Letterboxd diary RSS feed, pick the `--count` most recently
watched films, ask TMDB for each one's poster, and write `src/data/films.json`. Run by
hand or by the site's scheduled refresh whenever the record should be refreshed,
never as a build step and never at page-view time: the page reads only the
committed record, so a failed refresh keeps the last good record. This tool writes
records and nothing else. No card rendering lives here: the page reads
`films.json` for itself.

The parsing this doc describes below (`diary_entries`, `newest_films`, and everything
each of those calls) lives in the private sibling `src/film_parse.py`, and the TMDB
poster lookup (`tmdb_poster_lookup`, `poster_from_movie_json`, `poster_url_or_none`,
`tmdb_movie_url`) in `src/tmdb_poster.py`, not in `film_scan.py` itself.
`film_scan.py` re-exports every one of those names, so `film_scan.<name>` works for
each of them.

## The refresh command

```bash
python path/to/film_scan/film_scan.py --username YOUR_LETTERBOXD_USERNAME
```

Or, with `FILM_SCAN_USERNAME` exported in the shell, the same with no `--username`
at all. See "Where the username comes from" below for why that form is the one to
prefer on the operator's machine.

Run from the site's root, the directory holding `src/data/`, because `--output`'s
default is relative to the working directory. This fetches
`https://letterboxd.com/<username>/rss/`, picks the `--count` films (default `1`)
with the newest `watched` dates, and writes them to `--output` as a JSON array,
always an array, even at the default count of one (see "`--count`: how many films to
record" below). On exit `0` it also prints a one-line confirmation naming how many
films were written, the newest one's title, and its watched date
(`film_scan: wrote <n> film(s), newest <title> (<watched>)`).

## `--count`: how many films to record

`--count` (default `1`) is the number of most-recently-watched films `newest_films`
returns, newest first. Asking for more than the diary holds is not an error: a
short diary (the site's 1-or-2-film case) simply returns everything it has, and the
record file still comes out as a well-formed array. The record file is a JSON array
at every count, including the default of one, so a reader never has to branch on
whether it received an object or an array (`feed_read.write_records`).

`--count` and the director lookup's cost are coupled. The director is resolved
after the `--count` films are chosen, never inside `_record`; see "The network
boundaries" below for why that ordering is load-bearing rather than a nicety, and how
it keeps a short diary cheap by construction.

## Why half a feed is not films

A real Letterboxd diary feed mixes two kinds of `<item>`: diary entries (a film
watched and logged) and lists (a curated list of films, published to the same feed).
Measured on a real public feed: 50 of 100 items were lists, not films. Treating
"the first N items" or "an item with a `letterboxd:` namespace at all" as a film would
be wrong on real data from day one.

The discriminator `diary_entries` uses is structural, not positional: a genuine
diary entry carries a `letterboxd:filmTitle` element; a list item carries no film
elements at all. This is the one property that cannot be spoofed by an unusual title,
a reordered feed, or a list whose name happens to mention a real film
(`fixtures/diary.xml`'s "A Made-Up List of Films" item exists specifically to catch a
naive filter that trusted the title instead).

## Why the poster comes from TMDB

The diary feed does carry a poster: an `<img>` inside each entry's `description`,
served from Letterboxd's own image host. That host refuses scripted requests.
Measured 2026-10-26, when the site's first artifact publish fetched its covers: every
film poster answered HTTP 403 with Cloudflare's `error code: 1010`, a refusal based on
the client's browser signature. The same honest request from another network got the
same answer. Nothing but imitating a browser gets past it, and this project never
imitates a browser or changes its User-Agent to get past a refusal. A record carrying
that address would fail every deploy that fetches it.

Letterboxd's posters come from TMDB, whose API is meant for scripted use, and the
feed already names each film's TMDB id (`tmdb:movieId`, the key the director lookup
uses too). So for each recorded film, `main` makes one request:

```text
GET https://api.themoviedb.org/3/movie/{id}
Authorization: Bearer <the token in FILM_SCAN_TMDB_TOKEN>
```

and records `https://image.tmdb.org/t/p/w500` + the answer's `poster_path`. `w500` is
the smallest TMDB size at least as wide as the 400px poster the card used before.
The description's `<img>` is never read, not even as a fallback: an address the deploy
cannot fetch is no better than none.

Two checks keep a third party's strings out of the request and the record:

- **The id must be all ASCII digits** (`tmdb_movie_url`) before it is put in the URL;
  anything else, a slash or another script's digits included, is no poster and no
  request.
- **`poster_path` must be one plain file name** (`poster_url_or_none`): a `/`, then
  ASCII letters, digits, `_`, or `-`, then `.jpg`, `.jpeg`, or `.png` in either case.
  A second path segment, a `..`, a query, a whole URL, or a null is no poster. The
  match is ASCII as well as case-insensitive, because a Unicode case-insensitive
  `[A-Za-z]` also matches the Kelvin sign.

The answer's parse (`poster_from_movie_json`) never raises: a body that is not JSON,
not an object, or nested deeply enough to exhaust the parser's recursion limit is no
poster. Both checks and the parse carry `# fuzz-target` markers, and
`tests/test_film_scan_tmdb_lookup.py` holds their property tests.

### The token

The TMDB API read token (v4) comes from the `FILM_SCAN_TMDB_TOKEN` environment
variable, never from a command-line option, so it lands in no shell history and no
scheduled command line. The site's scheduled refresh passes it through from the feeds
env file, beside `FILM_SCAN_USERNAME`. It is sent only as the `Authorization: Bearer`
header on the movie-details requests, which `bounded_fetch` re-attaches on a redirect
only within the same origin. It appears in no message, log line, record, or exception
text this tool prints: a failed lookup's exception never leaves the lookup, and a
token that cannot be a header value is refused by `bounded_fetch.build_request` with a
message naming neither. `tests/test_film_scan_tmdb_poster.py` proves that with a
sentinel token, including a failure whose own text carries it.

An unset or empty variable is not an error. The run records every film with
`poster: null` and says once on stderr that the variable is unset, by its name.

## The no-poster degrade path

A film gets `poster: null` when there is no token, when its diary entry carries no
TMDB id, when TMDB has no poster for it or answers one the rule above refuses, or when
the request fails (an HTTP error such as a 401 for a bad token, a timeout, a body that
is not JSON). That film alone loses its poster: the run goes on, writes every record,
and exits as it would have otherwise. `FilmRecord.poster` is typed `str | None` to
carry it through.

After writing, the run reports on stderr how many films it left without a poster, as
a count with no titles:

```text
film_scan: 1 of 3 film(s) left without a poster
```

Nothing in this tool treats a missing poster as an error: a card with no poster is
a normal, correctly shaped record, and the page renders it as a text-only card, the
degrade path it already has for a cover that fails to load; degrading is the
renderer's job, not this tool's.

## The network boundaries, and why the director lookup is cheap

Every call happens on the operator's machine at refresh time, never in a visitor's
browser, and each has its own opt-out, so any combination is runnable offline:

| Boundary       | Call                                                                      | Skipped by                                     |
| -------------- | ------------------------------------------------------------------------- | ---------------------------------------------- |
| The diary feed | one fetch of `feed_url(--username)`                                       | `--feed-file`                                  |
| The poster     | one TMDB movie-details request per film recorded, with its TMDB id        | `--feed-file`, or `FILM_SCAN_TMDB_TOKEN` unset |
| The director   | about three Wikidata API calls per film recorded, via `wikidata_director` | `--no-director`                                |

Every request has a timeout on each socket operation and a bounded body
(`bounded_fetch`), and all three send the same User-Agent (`feed_read`'s): the TMDB
request goes through `feed_read.real_fetch_with_headers`, which adds the
`Authorization` header beside it.

**The director lookup runs once per film recorded, not once per candidate diary
entry.** `newest_films` ranks every candidate first, slices to the `--count`
newest, and resolves the director only for those. This is load-bearing rather than
incidental: a real diary feed carries around fifty entries, `directors_for` spends
three API calls each, so resolving inside `_record` (before the slice) would aim
roughly a hundred and fifty requests at Wikimedia every refresh regardless of
`--count`. Slicing first also keeps a short diary cheap: asking for `--count 3`
against a one-film diary costs one lookup, not three, because there is only one
record to slice to. The record never holds the TMDB id, so the item is carried
alongside its record through the ranking to keep the id reachable at the moment
each winner is known. The poster lookup follows the same rule for the same reason:
one TMDB request per recorded film, none for the candidates that did not make the cut.

The library default is inert: `newest_films(items, count)` with no `lookup` and no
`poster_lookup` resolves nothing and touches no network, which is what lets
`tests/test_live_feed.py` exercise the Letterboxd boundary without silently
acquiring a Wikidata or a TMDB one. Only `main` defaults to the real lookups, because
a bare command-line run is the case that wants them.

## Why the record carries no filesystem path and no username

`FilmRecord` holds only the film's own facts (`title`, `year`, `rating`, `watched`,
`rewatch`, `poster`, `director`: every one from the feed but `director` and `poster`,
which are resolved from the feed's TMDB id), never a path, never the `--username` that
built the fetch URL, and never the TMDB token. The record is written into the site's tree, so the site's PII scan governs it,
and it must stay inside that scan: a published record is where a leaked machine path
would land. A bare username is not a machine path or a credential,
so no keyword-based scanner would flag it either way. Keeping it out of every
record, message, and log line is a structural guarantee (`feed_url`'s docstring, and
`main`'s error-handling comment on why `exc` can never carry it), not something left
to the scanner to catch after the fact.

## Exit codes

- `0`: wrote the record(s): between one and `--count` films, whichever is fewer
  than the diary actually holds a diary entry for.
- `1`: the feed holds zero diary entries: every item was a list, or the feed
  was genuinely empty. No file is written. Finding fewer than `--count` but at
  least one is success (exit `0`), not this code: a short diary is the site's
  1-or-2-film case, not an error.
- `2`: the feed could not be read: a network failure fetching it, an unreadable
  `--feed-file`, or a feed that is not parseable XML (including one carrying a
  `<!DOCTYPE`, which `feed_read.feed_items`'s entity-expansion guard refuses
  outright). Also `2` when the record could not be written: `--output` names a
  directory, or a path that cannot be created or renamed onto. A failure at either
  stage leaves any pre-existing `--output` file untouched: a failed
  refresh keeps the last good record, since `feed_read.write_records` is either
  never reached or fails before its atomic rename, removing its own temp file on the
  way out. Every failure line goes to stderr; stdout carries only the success
  summary.
- `3`: the server answered that the feed is gone: HTTP 404 (Not Found) or 410
  (Gone), `feed_read.feed_gone`. Split from `2` because a retry cannot change that
  answer, so a scheduled caller can count it as a failed source without retrying.
  Every other HTTP refusal (a 403, a 429, a 5xx) stays `2`. Like `2`, it leaves the
  last good record untouched.

## `--feed-file`: reading a local feed instead of fetching one

```bash
python path/to/film_scan/film_scan.py --username YOUR_LETTERBOXD_USERNAME \
  --feed-file path/to/diary.xml
```

Reads the diary feed from a local file instead of fetching it over the network,
and makes no TMDB request either, so every film is recorded with `poster: null`
whether `FILM_SCAN_TMDB_TOKEN` is set or not.
A username is still required, from `--username` or from `FILM_SCAN_USERNAME` (there
is no separate "offline mode" flag), but its value is never read on this path:
`feed_url` is only ever called in the fetch branch. This exists so the samples pair
(`samples/README.md`) is hand-runnable with no network, and so reproducing a record
never depends on the live feed being reachable. Treat it as a reproducibility
feature, not a debugging flag.

## Where the username comes from

`--username` when given; otherwise the `FILM_SCAN_USERNAME` environment variable;
otherwise a usage error (exit `2`, naming both). `--username` always wins over the
variable, and an empty variable counts as unset: a half-filled identities file that
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
   `FILM_SCAN_USERNAME` or passed as `--username`, and the TMDB read token exported
   as `FILM_SCAN_TMDB_TOKEN`. Without the token the run still succeeds, with no
   posters.
2. On exit `0`, review the written `--output` records by eye (a bare username
   matches no keyword-based scanner, so a secret scan cannot be the only check; see
   "Why the record carries no filesystem path and no username").
3. Commit the record file. There is no render/paste step: no card rendering lives
   here, and the page reads `films.json` directly.

On exit `1`, the feed held no diary entries at all this refresh. There is nothing
to fix; no film means no card, not an error. On exit `2`, fix the reported problem
(network access, the `--feed-file` path, or the feed's own shape) and re-run.
On exit `3`, the diary feed's URL no longer answers: check the account and
its privacy setting, then whether the vendor still serves the feed at all.

## Usage

```text
python film_scan.py [--username USERNAME] [--count COUNT] [--output OUTPUT]
                    [--no-director] [--feed-file FEED_FILE]
```

A username is required, from `--username` or `FILM_SCAN_USERNAME`; run `--help` for
the current default of every optional argument (this doc does not restate them, so it
cannot drift from the parser). The TMDB token is an environment variable only, never
an option:

| Option          | Required | Description                                                           |
| --------------- | -------- | --------------------------------------------------------------------- |
| `--username`    | no*      | Letterboxd username; falls back to `FILM_SCAN_USERNAME`.              |
| `--count`       | no       | How many most-recently-watched films to record.                       |
| `--output`      | no       | Where to write the film record(s), relative to the working directory. |
| `--no-director` | no       | Skip the Wikidata director lookup and record a null director.         |
| `--feed-file`   | no       | Read the diary feed from this local file instead of fetching it.      |

\* Optional at the parser only: one of `--username` and `FILM_SCAN_USERNAME` must be
set, and `main` refuses with a usage error when neither is.

## Worked example

`samples/README.md` regenerates `samples/expected_films.json` from the committed
`fixtures/diary.xml` with `--feed-file --no-director`, which proves the
feed-to-record path with no network call at any boundary; see that file for the real,
captured command and output.
`tests/test_parse.py` covers the parsing functions directly (`diary_entries`,
`newest_films`, and its two lookups); `tests/test_film_scan_tmdb_lookup.py` covers the
TMDB request, the id and `poster_path` rules, and the answer's parse, with property
tests; `tests/test_film_scan_tmdb_poster.py` covers the poster as the CLI records and
reports it (the token, the degrade path, the offline path);
`tests/test_film_scan_cli.py` covers the CLI's exit codes, atomic write, and the
samples pair's own drift guard; `tests/test_film_scan_count.py` covers `--count`'s
surface end to end (what it accepts, what it refuses, and how many records it
writes).

## Running the test suite on a host with no network

Set `FILM_SCAN_OFFLINE=1` to skip the one live-boundary test class
`tests/test_live_feed.py` carries (the real Letterboxd diary feed), for a CI runner or
any host with no network access, where a live test could never pass regardless of
whether the code is correct. That test class also skips on its own, independently of
`FILM_SCAN_OFFLINE`, whenever `FILM_SCAN_USERNAME` is not set in the environment: the
username is a personal identifier that must never be written into a committed file
(see "Why the record carries no filesystem path and no username" above), so the live
test reads it from the environment only, with no literal username anywhere in that
file. Set `FILM_SCAN_USERNAME` to a real Letterboxd username to actually exercise that
boundary; leave it unset (the default on any shared or sandboxed machine) to skip it.
The CLI reads the same variable as its `--username` fallback ("Where the username
comes from" above), and a real value in the operator's shell cannot change a unit
test's outcome: the option always wins where one is passed, and the tests that
exercise its absence clear the variable first. `FILM_SCAN_TMDB_TOKEN` cannot change a
unit test's outcome either: every test that drives `main` pins it to a placeholder or
clears it, and injects a fake TMDB fetch (the aggregator `test_film_scan.py` fails any
`main` call left on the real one). No test reaches TMDB, so there is no TMDB live test
to skip.

This is a convenience for running the rest of the suite quickly, not a complete run:
skipping the live class also means `feed_read.real_fetch`'s only caller in this tool's
own tests never runs. A complete run of this folder has neither variable set to skip
it, on a machine with network access and a real `FILM_SCAN_USERNAME`.
