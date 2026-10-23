# wikidata_director.py

Resolve a film's director(s) from its **TMDB movie id**, through Wikidata, with **no API
key**. The Letterboxd diary feed already carries a `tmdb:movieId` for every entry, so
this is an exact lookup by identifier -- not a title-and-year guess that a remake or a
re-release would quietly get wrong.

Used two ways: `film_scan` imports `directors_for` when it writes the film record, and
the CLI below resolves a single id by hand.

## Why Wikidata rather than the TMDB API

TMDB would answer this in one request, and it needs a **credential** to do it. Wikidata
needs none: there is nothing to keep out of the repository, nothing to rotate, and the
tool stays runnable from a fresh clone by anyone. That is worth two extra requests per
film on a tool that, by default, resolves one film per refresh.

## Why the Action API rather than SPARQL

Both were tested against the live services. The query service (WDQS) answered correctly
and then began returning `HTTP 429` with _"aggressively rate-limiting to 1 req / min --
this rule was created during active wdqs outage"_. One query per refresh survives that,
but a service in a declared outage is no contract to build on when a stable alternative
answers the same question. The Action API answers the same lookups.

The cost is three requests instead of one:

1. `action=query&list=search&srsearch=haswbstatement:P4947=<tmdb id>` -- the film's QID.
   `haswbstatement` searches on the **statement**, so this is an identifier lookup.
2. `action=wbgetclaims&entity=<QID>&property=P57` -- the director QIDs.
3. `action=wbgetentities&ids=<QIDs>&props=labels&languages=en&languagefallback=1` --
   their names, all in one request rather than one per director.

## The `languagefallback` parameter is load-bearing

**Measured, not defensive.** Wikidata moved a large class of person names to the `mul`
(multilingual) language code -- a name spelled identically in every Latin-script
language is stored once there rather than repeated per language. A plain
`languages=en` request returns an **empty** `labels` object for such an item.

Lana Wachowski (`Q9545711`) is one of them. Without `languagefallback=1`, TMDB id `603`
resolved to **"Lilly Wachowski" alone** -- a half-correct credit, which is worse than no
credit at all, because it reads as a fact rather than as an absence. With the fallback
the value still arrives under the `en` key (annotated with the language it actually came
from), so nothing downstream changes.

`test_wikidata_director.py` pins both halves: that the URL asks for the
fallback, and that a fallback-shaped label payload still resolves.

## A descriptive `User-Agent` is required

Wikimedia **blocks** a request that does not carry one, and urllib's own default -- the
`Python-urllib` product string with the interpreter version appended -- is exactly the
shape that gets blocked. The rejection arrives
looking like a network fault rather than like a policy decision, which is why the
constant carries its reason in the source and why the live-boundary test treats a `403`
as a **failure**, not as a reason to skip.

An honest product identifier is enough. Do not "fix" a future rejection by
impersonating a browser, and note that the string names no person, host, or account, so
it puts no personal data in the repository.

## A missing director is a normal outcome

`directors_for` **never raises**. No Wikidata item for the id, no `P57` statement on the
item, no label for the director, a rate limit, a maintenance page served as HTML, a
socket that never opened, a response that outgrows the body cap or drips past its
deadline -- every one of them comes back as `None`, and the card simply omits the line.
That is why the caller needs no error handling of its own.

Two consequences worth stating plainly:

- **Wikidata's coverage is not TMDB's.** A film can have a Wikidata item with no `P57`
  statement, and a director can have an item with no usable label. Both are data gaps
  upstream, not defects here.
- **Co-directors are not an edge case**, so the `&` join runs on ordinary records, not
  exotic ones. Names are **sorted** before joining: Wikidata does not guarantee
  statement order, and an unsorted join would write a different string into the record
  on different days for the same unchanged film.
  `&` is the film-credit convention and deliberately not the middle dot the card uses
  to separate its own fields, which would make one co-directed film read as two.

## Only a decimal id reaches the search

The id is interpolated into a CirrusSearch expression (`haswbstatement:P4947=<id>`),
and percent-encoding protects the URL, not the expression: `12 OR
haswbstatement:P4947=550` is a valid two-film query. The id arrives from a
third-party feed, so `directors_for` answers `None` for anything that is not all ASCII
digits -- `str.isdigit` alone would admit a superscript digit -- without making a
request. The CLI reports that as exit `1`, the same normal outcome as no director.

## More directors than one labels request takes is no answer

`wbgetentities` accepts at most **fifty ids per request** from a client without the bot
right (`LABELS_PER_REQUEST`). A `wbgetclaims` payload is third-party input, so it can
name any number of distinct director QIDs; past fifty, `directors_for` answers `None`
without making the labels request at all. The API would refuse the whole batch, and
trimming the batch to fit would render exactly the half-correct credit the
`languagefallback` section rules out -- a partial credit reads as a fact. The check is
on **distinct** ids, after the same deduplication that keeps a statement carried twice
from reading "X & X" (kept linear with a set: a membership test on the growing list
took seconds on a payload of tens of thousands of statements). `tests/test_label_batch_limit.py`
pins the boundary from both sides and that repeated statements count once.

## The Action API rate-limits too

Observed live while this tool was written: `HTTP 429` with `x-envoy-ratelimited: true`
and a `Retry-After` header of 16-47 seconds, after enough requests from one address in
one window. A default refresh -- one film, three requests -- is nowhere near it. A batch
of back-to-back lookups is, so anything that resolves many films should space them and
honor `Retry-After`.

This is also why the live-boundary test probes availability **twice**: one lookup is
three requests, so the limit can arrive between them, long after the first probe passed.
Re-establishing the precondition before calling a `None` a broken contract is what keeps
a rate limit from being reported as "the contract moved".

## Exit codes

- `0` -- printed the director(s) to stdout.
- `1` -- no director was resolved. No Wikidata item, no director statement, no label, or
  the service was unreachable; all four are normal outcomes, not errors. The diagnostic
  goes to stderr, so stdout stays empty and a pipeline reads nothing.

## Usage

```text
python wikidata_director.py --tmdb-id TMDB_ID [--timeout TIMEOUT]
```

`--tmdb-id` is required; run `--help` for the current default of every optional argument
(this doc does not restate them, so it cannot drift from the parser):

| Option      | Required | Description                                                            |
| ----------- | -------- | ---------------------------------------------------------------------- |
| `--tmdb-id` | yes      | The film's TMDB movie id, as carried by the Letterboxd diary feed.     |
| `--timeout` | no       | Per-request timeout in seconds, applied to each of the three requests. |

The timeout bounds each socket operation of a request, because that is the only place
a bound can interrupt a call already in flight. Each response body is then read through
`feed_read.read_body`, which caps its size and ends a read that is still arriving past
its deadline (`feed_read.md`, "The three bounds on a fetch"); both refusals reach
`_api_object` as exceptions it already turns into `{}`, so an oversized or dripping
response is one more `None`.

## Worked example

`samples/README.md` holds the real captured run -- the command, its stdout, and its exit
code -- for a co-directed film, a single-director film, and an id Wikidata does not
carry.

## Running the test suite on a host with no network

Set `WIKIDATA_DIRECTOR_OFFLINE=1` to skip the one live-boundary class in
`tests/test_live_wikidata.py`, for a CI runner or any host with no network access, where
a live test could never pass regardless of whether the code is correct.

This is a convenience for running the rest of the suite quickly, **not** a way to satisfy
`python_gate` on this folder: skipping that class also means `_real_fetch` -- the module's
only real-network seam, and the only code besides the CLI entry point excluded from
coverage -- never runs at all. The normal way to gate this folder is with the variable
unset, on a machine with network access.
