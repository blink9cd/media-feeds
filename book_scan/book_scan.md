# book_scan.py

Read a member's Goodreads books, from the public shelf RSS feed, a hand-exported library
CSV, both, or Goodreads' shelf widget (`--source`; see "The library export: a second
source" and "The shelf widget: a third source" below), pick the `--count` most recently
read books (from the widget, its first `--count` books in the widget's own order, which
is the member's finishing order), and write `src/data/books.json`. Run by hand whenever
the record should be refreshed, never as a build step and never at page-view time.
This tool writes records and nothing else. No card rendering lives here: the page reads
`books.json` for itself.

## The refresh command

```bash
python path/to/book_scan/book_scan.py --user-id YOUR_GOODREADS_USER_ID
```

Or, with `BOOK_SCAN_USER_ID` exported in the shell, the same with no `--user-id` at
all. See "Where the user id comes from" below for why that form is the one to
prefer on the operator's machine.

Run from the site's root, the directory holding `src/data/`, because `--output`'s
default is relative to the working directory. This fetches
`https://www.goodreads.com/review/list_rss/<user_id>?shelf=read&per_page=50`, picks
the `--count` books (default `1`) with the newest `read` dates, and writes them to
`--output` as a JSON array, always an array, even at the default count of one (see
"`--count`: how many books to record" below). On exit `0` it also prints a one-line
confirmation naming how many books were written, the newest one's title, and its read
date when it has one (`book_scan: wrote <n> book(s), newest <title> (<read>)`; an
undated record's `read` is null and the parenthesized date is left off).

## The library export: a second source

Goodreads' shelf RSS has answered 404 for every account tried since 2026-10-28, so
`book_scan` also reads the library export Goodreads offers its members (My Books, Import
and export, Export Library): a CSV of the whole library, one row per book. It produces
the same `BookRecord`s the feed does, so the card, the covers, and the exit codes a
scheduled caller relies on stay as they are.

```bash
python path/to/book_scan/book_scan.py --source csv --csv-dir path/to/exports
```

### How the export is read

- **Where the file comes from: a set folder.** Each export is moved by hand into one
  folder, outside every checkout, named by `--csv-dir` or `BOOK_SCAN_CSV_DIR`. The most
  recently modified `.csv` in it is read (the suffix compared case-blind; the later
  name breaks a tie), so an old export can stay beside a new one. The tool never reads
  the Downloads folder: a set folder holds only what was put there on purpose.
- **Which source a run reads: a setting, `rss`, `csv`, or `both`**, from `--source`
  or `BOOK_SCAN_SOURCE`, default `rss`. Under `both`, the rule is "merge, never
  go backward" (below).
- **Covers for export books: looked up.** The export carries no cover, so each
  chosen book asks its public book page through the cover fallback below, by its Book
  Id: at most `--count` page requests a run.

### The settings

| Option | Variable | Default | Meaning |
| --- | --- | --- | --- |
| `--source {rss,csv,both,widget}` | `BOOK_SCAN_SOURCE` | `rss` | which source(s) a run reads |
| `--csv-dir PATH` | `BOOK_SCAN_CSV_DIR` | none | the export folder; its newest `.csv` is read |
| `--cover-lookup` / `--no-cover-lookup` | none | on | off means no book-page fetch at all |

Each option wins over its variable, and an empty variable counts as unset, as with
`--user-id`. An invalid `BOOK_SCAN_SOURCE` is a usage error (exit `2`) naming the
variable. A leading `~` in the folder is expanded, because a value a scheduled caller
sets in the environment never passes through a shell. `--user-id` is needed only when the feed or the
widget is read (`rss`, `both`, or `widget`), and a folder only when the export is (`csv`
or `both`); either one
missing is a usage error. `--shelf` names the export's `Exclusive Shelf` too, so the
default reads the `read` shelf from either source.

`--feed-file` keeps its meaning: the feed's body (under `widget`, the widget's) from a
file, and no page lookups, under any source. So `--source both --feed-file` reads the
file and the export and asks no page.

### Merge, never go backward (`--source both`)

1. Both sources are read, each once: no source waits on or retries the other, and
   retrying is the caller's job.
2. Books are de-duplicated by Book Id (the feed's `book_id`, the export's Book Id). When
   both carry a book, the feed's record is kept, since it may carry a feed cover. The
   merged list is ranked by read date, newest first, sliced to `--count`, and only then
   are coverless books looked up, as on the feed alone.
3. Before anything is written, the existing `--output` is read. When it is a JSON list
   whose newest `read` is later than the merged newest, nothing is written, the run
   prints `book_scan: kept the record; its newest book (<date>) is newer than anything
   the sources gave`, and exits `0` having asked no page. An absent or unreadable record
   guards nothing. The guard is for `both` only: a run naming one source writes what that
   source gives, so the record can still be replaced by hand.
4. One source failing does not fail the run while the other gives a book: its own failure
   line goes to stderr, then `book_scan: recording from the library export alone` (or
   `the shelf feed alone`). When neither gives a book, the feed's failure code wins (so a
   gone feed stays `3` and a timeout `2`), else the export's, else `1`.

The guard exists because the two sources go stale differently: the feed follows the
shelf on its own, while an export is only as new as the last one moved into the
folder. A merge alone would never write an older book first, but a record written while
the feed answered could hold books the next export predates; the guard keeps that record
rather than replacing it with an older one.

### The export's structure, as measured

Measured 2026-10-28 on a real export, whose values were never copied (every row of
`fixtures/library_export.csv` is invented, reproducing only the structure):

- UTF-8 with no byte order mark and LF line ends; a field holding a comma is quoted, so
  the file is read with the `csv` module. A byte order mark and CRLF are tolerated anyway.
- 23 named columns. The parser finds columns by name, never by position, and refuses a
  file lacking any of `Book Id`, `Title`, `Author`, `My Rating`, `Year Published`,
  `Original Publication Year`, `Date Read`, `Date Added`, and `Exclusive Shelf` as not a
  Goodreads library export (exit `2`).
- `Book Id` is all ASCII digits. A row whose id is anything else is still recorded, but
  asks no page and matches no feed book.
- `My Rating` is written both `4` and `4.0` in the same file. A whole star 1 to 5 in
  either form is the rating; `0` or `0.0` (unrated), a half star or anything else is
  `null`.
- `Year Published` and `Original Publication Year` are bare years, sometimes empty. The
  card's `year` matches the feed's `book_published`, which is the original year, so
  `year` is `Original Publication Year`, else `Year Published`, else `null`.
- `Date Read` and `Date Added` are four-digit year, month, and day, joined by
  slashes, and Date Read is set only on `read` shelf rows. The read date is Date
  Read, else Date Added, as on the feed; a row with neither is dropped.
- `ISBN` and `ISBN13` are spreadsheet formulas (`="..."`), and several columns are
  routinely empty (`Additional Authors`, `My Review`, `Spoiler`, `Private Notes`,
  `Bookshelves`). No record reads them.

A CSV is untrusted structured input, so `parse_library_export` carries a `# fuzz-target`
marker and a property test (`tests/test_book_scan_csv.py`): over arbitrary text it either
raises the documented `ValueError` or returns well-formed records. No message ever
prints a row: a refusal names a column, the file's name (never its folder) or the
decoder's reason, because the rows are the member's library.

## The shelf widget: a third source

The shelf widget is the embed Goodreads offers a member for a blog: a public,
credential-free endpoint separate from the shelf RSS, which still answered when the
RSS began answering 404. It needs no hand export, so a scheduled
run can follow the shelf on its own.

```bash
python path/to/book_scan/book_scan.py --source widget --user-id YOUR_GOODREADS_USER_ID
```

The endpoint is `https://www.goodreads.com/review/custom_widget/<user_id>`
(`WIDGET_URL`; `widget_url` percent-encodes the id, so it cannot add a path segment or a
query). It is fetched once, with `feed_read`'s honest User-Agent, its timeout, and its
body cap, and never retried: retrying is the caller's job. The parser is
`src/goodreads_widget.py`.

### What was measured (2026-10-28)

All with honest, credential-free requests, no browser imitation:

- The endpoint answers 200, a JavaScript body of about 9 KB, for the bare user id; the
  shelf-name suffix in the embed code is not needed.
- The body sets one single-quoted JavaScript string, `var widget_code = '...'`, to
  server-rendered HTML. The string escapes `'`, `"`, `/`, and newline with a backslash.
  `widget_html` extracts it and undoes the escapes.
- The HTML holds one `gr_custom_each_container_` div per book, each with
  `gr_custom_book_container_` (a cover `<img>`), `gr_custom_rating_` (a `<span>` whose
  `title` is Goodreads' rating word), and `gr_custom_title_` and `gr_custom_author_`
  (each a link whose text is the value). `widget_candidates` reads it with the stdlib's
  `html.parser`, never a regular expression over markup.
- **Every query parameter is ignored.** Book counts of 2, 10, 50, and 200, both sort
  keys, both orders, and another shelf all returned byte-identical bodies: the same five
  books from the read shelf. So the widget is fixed at five books, `--count` above five
  still writes five, and no parameter is sent (`--shelf`, `--window`, and `--key` do
  not apply).
- The cover URL carries the Goodreads Book Id as its file name, a 75px thumbnail. The
  thumbnail is not recorded; the Book Id is how the book page is found.
- `robots.txt`, read the same day, disallows `/review/list`, `/review/list_rss`,
  `/review/show`, and `/review/rate`; no line matches `/review/custom_widget` or
  `/book/show`.

A widget body is untrusted structured input, so `widget_candidates` carries a
`# fuzz-target` marker and a property test (`tests/test_book_scan_widget.py`): over
arbitrary bytes it never raises. Every book in `fixtures/widget_response.txt` is
invented.

### How a widget run behaves

- **Order: the widget's order is the member's finishing order, newest first.** It
  was confirmed against the five books the widget returned, and it ranks the card: a
  widget run takes the first `--count` books as given and never re-sorts.
- **Read date: null.** A widget book has no read date, so its record's `read` is
  `null`, and the site's books schema does not require one. The card never shows the
  date, so nothing visible changes. Two alternatives would be worse: stamping the date
  a run first saw a book puts a sighting date in a field that claims a read date, and
  borrowing the export's date keeps the export in the loop and still invents new books'
  dates.
- **Year: from the book page.** The widget gives none, so the year comes from the
  public book page the cover fallback already fetches (below), at no added request:
  `null` when the page states none or is not served, or when no page is asked
  (`--feed-file`, `--no-cover-lookup`).
- **`widget` stands alone.** It is never merged with another source and never part
  of a fallback chain. A merge ranks by read date, which widget books lack, and a chain
  falling through to a stale export could move the card backward, since the
  never-go-backward guard has no dates to compare once the widget has written the card.
  So a widget run never runs that guard, and a failed widget run writes nothing.

### Its exit codes

- `0`: wrote the widget's first `--count` books.
- `2`: the fetch failed (a timeout, a refusal other than 404 or 410), the
  `--feed-file` could not be read, or the body held no readable book. An empty parse is
  `2` and never `1`, because a scheduled caller may treat `1` as a quiet no-data day, and an
  empty parse of this undocumented format most likely means its markup changed. A
  member whose read shelf is genuinely empty uses another source.
- `3`: the widget answered 404 or 410: gone, and a retry cannot change that.

Either failure leaves the last good record in place.

### Switching a scheduled run

A scheduled run that sets this tool's environment can move onto the widget with one
setting, `BOOK_SCAN_SOURCE=widget`, beside `BOOK_SCAN_USER_ID`.

## Why the feed order cannot be trusted

**Measured 2026-08-31 on a real shelf:** the shelf RSS is ordered by
`user_date_added`, not `user_read_at`, and the two orders diverge just below the
top: the 4th and 5th most-recently-_read_ books sit at feed positions 5 and 6. Taking
`items[0]` (or any prefix of feed order) would silently announce a book read months
ago as "last read" the moment a newly-added, not-yet-read book (or a book added but
read out of order) lands near the top of the feed.

`newest_books` re-sorts explicitly by `read` after building every candidate
record; nothing here trusts the vendor's own ordering. This is also _why_ `--window`
defaults wider than the requested `--count` (see "Why `--window` defaults to 50, not
the count requested" below).

## `--count`: how many books to record

`--count` (default `1`) is the number of most-recently-read books `newest_books`
returns, newest first (under `--source widget`, the widget's first `--count` books, in
its order: "The shelf widget: a third source" below). Asking for more than the shelf
holds is not an error: a short shelf (the site's 1-or-2-book case) simply returns
everything it has, and the record file still comes out as a well-formed array. The
record file is a JSON array at every count, including the default of one, so a
reader never has to branch on whether it received an object or an array
(`feed_read.write_records`).

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
`book_large_image_url`. Measured 2026-08-31: `book_image_url` and
`book_small_image_url` are both the `_SX50_` thumbnail, 50 pixels wide and unusable
on a card, and `book_medium_image_url` is `_SX98_`, still too small. Only
`book_large_image_url` (`_SX318_`) is large enough to use. `_SX318_` scales by
_width_, so the height varies per book. A book with no `book_large_image_url` element
at all records `cover: None` rather than falling back to one of the unusable smaller
variants. A missing cover is a normal, correctly shaped record, and it degrades to a
text-only card, not an error; that degrading is the renderer's job, not this tool's.

A `book_large_image_url` that is not an https URL with a host is recorded the same
way. `_cover` runs the value through `feed_read.https_url_or_none`, which answers
`None` for `http:`, `file:`, `ftp:`, `data:`, a `javascript:` string, or a host-less
or scheme-relative URL: the string is a third party's and the record is published for
the page to load an image from, so it is refused where the record is decided, and the
card degrades to text exactly as for a book with no large variant. `feed_read.md`,
"Only an https URL with a host is fetched, or recorded", records why https-only is
enough without a host allowlist.

Goodreads' "nophoto" placeholder is refused too (`usable_cover`): a URL with a whole
`nophoto` path segment, the asset Goodreads names for a book it has no cover for, is
a picture of "no cover", so the book goes to the cover fallback below instead.

## The cover fallback: a coverless book's public page

A recorded book left with no usable cover by the rules above asks its public Goodreads
page, `https://www.goodreads.com/book/show/<book_id>`, and records the cover the page's
`<meta property="og:image">` names. The same fetch gives the publication year, which a
record without one takes (below). The id is the item's `book_id`, which the shelf
RSS carries and a Goodreads library export carries as Book Id. The lookup lives in
`src/goodreads_cover.py`; `book_scan.py` re-exports it. Measured 2026-10-28 with
this project's honest User-Agent: `robots.txt` allows `/book/show/` (it disallows
`/review/list_rss` and `/book/reviews/`), and the pages for public ids 1, 2, and 11 each
answered 200 with one `og:image`.

- **Only for a book with no usable cover**, and only for the books chosen: the lookup
  runs after the `--count` slice, so a run makes at most `--count` page requests,
  however long the shelf. A book with a usable feed cover makes none.
- **One attempt, no retry.** A refused request, a 403 or a challenge page, a 404, a
  timeout, or a page with no tag all mean no cover for that book: the card shows text,
  and the run's exit code does not change. A missing cover costs the card its picture,
  never the run. The page is never fetched with anything but the project's honest
  User-Agent, and a block is never worked around.
- **Expect throttling.** Measured 2026-10-28: after about ten requests in a few
  minutes, the page answered 200 with an empty body, which the lookup reads as no cover.
  A daily run asks at most `--count` pages, far below that, and the live test skips
  rather than fails when no page is served.
- **Parsed with the stdlib's `html.parser`**, never a regular expression over HTML, and
  matched on the exact `og:image` property (`og:image:width` is not the cover). The
  first such tag wins.
- **The same rule as a feed cover**: the value must pass `usable_cover` (https, a host,
  not the placeholder), and it then goes through `cover_at_width` like any other cover.
  The page's URL is the bare master, with no `_SX<n>_` token, so `cover_at_width`
  inserts one before the extension and `--cover-width` applies as it does to a feed
  cover. Until 2026-10-28 it was recorded as given, at full size: measured on the
  card's books, one master was 68,553 bytes where `_SX318_` is 14,329. Both cover hosts
  honor the inserted token; a file name holding any other
  modifier, such as the height-scaled `_SY475_`, is left as given.
- **No request with `--feed-file`**, which promises a run with no network call; nor
  from a bare `newest_books` call, whose default lookup (`no_page`) asks nothing.
- **The year comes from the same page**, and only fills a record whose `year` is
  null: a year the source gave is kept. It is read only inside the page's one
  `<p data-testid="publicationInfo">` element (measured 2026-10-28), never from the
  description, the reviews, or a script, whose prose says things like "First published
  in 1959 as a short story". There, a text node that begins
  `First published <Month> <day>, <year>` gives the year, the month a full English
  name and the month and day each optional; failing that, one that begins
  `Published ...`, which names the edition shown rather than the original. The first
  line of each kind wins. A book with a usable cover asks no page, so it gains no year.

**The page's cover host is `m.media-amazon.com`** (2026-10-28), not the
`i.gr-assets.com` the shelf RSS names, so a record can carry covers from either. A
consumer that accepts a cover only from a list of allowed hosts must list both, and a
change of the page's host is a change to that list too.

## Why `year` is a string, and why a missing one is `null`

`<book_published>` arrives in the shelf RSS as a bare year (`2018`), not a date, so
`_year` carries it through as the string it already is rather than parsing it to an int.
That matches `FilmRecord.year`'s existing `str | None` shape, so the two feed-derived
records in this project share one convention for the same concept instead of disagreeing
about it, and nothing downstream does arithmetic on a publication year; the card prints
it.

A book whose item carries no `book_published` element, or one present but blank, records
`year: null`, never `""`. The renderer omits a line whose value is null, so an
empty string would render a blank row rather than no row at all. `_year` is written as a
statement-form `if`/`else` for the same reason `_rating` is: coverage.py emits no branch
arcs for a conditional expression (or for a trailing `or None`), so the untested arm of
one would sit invisible behind a truthful 100%.

## `--cover-width`: measured, and deliberately left where it is

**The feed's own `_SX318_` has no oversize to give back**, and that is a measurement,
not an assumption.

`--cover-width` rewrites (or, on a bare master, inserts) the Goodreads `_SX<n>_`
sizing token, which scales the cover to `n` pixels wide. Measured 2026-09-01 against a real cover:

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
caller's choice rather than a constant buried in this tool, not because the default
should move.

### The ceiling: Goodreads honors a size request downward only

This is the trap this option must not walk into. The sample book's master image is
only 348x475. Measured, every one of `_SX400_`, `_SX475_`, `_SX500_`, `_SX636_`,
`_SX800_`, and the URL with no sizing token at all, returned the identical
348x475 image with an HTTP 200.

So a width above the master does not fail. It silently returns the master behind a URL
that names a larger number, which would ship a record that lies about its own size
while every offline test stayed green. `cover_at_width` cannot detect that: each
book's master differs, so there is no fixed ceiling to check against, and detecting it
would mean fetching the image, a network call this tool does not otherwise make, in
a tool that only parses RSS. The honest guard is therefore this section plus the
default staying below every observed master, and `cover_at_width`'s docstring says so
in as many words rather than implying a guarantee it cannot give.

### Goodreads serves no WebP either, and this was measured: do not re-probe

Measured 2026-09-01: `.webp`, `.avif`, and `.png` all return 403 on a cover URL whose
`.jpg` form works, with or without a sizing token, and no `Vary: Accept` header appears.
Like Letterboxd, this host serves pre-rendered files rather than running a transform, so
the format is fixed.

## The key that isn't required

**Verified 2026-08-31, against a real public shelf:** the per-shelf RSS
survived Goodreads' 2020 API shutdown and needs no key at all on a public profile:
a deliberately bogus key produced byte-identical output to no key at all. `--key`
is therefore optional (`feed_url`'s `key: str | None` parameter), and when it is
absent the `key` query parameter is omitted from the built URL entirely, not sent
as an empty string, since an empty `key=` would misrepresent the measured behavior. The
option exists purely as insurance for a profile made private in the future, at which
point a real key would become necessary. Pass it with `--key`, or set it via the
`BOOK_SCAN_KEY` environment variable (read only when `--key` is absent) so the key
never needs to appear in a shell history or a scheduled command line.

## Where the user id comes from

`--user-id` when given; otherwise the `BOOK_SCAN_USER_ID` environment variable;
otherwise a usage error (exit `2`, naming both). `--user-id` always wins over the
variable, and an empty variable counts as unset: a half-filled identities file that
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
`cover`), never a user id, a key, or a filesystem path. A bare user id or key
matches no keyword-based scanner, so keeping both out of every record, message, and
log line is a structural guarantee (`feed_url`'s docstring, and `main`'s
error-handling comment on why `exc` can never carry either), not something left to a
scanner to catch after the fact.

## The network calls

Under `--source widget` the one book-list fetch is the shelf widget's instead
(`widget_url`), with the same page fetches after it. With the feed read (`--source rss`
or `both`), this tool makes the shelf feed fetch
(`fetch(feed_url(...), feed_read.FEED_TIMEOUT_SECONDS)`) and, for each recorded book the
feed gives no usable cover, one public book-page fetch (`cover_fetch`, the cover
fallback above): at most `--count` of them, never retried, under any source (the export
itself is a local file). `--no-cover-lookup` turns the page fetches off. All are made on
the operator's machine at refresh time, never in a visitor's browser. Pass
`--feed-file` to skip every one of them and read a local feed body instead (see below);
no other network call exists anywhere in this tool. Each URL is built from an https
template (`FEED_TEMPLATE`, `WIDGET_URL`, `BOOK_PAGE_URL`, the last only for an all-digit
id), and `bounded_fetch.build_request`, inside `fetch_url`, would refuse to build a
request for any other scheme. `main` takes the two fetches as separate seams, and every
unit test replaces both.

## Exit codes

- `0`: wrote the record(s): between one and `--count` books, whichever is fewer
  than the source(s) actually hold a dateable book for. Also `0` when `--source both`
  kept a record newer than either source ("Merge, never go backward" above).
- `1`: never under `--source widget` (below). Otherwise, the shelf holds zero
  dateable books: every item lacked a usable
  `user_read_at` _and_ `user_date_added` (every export row on the shelf a usable Date
  Read and Date Added), or the shelf was genuinely empty. No file
  is written. Finding fewer than `--count` but at least one is success (exit `0`),
  not this code: a short shelf is the site's 1-or-2-book case, not an error.
- `2`: the feed could not be read: a network failure fetching it, an unreadable
  `--feed-file`, or a feed that is not parseable XML (including one carrying a
  `<!DOCTYPE`, which `feed_read.feed_items`'s entity-expansion guard refuses
  outright). For the export: a missing folder, a folder with no `.csv`, an unreadable or
  non-UTF-8 file, or one that is not a Goodreads export
  (`book_scan: could not read the library export: ...`). For the widget: a failed
  fetch, an unreadable `--feed-file`, or a body holding no readable book, which is `2`
  and never `1` because an empty parse most likely means the markup changed
  (`book_scan: the shelf widget held no book ...`). Also `2` when the record
  could not be written: `--output` names a
  directory, or a path that cannot be created or renamed onto. A failure at either
  stage leaves any existing `--output` file untouched, so a failed refresh keeps the
  last good record: `feed_read.write_records` is either never reached or fails
  before its atomic rename, removing its own temp file. Every failure line goes to
  stderr; stdout carries only the success summary. A `--count`,
  `--cover-width`, or `--window` below one is a usage error, also exit `2`, refused
  by the parser before any fetch, as are an invalid `BOOK_SCAN_SOURCE` and a missing
  `--user-id` or `--csv-dir` for the source chosen.
- `3`: the server answered that the feed (or, under `widget`, the shelf widget) is
  gone: HTTP 404 (Not Found) or 410
  (Gone), `feed_read.feed_gone`. Split from `2` because a retry cannot change that
  answer, so a scheduled caller can count it as a failed source without retrying.
  Every other HTTP refusal (a 403, a 429, a 5xx) stays `2`. Like `2`, it leaves the
  last good record untouched.

Under `--source both` one source failing is no failure while the other gives a book;
when neither does, the feed's code wins, then the export's (see "Merge, never go
backward" above).

## `--feed-file`: reading a local feed instead of fetching one

```bash
python path/to/book_scan/book_scan.py --user-id YOUR_GOODREADS_USER_ID \
  --feed-file path/to/shelf.xml
```

Reads the shelf feed from a local file instead of fetching it over the network.
A user id is still required, from `--user-id` or from `BOOK_SCAN_USER_ID` (there is
no separate "offline mode" flag), but its value is never read on this path:
`feed_url` is only ever called in the fetch branch. This exists so the samples pair
(`samples/README.md`) is hand-runnable with no network, and so reproducing a record
never depends on the live feed being reachable. Treat it as a reproducibility
feature, not a debugging flag.

## Run order

1. Run the refresh command above, with the real Goodreads user id exported as
   `BOOK_SCAN_USER_ID` or passed as `--user-id`. For the export, first move a fresh
   export into the folder `--csv-dir` or `BOOK_SCAN_CSV_DIR` names.
2. On exit `0`, review the written `--output` records by eye (a bare user id or key
   matches no keyword-based scanner, so a secret scan cannot be the only check; see "No
   message ever names the user id or the key").
3. Commit the record file. There is no render/paste step: no card rendering lives
   here, and the page reads `books.json` directly.

On exit `1`, the shelf held no dateable book at all this refresh. There is nothing
to fix; no book means no card, not an error. On exit `2`, fix the reported problem
(network access, the `--feed-file` path, the feed's own shape, or the export folder
and its newest file) and re-run.
On exit `3`, the shelf feed's URL (or, under `--source widget`, the shelf widget's)
answered 404 or 410: check the account and its privacy setting, then whether the vendor
still serves the feed or the widget at all.

## Usage

```text
python book_scan.py [--user-id USER_ID] [--count COUNT] [--cover-width COVER_WIDTH]
                     [--shelf SHELF] [--window WINDOW] [--key KEY]
                     [--output OUTPUT] [--feed-file FEED_FILE]
                     [--source {rss,csv,both,widget}] [--csv-dir CSV_DIR]
                     [--cover-lookup | --no-cover-lookup]
```

A user id is required when the feed is read, from `--user-id` or `BOOK_SCAN_USER_ID`,
and a folder when the export is; run `--help` for the
current default of every optional argument (this doc does not restate them, so it
cannot drift from the parser):

| Option           | Required | Description                                                          |
| ---------------- | -------- | -------------------------------------------------------------------- |
| `--user-id`      | no*      | Goodreads user id; falls back to `BOOK_SCAN_USER_ID`.                |
| `--count`        | no       | How many books to record: the newest read, or the widget's first.    |
| `--cover-width`  | no       | Cover width to record, as the `_SX<n>_` token; downward only.        |
| `--shelf`        | no       | Which Goodreads shelf to read.                                       |
| `--window`       | no       | How many shelf items to request (the feed's `per_page`).             |
| `--key`          | no       | Goodreads API key; falls back to `BOOK_SCAN_KEY` when omitted.       |
| `--output`       | no       | Where to write the book records, relative to the working directory.  |
| `--feed-file`    | no       | Read the feed (or widget) from this local file, not the network.     |
| `--source`       | no       | `rss`, `csv`, `both`, or `widget`; falls back to `BOOK_SCAN_SOURCE`. |
| `--csv-dir`      | no*      | Folder of library exports; falls back to `BOOK_SCAN_CSV_DIR`.        |
| `--cover-lookup` | no       | Ask a coverless book's page; `--no-cover-lookup` turns it off.       |

\* Optional at the parser only: one of `--user-id` and `BOOK_SCAN_USER_ID` must be
set when the feed or the widget is read, and one of `--csv-dir` and `BOOK_SCAN_CSV_DIR`
when the export is; `main` refuses with a usage error when the one needed is missing.

## Worked example

`samples/README.md` regenerates `samples/expected_books.json` from the committed
`fixtures/shelf.xml` with `--feed-file`, which proves the fetch-to-record path with no
network call; `samples/expected_books_from_export.json` from `samples/library_export/`
with `--source csv --no-cover-lookup`; and `samples/expected_books_from_widget.json`
from `fixtures/widget_response.txt` with `--source widget --feed-file`; see that file
for the real, captured commands and output. `tests/test_book_scan_parse.py` covers the
parsing functions directly (`_cover`, `_rating`, `_year`, `_read_date`, `newest_books`);
`tests/test_book_scan_cli.py` covers `feed_url`, the CLI's exit codes, the atomic write,
the `--key`/ `BOOK_SCAN_KEY` resolution, and the samples pair's own drift guard;
`tests/test_book_scan_count.py` covers `--count`'s surface end to end (what it accepts,
what it refuses, and how many records it writes); `tests/test_book_scan_cover_lookup.py`
covers the cover fallback on the fixture pages in `fixtures/` (the page parse and its
property test, the page URL rule, every lookup failure, and `main`'s wiring);
`tests/test_book_scan_book_page.py` the year read from the same page, a stdlib parser
that raises mid-page, and `fill_from_pages`; and `tests/test_book_scan_live_cover.py`
asks one real public book page. The suites written before the fallback call `main`
through `tests/_book_scan_offline.py`, which closes the page seam.
`tests/test_book_scan_csv.py` covers the export parser (with its property test), the
newest-export pick and the read; `tests/test_book_scan_sources.py` the source settings
and `--source csv` runs; and `tests/test_book_scan_merge.py` `--source both`'s merge,
its one-source-failing exits and the never-go-backward guard.
`tests/test_book_scan_widget.py` covers the widget parser (with its property test) and
`tests/test_book_scan_widget_source.py` `--source widget` runs, their exits, and the
widget sample's drift guard; `tests/test_book_scan_live_widget.py` reads the real widget
once. The source suites share `tests/_book_scan_sources_kit.py`, which clears every
`BOOK_SCAN_*` variable first.

## Running the test suite on a host with no network

Set `BOOK_SCAN_OFFLINE=1` to skip the three live-boundary test classes, the real
Goodreads shelf feed (`tests/test_book_scan_live_feed.py`), the real shelf widget
(`tests/test_book_scan_live_widget.py`), and one real public book page
(`tests/test_book_scan_live_cover.py`, which needs no identity), for a CI runner or any
host with no network access, where a live test could never pass regardless of whether
the code is correct. The shelf and widget classes also skip on their own,
independently of `BOOK_SCAN_OFFLINE`, whenever `BOOK_SCAN_USER_ID` is not set in the
environment: the user id is a personal identifier that must never be written into a
committed file (see "No message ever names the user id or the key" above), so each
live test reads it from the environment only, with no literal user id anywhere in
either file. Set `BOOK_SCAN_USER_ID` to a real Goodreads user id to actually exercise that
boundary; leave it unset (the default on any shared or sandboxed machine) to skip it.
The CLI reads the same variable as its `--user-id` fallback ("Where the user id comes
from" above), and a real value in the operator's shell cannot change a unit test's
outcome: the option always wins where one is passed, and the tests that exercise its
absence clear the variable first.

This is a convenience for running the rest of the suite quickly, not a complete run:
skipping the live class also means `feed_read.real_fetch`'s only caller in this tool's
own tests never runs. A complete run of this folder has neither skip in force, on a
machine with network access and a real `BOOK_SCAN_USER_ID`.
