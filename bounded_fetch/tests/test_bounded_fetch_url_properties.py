"""Grouped suite: `https_url_or_none`'s security contract over arbitrary input.

`https_url_or_none` carries the `# fuzz-target` marker `fuzz_gate` looks for, and
`test_fuzz_https_url_or_none` is the property that satisfies it. Anything the rule
accepts is what a record may carry and what a request may be built from, so the
property holds every accepted string to the whole contract -- https with a host,
no userinfo, no space, control or format character -- over arbitrary text and over
URL-shaped strings built from schemes, userinfo, hosts, slashes and backslashes.
The rule never raises: a string it cannot parse is "no URL".
"""

from __future__ import annotations

import sys
import unicodedata
import unittest
import urllib.parse
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bounded_fetch  # noqa: E402 - import follows the sys.path bootstrap above

# The fuzz_gate.md determinism convention: a registered profile loaded at import.
# deadline=None: a wall-clock deadline measures machine load, not correctness.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: The pieces a URL-shaped string is built from, each biased toward a way a URL can
#: lie about its scheme, its host or who it logs in as.
_SCHEMES = st.sampled_from(
    ["https", "HTTPS", "hTtPs", "http", "javascript", "data", "file", "ftp", ""]
)
_SEPARATORS = st.sampled_from(["://", ":", ":/", ":///", ":\\\\", "://\\", "//"])
_USERINFO = st.sampled_from(["", "u@", "u:p@", ":@", "@", "a.b\\@", "u@v@"])
#: Host and path characters: delimiters, brackets, a backslash, and spaces,
#: controls and format characters from ASCII, C1 and wider Unicode.
_URL_CHARS = st.sampled_from(
    [*"ab.-:@[]/?#%\\ ", "\t", "\n", "\r", "\x00", "\x1b", "\x85", "\xa0"]
    + ["​", "‮", " ", "　"]
)
_URL_TEXT = st.text(alphabet=_URL_CHARS, max_size=12)
#: What may pad a URL on either side: characters `urlsplit` strips on its own.
_PADDING = st.text(alphabet=st.sampled_from(" \t\x00\x1f"), max_size=2)
_URL_SHAPED = st.builds(
    lambda *parts: "".join(parts),
    _PADDING,
    _SCHEMES,
    _SEPARATORS,
    _USERINFO,
    _URL_TEXT,
    _URL_TEXT,
    _PADDING,
)
#: An otherwise valid https URL with ONE character from the whole alphabet planted
#: in it. Most URL-shaped strings already fail on their scheme or on a second bad
#: character, so this is the search that reaches each check on its own.
_SAFE_TEXT = st.text(alphabet=st.sampled_from([*"ab.-/?#%"]), max_size=6)
_HTTPS_SHAPED = st.builds(
    "https://a{}{}{}".format, _SAFE_TEXT, _URL_CHARS | st.characters(), _SAFE_TEXT
)


def _is_unsafe_character(char: str) -> bool:
    """True for any space, and any control, format, surrogate, private or unassigned."""
    return char.isspace() or unicodedata.category(char).startswith("C")


class HttpsUrlProperties(unittest.TestCase):
    """Anything `https_url_or_none` accepts is a whole https URL with a host, as typed."""

    @given(st.one_of(st.text(), _URL_SHAPED, _HTTPS_SHAPED))
    def test_fuzz_https_url_or_none(self, url: str) -> None:
        """Accepted means verbatim, https, a host, no userinfo, no unsafe character."""
        accepted = bounded_fetch.https_url_or_none(url)
        if accepted is None:
            return
        self.assertIs(accepted, url)
        self.assertEqual([c for c in url if _is_unsafe_character(c)], [], url)
        # Checked on the raw text as well as through the parser: the raw prefix is
        # what any other reader of the record (a browser, a later tool) sees first.
        self.assertEqual(url[: len("https://")].lower(), "https://", url)
        parts = urllib.parse.urlsplit(url)
        self.assertEqual(parts.scheme, "https")
        self.assertTrue(parts.hostname, url)
        self.assertNotIn("@", parts.netloc, url)


if __name__ == "__main__":
    unittest.main()
