"""Resolve a film's director(s) from its TMDB id, through Wikidata, with no API key.

The Letterboxd diary feed already carries a `<tmdb:movieId>` for every entry, so this
is an EXACT lookup by identifier, not a title-and-year guess. Wikidata was chosen over
the TMDB API itself for one reason: it needs **no credential** -- nothing to keep out
of the repository, nothing to rotate, and the tool stays runnable from a fresh clone.

WHY THE ACTION API AND NOT SPARQL. Both were tested against the live services. The
query service (WDQS) answered correctly and then began returning `HTTP 429` with
"aggressively rate-limiting to 1 req / min - this rule was created during active wdqs
outage". One query per refresh survives that, but a service in a declared outage is
no contract to build on when a stable alternative answers the same question. The
Action API served the same lookups without complaint, at the cost of three requests
instead of one -- nothing for a hand-run tool that, by default, resolves one film
per refresh:

1. `list=search` for `haswbstatement:P4947=<tmdb id>` -> the film's QID.
2. `wbgetclaims` for `P57` on that QID -> the director QIDs.
3. `wbgetentities` for those QIDs' English labels -> their names. WITH
   `languagefallback=1`: see `labels_url` -- without it, a real co-director was
   measured missing.

A DESCRIPTIVE `User-Agent` IS REQUIRED, not optional -- see `_USER_AGENT`.

NEVER RAISES. A missing director is a NORMAL outcome, not an error: no Wikidata item,
no `P57` statement, no English label, a rate limit, a maintenance page served as HTML,
a socket that never opened -- every one of them comes back as `None`, and the card
simply omits the line. That is why the caller (`film_scan`) needs no error handling
of its own.

`film_scan` imports this through its repository-root bootstrap. It has its own tool
folder because a script with its own test and doc gets one folder, never a slot
inside another tool's.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

# Put the vendored libraries (stdio_utf8, help_formatter, bounded_fetch) and the
# sibling feed_read tool folder on sys.path from this repository's root, the folder
# above this file's own. One fixed candidate per name, deliberately: a search up the
# ancestors would also take any same-named folder ABOVE the checkout and put it ahead
# of the standard library, where a module planted in it would be imported in place
# of the real one (stdio_utf8/tests/test_bootstrap.py plants exactly that).
sys.path += [
    str(Path(__file__).resolve().parents[1] / name)
    for name in ("stdio_utf8", "help_formatter", "bounded_fetch", "feed_read")
]

import feed_read
import help_formatter
import stdio_utf8

#: The injection seam. `(url, timeout_seconds) -> response body`, deliberately the
#: shape of `feed_read.Fetcher`, so every tool in this project takes the same fakes
#: and no unit test reaches the network.
Fetcher = Callable[[str, float], bytes]

#: The Wikidata Action API. No key, no account, no quota to register -- verified live.
API_ENDPOINT = "https://www.wikidata.org/w/api.php"

#: Wikidata's "TMDb movie ID" property: the statement this lookup searches ON.
TMDB_MOVIE_PROPERTY = "P4947"

#: Wikidata's "director" property: the statement this lookup reads OFF the film.
DIRECTOR_PROPERTY = "P57"

#: How co-directors are joined. The film-credit convention, and deliberately NOT the
#: middle dot the card uses to separate its own FIELDS -- joining with that would make
#: one co-directed film read as two card fields.
DIRECTOR_SEPARATOR = " & "

#: The most ids one `wbgetentities` request may carry: the Action API's per-request
#: limit for a client without the bot right. A film with more distinct director
#: statements than this gets no labels request and no credit -- the API would refuse
#: the whole batch, and a batch trimmed to fit would be the partial credit
#: `labels_url` explains is worse than none.
LABELS_PER_REQUEST = 50

#: Bounds each socket operation of a request, because that is the only place a bound
#: can interrupt a call already in flight; `feed_read.read_body` bounds what a
#: response's reads add up to, in bytes and in seconds. Headroom rather than a
#: measured need: the three responses are small JSON.
LOOKUP_TIMEOUT_SECONDS = 15.0

#: Sent on every request. Wikimedia rejects a request carrying no descriptive
#: User-Agent, and urllib's own default ("Python-urllib/3.x") is exactly the shape
#: that gets rejected. An honest product identifier is enough -- do NOT "fix" a future
#: rejection by impersonating a browser. It names no person, host, or account, so it
#: puts no personal data in the repository.
_USER_AGENT = "media-feeds-wikidata-director/1.0"

#: Exit codes. `main` returns these; they are the tool's contract with a caller.
EXIT_OK = 0
EXIT_NO_DIRECTOR = 1

#: Everything a decoded response can go wrong with. `urllib.error.HTTPError` (so every
#: `429` and `503`) is an `OSError` subclass and is covered by the first entry;
#: `ValueError` covers a body that is not JSON at all; `RecursionError` covers a
#: pathologically nested one, which `json.loads` raises rather than returning.
_DECODE_FAILURES = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    http.client.HTTPException,
    RecursionError,
)


def _api_url(**params: str) -> str:
    """One Action API URL with `params` percent-encoded onto it.

    `format=json` is not optional: without it the API answers with its own HTML help
    rendering, which the parser would then be handed as a "response".
    """
    query = urllib.parse.urlencode({"format": "json", **params})
    return f"{API_ENDPOINT}?{query}"


def search_url(tmdb_id: str) -> str:
    """The URL that finds the Wikidata item carrying `tmdb_id` as its TMDb movie ID.

    `haswbstatement` searches on the STATEMENT, so this is an exact identifier lookup
    rather than a title match. The id is percent-encoded by `_api_url`, which keeps a
    value arriving from the feed from reshaping the URL -- but not the search
    EXPRESSION it lands in, whose grammar encoding does not touch:
    `12 OR haswbstatement:P4947=550` is a valid two-film query. That is why
    `directors_for` admits only an all-ASCII-digit id before this is ever built.
    """
    return _api_url(
        action="query",
        list="search",
        srsearch=f"haswbstatement:{TMDB_MOVIE_PROPERTY}={tmdb_id}",
    )


def claims_url(qid: str) -> str:
    """The URL that returns just the director statements of the item `qid`."""
    return _api_url(action="wbgetclaims", entity=qid, property=DIRECTOR_PROPERTY)


def labels_url(qids: Sequence[str]) -> str:
    """The URL that returns the English labels of every item in `qids`.

    One request for all of them, not one per director: the API takes a pipe-separated
    id list, so a co-directed film still costs three requests in total.

    `languagefallback=1` IS LOAD-BEARING, and its absence was measured as a real
    defect rather than guessed at. Wikidata moved a large class of person names to the
    `mul` (multilingual) language code -- a name spelled identically in every
    Latin-script language is stored once there instead of per language -- and a plain
    `languages=en` request returns an EMPTY labels object for such an item. Lana
    Wachowski (Q9545711) is one, so The Matrix resolved to "Lilly Wachowski" ALONE:
    a half-correct credit, which is worse than none, because it reads as a fact. With
    the fallback the value still arrives under the `en` key (annotated with the
    language it came from), so nothing downstream changes.
    """
    return _api_url(
        action="wbgetentities",
        ids="|".join(qids),
        props="labels",
        languages="en",
        languagefallback="1",
    )


def _build_request(url: str) -> urllib.request.Request:
    """A GET request for `url`, carrying the User-Agent Wikimedia requires.

    Separating request CONSTRUCTION from sending it (`_real_fetch`) makes the header
    provable with no network, so a dropped or reverted User-Agent is caught at test
    time instead of surfacing as a live rejection that reads like a network fault.
    """
    return urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})


def _real_fetch(url: str, timeout: float) -> bytes:
    """Fetch a URL: `timeout` bounds each socket operation, `read_body` the body.

    The ONE real-network seam. Every unit test injects a fake `fetch` instead, so this
    is reachable only from a real run or from the live-boundary suite -- which is why
    the body below is excluded from coverage, and why `tests/test_live_wikidata.py`
    exercises it end to end rather than leaving it unproven. The body is read
    through `feed_read.read_body`, which caps its size and ends a read still
    arriving past its deadline; both refusals are exceptions `_api_object` already
    turns into `{}`, so the never-raises contract holds through them.
    """
    # A pragma on each line, since none runs under a unit test. `_build_request` is
    # covered by RequestBuildingTest and `read_body` by feed_read's own suite -- only
    # opening the connection is not.
    request = _build_request(url)  # pragma: no cover
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https endpoint  # pragma: no cover
        return feed_read.read_body(response)  # pragma: no cover


def _api_object(
    build_url: Callable[[], str], fetch: Fetcher, timeout: float
) -> dict[str, object]:
    """One API request decoded as a JSON object, or `{}` on any failure.

    Never raises -- this is where the module's whole contract is enforced, once, for
    all three requests. `{}` rather than `None` so every caller below can read it with
    `.get` and needs no separate "did the request work" branch.

    It takes a URL BUILDER, not a URL, and that is load-bearing. Every URL is built from
    a value this module does not control -- the caller's TMDB id, then a QID out of one
    response, then QIDs out of the next -- and `urlencode` raises `UnicodeEncodeError`
    on an unpaired surrogate. POSIX argv delivers one through surrogateescape, and any
    server can send one, since JSON's own unicode escapes can spell it and `json.loads`
    decodes it without complaint. Passed a finished URL, that raise would happen while
    the ARGUMENT was evaluated, before this `try` began, and escape `directors_for`
    although `ValueError` -- its base class -- is caught here.
    """
    try:
        payload = json.loads(fetch(build_url(), timeout))
    except _DECODE_FAILURES as failure:
        # An HTTP error is still a RESPONSE: urllib hands the error page over inside
        # the exception, so the object owns an open body. This function discards it,
        # so this function has to close it -- dropped unclosed, it leaks the connection
        # and surfaces later as a ResourceWarning raised nowhere near its cause.
        # Nothing else in `_DECODE_FAILURES` owns a stream, hence the narrow check.
        if isinstance(failure, urllib.error.HTTPError):
            failure.close()
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _film_qid(payload: dict[str, object]) -> str | None:
    """The QID of the first search hit, or None when the search found no item.

    Every level is type-checked rather than indexed: this is third-party JSON, and an
    error page, a maintenance response, or a future schema change must degrade to
    "no director" instead of raising.
    """
    query = payload.get("query")
    if not isinstance(query, dict):
        return None
    hits = query.get("search")
    if not isinstance(hits, list) or not hits:
        return None
    head = hits[0]
    if not isinstance(head, dict):
        return None
    title = head.get("title")
    if not isinstance(title, str) or not title:
        return None
    return title


def _statement_item_id(statement: object) -> str | None:
    """The item QID one `P57` statement points at, or None if it points at no item.

    A statement can legitimately carry a non-item value -- an "unknown value" or "no
    value" snak has no `datavalue` at all -- so this returns None for that shape
    rather than treating it as malformed.
    """
    if not isinstance(statement, dict):
        return None
    snak = statement.get("mainsnak")
    if not isinstance(snak, dict):
        return None
    datavalue = snak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    value = datavalue.get("value")
    if not isinstance(value, dict):
        return None
    item_id = value.get("id")
    if not isinstance(item_id, str) or not item_id:
        return None
    return item_id


def _director_qids(payload: dict[str, object]) -> list[str]:
    """Every distinct director QID in a `wbgetclaims` payload, in statement order.

    Deduplicated because Wikidata can carry the same value twice under different
    qualifiers or ranks, and "X & X" would be a visibly wrong card line. The set
    beside the list is what keeps that linear: a membership test on the list made a
    payload of tens of thousands of statements -- third-party input, so possible --
    take seconds rather than milliseconds.
    """
    claims = payload.get("claims")
    if not isinstance(claims, dict):
        return []
    statements = claims.get(DIRECTOR_PROPERTY)
    if not isinstance(statements, list):
        return []
    qids: list[str] = []
    seen: set[str] = set()
    for statement in statements:
        item_id = _statement_item_id(statement)
        if item_id is None:
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        qids.append(item_id)
    return qids


def _english_label(entity: object) -> str | None:
    """One entity's English label, or None when it has none this lookup can use.

    "English" means whatever `languages=en` plus the language fallback resolved to; a
    fallback hit still arrives under the `en` key (see `labels_url`).
    """
    if not isinstance(entity, dict):
        return None
    labels = entity.get("labels")
    if not isinstance(labels, dict):
        return None
    english = labels.get("en")
    if not isinstance(english, dict):
        return None
    value = english.get("value")
    if not isinstance(value, str) or not value:
        return None
    return value


def _director_names(payload: dict[str, object], qids: Sequence[str]) -> list[str]:
    """The English label of each QID that has one; the rest are dropped, not faked.

    An unlabeled director is skipped rather than rendered as its QID: "Q3772" on a
    card is worse than a shorter list, and worse still than no line at all.
    """
    entities = payload.get("entities")
    if not isinstance(entities, dict):
        return []
    names: list[str] = []
    for qid in qids:
        name = _english_label(entities.get(qid))
        if name is None:
            continue
        names.append(name)
    return names


def _is_tmdb_id(text: str) -> bool:
    """Whether `text` is a plain decimal id: ASCII digits only, and at least one.

    `str.isdigit` alone is not enough -- it accepts a superscript two and the other
    Unicode digit characters -- so the ASCII test is what pins the value to the ten
    characters an id can hold.
    """
    return text.isascii() and text.isdigit()


# fuzz-target
def directors_for(
    tmdb_id: str,
    *,
    fetch: Fetcher = _real_fetch,
    timeout: float = LOOKUP_TIMEOUT_SECONDS,
) -> str | None:
    """The film's director(s) as one display string, or None when there is no answer.

    Never raises -- see the module docstring. Co-directors are joined with
    `DIRECTOR_SEPARATOR` and **sorted**: Wikidata does not guarantee statement order,
    so an unsorted join would write a different string into the record on different
    days for the same unchanged film.

    Short-circuits at each step, so a film with no Wikidata item costs one request and
    a film with no director statement costs two -- the third is only made once there
    is something to label, and something one request can label: more distinct
    directors than `LABELS_PER_REQUEST` is None at two requests as well. An id that
    is not made of ASCII digits costs none at all.
    """
    if not _is_tmdb_id(tmdb_id):
        # The id lands in a search EXPRESSION (see search_url), where encoding
        # cannot stop a search operator from changing the query. A feed value that is
        # not a decimal id is refused before any request, as the None a missing
        # director already is.
        return None
    qid = _film_qid(_api_object(lambda: search_url(tmdb_id), fetch, timeout))
    if qid is None:
        return None
    qids = _director_qids(_api_object(lambda: claims_url(qid), fetch, timeout))
    if not qids:
        return None
    if len(qids) > LABELS_PER_REQUEST:
        # Not a credit one request can label whole, so no request: see the
        # constant for why a trimmed batch is not the answer either.
        return None
    names = _director_names(_api_object(lambda: labels_url(qids), fetch, timeout), qids)
    if not names:
        return None
    return DIRECTOR_SEPARATOR.join(sorted(names))


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    return _add_arguments(
        argparse.ArgumentParser(
            description=(
                "Resolve a film's director(s) from its TMDB id, via Wikidata, "
                "with no API key."
            ),
            # The shared formatter, not argparse's stock one: below CPython 3.13 the stock
            # formatter stamps a misleading "(default: None)" on a REQUIRED option, and this
            # project's floor is 3.12 (pyproject.toml's [project].requires-python).
            formatter_class=help_formatter.ReqAwareDefaultsFormatter,
            epilog=(
                "Prerequisites: network access to the Wikidata Action API. No key, "
                "no account, and no quota registration -- which is why this tool "
                "exists instead of a TMDB API client.\n\n"
                "Run order: this is a lookup, not a refresh step. film_scan calls "
                "directors_for() directly when it writes the film record; run this "
                "CLI by hand to check one id, or to reproduce the samples/ pair.\n\n"
                "Exit codes: 0 printed the director(s) - 1 no director was resolved "
                "(no Wikidata item, no director statement, no English label, or the "
                "service was unreachable; all four are normal outcomes, not errors)."
            ),
        )
    )


def _add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach this tool's options to `parser` and return it."""
    parser.add_argument(
        "--tmdb-id",
        required=True,
        help="The film's TMDB movie id, as carried by the Letterboxd diary feed.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=LOOKUP_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds, applied to each of the three requests.",
    )
    return parser


def main(argv: list[str] | None = None, *, fetch: Fetcher = _real_fetch) -> int:
    """Print the film's director(s) and return the exit code.

    The resolved names go to stdout ALONE, so the tool composes in a pipeline;
    anything conversational goes to stderr. `fetch` is the same injection seam
    `Fetcher` names -- every unit test replaces it.
    """
    # First statement, before anything is printed: this tool's output is captured by a
    # pipe as often as it is read in a terminal, and a locale-derived encoding would
    # fail on any non-ASCII director name -- which is most of the world's.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    args = build_parser().parse_args(argv)

    names = directors_for(args.tmdb_id, fetch=fetch, timeout=args.timeout)
    if names is None:
        print(
            f"wikidata_director: no director resolved for TMDB id {args.tmdb_id}",
            file=sys.stderr,
        )
        return EXIT_NO_DIRECTOR

    print(names)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
