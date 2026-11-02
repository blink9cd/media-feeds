"""`--artwork-px`'s floor: an edge length below one is a usage error, refused at parse time.

The store's thumbnailer would happily mint a `0x0bb.webp` or `-5x-5bb.webp` URL into
a committed record, so the floor lives in the parser, where a non-integer is already
refused, and lands before either boundary seam runs. Every `main` call injects both
seams with fakes that raise, which is what proves neither was reached.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import no_music, no_store  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import main  # noqa: E402 - import follows the sys.path bootstrap above


class ArtworkPxFloorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # A real album, so a value that got past the parser would go on to reach a
        # seam and fail the test there rather than exiting 1 on an empty library.
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        self.out = self.root / "album.json"

    def _run(self, extra: list[str]) -> tuple[int, str]:
        """Drive `main`; a parser refusal's SystemExit is folded into the exit code."""
        stderr = io.StringIO()
        args = [
            "--library",
            str(self.root),
            "--output",
            str(self.out),
            "--source",
            "folder",
            *extra,
        ]
        try:
            with redirect_stderr(stderr):
                code = main(args, fetch=no_store, run=no_music)
        except SystemExit as exc:
            # argparse's own usage-error exit always passes an int status.
            assert isinstance(exc.code, int)
            code = exc.code
        return code, stderr.getvalue()

    def test_a_size_below_one_is_a_usage_error_with_nothing_written(self) -> None:
        for bad in ("0", "-5"):
            with self.subTest(px=bad):
                code, stderr = self._run(["--artwork-px", bad])
                self.assertEqual(code, 2)
                self.assertIn("argument --artwork-px: must be at least 1", stderr)
                self.assertFalse(self.out.exists())

    def test_a_size_of_one_is_accepted(self) -> None:
        code, _ = self._run(["--artwork-px", "1", "--no-artwork", "--no-favorites"])
        self.assertEqual(code, 0)
        self.assertTrue(self.out.exists())


if __name__ == "__main__":
    unittest.main()
