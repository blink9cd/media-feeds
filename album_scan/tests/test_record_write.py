"""`--output`: the record lands whole or not at all, and a failed write says so.

The file branch is `feed_read.write_records`, the writer every adapter here shares: it
stages the record in a temporary sibling and swaps it into place in one step, so a
target that cannot be written (a directory, a read-only location, a replace that
fails) leaves whatever record was there before byte-identical, and `main` reports the
failure by file name on stderr and exits 2. The written file is mode 0644 whatever the
umask or the mode of the file it replaced: that writer's fixed mode, taken on
deliberately in place of this tool's old carry-the-mode-forward one, so every record
this project writes reads the same way. The writer's own suite pins its internals;
this one pins what `album_scan` gets from it. The stdout branch is here too, for the
two stream properties `main` pins before writing: the LF newline and UTF-8.

Every `main` call injects both boundary seams with fakes that raise, so nothing here
can reach Music.app or the network even if a flag were dropped by mistake.
"""

from __future__ import annotations

import datetime as dt
import io
import os
import stat
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import no_music, no_store, posix_filesystem_only  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import main  # noqa: E402 - import follows the sys.path bootstrap above

# album_scan's bootstrap above is what puts its private src/ on sys.path, the same
# way tests/test_discovery.py reaches folder_scan.
import folder_scan  # noqa: E402 - import follows the sys.path bootstrap above
import record_writer  # noqa: E402 - import follows the sys.path bootstrap above

#: Flags that keep every run here off Music.app and off the network; the two raising
#: fakes from `_seams` are what prove it, by failing the test if either seam is reached.
_OFFLINE = ["--no-artwork", "--no-favorites", "--source", "folder"]

#: A birth time at local noon, so the folder's date is the same calendar day in every
#: time zone the suite might run in; `os.utime` pulls a macOS birth time back to it.
_ADDED = dt.datetime(2026, 1, 15, 12, 0)


def _expected_bytes(library: Path) -> bytes:
    """The exact bytes `--output` writes for the one-album `library` `_make_library` built.

    The indent, the key order, and the trailing newline are the contract a consumer
    reads, not an accident of json.dumps' defaults. The date is the album folder's
    birth time as this host reports it: `_ADDED` wherever `os.utime` can set the
    stamp `folder_scan` ranks by (macOS pulls its birth time back; a host with no
    birth time ranks by the modification time `os.utime` sets), and the day the
    folder was made on Windows, whose birth time is the creation time `os.utime`
    cannot touch. Where the date comes from is `test_discovery`'s to pin, not this
    suite's.
    """
    album = next(path for path in library.glob("*/*") if path.is_dir())
    added = folder_scan._birth_time(album).date()
    return (
        "[\n"
        "  {\n"
        '    "artist": "An Artist",\n'
        '    "album": "A Record",\n'
        f'    "added": "{added.isoformat()}",\n'
        '    "artwork": null,\n'
        '    "tracks": null,\n'
        '    "favorited": null,\n'
        '    "year": null,\n'
        '    "favorite_signal": null\n'
        "  }\n"
        "]\n"
    ).encode("utf-8")


#: What every run here prints before its outcome: `_make_library`'s album folder holds
#: no audio file, and the folder source reads its tracks and year even under
#: `--no-favorites`, so its null facts are a fact asked for and lost.
_COUNTS_NOTICE = (
    "album_scan: counts unavailable for An Artist - A Record (no audio file in its "
    "folder, or the folder could not be read)\n"
)

#: A record from an earlier run, to be found untouched after every failed write.
_OLD_RECORD = b'[{"artist": "Earlier", "album": "Record"}]\n'


# os.geteuid exists only off Windows, and the gate type-checks for win32 too, so the
# root check is a getattr rather than a bare attribute; where it is absent, the test
# runs (a Windows account is never root in this sense).
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


def _make_library(
    root: Path, artist: str = "An Artist", album: str = "A Record"
) -> Path:
    """One album folder under `root`, born at `_ADDED`."""
    library = root / "library"
    path = library / artist / album
    path.mkdir(parents=True)
    stamp = _ADDED.timestamp()
    os.utime(path, (stamp, stamp))
    return library


def _rename_onto_a_directory_reason(case: unittest.TestCase) -> str:
    """The reason this host gives for renaming a file onto a directory.

    That rename is the step the record's writer fails at for a directory target, and
    `main` prints the OS's own reason for it: "Is a directory" on POSIX, "Access is
    denied" on Windows. Asked of the host the same way, in a folder of its own, so the
    case pins the reason `main` passes through without hardcoding one host's wording.
    """
    with TemporaryDirectory() as tmp:
        source, folder = Path(tmp) / "file", Path(tmp) / "folder"
        source.write_bytes(b"")
        folder.mkdir()
        with case.assertRaises(OSError) as refused:
            os.replace(source, folder)
    return str(refused.exception.strerror)


def _run(library: Path, output: str) -> tuple[int, str, str]:
    """Drive `main` offline; return the exit code and what reached stdout and stderr."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(
            ["--library", str(library), "--output", output, *_OFFLINE],
            fetch=no_store,
            run=no_music,
        )
    return code, stdout.getvalue(), stderr.getvalue()


class RecordFileTest(unittest.TestCase):
    """The file branch: what a successful write leaves, and what a failed one does not."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.library = _make_library(self.root)
        self.out = self.root / "album.json"

    def test_the_written_bytes_are_exact(self) -> None:
        code, _, _ = _run(self.library, str(self.out))
        self.assertEqual(code, 0)
        self.assertEqual(self.out.read_bytes(), _expected_bytes(self.library))

    def test_stdout_and_a_file_carry_the_same_bytes(self) -> None:
        # Two serializers since the file branch moved to feed_read: this keeps them one
        # format, with a non-ASCII name so an escaping difference cannot hide.
        library = _make_library(self.root / "accented", artist="Ünïcode Ärtist")
        code, _, _ = _run(library, str(self.out))
        self.assertEqual(code, 0)
        code, stdout, _ = _run(library, "-")
        self.assertEqual(code, 0)
        self.assertEqual(stdout.encode("utf-8"), self.out.read_bytes())

    def test_missing_parent_directories_are_created(self) -> None:
        nested = self.root / "not" / "yet" / "there" / "album.json"
        code, _, _ = _run(self.library, str(nested))
        self.assertEqual(code, 0)
        self.assertEqual(nested.read_bytes(), _expected_bytes(self.library))

    def test_a_directory_target_is_reported_by_name_and_exits_2(self) -> None:
        target = self.root / "outdir"
        target.mkdir()
        code, stdout, stderr = _run(self.library, str(target))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            _COUNTS_NOTICE
            + f"album_scan: cannot write outdir: {_rename_onto_a_directory_reason(self)}\n",
        )
        self.assertNotIn(str(self.root), stderr)

    @posix_filesystem_only
    @unittest.skipIf(_IS_ROOT, "root writes into a read-only directory")
    def test_a_read_only_directory_leaves_the_old_record_byte_identical(self) -> None:
        self.out.write_bytes(_OLD_RECORD)
        self.addCleanup(os.chmod, self.root, 0o755)
        os.chmod(self.root, 0o555)
        code, stdout, stderr = _run(self.library, str(self.out))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            _COUNTS_NOTICE + "album_scan: cannot write album.json: Permission denied\n",
        )
        self.assertEqual(self.out.read_bytes(), _OLD_RECORD)

    def test_a_symlink_at_the_target_is_replaced_not_written_through(self) -> None:
        # A link planted at the target must not redirect the write: the record has
        # to land in a regular file at the target, and whatever the link pointed at
        # has to survive byte for byte. The swap replaces the link itself, so this
        # holds by construction, and a writer that opened the target by path
        # would follow the link and overwrite its destination instead.
        elsewhere = self.root / "elsewhere.json"
        elsewhere.write_bytes(_OLD_RECORD)
        self.out.symlink_to(elsewhere)
        code, _, _ = _run(self.library, str(self.out))
        self.assertEqual(code, 0)
        self.assertFalse(self.out.is_symlink(), "the link was written through")
        self.assertTrue(self.out.is_file())
        self.assertEqual(self.out.read_bytes(), _expected_bytes(self.library))
        self.assertEqual(elsewhere.read_bytes(), _OLD_RECORD)

    def test_a_leading_tilde_in_output_is_the_home_directory(self) -> None:
        # `~` is read the way `--library` already reads it. Quoted on a command
        # line the shell leaves it alone, and before the expansion the tool made a
        # directory literally named `~` beside wherever it ran. HOME (and
        # USERPROFILE) is pinned to this test's own directory, and the run happens
        # inside it too, so a regression leaves its `~` folder here, never in the
        # checkout.
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        # Both spellings: `expanduser` reads HOME on POSIX and USERPROFILE on Windows.
        home = {"HOME": str(self.root), "USERPROFILE": str(self.root)}
        with mock.patch.dict(os.environ, home):
            code, _, _ = _run(self.library, "~/album.json")
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "~").exists(), "a directory named ~ was made")
        self.assertTrue(self.out.is_file(), "the record did not land under HOME")
        self.assertEqual(self.out.read_bytes(), _expected_bytes(self.library))

    def test_a_failed_replace_leaves_the_old_record_and_no_temp_file(self) -> None:
        self.out.write_bytes(_OLD_RECORD)
        with mock.patch.object(
            os, "replace", side_effect=OSError(5, "injected replace failure")
        ):
            code, _, stderr = _run(self.library, str(self.out))
        self.assertEqual(code, 2)
        self.assertEqual(
            stderr,
            _COUNTS_NOTICE
            + "album_scan: cannot write album.json: injected replace failure\n",
        )
        self.assertEqual(self.out.read_bytes(), _OLD_RECORD)
        # The library folder and the old record are all that may remain: the staged
        # file is removed on the failure it could not survive.
        self.assertEqual(
            sorted(entry.name for entry in self.root.iterdir()),
            ["album.json", "library"],
        )

    def test_the_record_is_staged_in_the_targets_own_directory(self) -> None:
        # A staging file elsewhere (the system temp dir, say) would turn the final
        # step into a copy across file systems, which is no longer one atomic swap.
        staged: list[Path] = []
        real_replace = os.replace

        def recording_replace(src: str, dst: str) -> None:
            staged.append(Path(src))
            real_replace(src, dst)

        with mock.patch.object(os, "replace", side_effect=recording_replace):
            code, _, _ = _run(self.library, str(self.out))
        self.assertEqual(code, 0)
        self.assertEqual([path.parent for path in staged], [self.out.parent])
        # Prefixed with the target's own name, so a leftover from a crash is
        # recognizable beside it.
        self.assertRegex(staged[0].name, r"^album\.json\..+\.tmp$")

    @posix_filesystem_only
    def test_a_new_file_is_0644_whatever_the_umask(self) -> None:
        # A restrictive umask does not narrow it: the record is read by whatever
        # serves the site, so the writer gives it the one mode it needs.
        previous = os.umask(0o077)
        try:
            code, _, _ = _run(self.library, str(self.out))
        finally:
            os.umask(previous)
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o644)

    @posix_filesystem_only
    def test_a_rewrite_is_0644_whatever_the_old_files_mode(self) -> None:
        # The deliberate change from carrying the replaced file's mode forward: a
        # record that was 0640 comes back 0644, the mode every record here gets.
        self.out.write_bytes(_OLD_RECORD)
        os.chmod(self.out, 0o640)
        code, _, _ = _run(self.library, str(self.out))
        self.assertEqual(code, 0)
        self.assertEqual(self.out.read_bytes(), _expected_bytes(self.library))
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o644)


class StdoutStreamTest(unittest.TestCase):
    """The two stream properties the stdout branch pins before it writes."""

    def test_a_detached_stdout_does_not_fail_the_newline_pin(self) -> None:
        # `reconfigure` raises ValueError on a closed or detached stream;
        # `stdio_utf8.force_utf8_streams` skips such a stream rather than failing,
        # and the newline pin matches it; the write that follows reports the
        # real failure, in its own words.
        wrapper = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        wrapper.detach()
        with redirect_stdout(wrapper):
            record_writer.pin_stdout_newlines()

    def test_a_non_utf8_stderr_still_carries_a_non_ascii_name(self) -> None:
        # A real text stream whose encoding cannot spell the artist, which is what a
        # locale-derived stream is under a pipe on a C locale. `main`'s first
        # statement forces UTF-8 onto both streams; without it, the confirmation
        # line raises UnicodeEncodeError on the first non-ASCII artist. stderr, not
        # stdout, is the stream that shows it: the record is `json.dumps` output,
        # which escapes non-ASCII to `\uXXXX` and so survives any encoding.
        with TemporaryDirectory() as tmp:
            library = _make_library(
                Path(tmp), artist="Sœur Inventée", album="Première Prise"
            )
            raw = io.BytesIO()
            wrapper = io.TextIOWrapper(raw, encoding="ascii")
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(wrapper):
                    code = main(
                        ["--library", str(library), "--output", "-", *_OFFLINE],
                        fetch=no_store,
                        run=no_music,
                    )
                wrapper.flush()
                emitted = raw.getvalue()
            finally:
                wrapper.close()
        self.assertEqual(code, 0)
        self.assertIn("newest Sœur Inventée - Première Prise", emitted.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
