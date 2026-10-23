# book_scan samples

A committed input and the output it must reproduce, runnable by hand: the shelf
fixture at `../fixtures/shelf.xml`, and the book record `book_scan` writes for it.
`--feed-file` reads that fixture directly -- no network call, and no real Goodreads
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
not a real Goodreads account -- example values are obvious fakes, and the fixture's own
`<title>` and `<link>` elements separately name "Fictional Reader" as the shelf's
account holder.

Real run, captured verbatim:

```text
book_scan: wrote 1 book(s), newest The Later Read (2026-08-01)
```

Exit code `0`. The command writes `expected_books.json`, an array even though
`--count` was left at its default of `1` -- the record file is always a JSON array,
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

- The fixture holds six `<item>`s (`../fixtures/shelf.xml`), every one a book -- the
  Goodreads shelf feed carries no non-book items the way Letterboxd's diary mixes in
  lists, so this tool needs no filter for them.
- The array holds exactly one record at the default `--count 1` -- `title`, `author`,
  `year`, `rating`, `read`, and `cover` are exactly that record's `BookRecord` keys --
  no `link`, no filesystem path, no user id.
- `year` is the book's PUBLICATION year (`<book_published>`, `2023` here), not anything
  about when it was read -- that is `read`. It is a string, not an int, because the feed
  carries a bare year and the sibling `FilmRecord.year` already uses that shape; nothing
  downstream does arithmetic on it. A book whose item carries no `book_published` (or a
  blank one) records `year: null`, never `""` -- the card omits a line whose value is
  null, where an empty string would render a blank row.
- The record picked is the book with the newest `read` date among the six, which is
  "The Later Read" (`read: 2026-08-01`) -- **not** the first item in the feed
  (`"An Early Addition"`, added most recently but read six months earlier). This is
  the read-date re-sort (`book_scan.md`, "Why the feed order cannot be trusted") shown
  end to end through the CLI, not just at the parsing layer.
- `cover` carries the `_SX318_` large-image variant, not the `_SX50_` thumbnail
  `book_image_url` itself points at (`book_scan.md`, "Why `book_image_url` is not the
  cover URL").

## A verification note

Every field here is stable and fully reproducible: it comes entirely from the
committed fixture. Regenerating this file should produce byte-for-byte the same
output every time. `../tests/test_book_scan_cli.py`'s `SamplesPairTest` compares a
fresh run's parsed records against it, so the pair cannot silently drift from the tool.
