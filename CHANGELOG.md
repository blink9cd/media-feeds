# Changelog

Notable changes to this project, newest first.

## 0.3.0 - 2026-11-17

### Added

- `album_scan` says why Music.app answered nothing. The apple-music-toolbox reports a
  timeout, a failed `osascript`, and an empty lookback window alike as no answer, so
  `album_scan` records what each Music.app query did and names the cause in its
  `no album:` line, in the `--source auto` fallback notice, and in the `counts
  unavailable` line: `Music.app timed out after <n>s`, `osascript could not be run`,
  `automation permission denied (...)`, `the Music.app query failed (...)`, `Music.app
  has nothing added within the last <n> days`, and others. A quoted `osascript` error
  line is escaped and then cut to a fixed length. The toolbox is handed exactly what it
  was handed before. See "Why Music.app answered nothing" in `album_scan.md`.
- `album_scan` exits `3` when macOS refuses Music.app's Automation permission (osascript
  error `-1743`, or `-1744` for a consent an unattended run cannot ask for). Under
  `--source music` nothing is written. Under `--source auto` the folder fallback still
  runs and writes its record when it finds an album, but the run still exits `3`, so a
  lost permission is never reported as a success or as a day with nothing new.
- `book_scan` keeps what the record already holds when a page lookup comes back empty.
  Before writing, it reads the record at `--output` and matches each book by title and
  author; a cover or year the page did not give this run falls back to the recorded one
  when that still passes the cover rule. Before, a day the page was not served wrote
  `cover: None` over a good cover. A run that asks no page (`--feed-file`,
  `--no-cover-lookup`) reads no record.

### Changed

- `cover_art`'s album matcher keeps the letters, digits, and combining marks of every
  script, so a Cyrillic, CJK, Greek, Arabic, Hebrew, or Devanagari title matches on its
  own letters; before, only ASCII was kept, and every such title reduced to nothing and
  matched every other. It also deletes variation selectors, so "I ❤️ NY" and "I ❤ NY"
  are one name, and applies NFKC again after casefolding, so a name always normalizes to
  itself.
- A title with no letter, digit, or mark (such as `+`, `×`, or `!!!`) is compared by its
  own text, as is each such trailing qualifier group, so an artist whose albums are each
  one symbol no longer gets the wrong album's cover. A blank album name, on either side,
  matches nothing.
- `wikidata_director` sends its User-Agent only to the origin it asked: a redirect to
  another host, scheme, or port goes without it.
- The cover art tests are split into smaller suites, and every offline test module
  carries a tripwire that fails it when a test reaches for any host but loopback. A new
  parity test holds `cover_art`'s copies of the URL rule, the body read, and the redirect
  handler to `bounded_fetch` case by case.
- `bounded_run.md` records that the Windows job-object path now passes on a Windows
  runner at 100% branch coverage.
- Tests and comments only otherwise: the property tests load a Hypothesis profile
  renamed `deterministic`, invisible characters in tests are written as escapes, and
  comments and docstrings gain serial commas.

### Fixed

- `album_scan`'s folder source bounds what one track's ID3 tag can cost. A tag's read is
  capped at 16 MiB, and a compressed frame inflates within 1 MiB and a whole tag's
  compressed frames within 4 MiB, so a small hostile file can no longer make the scan
  allocate or inflate without limit. A tag refused for its size counts the track as
  unrated, with the line `could not read the tags of <file> (the tag is over the size
  cap)`, and an extended header that claims more than its tag is read as an unreadable
  tag rather than ending the refresh.
- `cover_art`, `feed_read`, and the bundled `bounded_fetch` refuse a URL with a backslash
  anywhere in its host part. Python's `urlsplit` reads `\` as a host character while a
  browser reads it as `/`, so such a URL could pass a host check and then load from
  another host. `cover_art`'s redirect handler refuses such a hop too.
- `cover_art`'s capped body read refuses a chunk only once it lands strictly past the
  deadline, as `bounded_fetch` does; a chunk landing exactly at it is kept.
- `cover_art`'s album matcher refuses an over-long album or artist name on its length
  alone, before any of it is Unicode-normalized, so a store reply packed with characters
  that NFKC expands can no longer cost seconds of CPU per album.

## 0.2.2 - 2026-11-10

### Changed

- The license is now two files. `LICENSE` holds the GNU AGPL v3 text exactly as the Free
  Software Foundation publishes it, so GitHub and other tools recognize it. `NOTICE` holds
  the copyright line and the "or any later version" grant. The terms are unchanged:
  AGPL-3.0-or-later.

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
