"""The CLI's exit codes and output, driven end to end over a synthetic library.

The --source dispatch cases live in test_source_selection.py; the two files share no
fixture, so each keeps its own helpers.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import (  # noqa: E402 - import follows the sys.path bootstrap above
    music_answering,
    needs_toolbox,
    no_store,
    posix_filesystem_only,
)
from album_scan import (  # noqa: E402 - import follows the sys.path bootstrap above
    build_parser,
    main,
)


# os.geteuid exists only off Windows, and the gate type-checks for win32 too, so the
# root check is a getattr rather than a bare attribute; root can list a mode-000
# directory, so the unreadable-root case skips rather than fails under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


#: A search result that VERIFIES as the album these tests build: the matcher refuses a
#: candidate it cannot verify, so a bare artworkUrl100 records a null cover.
_VERIFIABLE_RESULT = {
    "artistName": "An Artist",
    "collectionName": "A Record",
    "collectionId": 1,
    "releaseDate": "1994-01-01T00:00:00Z",
    "trackCount": 10,
    "artworkUrl100": "https://example.com/a/100x100bb.jpg",
}


def _music_says_nothing(
    _args: list[str], _timeout: float
) -> subprocess.CompletedProcess[str]:
    """A Music.app that answers, and has nothing to say about this album.

    `--source folder` never reaches the `run` seam, but every `main` call injects one
    anyway: a flag keeps a call off a boundary only while the code honoring it is
    correct. Uninjected, a stray call answers in ~0.2s on an awake machine and costs
    the full 60s timeout where the boundary stalls, measured turning a 47s unit into
    a 720s one on an unattended run.

    A non-zero exit is the faithful stand-in: `album_facts` maps it to `None`, which
    is also what a real Music.app yields for these invented albums, so the records
    these tests assert on are the same -- only the host call is gone.
    """
    return subprocess.CompletedProcess(
        args=["osascript"], returncode=1, stdout="", stderr="no such album"
    )


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_every_option_is_named_not_positional(self) -> None:
        parser = build_parser()
        positional = [
            a for a in parser._actions if not a.option_strings and a.dest != "help"
        ]
        self.assertEqual(positional, [], "CLI arguments must be named options")

    def test_help_states_every_default(self) -> None:
        text = build_parser().format_help()
        self.assertIn("--library", text)
        self.assertIn("default", text)

    def test_a_library_with_one_album_writes_the_record(self) -> None:
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        out = self.root / "album.json"
        code = main(
            [
                "--library",
                str(self.root),
                "--output",
                str(out),
                "--no-artwork",
                "--source",
                "folder",
            ],
            fetch=no_store,
            run=_music_says_nothing,
        )
        self.assertEqual(code, 0)
        # The written record file is always a JSON array, even at the default
        # --count of one -- index [0] rather than parsing it as a bare object.
        record = json.loads(out.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["artist"], "An Artist")
        self.assertIsNone(record["artwork"])

    def test_an_empty_library_exits_1_and_writes_nothing(self) -> None:
        out = self.root / "album.json"
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    str(out),
                    "--no-artwork",
                    "--source",
                    "folder",
                ],
                fetch=no_store,
                run=_music_says_nothing,
            )
        self.assertEqual(code, 1)
        self.assertFalse(out.exists(), "no album means no file, not an empty one")
        self.assertIn("no album", buffer.getvalue().lower())

    def test_a_missing_library_exits_2_and_says_so_without_the_path(self) -> None:
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(
                [
                    "--library",
                    str(self.root / "nope"),
                    "--output",
                    str(self.root / "a.json"),
                    "--no-artwork",
                    "--source",
                    "folder",
                ],
                fetch=no_store,
                run=_music_says_nothing,
            )
        self.assertEqual(code, 2)
        # Both halves: the message is THERE (an exit 2 with nothing said would leave
        # the operator guessing which path was wrong), and it names the folder, never
        # the absolute path -- the rule tests/test_discovery.py pins at the source.
        self.assertEqual(
            buffer.getvalue(), "album_scan: library root is not a directory: nope\n"
        )

    @posix_filesystem_only
    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 directory")
    def test_an_unreadable_library_exits_2_and_says_so_without_the_path(self) -> None:
        # A root that exists but cannot be listed is the same "fix the command line"
        # outcome as a missing one: exit 2 with one line naming the folder, never a
        # traceback and never the path.
        locked = self.root / "locked"
        locked.mkdir()
        self.addCleanup(os.chmod, locked, 0o755)
        os.chmod(locked, 0o000)
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(
                [
                    "--library",
                    str(locked),
                    "--output",
                    str(self.root / "a.json"),
                    "--no-artwork",
                    "--source",
                    "folder",
                ],
                fetch=no_store,
                run=_music_says_nothing,
            )
        self.assertEqual(code, 2)
        self.assertEqual(
            buffer.getvalue(), "album_scan: library root is not readable: locked\n"
        )

    def test_the_written_file_ends_with_a_newline(self) -> None:
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        out = self.root / "album.json"
        main(
            [
                "--library",
                str(self.root),
                "--output",
                str(out),
                "--no-artwork",
                "--source",
                "folder",
            ],
            fetch=no_store,
            run=_music_says_nothing,
        )
        self.assertTrue(out.read_text(encoding="utf-8").endswith("\n"))

    def test_artwork_format_reaches_the_recorded_url(self) -> None:
        """`--artwork-format` must reach the URL that is actually written, not stop at
        the parser. The default is webp; asking for jpg is what proves the option is
        threaded through rather than a constant being echoed back.
        """
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        out = self.root / "album.json"

        def fake_fetch(url: str, timeout: float) -> bytes:
            del url, timeout  # unused: the fake ignores what it was asked for
            # _VERIFIABLE_RESULT, not a bare artworkUrl100: the matcher refuses a
            # candidate that does not verify as this album, so a bare payload would
            # record null and this test would assert the format of a URL that was
            # never produced.
            return json.dumps({"results": [_VERIFIABLE_RESULT]}).encode("utf-8")

        code = main(
            [
                "--library",
                str(self.root),
                "--output",
                str(out),
                "--source",
                "folder",
                "--artwork-format",
                "jpg",
            ],
            fetch=fake_fetch,
            run=_music_says_nothing,
        )
        self.assertEqual(code, 0)
        record = json.loads(out.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["artwork"], "https://example.com/a/400x400bb.jpg")

    def test_an_unmeasured_artwork_format_is_refused_by_the_parser(self) -> None:
        # An unverified format is a claim about the CDN nobody has checked, so it dies
        # at parse time rather than becoming a URL in a committed record.
        with self.assertRaises(SystemExit) as caught:
            with redirect_stderr(io.StringIO()):
                build_parser().parse_args(["--artwork-format", "avif"])
        self.assertEqual(caught.exception.code, 2)

    def test_the_default_path_resolves_artwork_via_the_injected_fetch(self) -> None:
        """Drives the artwork-resolving arm of `main()` -- the one every DEFAULT
        invocation takes, with no `--no-artwork` flag and the default webp format --
        through the injected `fetch` seam, so it runs under test without reaching the
        network.
        """
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        out = self.root / "album.json"

        def fake_fetch(url: str, timeout: float) -> bytes:
            del url, timeout  # unused: the fake ignores what it was asked for
            return json.dumps({"results": [_VERIFIABLE_RESULT]}).encode("utf-8")

        code = main(
            ["--library", str(self.root), "--output", str(out), "--source", "folder"],
            fetch=fake_fetch,
            run=_music_says_nothing,
        )
        self.assertEqual(code, 0)
        record = json.loads(out.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["artwork"], "https://example.com/a/400x400bb.webp")

    @needs_toolbox
    def test_musics_facts_are_handed_to_the_artwork_matcher(self) -> None:
        """The arm where BOTH seams answer: Music.app reports a year and a track
        count, and they are unpacked and passed to the artwork lookup as the
        tiebreakers that decide which search result really is this album.

        Every other test drives at most one of the two seams -- the other artwork
        tests here let the Music query fail, and the counts tests in
        test_favorites_in_record.py pass --no-artwork -- so this is the only test that
        executes `artwork_for`'s `facts.year`/`facts.tracks` unpacking, the line that
        makes the verification possible.
        """
        out = self.root / "album.json"
        asked: list[str] = []
        # 1969 here against the store fixture's 1994, so the record's year names its
        # source; a matching pair would show only that SOME year arrived.
        fake_run = music_answering(
            "2026-08-20T10:00:00\tAn Artist\tA Record\n", "10\t2\t1969"
        )

        def fake_fetch(url: str, timeout: float) -> bytes:
            del timeout  # unused: the fake ignores how long it was given
            asked.append(url)
            return json.dumps({"results": [_VERIFIABLE_RESULT]}).encode("utf-8")

        with redirect_stderr(io.StringIO()):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    str(out),
                    "--source",
                    "music",
                ],
                fetch=fake_fetch,
                run=fake_run,
            )
        self.assertEqual(code, 0)
        record = json.loads(out.read_text(encoding="utf-8"))[0]
        # The counts came from the fake Music query and the cover from the fake store
        # reply, so both seams demonstrably ran in the same invocation.
        self.assertEqual(record["tracks"], 10)
        self.assertEqual(record["favorited"], 2)
        self.assertEqual(record["artwork"], "https://example.com/a/400x400bb.webp")
        self.assertTrue(asked, "the artwork lookup was never reached")
        # Music.app's tag wins over the store's release year, end to end: the name
        # match still admits the candidate (the year is a tiebreaker, never a gate),
        # and the record carries 1969, not the store's 1994. RecordCarriesYearTest
        # in test_favorites_in_record.py pins the same rule at the record boundary.
        self.assertEqual(record["year"], "1969")

    def test_no_artwork_never_calls_the_injected_fetch(self) -> None:
        """`--no-artwork` must record null WITHOUT ever invoking `fetch` -- a fetch
        that raises if called proves the CLI never reaches the network-resolving
        arm, rather than merely proving the two arms produce the same field.
        """
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        out = self.root / "album.json"

        def explode(url: str, timeout: float) -> bytes:
            del url, timeout
            raise AssertionError("fetch must not be called when --no-artwork is passed")

        code = main(
            [
                "--library",
                str(self.root),
                "--output",
                str(out),
                "--no-artwork",
                "--source",
                "folder",
            ],
            fetch=explode,
            run=_music_says_nothing,
        )
        self.assertEqual(code, 0)
        record = json.loads(out.read_text(encoding="utf-8"))[0]
        self.assertIsNone(record["artwork"])

    def test_the_record_goes_to_stdout_when_output_is_a_dash(self) -> None:
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    "-",
                    "--no-artwork",
                    "--source",
                    "folder",
                ],
                fetch=no_store,
                run=_music_says_nothing,
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())[0]["album"], "A Record")

    def test_output_defaults_to_stdout(self) -> None:
        # The default must not name any consumer's file. A general tool prints its
        # record; a caller names its own destination.
        self.assertEqual(build_parser().parse_args([]).output, "-")

    def test_nothing_but_the_record_reaches_stdout(self) -> None:
        # Why nothing else may reach stdout: `album_scan --output - > album.json`
        # must produce parseable JSON, not JSON with a status line wrapped around it.
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    "-",
                    "--no-artwork",
                    "--source",
                    "folder",
                ],
                fetch=no_store,
                run=_music_says_nothing,
            )
        json.loads(stdout.getvalue())  # raises if narration leaked in
        self.assertIn("album_scan:", stderr.getvalue())

    def test_the_success_line_goes_to_stderr_even_when_writing_a_file(self) -> None:
        # Unconditional, not mode-dependent: a program whose messages change stream
        # depending on a flag cannot be reasoned about without knowing how it was
        # invoked.
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        out = self.root / "album.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    str(out),
                    "--no-artwork",
                    "--source",
                    "folder",
                ],
                fetch=no_store,
                run=_music_says_nothing,
            )
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("wrote", stderr.getvalue())

    @needs_toolbox
    def test_the_fallback_notice_goes_to_stderr_so_a_redirect_cannot_bury_it(
        self,
    ) -> None:
        # The folder scan is known to mis-rank a real library, so its fallback notice
        # is the loudest guard here. On stdout with --output -, this warning would be
        # swallowed INTO the record on any redirect: corrupting the JSON and hiding the
        # warning in one stroke, at exactly the moment it most needs to be seen.
        (self.root / "An Artist" / "A Record").mkdir(parents=True)

        def silent_music(
            args: list[str], timeout: float
        ) -> subprocess.CompletedProcess[str]:
            del args, timeout
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            main(
                [
                    "--library",
                    str(self.root),
                    "--output",
                    "-",
                    "--no-artwork",
                    "--source",
                    "auto",
                ],
                fetch=no_store,
                run=silent_music,
            )
        json.loads(stdout.getvalue())
        self.assertIn("falling back to the folder scan", stderr.getvalue())

    def test_stdout_newline_translation_is_pinned_for_a_real_text_stream(self) -> None:
        # io.StringIO -- what every other stdout test in this file substitutes -- has
        # no `.reconfigure`, so it can never exercise the branch inside
        # `pin_stdout_newlines` that actually calls it. This test substitutes a REAL
        # io.TextIOWrapper instead (the same runtime type a genuine stdout is),
        # constructed with newline="\r\n" to simulate the untouched Windows-default
        # translation `force_utf8_streams` deliberately leaves alone (see
        # `pin_stdout_newlines`'s own docstring) -- proving the pin actually takes
        # effect, not merely that the isinstance check runs.
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        raw = io.BytesIO()
        wrapper = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")
        try:
            with redirect_stdout(wrapper):
                code = main(
                    [
                        "--library",
                        str(self.root),
                        "--output",
                        "-",
                        "--no-artwork",
                        "--source",
                        "folder",
                    ],
                    fetch=no_store,
                    run=_music_says_nothing,
                )
            wrapper.flush()
            emitted = raw.getvalue()
        finally:
            wrapper.close()
        self.assertEqual(code, 0)
        self.assertNotIn(b"\r\n", emitted)
        self.assertEqual(json.loads(emitted.decode("utf-8"))[0]["album"], "A Record")


if __name__ == "__main__":
    unittest.main()
