# cover_art.py

Resolves an album's cover-art URL at Apple's CDN, at a size the caller chooses.

This is a library module, not a CLI: `album_scan.py` imports `artwork_url` and
passes the size and format through from its own `--artwork-px` and `--artwork-format`
options (their defaults are the two "Why the default..." sections below). There is
nothing to run here directly.

## Why this is its own folder

It is a separate concern: a vendor adapter, owning the iTunes Search API traffic
behind a cover lookup and Apple's own URL-sizing scheme, and knowing nothing about which
album is newest or how a record is shaped.

That boundary also matches what the size option is *for*. A cover's display size is the
consumer's business, so the adapter's job is to translate a requested size into the
vendor's URL scheme and nothing more, which is much easier to see when the adapter
is a file rather than a region of a larger one.

`AlbumRef`, the album reference this project's tools pass around, is defined here.
`album_scan` produces it and this module resolves its cover, so it lives in the one of
the two that imports the other's nothing: `album_scan` depends on this module, this
module depends on nothing outside its own folder, and there is no import cycle. The
apple-music-toolbox has a type of the same shape; `album_scan` converts the toolbox's
into this one at its Apple boundary, so neither project needs the other to be
installed. The two are kept field for field in step: a change to one is a change to
the other.

## One lookup is two searches

`artwork_url` sends two queries to the iTunes Search API for every cover it resolves,
and it sends both unconditionally: it is not a loop that stops as soon as one of them
matches. The first asks for the artist and the album together, the second for the artist
alone; the results of both are pooled, and only then ranked.

Both are needed because the store does not always use the library's name for an album. It
lists *Fictionals (Gold)* as plain `Fictionals`, so the `(Gold)` token matches nothing and the
right record is absent from the artist-and-album query entirely, while the artist-alone
query does contain it. Returning as soon as the first query produces any name match is
the rejected alternative: it kept choosing *Fictionals (Teal Album)*, because both names
reduce to `fictionals`, so the narrow query "succeeded" and the query holding the right answer
never ran.

That reduction is `src/artwork_match.py`'s `normalize`. It deletes variation selectors
first, then puts the name in NFKC, so a composed and a decomposed accent are one spelling
and a full-width bracket is an ASCII one; it drops the trailing qualifiers, folds `&`
to `and`, casefolds and puts the result in NFKC again, deletes apostrophes, and keeps the
letters, digits and combining marks of every script, with each run of anything else one
space, and puts those words in NFKC once more. Casefold can split a letter into a base and
separate marks, or an iota, that NFKC composes (*ΐ*, *ǰ*, *ᾂ* before an acute), and the
apostrophe deletion can leave a letter beside the accent that followed it (*Writer’́s*),
so without the two passes a name's `normalize` was not its own `normalize` again, and
*ᾂ́* and *ἂί* were two albums. A variation selector is a combining mark too, but it
chooses how a character is drawn rather than which name it spells, so it is deleted: *I ❤️ NY* and *I ❤ NY* are one name. It goes before the
qualifier strip, because one after a closing bracket would otherwise hide the qualifier
from it. A Cyrillic, CJK, Greek, Arabic, Hebrew or Devanagari title therefore matches on
its own letters; an earlier filter kept ASCII only, so every such title reduced to
nothing and matched every other. No accent is folded away: *Björk* is not *Bjork*.

A title with no letter, digit or mark, such as *+*, *×* or *!!!*, would reduce to nothing
the same way, and every such title by one artist would match every other, so an artist
whose albums are each one symbol would get the wrong album's cover. Such a title is
compared by its own text instead: NFKC, casefolded, outer whitespace stripped and each
inner run one space, so *＋* is *+* and *★️* is *★*. It takes no second NFKC, because none
would change it: casefold makes nothing NFKC alters out of such a character, and every
character NFKC composes onto is a letter, digit or mark (tests check both over every code
point). A title with none at all is compared whole, brackets included, so *(+)* is not
*(×)*; one whose base alone has none, *+ (Live)*, compares that base as text and its
qualifier as usual. The test runs before the `&` fold,
so a title that is only *&* is not an album called *And*. A title that is only a
qualifier, *(Live)*, keeps an empty base and is told apart by its qualifier. A title the
folds reduce to nothing keeps its own text the same way: U+02BC MODIFIER LETTER
APOSTROPHE is a letter, so it passes the test, but it is deleted as an apostrophe, so a
title that is only *ʼ* is compared as *ʼ* rather than read as a blank name.

Each trailing qualifier group takes the same rule, read by its inner text without the
brackets: a group with no letter, digit or mark keeps its own text, so *X (+)* and
*X (×)* are two editions rather than both reducing to a plain *X*, and *(+) (Live)* is
not *(Live)*. The test runs before the `&` fold here too, so *X (&)* has the qualifier
`&`, not `and`. A group the folds reduce to nothing keeps its own text too, so *X (ʼ)*
has the qualifier `ʼ`, as *X (’)* has `’`, and no longer matches *X (Live)*; on one side
only it still matches a plain *X*. A group blank inside, *()* or *( )*, adds nothing, so *Album ()* is still
*Album* and still matches *Album (Deluxe)*. Blank means whitespace only: a group
holding an invisible character, a zero-width space or a soft hyphen, keeps it as its
text, as a symbol-only name keeps one, so *Album* with a zero-width space
in brackets no longer matches *Album (Deluxe)*. A stray opening bracket stays in the group's text,
so *X ((+)* has the qualifier `(+`. A symbol qualifier on one side only is
accepted like any other one-sided qualifier: *X (+)* matches a plain *X*, because the
store may simply not carry the qualifier.

A blank name, one with no text left at all (empty, whitespace, or variation selectors
alone), is refused on either side by `pick_album`: a blank wanted album is answered with
no match, and a candidate with a blank name is skipped, on the year-and-track-count
route too. Blank has nothing to compare, so it would otherwise meet a qualifier-only
title such as *(Live)* as the same work.

The qualifier strip is quadratic in length and the store chooses every candidate's name,
so a name is bounded at `_MAX_NAME_CHARS`. `pick_album` and `same_artist_ids` measure it
after NFKC and the `&` fold, the two steps that can lengthen it, and do not compare a name
past that bound at all, on either side. Measuring after NFKC must not itself cost what the
bound saves, so a name longer raw than `_MAX_RAW_NAME_CHARS` is refused on its length
alone, before any of it is normalized; that bound is looser than the cap by the most NFKC
and the selector deletion can shorten a real name, and the source derives it. A direct
`normalize` or `split_name` call instead reads only its first `_MAX_NAME_CHARS` characters
after NFKC.

The request count therefore scales as two per album resolved (plus the catalog
fallback below, only on a miss), and the caller decides
how many albums that is. `album_scan.py` is that caller; what a whole refresh spends is
stated once, in its doc: see `../album_scan/album_scan.md`,
"What a refresh actually spends, and the request that is not this tool's".

## When the search misses: the artist's catalog

The store's search index can leave out an album the store carries. Measured 2026-10-24
on one real album: both searches came back without it (the artist-alone one listing
six of the artist's other albums), so the card's cover was null in every refresh since
the album was added, while `https://itunes.apple.com/lookup?id=<artistId>&entity=album`
listed it, beside a later remaster, in every storefront tried.

So when the pooled searches hold no match, `artwork_url` takes the `artistId` of a
candidate the searches did return under the same artist name, fetches that artist's
album catalog (`limit=200`), and ranks it with the same `pick_album`. Nothing about the
match is loosened: a remaster shares the album's name, and the library's own year and
track count are what put the original ahead of it.

The fallback is bounded in cost and in what it sends:

- **Only on a miss.** A matched search, or a pool with no candidate under the artist's
  name, sends no lookup at all.
- **At most two artist ids**, in pool order, first match wins. Two artists can share a
  name, so the first id is not always the right one; a third would be spending a
  request for an ever-smaller chance.
- **Only a positive JSON integer is an id.** The value goes into a URL, so a string, a
  float, an array, or a JSON `true` (a Python `bool`, which `isinstance` calls an `int`)
  lends nothing. The artist name it came with passes the same length cap as every name
  the matcher reads.
- **The same fetch, the same bounds.** The lookup goes through the injected fetcher
  with the same timeout, body cap, and deadline as a search, and a failed one is the
  same "no cover" outcome: `artwork_url` still never raises.

## Why a cover is null

A null URL comes back with a `reason`, one of three, because each asks something
different of whoever reads it:

| `reason` | Meaning | What it asks |
| --- | --- | --- |
| `no-match` | every request was answered, and no album in any reply is this one | a persistent one is a real miss: look at the store |
| `lookup-failed` | at least one request failed, so the reply that would have held it may be the one missing | nothing, unless it persists |
| `no-art` | the album was found, but its image is absent or refused (see below) | nothing this tool can fix; the year is still returned |

A failure outranks a miss: if any search or catalog request failed and nothing matched,
the reason is `lookup-failed`, never `no-match`. A reply whose `results` is not a list
counts as failed, not as empty. The two used to share one null and one notice line
naming both causes, which is how an album the store's search never returned passed for
a network blip in every daily refresh for five weeks. Reporting a *persistent*
`no-match` to a person is the scheduled job's business, not this adapter's: this module
keeps no state between runs. `album_scan --cover-report` hands that job each album's
reason as data (`album_scan.md`, "The cover report").

## Why the default is 400

The default was sized against a real consumer: a card whose cover box, measured
2026-09-01 in a real browser, maxes out at 168.7 CSS pixels wide. That is a hard
cap, not a typical value: the page's container had a fixed maximum width, so no monitor
made the cover bigger, and at narrow widths it hid covers entirely, so there was no
wider mobile case either. A consumer with a bigger box passes its own size.

A 2x display therefore needs 338 physical pixels. The default is 400, the
next round size up, with the headroom absorbing a layout change without immediately
under-serving the card.

Requesting 600x600 costs 66,794 bytes for pixels no visitor can see. Compared at
the size actually rendered:

| Requested | Bytes | Saved | SSIM vs 600 |
| --- | --- | --- | --- |
| 169x169 | 9,081 | 86.4% | 0.9312 |
| 250x250 | 16,950 | 74.6% | 0.9765 |
| 338x338 | 26,978 | 59.6% | 0.9900 |
| **400x400** (default) | **34,757** | **48.0%** | **0.9950** |
| 600x600 | 66,794 | 0.0% | 1.0000 |

Judged by eye as well as by SSIM, at 400 and at 338 the art is indistinguishable from
the 600. At 250 the softening is visible, and at 169 it is obvious, which is the
reason the default is not simply the CSS display width. A cover shown at 168.7 CSS
pixels needs *twice* that on the 2x displays most people read on; requesting 169 would
have visibly softened every cover on those machines.

## Why the default format is WebP

Apple's thumbnailer serves the same artwork as WebP by swapping the URL's final
extension, and measured on 2026-09-01 that is worth more than any remaining size change:

| Request | Bytes | vs 400 JPEG | SSIM at the rendered size |
| --- | --- | --- | --- |
| 400x400 `.jpg` | 34,757 | (baseline) | 0.9950 |
| **400x400 `.webp`** (default) | **14,192** | **-59%** | **0.9847** |
| 500x500 `.webp` | 18,956 | -45% | 0.9911 |
| 600x600 `.webp` | 23,968 | -31% | 0.9938 |

**The SSIM drop is real and was checked rather than waved away.** Apple encodes its WebP
more aggressively than its JPEG, so 400 WebP scores below 400 JPEG. Two things say it does
not matter here. Split by region, the penalty is *larger* on the flat background (0.9656)
than on the faces and detail (0.9767), and SSIM is unstable in low-variance areas, where
its variance terms approach zero and tiny value shifts score disproportionately. And viewed
at 4x magnification on the faces, the WebP holds edges with no blockiness or smearing; at
the size the card actually renders, the two are indistinguishable.

The size and the format are separate decisions and were kept that way. 400 is justified by
the display box (above) whatever the format is; WebP is justified by the byte count at that
size. Buying quality back by asking for 500 or 600 WebP would be paying on the wrong axis,
re-introducing the oversize this tool exists to remove.

**One URL, no fallback format, deliberately.** WebP decodes in every current browser. A
consumer must already read correctly with no cover at all (a null artwork URL is a
normal outcome of this tool), and an image that fails to decode can take that same
no-cover path. A second URL in a fallback format (for a `<picture>` element, say) would
be one more thing to keep in step, for a case that contract already covers.

## What is recorded, and what is refused

The store's `artworkUrl100` is remote input, and the URL this module records ends up
in an `<img src>` on a page. So a URL is recorded only when it is an `https` URL with
a host and nothing else: no other scheme (`javascript:`, `data:`, `file:`, plain
`http:`), no scheme-relative `//host`, no `user:password@` prefix, no backslash in the
netloc (`urlsplit` reads it as a host character, a browser as `/`, so the page would
load from a host the rule never saw), and no whitespace or Unicode `C*` character
(control, format, surrogate, private use, unassigned) anywhere in it. Anything else is
treated like a candidate with no art at all: the album is still the right one, so its
release year is kept and only the URL is dropped.

The rule is judged on the URL after the size rewrite, because that is the string
actually recorded. There is deliberately no allowlist of vendor hosts: the rule is about
what a page can safely be handed, not about which CDN the store happens to use today.

## What Apple honors, and what the CLI still constrains

Measured by fetching each candidate and decoding the returned image's real dimensions:
Apple's thumbnailer returned the requested size at every width tried from 284
to 1200, and both `.jpg` and `.webp` at every size. There is no pre-generated set and no
silent cap, so `artwork_url` does not constrain `width` at all.

`--artwork-format` (on `album_scan.py`) is constrained, for a different reason: not
because Apple would refuse an unlisted format, but because nobody has checked what it does
with one. The choices are the formats measured against the live service, so the CLI
cannot emit a URL resting on a guess.

That distinction is worth stating because other cover CDNs are not like this at all,
and an adapter for one needs guards this one does not. Two measured 2026-09-01:

- **Letterboxd** serves a fixed set of pre-generated sizes and answers 403 for
  anything else.
- **Goodreads** honors a size request downward only, silently returning the master
  image with a 200 for anything larger.

Neither serves WebP: every alternative extension (`.webp`, `.avif`, `.png`, even
`.jpeg`) returns 403 from both, and neither sets `Vary: Accept`, so content negotiation is
not happening either. The reason is structural rather than a policy choice: both serve
pre-rendered files from object storage, where the size in the URL is part of a
filename generated in advance, not a parameter. Apple's host is a live thumbnailer, which
is why it alone honors arbitrary sizes *and* formats.

Reading the size out of the URL is not the same fact as the CDN honoring it. A
service that ignored the size segment would return the original image behind a
smaller-looking URL, saving nothing while passing every offline test. `--artwork-px`
is worth having only as long as Apple keeps honoring it, so the live boundary test
below fetches the resolved URL and reads the dimensions out of the returned image's own
header.

## Testing

`test_cover_art.py` is the aggregator: its `load_tests` folds every `tests/test_*.py`
into its own run, and it floors each of those files at the test count it holds, so a
file that loses tests, or a new one with no floor, fails loudly rather than shrinking
the run unnoticed. `tests/test_artwork_url.py` drives `artwork_url` against a fake
fetcher (every not-found branch and the surrogate-in-a-field cases), and
`tests/test_artwork_url_properties.py` holds its property tests over the response body
and the album's names, so no unit test reaches the network, and a tripwire (below)
fails any that tries. `tests/test_artwork_match.py` pins the
pure matcher, `src/artwork_match.py`, against candidate sets modeled on the store's own
reply for a band with several self-titled albums. `tests/test_artist_catalog.py` pins
the catalog fallback against a fake that answers searches and lookups separately, with a
fixture shaped like the measured miss. Every artist and album name in this
document and those tests is an invented stand-in; only the live test,
`tests/test_live_artwork.py`, names a real album, because it must find one in the real
catalog.

The real fetch is bounded as well as timed out. The per-call timeout only bounds each
socket read, so a server that sends one byte every few seconds would restart it on every
byte and hold the fetch open for as long as it cared to drip; the body is therefore read
in chunks against a size cap and a deadline, and a reply that is too large or too
slow is refused the same way any other failed fetch is: no cover, never an exception.
`tests/test_read_body.py` proves that on the real code path, against a throwaway HTTP
server on the loopback interface: no network, no host, and the whole class runs in
a few seconds. Only the drip tests shrink the deadline below a second, and their server
drips until the client hangs up, so neither verdict depends on how promptly a loaded
machine schedules the client; two tests stall the client on purpose to prove it.

The real fetch also follows a redirect only to https. urllib's stock opener follows one
onto plain `http:` or `ftp:`, and the reply read there would be parsed as the store's
own, so `_real_fetch` opens through `src/artwork_redirect.py`'s handler, which refuses
any other hop with a `ValueError`: one more failed fetch, so no cover. It copies
the https half of the rule the shared `bounded_fetch` library holds for the feed
adapters, kept here because this module depends on nothing outside its own folder (*Why
this is its own folder*). It also refuses a backslash anywhere in the netloc, as that
library does, since `urlsplit` reads one as a host character and a browser reads it as
`/`. It leaves out three of that library's refusals. It does not refuse userinfo or an
unreadable port, since this fetch sends no header worth guarding.
Nor does it refuse any raw whitespace or Unicode `C*` character in the URL: that one is
a deliberate known difference, left out on purpose and pinned case by case by
`tests/test_fetch_parity.py`, rather than a hop this copy guards.
`tests/test_artwork_redirect.py` proves it through the real opener, with urllib's https
and http handlers scripted so nothing reaches a socket.

Three copies, this redirect handler, `_read_body`, and `_is_recordable_url`, are held
to the shared library by `tests/test_fetch_parity.py`, which runs each beside its
`bounded_fetch` original on the same cases and fails when they disagree. The recording
rule must match `https_url_or_none` on every case, the redirect cases included. The
readers must agree on every case, a chunk landing exactly at the deadline included
(kept, by both: only one landing after it is refused); only the deadline's value
differs, and the test passes both the same one. The handlers must agree on every follow and refuse case the test lists. On the three known
differences above (userinfo, an unreadable port, and any raw whitespace or Unicode `C*`
character), the copy follows and the library refuses, and each is asserted so a change
on either side shows up. The test puts `bounded_fetch` on its path from the one place
this repository vendors it, directly under the root.

`tests/test_live_artwork.py` is the one file that reaches the network. It proves the real seam: that the
endpoint answers, that the response still parses into a URL (a renamed field would
show up here as a loud failure rather than a silent `None`), and that the CDN returns
an image whose actual pixel dimensions and actual format match what the URL asked for.

Both halves are needed and neither implies the other. A service could honor the size and
ignore the format: answering a `.webp` request with a JPEG keeps every dimension correct
while silently returning the bytes the format was chosen to save. The format is therefore
read from the file's own magic bytes, not from the URL's extension or the response's
`Content-Type`, since both of those are claims rather than evidence.

Set `COVER_ART_OFFLINE=1` to skip the live class on a host with no network; it also
skips on its own when the search endpoint is unreachable at the network layer, but an
HTTP error counts as reachable and fails. `COVER_ART_OFFLINE` is this folder's own
switch, like each other live tool's in this project (`album_scan`'s is
`ALBUM_SCAN_OFFLINE`), so a fully offline run sets each of them.

Every other test module, `test_cover_art.py` included, is held offline by a tripwire
rather than by care: each binds unittest's `setUpModule` and `tearDownModule` from
`tests/_offline_guard.py`. While that module's tests run, a connection to any host but
loopback is refused and every proxy is removed (a loopback proxy would tunnel past the
refusal). The refusal alone would not be loud, since the fetch turns any `OSError` into
a quiet miss, so each one is also logged and the module's teardown fails naming every
host a test reached for. The hooks are per module, not one import-time patch, so the
live file, which binds neither, still reaches the network.
`tests/test_offline_guard.py` pins both halves: the guard's behavior, and which modules
carry it (read from each file's syntax tree). Every `test_*.py` in this folder or in
`tests/` but the live one must bind the two hooks, so a new test module in either place
fails that test until it does. A guard whose start fails midway undoes what it had
done, because unittest skips a module's teardown after its setup errors.
