# book_scan.py

Fetch a member's public Goodreads shelf RSS feed, pick the `--count` most recently
READ books, and write `src/data/books.json`. Run by hand whenever the record should
be refreshed -- **never** as a build step and never at page-view time. **This tool
writes records and nothing else** -- no card rendering lives here -- the page reads
`books.json` for itself.

## The refresh command

```bash
python path/to/book_scan/book_scan.py --user-id YOUR_GOODREADS_USER_ID
```

Or, with `BOOK_SCAN_USER_ID` exported in the shell, the same with no `--user-id` at
all -- see "Where the user id comes from" below for why that form is the one to
prefer on the operator's machine.

Run from the site's root, the directory holding `src/data/` -- `--output`'s default
is relative to the working directory. This fetches
`https://www.goodreads.com/review/list_rss/<user_id>?shelf=read&per_page=50`, picks
the `--count` books (default `1`) with the newest `read` dates, and writes them to
`--output` as a JSON array -- always an array, even at the default count of one (see
"`--count`: how many books to record" below). On exit `0` it also prints a one-line
confirmation naming how many books were written, the newest one's title, and its read
date (`book_scan: wrote <n> book(s), newest <title> (<read>)`).

## Why the feed order cannot be trusted

**Measured 2026-08-31 on a real shelf:** the shelf RSS is ordered by
`user_date_added`, not `user_read_at` -- and the two orders diverge just below the
top: the 4th and 5th most-recently-_read_ books sit at feed positions 5 and 6. Taking
`items[0]` (or any prefix of feed order) would silently announce a book read months
ago as "last read" the moment a newly-added, not-yet-read book (or a book added but
read out of order) lands near the top of the feed.

`newest_books` re-sorts explicitly by `read` after building every candidate
record; nothing here trusts the vendor's own ordering. This is also _why_ `--window`
defaults wider than the requested `--count` -- see "Why `--window` defaults to 50, not
the count requested" below.

## `--count`: how many books to record

`--count` (default `1`) is the number of most-recently-read books `newest_books`
returns, newest first. Asking for more than the shelf holds is not an error -- a
short shelf (the site's 1-or-2-book case) simply returns everything it has, and the
record file still comes out as a well-formed array. The record file is a JSON array
at **every** count, including the default of one, so a reader never has to branch on
whether it received an object or an array (`feed_read.write_records`).

**`--count` and `--window` are coupled.** The more books asked for, the further down
the feed the true top-N can sit, so keep `--window` comfortably larger than `--count`
(next section).

## Why `--window` defaults to 50, not the count requested

`--window` becomes the feed's own `per_page` parameter (`feed_url`'s `params["per_page"]`).
Because the feed is ordered by date _added_ and this tool re-sorts by date _read_, the
true most-recently-read books are not guaranteed to be near the front of the fetched
window (the measurement is under "Why the feed order cannot be trusted"). Requesting
only as many items as `--count` needs would risk fetching a window that does not even
contain the true top-N, silently returning a stale "last read" book instead of failing
loudly. `DEFAULT_WINDOW = 50` comfortably contained the true winners in every case
measured; raise it if a shelf shows the gap growing beyond that, or if `--count` is
raised enough to approach it.

## Why `book_image_url` is not the cover URL

Goodreads' shelf RSS gives four image-size elements per item:
`book_image_url`, `book_small_image_url`, `book_medium_image_url`, and
`book_large_image_url`. **Measured 2026-08-31:** `book_image_url` and
`book_small_image_url` are _both_ the `_SX50_` thumbnail -- 50 pixels wide, unusable
on a card -- and `book_medium_image_url` is `_SX98_`, still too small. Only
`book_large_image_url` (`_SX318_`) is large enough to use. `_SX318_` scales by
_width_, so the height varies per book. A book with no `book_large_image_url` element
at all records `cover: None` rather than falling back to one of the unusable smaller
variants -- a missing cover is a normal, correctly-shaped record, and it degrades to a
text-only card, not an error; that degrading is the renderer's job, not this tool's.

A `book_large_image_url` that is not an https URL with a host is recorded the same
way. `_cover` runs the value through `feed_read.https_url_or_none`, which answers
`None` for `http:`, `file:`, `ftp:`, `data:`, a `javascript:` string, or a host-less
or scheme-relative URL: the string is a third party's and the record is published for
the page to load an image from, so it is refused where the record is decided, and the
card degrades to text exactly as for a book with no large variant. `feed_read.md`,
"Only an https URL with a host is fetched, or recorded", records why https-only is
enough without a host allowlist.

## Why `year` is a string, and why a missing one is `null`

`<book_published>` arrives in the shelf RSS as a **bare year** (`2018`), not a date, so
`_year` carries it through as the string it already is rather than parsing it to an int.
That matches `FilmRecord.year`'s existing `str | None` shape, so the two feed-derived
records in this project share one convention for the same concept instead of disagreeing
about it -- and nothing downstream does arithmetic on a publication year; the card prints
it.

A book whose item carries no `book_published` element, or one present but blank, records
`year: null` -- **never** `""`. The renderer omits a line whose value is null, so an
empty string would render a blank row rather than no row at all. `_year` is written as a
statement-form `if`/`else` for the same reason `_rating` is: coverage.py emits no branch
arcs for a conditional expression (or for a trailing `or None`), so the untested arm of
one would sit invisible behind a truthful 100%.

## `--cover-width`: measured, and deliberately left where it is

**The feed's own `_SX318_` has no oversize to give back**, and that is a measurement,
not an assumption.

`--cover-width` rewrites the Goodreads `_SX<n>_` sizing token, which scales the cover
to `n` pixels wide. Measured 2026-09-01 against a real cover:

| Token                   | Delivered   | Bytes     | SSIM vs master |
| ----------------------- | ----------- | --------- | -------------- |
| `_SX150_`               | 150x205     | 3,858     | 0.9548         |
| `_SX250_`               | 250x341     | 7,366     | 0.9648         |
| **`_SX318_`** (default) | **318x434** | **9,930** | **0.9673**     |
| `_SX338_`               | 338x461     | 10,862    | 0.9661         |
| `_SX348_`               | 348x475     | 14,914    | 1.0000         |

The card's cover box maxes out at 168.7 CSS pixels (measured in-browser; `.shell` caps
at 704px), so a 2x display wants 338. Note what the table says about going there:
`_SX338_` scored _marginally worse_ than the `_SX318_` the feed already hands out,
while costing 932 more bytes. The default therefore stays at `_SX318_`, and refreshing
with it is a no-op on the committed record. The option exists so the size stays the
caller's choice rather than a constant buried in this tool -- not because the default
should move.

### The ceiling: Goodreads honors a size request DOWNWARD only

This is the trap this option must not walk into. The sample book's **master image is
only 348x475**. Measured, every one of `_SX400_`, `_SX475_`, `_SX500_`, `_SX636_`,
`_SX800_` -- **and the URL with no sizing token at all** -- returned the identical
348x475 image with an HTTP **200**.

So a width above the master does not fail. It silently returns the master behind a URL
that names a larger number, which would ship a record that lies about its own size
while every offline test stayed green. `cover_at_width` cannot detect that: each
book's master differs, so there is no fixed ceiling to check against, and detecting it
would mean fetching the image -- a network call this tool does not otherwise make, in
a tool that only parses RSS. The honest guard is therefore this section plus the
default staying below every observed master, and `cover_at_width`'s docstring says so
in as many words rather than implying a guarantee it cannot give.

### Goodreads serves no WebP either, and this was measured -- do not re-probe

Measured 2026-09-01: `.webp`, `.avif` and `.png` **all return 403** on a cover URL whose
`.jpg` form works, with or without a sizing token, and no `Vary: Accept` header appears.
Like Letterboxd, this host serves pre-rendered files rather than running a transform, so
the format is fixed.

## The key that isn't required

**Verified 2026-08-31, against a real public shelf:** the per-shelf RSS
survived Goodreads' 2020 API shutdown and needs **no key at all** on a public profile
-- a deliberately bogus key produced byte-identical output to no key at all. `--key`
is therefore optional (`feed_url`'s `key: str | None` parameter), and when it is
absent the `key` query parameter is omitted from the built URL **entirely**, not sent
as an empty string -- an empty `key=` would misrepresent the measured behavior. The
option exists purely as insurance for a profile made private in the future, at which
point a real key would become necessary. Pass it with `--key`, or set it via the
`BOOK_SCAN_KEY` environment variable (read only when `--key` is absent) so the key
never needs to appear in a shell history or a scheduled command line.

## Where the user id comes from

`--user-id` when given; otherwise the `BOOK_SCAN_USER_ID` environment variable;
otherwise a usage error (exit `2`, naming both). `--user-id` always wins over the
variable, and an empty variable counts as unset -- a half-filled identities file that
exports `BOOK_SCAN_USER_ID=` is refused rather than fetched as an empty id.

This is the key's own treatment (above) extended to the identity, for the same reason:
the user id is a personal identifier, and a value passed on the command line lands in
the shell's history and in any scheduled command that carries it. Exporting it once
from a file kept outside the repository keeps it out of both. The variable name is the
one `tests/test_book_scan_live_feed.py` already reads, so one exported environment
serves the refresh and the live suite alike. The check happens before the feed branch,
which is why a `--feed-file` run needs it too (below).

## No message ever names the user id or the key

`feed_url`'s only two runtime arguments that touch the URL text are the user id and
the key, and neither is echoed anywhere `main` prints: urllib's own exceptions
(`URLError`, `HTTPError`, `socket.timeout`) report a reason or an HTTP status, never
the request URL, and `feed_read.feed_items`'s `ValueError` messages describe the XML,
not the request that fetched it. `src/data/books.json` itself carries only
`BookRecord`'s feed-derived fields (`title`, `author`, `year`, `rating`, `read`,
`cover`) -- never a user id, a key, or a filesystem path. A bare user id or key
matches no keyword-based scanner, so keeping both out of every record, message, and
log line is a structural guarantee (`feed_url`'s docstring, and `main`'s
error-handling comment on why `exc` can never carry either), not something left to a
scanner to catch after the fact.

## The one network call

This tool makes **at most one** network call: the shelf feed fetch itself
(`fetch(feed_url(...), feed_read.FEED_TIMEOUT_SECONDS)`), made on the operator's
machine at refresh time -- never in a visitor's browser. Pass `--feed-file` to skip it
entirely and read a local feed body instead (see below); no other network call exists
anywhere in this tool. The URL it fetches is built from the https `FEED_TEMPLATE`, and
`bounded_fetch.build_request`, inside `fetch_url`, would refuse to build a request for
any other scheme.

## Exit codes

- `0` -- wrote the record(s): between one and `--count` books, whichever is fewer
  than the shelf actually holds a dateable book for.
- `1` -- the shelf holds **zero** dateable books: every item lacked a usable
  `user_read_at` _and_ `user_date_added`, or the shelf was genuinely empty. No file
  is written. Finding fewer than `--count` but at least one is success (exit `0`),
  not this code -- a short shelf is the site's 1-or-2-book case, not an error.
- `2` -- the feed could not be read: a network failure fetching it, an unreadable
  `--feed-file`, or a feed that is not parseable XML (including one carrying a
  `<!DOCTYPE`, which `feed_read.feed_items`'s entity-expansion guard refuses
  outright). Also `2` when the record could not be written: `--output` names a
  directory, or a path that cannot be created or renamed onto. A failure at either
  stage leaves any existing `--output` file untouched, so a failed refresh keeps the
  last good record: `feed_read.write_records` is either never reached or fails
  before its atomic rename, removing its own temp file. Every failure line goes to
  stderr; stdout carries only the success summary. A `--count`,
  `--cover-width` or `--window` below one is a usage error, also exit `2`, refused
  by the parser before any fetch.

## `--feed-file`: reading a local feed instead of fetching one

```bash
python path/to/book_scan/book_scan.py --user-id YOUR_GOODREADS_USER_ID \
  --feed-file path/to/shelf.xml
```

Reads the shelf feed from a local file instead of fetching it over the network.
A user id is still required, from `--user-id` or from `BOOK_SCAN_USER_ID` (there is
no separate "offline mode" flag), but its value is never read on this path --
`feed_url` is only ever called in the fetch branch. This exists so the samples pair
(`samples/README.md`) is hand-runnable with no network, and so reproducing a record
never depends on the live feed being reachable -- treat it as a reproducibility
feature, not a debugging flag.

## Run order

1. Run the refresh command above, with the real Goodreads user id exported as
   `BOOK_SCAN_USER_ID` or passed as `--user-id`.
2. On exit `0`, review the written `--output` records by eye (a bare user id or key
   matches no keyword-based scanner, so `pii_scan` cannot be the only check -- see "No
   message ever names the user id or the key").
3. Commit the record file. There is no render/paste step: no card rendering lives
   here -- the page reads `books.json` directly.

On exit `1`, the shelf held no dateable book at all this refresh -- there is nothing
to fix; no book means no card, not an error. On exit `2`, fix the reported problem
(network access, the `--feed-file` path, or the feed's own shape) and re-run.

## Usage

```text
python book_scan.py [--user-id USER_ID] [--count COUNT] [--cover-width COVER_WIDTH]
                     [--shelf SHELF] [--window WINDOW] [--key KEY]
                     [--output OUTPUT] [--feed-file FEED_FILE]
```

A user id is required, from `--user-id` or `BOOK_SCAN_USER_ID`; run `--help` for the
current default of every optional argument (this doc does not restate them, so it
cannot drift from the parser):

| Option          | Required | Description                                                         |
| --------------- | -------- | ------------------------------------------------------------------- |
| `--user-id`     | no*      | Goodreads user id; falls back to `BOOK_SCAN_USER_ID`.               |
| `--count`       | no       | How many most-recently-read books to record.                        |
| `--cover-width` | no       | Cover width to record, as the `_SX<n>_` token; downward only.       |
| `--shelf`       | no       | Which Goodreads shelf to read.                                      |
| `--window`      | no       | How many shelf items to request (the feed's `per_page`).            |
| `--key`         | no       | Goodreads API key; falls back to `BOOK_SCAN_KEY` when omitted.      |
| `--output`      | no       | Where to write the book records, relative to the working directory. |
| `--feed-file`   | no       | Read the shelf feed from this local file instead of fetching it.    |

\* Optional at the parser only: one of `--user-id` and `BOOK_SCAN_USER_ID` must be
set, and `main` refuses with a usage error when neither is.

## Worked example

`samples/README.md` regenerates `samples/expected_books.json` from the committed
`fixtures/shelf.xml` with `--feed-file`, which proves the fetch-to-record path with no
network call -- see that file for the real, captured command and output.
`tests/test_book_scan_parse.py` covers the parsing functions directly (`_cover`,
`_rating`, `_year`, `_read_date`, `newest_books`); `tests/test_book_scan_cli.py`
covers `feed_url`, the CLI's exit codes, the atomic write, the `--key`/
`BOOK_SCAN_KEY` resolution, and the samples pair's own drift guard;
`tests/test_book_scan_count.py` covers `--count`'s surface end to end (what it
accepts, what it refuses, and how many records it writes).

## Running the test suite on a host with no network

Set `BOOK_SCAN_OFFLINE=1` to skip the ONE live-boundary test class
`tests/test_book_scan_live_feed.py` carries (the real Goodreads shelf feed), for a CI
runner or any host with no network access, where a live test could never pass
regardless of whether the code is correct. That test class also skips on its own --
independently of `BOOK_SCAN_OFFLINE` -- whenever `BOOK_SCAN_USER_ID` is not set in the
environment: the user id is a personal identifier that must never be written into a
committed file (see "No message ever names the user id or the key" above), so the
live test reads it from the environment only, with no literal user id anywhere in that
file. Set `BOOK_SCAN_USER_ID` to a real Goodreads user id to actually exercise that
boundary; leave it unset (the default on any shared or sandboxed machine) to skip it.
The CLI reads the same variable as its `--user-id` fallback ("Where the user id comes
from" above), and a real value in the operator's shell cannot change a unit test's
outcome: the option always wins where one is passed, and the tests that exercise its
absence clear the variable first.

This is a convenience for running the rest of the suite quickly, **not** a way to
satisfy `python_gate` on this folder: skipping the live class also means
`feed_read.real_fetch`'s only caller in this tool's own tests never runs. The normal
way to gate this folder is with neither skip in force: on a machine with network
access and a real `BOOK_SCAN_USER_ID`.
