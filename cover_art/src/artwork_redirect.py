"""The redirect rule for cover_art's one real fetch: follow a hop only to https.

urllib's stock opener follows a 3xx onto plain `http:` or `ftp:`. cover_art's first hop
is always a fixed https endpoint, but every later hop is the server's choice, and the
reply read off a downgraded one would be parsed as the store's own, choosing or nulling
a cover. This copies only the https half of the rule the shared `bounded_fetch` library
holds for the feed adapters: it does not refuse userinfo or an unreadable port, since
this fetch sends no header worth guarding (`cover_art.md` says why). cover_art depends
on nothing outside its own folder (`cover_art.md`, "Why this is its own folder"), so it
keeps this small copy rather than a sys.path bootstrap, and
tests/test_artwork_redirect.py proves it through the real opener.
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
    """Whether `url` is https and names a host; False for one `urlsplit` cannot parse."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() == "https" and bool(parts.hostname)
