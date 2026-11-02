# Changelog

Notable changes to this project, newest first.

## 0.2.0 - 2026-11-02

### Added

- `book_scan --source` chooses where the books come from: `rss` (the shelf feed, still
  the default and the only source 0.1.0 read), `widget` (the public shelf widget), `csv`
  (the newest Goodreads library export in `--csv-dir`) or `both` (the feed and the export,
  merged). Under `both`, when the record already in `--output` holds a later read date
  than anything the two sources gave, it is kept rather than replaced.
- A widget book has no read date, so its record's `read` is null.
- `book_scan` fills in a missing cover, and the publication year, from the book's public
  Goodreads page. `--no-cover-lookup` turns the page fetch off.
- `album_scan --cover-report` writes each recorded album's cover result to a JSON file:
  `found`, `no-match`, `lookup-failed`, `no-art`, `not-asked`, or `unknown`. The notice for
  an album with no cover now gives the reason in words.
- When both of `cover_art`'s searches miss an album, it looks the album up in the artist's
  catalog before giving up.

### Changed

- `film_scan` takes each film's poster from TMDB, which needs a TMDB API read token in
  `FILM_SCAN_TMDB_TOKEN`; without it every film is recorded with no poster. 0.1.0 took
  the poster from the Letterboxd feed, whose image host now refuses scripted requests.
- `--help` no longer prints a default for an option left unset, `(default: None)`, or
  for a flag, `(default: False)`. A flag left off (`film_scan --no-director`,
  `album_scan --no-artwork`, and `--no-favorites`) now parses as None rather than False.
- `book_scan --help` now states the prerequisites of every source, and that Goodreads has
  answered 404 for the shelf feed since 2026-10-28.
- The project is now installable: `pip install .` installs the runtime requirement, and
  `pip install '.[test]'` the test requirements too. The README gives those steps, and a
  refresh command for each tool, and says that `album_scan` writes its record to standard
  output unless `--output` is given. The `.gitignore` covers the `build/` and
  `*.egg-info/` output an install leaves behind.

### Removed

- `film_scan --poster-width`. It chose among the widths Letterboxd pre-generates, and
  the poster no longer comes from Letterboxd. The TMDB poster is always TMDB's `w500`
  rendition; a command line that still passes the option now exits 2.

### Fixed

- `film_scan` and `book_scan` exit 3 when the feed answers 404 or 410, so a scheduled
  refresh can tell a feed that is gone from one that failed for a while. In 0.1.0 both
  exited 2. Goodreads has answered 404 for the shelf feed since 2026-10-28; use
  `--source widget` or `--source csv`.

## 0.1.0 - 2026-10-23

- Initial release.
