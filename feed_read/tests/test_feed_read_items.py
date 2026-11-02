"""Reading `<item>` elements out of an RSS body, and the entity-expansion guard.

The DOCTYPE bodies below are what a hostile feed would carry; the guard must refuse each
at the parser's own DOCTYPE event, in every encoding expat detects, before any entity is
expanded.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above


_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>A feed</title>
  <item><title>One</title></item>
  <item><title>Two</title></item>
</channel></rss>
"""


# A classic "billion laughs" shape: a DOCTYPE declaring an internal entity. The guard
# must reject this on the DOCTYPE alone, before ET.fromstring ever sees it; there is
# no need for the entity to actually be referenced anywhere in the body.
_DOCTYPE_FEED = b"""<?xml version="1.0"?>
<!DOCTYPE rss [<!ENTITY x "y">]>
<rss version="2.0"><channel><item><title>Bad</title></item></channel></rss>
"""


# The same shape as text, so it can be encoded in each width expat auto-detects. In
# UTF-16 every ASCII letter is interleaved with a NUL byte, so no raw-bytes search
# for `<!DOCTYPE` can see it, while expat still parses it and expands its entity.
_DOCTYPE_TEXT = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE rss [<!ENTITY x "y">]>'
    '<rss version="2.0"><channel><item><title>&x;</title></item></channel></rss>'
)


# A "<!doctype" string inside a CDATA section: inert text, not a declaration. It
# parses to one item whose title is the literal CDATA string, and a guard that reads
# the parser's own DOCTYPE event never sees it as markup.
_CDATA_LOOK_ALIKE_FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><item><title><![CDATA[<!doctype fake>]]></title></item></channel></rss>
"""


class TestFeedItems(unittest.TestCase):
    """Reading `<item>` elements out of an RSS body."""

    def test_it_returns_every_item_in_order(self) -> None:
        items = feed_read.feed_items(_FEED)
        self.assertEqual([i.findtext("title") for i in items], ["One", "Two"])

    def test_a_feed_with_no_channel_yields_nothing(self) -> None:
        # A vendor error page served as XML must be an empty result, not a crash:
        # the caller's contract is "no card this refresh", never a traceback.
        self.assertEqual(feed_read.feed_items(b"<rss></rss>"), [])

    def test_a_normal_feed_with_no_doctype_still_parses(self) -> None:
        # The non-DOCTYPE control case for the entity-expansion guard, kept apart from
        # test_it_returns_every_item_in_order above, which exists for ordering.
        items = feed_read.feed_items(_FEED)
        self.assertEqual(len(items), 2)

    def test_a_doctype_bearing_body_is_rejected(self) -> None:
        # Refused on the DOCTYPE outright, not parsed and inspected for entities
        # afterward: an entity declaration cannot exist without one.
        with self.assertRaises(ValueError):
            feed_read.feed_items(_DOCTYPE_FEED)

    def test_a_doctype_is_refused_in_every_encoding_expat_detects(self) -> None:
        # "utf-16" writes a byte-order mark; the other two do not, and expat detects
        # them from the NUL beside the leading `<`. Each is a body a raw-bytes match
        # passes and stock ElementTree expands; the parser's own DOCTYPE event fires
        # after expat has settled the encoding, so refusing there covers all three.
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            with (
                self.subTest(encoding=encoding),
                self.assertRaisesRegex(ValueError, "DOCTYPE declaration"),
            ):
                feed_read.feed_items(_DOCTYPE_TEXT.encode(encoding))

    def test_the_refusal_comes_at_the_doctype_start_not_after_its_subset(self) -> None:
        # The internal subset is where the entities live, so the refusal must land
        # before expat reads one byte of it. A subset cut off mid-declaration is
        # malformed XML; getting the DOCTYPE refusal for it rather than "not
        # parseable" proves the guard fired first. UTF-16, so no byte match can
        # answer for the parser.
        body = '<?xml version="1.0"?><!DOCTYPE rss [ <!ENTITY broken'.encode(
            "utf-16-le"
        )
        with self.assertRaisesRegex(
            ValueError, "^refusing to parse XML with a DOCTYPE"
        ):
            feed_read.feed_items(body)

    def test_a_doctype_look_alike_inside_cdata_is_inert_text(self) -> None:
        # A guard that reads the parser's DOCTYPE event sees markup, not bytes, so
        # a look-alike string in element text is the ordinary text it always was.
        items = feed_read.feed_items(_CDATA_LOOK_ALIKE_FEED)
        self.assertEqual([i.findtext("title") for i in items], ["<!doctype fake>"])

    def test_a_lowercase_doctype_keyword_is_still_refused(self) -> None:
        # Control, not a regression case: expat requires the exact-case keyword, so
        # a lowercase declaration is malformed XML and reaches the caller as the
        # same ValueError a DOCTYPE does, so there is no case a hostile body can pick
        # that parses.
        with self.assertRaises(ValueError):
            feed_read.feed_items(b'<?xml version="1.0"?><!doctype rss><rss/>')

    def test_malformed_xml_raises_a_value_error(self) -> None:
        with self.assertRaises(ValueError):
            feed_read.feed_items(b"not xml at all <<<")
