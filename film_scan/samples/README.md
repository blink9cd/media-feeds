# film_scan samples

A committed input for `film_scan` and the output it must reproduce, runnable by hand:
the diary fixture at `../fixtures/diary.xml`, and the film record the tool writes for it.
`--feed-file` reads that fixture directly -- no network call, and no real Letterboxd
username involved, since the fixture already stands in for a fetched feed body.

## Regenerate and compare

From this project's root:

```bash
python film_scan/film_scan.py \
  --username example-viewer \
  --feed-file film_scan/fixtures/diary.xml \
  --no-director \
  --output film_scan/samples/expected_films.json
```

`--no-director` is what keeps the director boundary offline. `--feed-file` only skips
the feed fetch; without `--no-director` this command would still ask Wikidata to name
the director, making a committed sample depend on a live third party and on whichever
answer it gave that day. The recorded `"director": null` is therefore the correct
sample value, not a missing one.

A username is required, from `--username` or `FILM_SCAN_USERNAME` (see `film_scan.md`),
but its value is never read on this path: `--feed-file` short-circuits the fetch entirely, so no URL is ever
built from it. `example-viewer` is the same placeholder identity already used
throughout `../fixtures/diary.xml` (the feed's own `<title>`, `<link>`, and
`<dc:creator>` all name "Example Viewer") -- an obvious fake, not a real Letterboxd
account.

Real run, captured verbatim:

```text
film_scan: wrote 1 film(s), newest The Invented Voyage (2026-08-30)
```

Exit code `0`. The command writes `expected_films.json`, an array even though
`--count` was left at its default of `1` -- the record file is always a JSON array,
at every count (`film_scan.md`, "`--count`: how many films to record"):

```json
[
  {
    "title": "The Invented Voyage",
    "year": "2024",
    "rating": 4.0,
    "watched": "2026-08-30",
    "rewatch": false,
    "poster": "https://example.invalid/poster/voyage-0-400-0-600-crop.jpg",
    "director": null
  }
]
```

## What you should see

- The fixture holds four `<item>`s: one rated film, one list (no film elements),
  one unrated film, and one film with no poster (see `../fixtures/diary.xml` and
  `film_scan.md`, "Why half a feed is not films"). Only the three films are
  candidates; the list is excluded entirely.
- The array holds exactly one record at the default `--count 1` -- `title`, `year`,
  `rating`, `watched`, `rewatch`, `poster`, and `director` are exactly that record's
  `FilmRecord` keys -- no `link`, no filesystem path, no username.
- The record picked is the film with the newest `watched` date among the three
  candidates, which is "The Invented Voyage" (`watched: 2026-08-30`), not
  necessarily the first item in the feed.
- `poster` carries the **400x600** pre-generated size (`--poster-width`'s default),
  not the 600x900 the feed itself hands out -- see `film_scan.md`,
  "`--poster-width`: asking for a poster the card can actually use". On this
  `--feed-file` path the rewrite is **unverified**: a live run GETs each rewritten
  URL and falls back to the feed's own where the size is refused, and an offline run
  cannot, which is why `example.invalid` never needs to answer here.

## A verification note

Every field here is stable and fully reproducible: `--feed-file` and `--no-director`
leave the committed fixture as the only input, so no network answer and nothing read
from the local machine reaches the record. Regenerating this file should produce
byte-for-byte the same output every time. `../tests/test_film_scan_cli.py`'s
`SamplesPairTest` compares a fresh run's parsed records against it, so the pair cannot
silently drift from the tool.
