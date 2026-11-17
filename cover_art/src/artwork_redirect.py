"""The redirect rule for cover_art's one real fetch: follow a hop only to https.

urllib's stock opener follows a 3xx onto plain `http:` or `ftp:`. cover_art's first hop
is always a fixed https endpoint, but every later hop is the server's choice, and the
reply read off a downgraded one would be parsed as the store's own, choosing or nulling
a cover. This copies the https half of the rule the shared `bounded_fetch` library
holds for the feed adapters. It leaves out three of that library's refusals, so it
follows a hop bounded_fetch would refuse when the URL carries userinfo, a port that
cannot be read, or any raw whitespace or Unicode `C*` character. The first two guard
headers this fetch never sends (`cover_art.md` says why). The third is a deliberate
known difference, left out on purpose and pinned case by case by the parity test,
rather than a hop this copy guards. It does refuse a backslash anywhere in the netloc,
as that library does, since a browser reads one as `/`.
cover_art depends on nothing outside its own folder (`cover_art.md`, "Why this is its
own folder"), so it keeps this small copy rather than a sys.path bootstrap.
tests/test_artwork_redirect.py proves it through the real opener, and
tests/test_fetch_parity.py holds it to bounded_fetch's handler on every case it lists
and asserts each of the three differences, so a change on either side fails a test.
"""

from __future__ import annotations

import http.client
import urllib.parse
import urllib.request
from typing import IO

#: The refusal names the rule, never the target, which may carry the query it was sent.
_REFUSAL = "refusing a redirect to anything but an https URL with a host"


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to an https URL with a host; refuse it otherwise."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """The next hop; `ValueError`, closing the unread response, unless https."""
        if not _is_https_with_host(newurl):
            fp.close()
            raise ValueError(_REFUSAL)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_https_with_host(url: str) -> bool:
    """Whether `url` is https and names a host; False for one `urlsplit` cannot parse.

    A backslash anywhere in the netloc is refused too, as bounded_fetch refuses it:
    `urlsplit` reads it as a host character while a browser reads it as `/`, so the
    host this check saw need not be the one the hop goes to.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (
        parts.scheme.lower() == "https"
        and bool(parts.hostname)
        and "\\" not in parts.netloc
    )
