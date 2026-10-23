"""The atomic record write on its own: its bytes, its parents, its temp file, and every
way it fails while leaving the previous record untouched.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feed_read  # noqa: E402 - import follows the sys.path bootstrap above


class _FailingWrite:
    """A stand-in for the temp file's handle whose every write fails.

    Owns the descriptor it was handed exactly as the real handle would, closing it
    on exit, so the failed write leaves no open descriptor behind in the test.
    """

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def __enter__(self) -> _FailingWrite:
        return self

    def __exit__(self, *_exc: object) -> None:
        os.close(self.fd)

    def fileno(self) -> int:
        """The descriptor, as the mode is set through it before the write."""
        return self.fd

    def write(self, _text: str) -> int:
        """Fail as a full disk would."""
        raise OSError("simulated write")


class TestWriteRecords(unittest.TestCase):
    """The atomic record write on its own: its bytes, its parents, and its failure.

    Each adapter's own suite proves the write through its CLI (exit 2 on an unusable
    `--output`, the parents it creates); these pin the function itself, once.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_the_bytes_are_two_space_indented_json_ending_in_one_lf(self) -> None:
        out = self.root / "records.json"
        feed_read.write_records(out, [{"title": "One"}, {"title": "Two"}])
        self.assertEqual(
            out.read_bytes(),
            b'[\n  {\n    "title": "One"\n  },\n  {\n    "title": "Two"\n  }\n]\n',
        )

    def test_an_empty_list_is_still_written_as_an_array(self) -> None:
        # A caller that has nothing to record still owns the shape: an array, never a
        # bare object, and never nothing.
        out = self.root / "records.json"
        feed_read.write_records(out, [])
        self.assertEqual(out.read_bytes(), b"[]\n")

    def test_missing_parents_are_created_and_no_temp_file_survives(self) -> None:
        out = self.root / "src" / "data" / "records.json"
        feed_read.write_records(out, [{"title": "One"}])
        self.assertTrue(out.is_file())
        self.assertEqual(list(out.parent.glob("*.tmp")), [])

    def test_a_path_that_is_a_directory_raises_oserror_and_leaves_no_temp_file(
        self,
    ) -> None:
        # The OS refuses the rename onto a directory; the error is the caller's to
        # report, and the temp file is this function's to remove.
        out = self.root / "records.json"
        out.mkdir()
        with self.assertRaises(OSError):
            feed_read.write_records(out, [{"title": "One"}])
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_a_failed_rename_leaves_the_old_file_untouched(self) -> None:
        out = self.root / "records.json"
        out.write_bytes(b"[]\n")
        with (
            patch.object(os, "replace", side_effect=OSError("simulated rename")),
            self.assertRaises(OSError),
        ):
            feed_read.write_records(out, [{"title": "One"}])
        self.assertEqual(out.read_bytes(), b"[]\n")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_a_non_finite_value_is_refused_and_the_old_file_is_untouched(self) -> None:
        # json.dumps writes a float NaN as the bare token NaN, which is not JSON: a
        # reader's JSON.parse fails on the whole file. Refused here, before the
        # filesystem is touched, so the previous record stays byte-identical and no
        # temp file is left beside it.
        out = self.root / "records.json"
        out.write_bytes(b"[]\n")
        with self.assertRaises(ValueError):
            feed_read.write_records(out, [{"rating": float("nan")}])
        self.assertEqual(out.read_bytes(), b"[]\n")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_a_non_finite_value_is_refused_before_any_directory_is_created(
        self,
    ) -> None:
        # The ordering half of the guarantee above: serialization comes before the
        # parent mkdir, so a refused write creates nothing at all.
        out = self.root / "new" / "records.json"
        with self.assertRaises(ValueError):
            feed_read.write_records(out, [{"rating": float("inf")}])
        self.assertFalse(out.parent.exists())

    def test_a_failure_before_the_temp_file_exists_raises_the_original_error(
        self,
    ) -> None:
        # Nothing was created to clean up, so the cleanup must not raise a
        # FileNotFoundError of its own over the cause the caller has to report.
        out = self.root / "records.json"
        refused = PermissionError("simulated open")
        with (
            patch.object(tempfile, "mkstemp", side_effect=refused),
            self.assertRaises(PermissionError) as caught,
        ):
            feed_read.write_records(out, [{"title": "One"}])
        self.assertIs(caught.exception, refused)
        self.assertFalse(out.exists())

    def test_a_failed_write_removes_the_temp_file_and_raises_the_original_error(
        self,
    ) -> None:
        # The other half of the cleanup: the temp file exists, the write into it
        # fails (a full disk), and the file is removed with the cause re-raised.
        out = self.root / "records.json"
        out.write_bytes(b"[]\n")
        with (
            patch.object(
                os, "fdopen", side_effect=lambda fd, *a, **k: _FailingWrite(fd)
            ),
            self.assertRaises(OSError) as caught,
        ):
            feed_read.write_records(out, [{"title": "One"}])
        self.assertEqual(str(caught.exception), "simulated write")
        self.assertEqual(out.read_bytes(), b"[]\n")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_a_symlink_at_the_old_fixed_temp_name_is_never_touched(self) -> None:
        # The temp file used to be <name>.tmp, a fixed name: a symlink planted there
        # (one committed into the checkout the tools write into) was written THROUGH
        # into its target, and the link itself was then renamed onto the record.
        # An exclusively-opened temp file under an unpredictable name never meets
        # the link: the victim stays byte-identical, the link stays where it was,
        # and the record is a regular file holding the records.
        out = self.root / "records.json"
        victim = self.root / "victim.txt"
        victim.write_bytes(b"untouched\n")
        planted = self.root / "records.json.tmp"
        planted.symlink_to(victim)
        feed_read.write_records(out, [{"title": "One"}])
        self.assertEqual(victim.read_bytes(), b"untouched\n")
        self.assertTrue(planted.is_symlink())
        self.assertFalse(out.is_symlink())
        self.assertTrue(out.is_file())
        self.assertEqual(json.loads(out.read_bytes()), [{"title": "One"}])

    def test_the_temp_file_sits_beside_the_record_under_an_unpredictable_name(
        self,
    ) -> None:
        # Pinned through the rename's source. It is in the record's own directory,
        # so the rename stays a same-filesystem rename and so atomic; it carries
        # the record's name and a .tmp suffix, so a leftover is recognizable; and
        # it is never the fixed <name>.tmp a link could be planted at ahead of time.
        out = self.root / "records.json"
        sources: list[str] = []
        real_replace = os.replace

        def spy(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
            sources.append(os.fspath(src))
            real_replace(src, dst)

        with patch.object(os, "replace", side_effect=spy):
            feed_read.write_records(out, [{"title": "One"}])
        (source,) = sources
        self.assertEqual(Path(source).parent, self.root)
        self.assertTrue(Path(source).name.startswith("records.json."))
        self.assertTrue(source.endswith(".tmp"))
        self.assertNotEqual(Path(source).name, "records.json.tmp")

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not meaningful on NTFS")
    def test_the_record_is_world_readable_whatever_the_umask(self) -> None:
        # The record is public data a web server must read. mkstemp creates its
        # file readable by its creator alone, and a plain open would take the
        # caller's umask, so the mode is set explicitly and depends on neither.
        self.addCleanup(os.umask, os.umask(0o077))
        out = self.root / "records.json"
        feed_read.write_records(out, [{"title": "One"}])
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o644)

    def test_a_host_without_fchmod_still_writes_the_record(self) -> None:
        # Windows had no os.fchmod before Python 3.13, and the floor is 3.12. A
        # Windows file has no POSIX mode bits for it to set, so the write skips the
        # mode there instead of failing on the missing name. patch.dict restores the
        # module's namespace on exit, and pop's default keeps this runnable on the
        # host it describes, where there is no fchmod to remove.
        out = self.root / "records.json"
        with patch.dict(vars(os)), patch.object(sys, "platform", "win32"):
            vars(os).pop("fchmod", None)
            feed_read.write_records(out, [{"title": "One"}])
        self.assertEqual(
            json.loads(out.read_text(encoding="utf-8")), [{"title": "One"}]
        )
