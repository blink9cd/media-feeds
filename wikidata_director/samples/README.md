# wikidata_director samples

Hand-verification for `wikidata_director`: three real, captured runs (a co-directed
film, a single-director film, and an id Wikidata does not carry), so a human can confirm
the tool in one command instead of reading its tests.

## Why there is no committed output file here

A samples directory normally pairs a committed input with the output it must reproduce.
This one cannot, honestly. The tool's input is a TMDB id and its output is a live
third-party answer; a captured response pinned as a fixture would re-prove the parser
(which `../test_wikidata_director.py` already covers, against fakes, at 100% branch
coverage) and prove nothing at all about the boundary. What is worth verifying by hand
is the part a fixture removes: that the real Action API still answers, and still
answers with what this tool reads. So the sample is a transcript, and
`../tests/test_live_wikidata.py` is its automated twin.

The three ids below are public film credits for well-known films. They identify films,
not anyone using this tool, and none of them is taken from anyone's viewing history.

## A co-directed film

From this project's root:

```bash
python wikidata_director/wikidata_director.py --tmdb-id 12
```

Real run, captured verbatim:

```text
Andrew Stanton & Lee Unkrich
```

Exit code `0`. This is the case worth running first: co-directors are not an edge case
here, and this run exercises the `&` join, the sort, and the one-request-for-all-names
label lookup at once.

## A single-director film

```bash
python wikidata_director/wikidata_director.py --tmdb-id 550
```

```text
David Fincher
```

Exit code `0`.

## A film Wikidata does not carry

```bash
python wikidata_director/wikidata_director.py --tmdb-id 999999999
```

Nothing on stdout. On stderr:

```text
wikidata_director: no director resolved for TMDB id 999999999
```

Exit code `1`. This is a normal outcome, not a failure of the tool: the card simply
omits the director line. The message goes to stderr precisely so a caller reading stdout
gets an empty answer rather than a sentence to parse.

## What you should see

- The name, or names, alone on stdout: no prefix, no label, nothing to strip. That
  is what makes the tool composable, and it is asserted by
  `../tests/test_wikidata_director_cli.py`.
- Co-directors joined with `&` and in alphabetical order, not in the order
  Wikidata happens to return the statements.
- Exit `0` when a name was printed, `1` when none was.

## If a run comes back empty when you expected a name

Check, in this order:

1. **A rate limit.** The Action API answers `HTTP 429` after enough requests from one
   address in a window, and this tool reports that as "no director" by design. Wait
   half a minute and run it again; `wikidata_director.md` has the measurements.
2. **A genuine Wikidata gap.** The film may have an item with no `P57` statement, or a
   director whose item has no usable label. Both are upstream data gaps; open the item
   on wikidata.org and look.
3. **A moved contract.** If neither of the above explains it, run
   `python -m unittest tests.test_live_wikidata` from the tool folder: it fails, rather
   than skipping, when the contract has moved.
