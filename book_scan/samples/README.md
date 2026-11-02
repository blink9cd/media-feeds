# book_scan samples

Three committed inputs and the outputs they must reproduce, runnable by hand: the shelf
fixture at `../fixtures/shelf.xml` with the book record `book_scan` writes for it, a
library export (see "The library export sample" below), and a shelf widget body (see
"The widget sample" below).
`--feed-file` reads that fixture directly: no network call, and no real Goodreads
user id involved, since the fixture already stands in for a fetched feed body.

## Regenerate and compare

From this project's root:

```bash
python book_scan/book_scan.py \
  --user-id example-reader \
  --feed-file book_scan/fixtures/shelf.xml \
  --output book_scan/samples/expected_books.json
```

A user id is required, from `--user-id` or `BOOK_SCAN_USER_ID` (see `book_scan.md`),
but its value is never read on this path: `--feed-file` short-circuits the fetch
entirely, so no URL is ever built from it. `example-reader` is an obvious placeholder,
not a real Goodreads account (example values are obvious fakes), and the fixture's own
`<title>` and `<link>` elements separately name "Fictional Reader" as the shelf's
account holder.

Real run, captured verbatim:

```text
book_scan: wrote 1 book(s), newest The Later Read (2026-08-01)
```

Exit code `0`. The command writes `expected_books.json`, an array even though
`--count` was left at its default of `1`: the record file is always a JSON array,
at every count (`book_scan.md`, "`--count`: how many books to record"):

```json
[
  {
    "title": "The Later Read",
    "author": "Dominic Ashworth",
    "year": "2023",
    "rating": 5,
    "read": "2026-08-01",
    "cover": "https://images.example.invalid/books/later-read._SX318_.jpg"
  }
]
```

## What you should see

- The fixture holds six `<item>`s (`../fixtures/shelf.xml`), every one a book: the
  Goodreads shelf feed carries no non-book items the way Letterboxd's diary mixes in
  lists, so this tool needs no filter for them.
- The array holds one record at the default `--count 1`. Its keys, `title`, `author`,
  `year`, `rating`, `read`, and `cover`, are that record's `BookRecord` fields, with
  no `link`, no filesystem path, no user id.
- `year` is the book's publication year (`<book_published>`, `2023` here), not anything
  about when it was read, which is `read`. It is a string, not an int, because the feed
  carries a bare year and the sibling `FilmRecord.year` already uses that shape; nothing
  downstream does arithmetic on it. A book whose item carries no `book_published` (or a
  blank one) records `year: null`, never `""`: the card omits a line whose value is
  null, where an empty string would render a blank row.
- The record picked is the book with the newest `read` date among the six, which is
  "The Later Read" (`read: 2026-08-01`), not the first item in the feed
  (`"An Early Addition"`, added most recently but read six months earlier). This is
  the read-date re-sort (`book_scan.md`, "Why the feed order cannot be trusted") shown
  end to end through the CLI, beyond the parsing layer.
- `cover` carries the `_SX318_` large-image variant, not the `_SX50_` thumbnail
  `book_image_url` itself points at (`book_scan.md`, "Why `book_image_url` is not the
  cover URL").

## A verification note

Every field here is stable and fully reproducible: it comes entirely from the
committed fixture. Regenerating this file should produce byte-for-byte the same
output every time. `../tests/test_book_scan_cli.py`'s `SamplesPairTest` compares a
fresh run's parsed records against it, so the pair cannot silently drift from the tool.

## The library export sample

`library_export/` holds one invented Goodreads library export, a byte-for-byte copy of
`../fixtures/library_export.csv`, alone in its folder because `--csv-dir` reads the
newest `.csv` in the folder it names. `expected_books_from_export.json` is what a csv
run writes for it. `--no-cover-lookup` keeps the run off the network: the export
carries no cover, and a live run would ask each chosen book's public page.

From this project's root:

```bash
python book_scan/book_scan.py \
  --source csv \
  --csv-dir book_scan/samples/library_export \
  --no-cover-lookup \
  --count 3 \
  --output book_scan/samples/expected_books_from_export.json
```

No user id is needed: a csv run reads no feed. Real run, captured verbatim:

```text
book_scan: wrote 3 book(s), newest A Lantern for Wren (2026-09-20)
```

Exit code `0`. What you should see in `expected_books_from_export.json`:

- Three records, newest `read` first: "A Lantern for Wren" (2026-09-20), "The Later
  Read" (2026-08-01) and "Harbors, Bridges and Tolls" (2026-07-14). The `to-read` and
  `currently-reading` rows were added later but are not on the `read` shelf, so they are
  not here.
- "Harbors, Bridges and Tolls" keeps its comma: the field is quoted in the file.
- Its `rating` is `4` although the file writes `4.0`, and its `year` is `2022`, the Year
  Published, because its Original Publication Year is empty. "A Lantern for Wren" shows
  the original year (`2017`) winning over the edition's (`2019`).
- Every `cover` is `null`: the export has none, and the lookup was turned off.

`../tests/test_book_scan_sources.py` compares a fresh run against this file byte for
byte, and pins the sample export to the fixture, so neither can drift.

## The widget sample

`../fixtures/widget_response.txt` is an invented shelf widget body: the JavaScript
Goodreads' custom shelf widget answers, holding five invented books, none of them any
real member's; `--count 3` records the first three. `expected_books_from_widget.json` is
what a widget run writes for it. `--feed-file` stands in for the fetched widget, so the
run reads no network and asks no book page.

From this project's root:

```bash
python book_scan/book_scan.py \
  --source widget \
  --user-id example-reader \
  --feed-file book_scan/fixtures/widget_response.txt \
  --count 3 \
  --output book_scan/samples/expected_books_from_widget.json
```

`example-reader` is the same obvious placeholder as above: a widget run needs a user id,
but `--feed-file` means no URL is ever built from it. Real run, captured verbatim:

```text
book_scan: wrote 3 book(s), newest The Lantern Keeper
```

Exit code `0`. What you should see in `expected_books_from_widget.json`:

- Three records in the widget's own order, "The Lantern Keeper", "Rook's Gambit", and
  "Café Nights": a widget run never re-sorts, because the widget's order is the member's
  finishing order (`book_scan.md`, "The shelf widget: a third source").
- Every `read` is `null`, and the confirmation line names no date: the widget gives no
  read date.
- Every `year` and `cover` is `null`: the widget gives neither (its 75px thumbnail is
  not recorded), and the book page that would fill them is not asked under
  `--feed-file`.
- `rating` is the number for Goodreads' rating word: `it was amazing` is `5`,
  `really liked it` is `4`, and `it was ok` is `2`.
- The accented titles and authors are written as JSON `\u` escapes, which a reader
  decodes back to the same text.

`../tests/test_book_scan_widget_source.py` compares a fresh run against this file byte
for byte, so the pair cannot drift.
