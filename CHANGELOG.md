# Changelog

Notable changes to this project, newest first.

## 0.2.1 - 2026-11-09

### Added

- `bounded_run`'s `run_bounded` takes `inherit_stderr=True`, which gives the child the
  caller's own stderr instead of a pipe, so a long run's progress lines appear as they
  are written rather than when it ends. The result's `stderr` is then `""`.

### Fixed

- `cover_art` and `wikidata_director` follow a redirect only to https. urllib's stock
  opener follows one onto plain `http:` or `ftp:`, and the reply read there would have
  been parsed as the service's own. A refused hop raises `ValueError` and counts as one
  more failed fetch.
- `book_scan`'s live cover test skips, instead of failing, when the server answers 429
  or a 5xx: a rate limit or an outage is not a broken contract.
- Two of `bounded_run`'s tests failed on Windows and now run only where their subject
  does. The check that an unkillable `ps` is abandoned runs on POSIX alone, as its only
  caller does, and the late-tracking live test waits for a grandchild process on POSIX
  alone, since on Windows the child is still suspended at that point.

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
