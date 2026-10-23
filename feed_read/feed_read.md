# feed_read

Shared helpers for the feed adapters (Letterboxd, Goodreads). This module holds only
the parts both adapters need identically -- fetching, walking `<item>` elements,
turning vendor date strings into dates, the floor-of-one integer option type, the one
rule for which URLs may be fetched or recorded, and the atomic record write. The fetch
itself -- that URL rule, the request, and the bounded body read -- is the shared
`bounded_fetch` library's; this module re-exports its names (`https_url_or_none`,
`read_body`, `ChunkReader`, `BODY_LIMIT_BYTES`, `BODY_DEADLINE_SECONDS`, and
`FEED_TIMEOUT_SECONDS` for its `DEFAULT_TIMEOUT_SECONDS`) so no adapter changed when it
moved, and adds only what is this project's: the User-Agent Letterboxd requires. It has no
CLI of its own and is never run directly; only the field extraction differs between the
two feeds, and that lives in each adapter, not here.

## `positive_int` and `write_records`: the adapters' CLI side, once

Both adapters need these two byte-for-byte identically, so they live here once rather
than as two copies that every fix would have to keep in step by hand.

- `positive_int(text)` is the `type=` of every option that must be at least one
  (`--count` on both, `--cover-width` and `--window` on the Goodreads adapter). It
  raises `argparse.ArgumentTypeError` for zero and below, so argparse renders a usage
  error and exits `2` **before any fetch** -- the same treatment a non-integer already
  gets, rather than a second spelling of "bad argument".
- `write_records(path, records)` writes the records as a two-space-indented JSON array
  ending in one LF, atomically: a temp file beside `path`, then `os.replace`, so a
  concurrent reader sees the whole old file or the whole new one and never a seam.
  Missing parent directories are created. The temp file is `tempfile.mkstemp`'s --
  created in `path`'s own directory (so the rename stays a same-filesystem rename),
  opened exclusively, under a name nothing can predict (`<name>.<random>.tmp`). It used
  to be the fixed `<name>.tmp`, and that was a name a symlink could be planted at ahead
  of time -- one committed into the site checkout the tools write into -- after which
  a plain open followed the link: the records were written **through** it into the
  link's target, and the rename put the link itself in the record's place. Before the
  rename the file is given mode `0o644` explicitly: `mkstemp` creates it readable by
  its creator alone, a plain open would take the caller's umask, and the record is
  public data a web server must read, so its mode depends on neither. On Windows, which
  has no POSIX mode bits (and no `os.fchmod` before Python 3.13), the step is skipped:
  `mkstemp`'s file is already writable there. When the write or the rename fails it
  removes its own temp file and raises `OSError`; the previous file, if any, is
  untouched, and the caller reports the cause (both adapters do so on
  stderr, exit `2`). When creating the temp file itself fails there is nothing to
  remove, and that error propagates as it is. It serializes with `allow_nan=False`,
  **before** touching the filesystem:
  `json.dumps` would otherwise write a float NaN or infinity as the bare tokens
  `NaN`/`Infinity`, which are not JSON, so a reader's `JSON.parse` would fail on the
  whole file and every card would disappear. A record holding such a value raises
  `ValueError` with no directory created, no temp file written and the previous file
  untouched. Neither adapter can produce one -- the film adapter maps a non-finite
  `memberRating` to `None` and the book adapter parses its rating as an `int` -- so
  this is the floor under both parsers, not a path either exercises.

## The fetch seam, and why it is injected rather than mocked

`real_fetch(url, timeout) -> bytes` is the one function in this module that reaches
the network: it is `bounded_fetch.fetch_url(url, timeout, {"User-Agent": _USER_AGENT})`
and nothing more. The unit tests under `tests/` call `feed_items`/`rfc822_date`/
`iso_date` directly on a `bytes` body or a string already in hand, so none of them
reach the network -- there is nothing to mock. `real_fetch`'s own wiring is pinned by
replacing `bounded_fetch.fetch_url` for one test and reading back the URL, the timeout
and the header it was handed; the connection itself is `bounded_fetch`'s, excluded from
coverage there, because a mocked opener proves only that the mock was called
correctly, never that a genuine HTTPS response comes back readable. The request itself
is built inside `fetch_url` by `bounded_fetch.build_request` (the URL rule plus the
User-Agent handed to it), so the header is pinned on what `real_fetch` hands over.
What exercises the fetch end to end is each adapter's own live
smoke test in its `tests/` folder (skipped by `<TOOL>_OFFLINE=1` or an unset identity
variable), run against the real vendor feed: a tool that crosses a host boundary needs
a live end-to-end test at that boundary. `Fetcher` (the `(url, timeout) -> bytes`
alias) is the injection seam every adapter's own tests use instead: a fake that returns
canned bytes, so both the success and failure paths stay reachable with no real network
call.

## Only an https URL with a host is fetched, or recorded

`https_url_or_none(url)` -- `bounded_fetch`'s, re-exported -- returns `url` verbatim
when it is an https URL with a host and no userinfo, whitespace, or Unicode control or
format character, and `None` for everything else. The `bounded_fetch` library's own
doc gives the full rule and why its character scan has to run on the raw string,
before the parse. It is `cover_art._is_recordable_url`'s rule, in this same project,
character for character: the two were written apart, and the weaker one here let
userinfo, a header break, a NUL, a zero-width space, a bidi override and a plain space
through until the security lens measured the gap (2026-09-22).

It is applied in two places, and both matter:

- **Where each record URL is decided.** `film_parse.poster_from_description` runs the
  first `<img>`'s `src` through it, and `book_scan._cover` runs `book_large_image_url`
  through it. A rejected URL is written as `poster: null` / `cover: null` -- the same
  record shape as a diary entry with no image or a shelf item with no large variant,
  which each adapter's page already degrades to a text-only card (`film_scan.md`, "The
  no-poster degrade path"; `book_scan.md`, "Why `book_image_url` is not the cover
  URL"). Refusing at the parser is what makes a live run and a `--feed-file` run agree,
  and what keeps the film adapter's poster probe from ever fetching a rejected URL: the
  probe only sees a poster the record carries.
- **At the sink.** `bounded_fetch.build_request` raises `ValueError` for any URL the
  rule rejects, so no `real_fetch` caller can send a non-https request whatever it was
  handed, and a redirect is followed only to https. Every caller's except clause around
  its fetch already catches `ValueError` (the film adapter's probe falls back to the
  feed's own poster, which has passed the rule itself), and the message names the rule
  rather than the URL, so a refusal can never echo a feed URL that carries an identity.

Before the rule existed, a description whose first `<img>` pointed at
`file:///...-0-600-0-900-crop` had that path rewritten to a size, fetched through
`urlopen`, and written into the record. `bounded_fetch`'s suites pin each accepted and
rejected shape and the sink's refusal; each adapter's own suite pins the null record
and, for the film adapter, that the only request such a run makes is the feed's.

**A redirect keeps the User-Agent only on the same origin.** `bounded_fetch` follows a
redirect only to https, and it carries the caller's headers onward only when the target
has the scheme, host and port of the request that was redirected. So a vendor moving a
feed path on its own host still receives `_USER_AGENT`. A redirect to another host or
port goes without it, and urllib sends its own default User-Agent there instead -- the
string Letterboxd answers with a 403. That is the price of the library's rule: a header
a caller attaches never reaches a host the caller did not name, which matters for the
callers whose headers carry a token or a contact address, even though this project's
User-Agent is neither. No cross-host redirect on either vendor's feed has been measured;
if one appears, it shows up as a live 403 from the target.

## `terminal_safe_text`: a title is data to a page, an instruction to a terminal

Each adapter ends a successful run by echoing the newest record's title to stdout, for
whoever ran the refresh to read. That title is a third party's text. Printed to a
terminal, a control character in it is not text at all but an instruction to the
terminal: it can rewrite the window title, erase the lines already printed, or wrap the
name in an OSC 8 hyperlink pointing anywhere. So the title goes through
`terminal_safe_text` first, which replaces every character of Unicode category `Cc` --
the C0 and C1 controls -- with a visible `\xNN` escape.

Escaped, not dropped: an operator should see that the title held something odd rather
than read a silently shortened title as the real one. Ordinary text is untouched,
accents, CJK and emoji included; the rule is about control characters, not about
non-ASCII. Category `Cf` (a soft hyphen, a bidi mark) is deliberately left alone --
bidi in titles is an accepted risk here, recorded 2026-09-21, not one this helper
re-opens.

**What can actually arrive is narrower than it looks, and was measured rather than
assumed (2026-09-22).** expat refuses a raw C0 character outright ("not well-formed
(invalid token)") and refuses `&#27;` as well ("reference to invalid character
number"), so ESC cannot reach a title through a feed at all. XML 1.0 _does_ admit
U+0080-U+009F unescaped, and both the raw character and `&#155;` parse, so C1 --
U+009B, the control sequence introducer, and U+0085, a line break -- is the class this
removes here. The whole category is escaped anyway rather than those two: it costs one
comparison, and a rule written as "the controls" needs no revisiting when a parser
somewhere grows more permissive.

The **record** is deliberately left alone. It is JSON the site reads through
`textContent`, where a control character is inert; escaping it there would change the
data in order to protect a terminal. `album_scan` and `cover_art` apply the same rule at
their own echo sites, in their own copy -- they share no code with `feed_read`, exactly as
`https_url_or_none` and `cover_art`'s `_is_recordable_url` do not.

## Why `parsedate_to_datetime`, not `strptime`

`rfc822_date` reads Goodreads' `<pubDate>` with `email.utils.parsedate_to_datetime`, not
`datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %z")`. The reason is **not** the
single-digit day: `strptime`'s `%d` accepts `'Sat, 1 Aug 2026 00:00:00 +0000'` (4 of
49 items on a real shelf are this shape) exactly as well as `parsedate_to_datetime`
does. `test_a_SINGLE_digit_day_parses` in `tests/test_feed_read_values.py` pins the real feed's
shape, but it does not distinguish the two parsers.

The real differentiator is that RFC-822/2822 permits several other optional and variant
forms a single fixed `strptime` format cannot all match at once, and
`datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %z")` **raises** `ValueError` on every
one of them: no leading day-of-week (`"1 Aug 2026 00:00:00 +0000"`), a named zone
(`"... +0000"` written as `"... GMT"`), an obsolete US zone (`"... EST"`), no seconds
field, or a trailing `(comment)` after the zone. `parsedate_to_datetime` is the standard
library's own RFC-822 parser (used by the email package to read `Date:` headers), and it
implements the grammar rather than pinning one template, so it accepts all of these.
`test_a_date_with_no_weekday_parses` in `tests/test_feed_read_values.py` pins one of them. Keeping
the parse here, once, means a hand-rolled format cannot fail separately in each
adapter.

## Why `feed_items` returns `[]` for a channel-less body but raises for malformed XML

These are two different failure shapes and the caller needs to tell them apart:

- **No `<channel>` (well-formed XML, wrong structure)** -- e.g. a vendor error page
  that happens to be served with an XML content type, or a feed that has been
  temporarily replaced with an empty stub. This degrades to an empty list: the adapter
  then writes nothing, so a failed refresh keeps the last good record. "Nothing to show
  this refresh" is an empty list, never an exception the caller has to catch specially.
- **Genuinely malformed XML (`ET.ParseError`)** -- truncated output, a network proxy's
  HTML error page with no XML wrapper at all, or a byte-level corruption. This is not
  "the feed has nothing new," it is "the fetch itself is broken," and `feed_items`
  re-raises it as `ValueError` so the caller's own error handling (not this module's)
  decides what to do. `ValueError` rather than letting `ET.ParseError` escape directly:
  callers of a shared library should not need to import `xml.etree.ElementTree`
  themselves just to catch its errors.

## The entity-expansion guard

`feed_items` rejects any body carrying a DOCTYPE declaration with `ValueError`, from
the XML parser's own DOCTYPE event. Stock `xml.etree.ElementTree` is not vulnerable to
external entity expansion (XXE -- reading local files or making network requests) --
that has been closed in the standard library since Python 3.7.1 -- but it remains
vulnerable to _internal_ entity expansion ("billion laughs" / "quadratic blowup"), a
memory/CPU exhaustion attack built entirely from a DOCTYPE's own internal subset, with
no network or filesystem access needed. `feed_items` parses a body fetched from a
third-party vendor's server, which makes it exactly that untrusted-XML case.

The refusal is raised by `_DoctypeRefusingBuilder`, a `TreeBuilder` whose `doctype()`
method raises, wired into `ET.fromstring` as the parser's target. `XMLParser` calls
that method from expat's start-of-DOCTYPE handler: **after** expat has settled the
document's encoding, and **before** it reads the internal subset where the entities
live. That ordering is what makes it a _guard_ rather than a _post-hoc inspection_ --
by the time a tree exists to inspect, any expansion has already cost the memory and CPU
it was going to cost -- and the encoding independence is why it is not a search of the
raw bytes for `<!DOCTYPE`. A byte search sees the keyword only in a one-byte encoding.
expat auto-detects UTF-16 from a byte-order mark, or from the NUL beside the leading
`<` when there is none, and in UTF-16 every letter of the keyword is interleaved with
a NUL: a UTF-16 "billion laughs" body passed a byte search and expanded anyway. An
earlier version of this guard was that byte search; it was removed rather than kept
beside the parser event, because a second mechanism that catches strictly less than
the first is a second thing to reason about and nothing more.

Rejecting the DOCTYPE is the **complete** guard, not a partial one, and deliberately
does not also look for `<!ENTITY`: an entity declaration can only appear inside a DTD,
and a DTD can only be introduced by a DOCTYPE, so there is no entity-bearing XML
document with no DOCTYPE for this check to miss. The omission is not a gap. Nor is
case: expat requires the exact-case keyword, so a lowercase `<!doctype` is malformed
XML and reaches the caller as the "not parseable" `ValueError` instead -- there is no
spelling a hostile body can pick that parses.

**Why this is safe to apply unconditionally:** four real feeds, captured across both
vendors (Letterboxd, Goodreads) and four separate accounts, contain zero `<!DOCTYPE` and
zero `<!ENTITY` declarations. An RSS feed has no legitimate reason to carry a DOCTYPE,
so the guard costs real feeds nothing while closing a real class of attack against a
fetch whose far end this module does not control.

`tests/test_feed_read_items.py` pins each property separately. `test_a_doctype_bearing_body_is_rejected`
raises on a "billion laughs"-shaped UTF-8 DOCTYPE.
`test_a_doctype_is_refused_in_every_encoding_expat_detects` raises on the same body in
UTF-16 with a byte-order mark and in UTF-16-LE and UTF-16-BE without one -- the three
bodies a byte search passes. `test_the_refusal_comes_at_the_doctype_start_not_after_its_subset`
hands the parser a UTF-16 body whose internal subset is cut off mid-declaration and
requires the DOCTYPE refusal, not "not parseable": the guard fired before the subset
was read. `test_a_doctype_look_alike_inside_cdata_is_inert_text` parses a `<!doctype`
string inside a CDATA section to the one item it is -- the parser sees markup, not
bytes, so a look-alike in element text is ordinary text. `test_a_lowercase_doctype_keyword_is_still_refused`
is the case control described above, and `test_a_normal_feed_with_no_doctype_still_parses`
the non-DOCTYPE control.

## The three bounds on a fetch

`FEED_TIMEOUT_SECONDS` (15 seconds, sized for a whole feed body: a real Goodreads read
shelf measured roughly 187 KB at `per_page=50`) bounds each blocking socket operation of
a fetch; `read_body` bounds the whole body to `BODY_LIMIT_BYTES` (4 MiB) and
`BODY_DEADLINE_SECONDS` (60), so neither a server that never stops sending nor one that
drips a byte at a time can hold a refresh open. All three are `bounded_fetch`'s, and its
doc carries their measurements and reasons. Both of `read_body`'s refusals ride the
`except (OSError, ValueError, ...)` clause every caller already has around its fetch --
no adapter needed a new branch. The `wikidata_director` tool reads its responses through
the same function, for the same reason.

## Usage

Not a standalone CLI -- imported by each adapter (`film_scan.py`, `book_scan.py`),
and by `wikidata_director.py` and `../film_scan/src/film_parse.py`, through a `sys.path`
bootstrap that puts `<root>/feed_read` on the path from the importer's own `__file__`
(`../stdio_utf8/stdio_utf8.md`, "How this project's tools import it"). `feed_read` in turn puts `bounded_fetch` on the
path with a bootstrap of its own, from its own file, so anything that can import
`feed_read` gets the fetch with it. An adapter calls `real_fetch` (or its own injected
fake) to get a feed body, `feed_items` to walk it, `rfc822_date`/`iso_date` to turn
whichever date field its vendor uses into a `datetime.date`, `https_url_or_none` to
decide whether an image URL out of the feed may be recorded at all,
`terminal_safe_text` on any vendor text it echoes to the terminal, `positive_int` as
the `type=` of any option that must be at least one, and `write_records` to land its
records atomically.

## Testing

The suites live under `tests/`, one module per concern, behind the `test_feed_read.py`
aggregator that folds them into one run and floors each file's test count: the suite
outgrew the file-length bar, and the standing rule is to decompose one rather than
compress it. `tests/test_feed_read_items.py` exercises `feed_items` against literal
`bytes` bodies, the entity-expansion-guard cases described above included, in addition
to its ordering, empty-channel and malformed-XML cases; `tests/test_feed_read_fetch.py`
exercises `real_fetch`'s wiring to `bounded_fetch`, the User-Agent included, without a
request being sent, and that the re-exported names are `bounded_fetch`'s own
rather than copies; `tests/test_feed_read_values.py`
exercises `rfc822_date`, `iso_date`, `terminal_safe_text` and `positive_int` against
literal `str` values (the property test holding the re-exported `https_url_or_none` to
its whole contract lives with the rule, in `bounded_fetch`'s own suites);
and `tests/test_feed_read_write.py` exercises `write_records` against a temporary
directory -- its bytes, the parents it creates, its temp file (a symlink planted at the
old fixed name is neither written through nor renamed, the file sits beside the record
under a name that is not that one, the record's mode is `0o644` under a restrictive
umask, and a host without `os.fchmod` still writes it), and its failure shapes (a path that is a directory, a rename that fails with the
old file in place, a write that fails after the temp file exists, a creation refused
before it does -- no `.tmp` survives any of them). No fake fetch is needed because none
of them touch the network. Each
adapter's own CLI suite proves the wiring at its call sites. Gated by `python_gate`
through the aggregator, via the ordinary same-directory `<name>.py` / `test_<name>.py`
naming convention.

Exempt from `samples_presence` (see this project's `pyproject.toml`,
`[tool.samples_presence]`): a library with no CLI, so nothing in it can be run by hand
against a sample input.
