# cover_art.py

Resolves an album's cover-art URL at Apple's CDN, **at a size the caller chooses**.

This is a library module, not a CLI: `album_scan.py` imports `artwork_url` and
passes the size and format through from its own `--artwork-px` and `--artwork-format`
options (their defaults are the two "Why the default..." sections below). There is
nothing to run here directly.

## Why this is its own folder

It is a separate concern: a **vendor adapter**, owning the iTunes Search API traffic
behind a cover lookup and Apple's own URL-sizing scheme, and knowing nothing about which
album is newest or how a record is shaped.

That boundary also matches what the size option is *for*. A cover's display size is the
**consumer's** business, so the adapter's job is to translate a requested size into the
vendor's URL scheme and nothing more -- which is much easier to see when the adapter
is a file rather than a region of a larger one.

`AlbumRef`, the album reference this project's tools pass around, is defined here.
`album_scan` produces it and this module resolves its cover, so it lives in the one of
the two that imports the other's nothing: `album_scan` depends on this module, this
module depends on nothing outside its own folder, and there is no import cycle. The
apple-music-toolbox has a type of the same shape; `album_scan` converts the toolbox's
into this one at its Apple boundary, so neither project needs the other to be
installed, and a test in the monorepo the two came from keeps their fields in step.

## One lookup is two searches

`artwork_url` sends **two** queries to the iTunes Search API for every cover it resolves,
and it sends both unconditionally -- it is not a loop that stops as soon as one of them
matches. The first asks for the artist and the album together, the second for the artist
alone; the results of both are pooled, and only then ranked.

Both are needed because the store does not always use the library's name for an album. It
lists *Fictionals (Gold)* as plain `Fictionals`, so the `(Gold)` token matches nothing and the
right record is absent from the artist-and-album query entirely -- while the artist-alone
query does contain it. Returning as soon as the first query produces any name match is
the rejected alternative: it kept choosing *Fictionals (Teal Album)*, because both names
reduce to `fictionals`, so the narrow query "succeeded" and the query holding the right answer
never ran.

The request count therefore scales as **two per album resolved**, and the caller decides
how many albums that is. `album_scan.py` is that caller; what a whole refresh spends is
stated once, in its doc -- see `../album_scan/album_scan.md`,
"What a refresh actually spends, and the request that is not this tool's".

## Why the default is 400

The default was sized against a real consumer: a card whose cover box, MEASURED
2026-09-01 in a real browser, maxes out at **168.7 CSS pixels** wide. That is a hard
cap, not a typical value: the page's container had a fixed maximum width, so no monitor
made the cover bigger, and at narrow widths it hid covers entirely, so there was no
wider mobile case either. A consumer with a bigger box passes its own size.

A 2x display therefore needs **338** physical pixels. The default is **400** -- the
next round size up, with the headroom absorbing a layout change without immediately
under-serving the card.

Requesting **600x600** costs 66,794 bytes for pixels no visitor can see. Compared at
the size actually rendered:

| Requested | Bytes | Saved | SSIM vs 600 |
| --- | --- | --- | --- |
| 169x169 | 9,081 | 86.4% | 0.9312 |
| 250x250 | 16,950 | 74.6% | 0.9765 |
| 338x338 | 26,978 | 59.6% | 0.9900 |
| **400x400** (default) | **34,757** | **48.0%** | **0.9950** |
| 600x600 | 66,794 | 0.0% | 1.0000 |

Judged by eye as well as by SSIM, at 400 and at 338 the art is indistinguishable from
the 600. At **250 the softening is visible**, and at 169 it is obvious -- which is the
reason the default is not simply the CSS display width. A cover shown at 168.7 CSS
pixels needs *twice* that on the 2x displays most people read on; requesting 169 would
have visibly softened every cover on exactly those machines.

## Why the default format is WebP

Apple's thumbnailer serves the same artwork as WebP by swapping the URL's final
extension, and MEASURED 2026-09-01 that is worth more than any remaining size change:

| Request | Bytes | vs 400 JPEG | SSIM at the rendered size |
| --- | --- | --- | --- |
| 400x400 `.jpg` | 34,757 | -- | 0.9950 |
| **400x400 `.webp`** (default) | **14,192** | **-59%** | **0.9847** |
| 500x500 `.webp` | 18,956 | -45% | 0.9911 |
| 600x600 `.webp` | 23,968 | -31% | 0.9938 |

**The SSIM drop is real and was checked rather than waved away.** Apple encodes its WebP
more aggressively than its JPEG, so 400 WebP scores below 400 JPEG. Two things say it does
not matter here. Split by region, the penalty is *larger* on the flat background (0.9656)
than on the faces and detail (0.9767) -- and SSIM is unstable in low-variance areas, where
its variance terms approach zero and tiny value shifts score disproportionately. And viewed
at 4x magnification on the faces, the WebP holds edges with no blockiness or smearing; at
the size the card actually renders, the two are indistinguishable.

The size and the format are separate decisions and were kept that way. 400 is justified by
the display box (above) whatever the format is; WebP is justified by the byte count at that
size. Buying quality back by asking for 500 or 600 WebP would be paying on the wrong axis --
re-introducing the oversize this tool exists to remove.

**One URL, no fallback format, deliberately.** WebP decodes in every current browser. A
consumer must already read correctly with no cover at all -- a null artwork URL is a
normal outcome of this tool -- and an image that fails to decode can take that same
no-cover path. A second URL in a fallback format (for a `<picture>` element, say) would
be one more thing to keep in step, for a case that contract already covers.

## What is recorded, and what is refused

The store's `artworkUrl100` is remote input, and the URL this module records ends up
in an `<img src>` on a page. So a URL is recorded only when it is an **`https` URL with
a host** and nothing else: no other scheme (`javascript:`, `data:`, `file:`, plain
`http:`), no scheme-relative `//host`, no `user:password@` prefix, and no whitespace or
Unicode control character anywhere in it. Anything else is treated exactly like a
candidate with no art at all -- the album is still the right one, so its release year
is kept and only the URL is dropped.

The rule is judged on the URL **after** the size rewrite, because that is the string
actually recorded. There is deliberately no allowlist of vendor hosts: the rule is about
what a page can safely be handed, not about which CDN the store happens to use today.

## What Apple honors, and what the CLI still constrains

Measured by fetching each candidate and decoding the returned image's real dimensions:
Apple's thumbnailer returned **exactly** the requested size at every width tried from 284
to 1200, and both `.jpg` and `.webp` at every size. There is no pre-generated set and no
silent cap, so `artwork_url` does not constrain `width` at all.

`--artwork-format` (on `album_scan.py`) **is** constrained, for a different reason: not
because Apple would refuse an unlisted format, but because nobody has checked what it does
with one. The choices are the formats measured against the live service, so the CLI
cannot emit a URL resting on a guess.

That distinction is worth stating because other cover CDNs are **not** like this at all,
and an adapter for one needs guards this one does not. Two measured 2026-09-01:

- **Letterboxd** serves a fixed set of pre-generated sizes and answers 403 for
  anything else.
- **Goodreads** honors a size request downward only, silently returning the master
  image with a 200 for anything larger.

Neither serves WebP: **every** alternative extension (`.webp`, `.avif`, `.png`, even
`.jpeg`) returns 403 from both, and neither sets `Vary: Accept`, so content negotiation is
not happening either. The reason is structural rather than a policy choice -- both serve
**pre-rendered files** from object storage, where the size in the URL is part of a
filename generated in advance, not a parameter. Apple's host is a live thumbnailer, which
is why it alone honors arbitrary sizes *and* formats.

Reading the size out of the URL is not the same fact as the CDN honoring it. A
service that ignored the size segment would return the original image behind a
smaller-looking URL, saving nothing while passing every offline test. `--artwork-px`
is worth having only as long as Apple keeps honoring it, so the live boundary test
below fetches the resolved URL and reads the dimensions out of the returned image's own
header.

## Testing

`test_cover_art.py` drives `artwork_url` against a **fake fetcher** -- every
not-found branch, a property test over the response body, and the surrogate-in-a-field
cases -- so no unit test reaches the network. `tests/test_artwork_match.py` pins the
pure matcher, `src/artwork_match.py`, against candidate sets modeled on the store's own
reply for a band with several self-titled albums. Every artist and album name in this
document and those tests is an invented stand-in; only the live test,
`tests/test_live_artwork.py`, names a real album, because it must find one in the real
catalog.

The real fetch is bounded as well as timed out. The per-call timeout only bounds each
socket read, so a server that sends one byte every few seconds would restart it on every
byte and hold the fetch open for as long as it cared to drip; the body is therefore read
in chunks against a **size cap and a deadline**, and a reply that is too large or too
slow is refused the same way any other failed fetch is -- no cover, never an exception.
`tests/test_read_body.py` proves that on the real code path, against a throwaway HTTP
server on the loopback interface: no network, no host, and the whole class runs in
about a second.

`tests/test_live_artwork.py` is the one file that reaches the network. It proves the real seam: that the
endpoint answers, that the response still parses into a URL (a renamed field would
show up here as a loud failure rather than a silent `None`), and that the CDN returns
an image whose **actual pixel dimensions and actual format** match what the URL asked for.

Both halves are needed and neither implies the other. A service could honor the size and
ignore the format -- answering a `.webp` request with a JPEG keeps every dimension correct
while silently returning the bytes the format was chosen to save. The format is therefore
read from the file's own **magic bytes**, not from the URL's extension or the response's
`Content-Type`, since both of those are claims rather than evidence.

Set `COVER_ART_OFFLINE=1` to skip the live class on a host with no network; it also
skips on its own when the search endpoint is unreachable at the network layer, but an
HTTP error counts as reachable and fails. `COVER_ART_OFFLINE` is this folder's own
switch, like each other live tool's in this project (`album_scan`'s is
`ALBUM_SCAN_OFFLINE`), so a fully offline run sets each of them.
