"""Finding the apple-music-toolbox: `--toolbox`, then the environment, then a search.

The toolbox is a separate project, imported only when `--source music` or `auto` asks
for Music.app. These cases build every candidate layout in a temporary folder (the
toolbox's two tool folders hold an empty module each, which is all the lookup checks),
so none of them depends on where this checkout sits or on what is installed beside it.
Nothing here imports a toolbox module, spawns `osascript`, or reaches the network.
"""

from __future__ import annotations

import importlib
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for _seams beside this file

from _seams import no_music, no_store, posix_filesystem_only  # noqa: E402 - import follows the sys.path bootstrap above
from album_scan import main  # noqa: E402 - import follows the sys.path bootstrap above

# album_scan's bootstrap above is what puts its private src/ on sys.path.
import album_lookups  # noqa: E402 - import follows the sys.path bootstrap above
import apple_source  # noqa: E402 - import follows the sys.path bootstrap above


def _make_toolbox(folder: Path) -> Path:
    """A folder holding the two toolbox modules the lookup checks for, both empty."""
    for name in ("recently_added", "album_stats"):
        (folder / name).mkdir(parents=True)
        (folder / name / f"{name}.py").write_bytes(b"")
    return folder


class LocateToolboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        # Where a tool file would sit two levels into a checkout under `root`.
        self.start = self.root / "checkout" / "album_scan" / "album_scan.py"
        self.start.parent.mkdir(parents=True)
        self.start.write_bytes(b"")

    def _locate(
        self,
        option: str | None,
        environ: dict[str, str],
        *,
        home: Path | None = None,
        uid: int | None = None,
    ) -> Path:
        # The home folder is injected, never read from the host: `root` is the home
        # unless a case names another. The user id is the one the host itself would
        # answer, so every folder the case builds is trusted unless it says not: on
        # POSIX that is the user `root` belongs to, since this process made it, and
        # on Windows it is None, which checks nothing. Windows' `st_uid` is a
        # stand-in 0 while its directories report every write bit, so injecting
        # `root`'s `st_uid` there would refuse every folder the product itself never
        # checks.
        expected = apple_source.current_uid() if uid is None else uid
        return apple_source.locate_toolbox(
            option,
            environ=environ,
            start=self.start,
            home=self.root if home is None else home,
            user_id=lambda: expected,
        )

    def test_the_option_wins_over_the_environment_and_the_search(self) -> None:
        chosen = _make_toolbox(self.root / "chosen")
        other = _make_toolbox(self.root / "other")
        _make_toolbox(self.root / "apple_music_toolbox")
        found = self._locate(str(chosen), {apple_source.TOOLBOX_ENV: str(other)})
        self.assertEqual(found, chosen)

    def test_a_leading_tilde_in_the_option_is_the_home_directory(self) -> None:
        _make_toolbox(self.root / "kit")
        # Both spellings: expanduser reads HOME on POSIX and USERPROFILE on Windows.
        home = {"HOME": str(self.root), "USERPROFILE": str(self.root)}
        with mock.patch.dict("os.environ", home):
            found = self._locate("~/kit", {})
        self.assertEqual(found, self.root / "kit")

    def test_the_environment_wins_over_the_search(self) -> None:
        named = _make_toolbox(self.root / "named")
        _make_toolbox(self.root / "apple_music_toolbox")
        found = self._locate(None, {apple_source.TOOLBOX_ENV: str(named)})
        self.assertEqual(found, named)

    def test_an_empty_environment_value_is_the_same_as_none(self) -> None:
        sibling = _make_toolbox(self.root / "apple_music_toolbox")
        self.assertEqual(self._locate(None, {apple_source.TOOLBOX_ENV: ""}), sibling)

    def test_the_search_finds_the_underscored_toolbox_beside_an_ancestor(self) -> None:
        sibling = _make_toolbox(self.root / "apple_music_toolbox")
        self.assertEqual(self._locate(None, {}), sibling)

    def test_the_search_finds_a_sibling_clone_spelled_with_hyphens(self) -> None:
        clone = _make_toolbox(self.root / "apple-music-toolbox")
        self.assertEqual(self._locate(None, {}), clone)

    def test_the_nearest_ancestor_wins(self) -> None:
        _make_toolbox(self.root / "apple_music_toolbox")
        nearer = _make_toolbox(self.root / "checkout" / "apple-music-toolbox")
        self.assertEqual(self._locate(None, {}), nearer)

    def test_a_folder_missing_one_module_is_not_a_toolbox(self) -> None:
        half = self.root / "apple_music_toolbox" / "recently_added"
        half.mkdir(parents=True)
        (half / "recently_added.py").write_bytes(b"")
        with self.assertRaises(apple_source.ToolboxNotFoundError):
            self._locate(None, {})

    def test_nothing_found_names_every_way_to_point_at_it(self) -> None:
        with self.assertRaises(apple_source.ToolboxNotFoundError) as raised:
            self._locate(None, {})
        message = str(raised.exception)
        for phrase in ("--toolbox", apple_source.TOOLBOX_ENV, "apple-music-toolbox"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, message)

    def test_a_named_folder_that_is_no_toolbox_is_refused_by_name_only(self) -> None:
        empty = self.root / "not-a-kit"
        empty.mkdir()
        for option, environ in (
            (str(empty), {}),
            (None, {apple_source.TOOLBOX_ENV: str(empty)}),
        ):
            with self.subTest(option=option):
                with self.assertRaises(apple_source.ToolboxNotFoundError) as raised:
                    self._locate(option, environ)
                self.assertIn("not-a-kit", str(raised.exception))
                self.assertNotIn(str(self.root), str(raised.exception))

    def test_the_search_stops_at_the_home_folder(self) -> None:
        # A toolbox beside the home folder's own parent is outside the search: home
        # is the last ancestor searched, so a checkout under it never reaches up into
        # a folder another account could have planted one in.
        _make_toolbox(self.root / "apple_music_toolbox")
        with self.assertRaises(apple_source.ToolboxNotFoundError):
            self._locate(None, {}, home=self.root / "checkout")

    def test_the_home_folder_itself_is_searched(self) -> None:
        beside = _make_toolbox(self.root / "checkout" / "apple_music_toolbox")
        found = self._locate(None, {}, home=self.root / "checkout")
        self.assertEqual(found, beside)

    def test_a_checkout_outside_home_searches_every_ancestor(self) -> None:
        sibling = _make_toolbox(self.root / "apple_music_toolbox")
        elsewhere = self.root.parent / "some-other-home"
        self.assertEqual(self._locate(None, {}, home=elsewhere), sibling)

    def test_the_search_skips_a_toolbox_another_user_owns(self) -> None:
        _make_toolbox(self.root / "checkout" / "apple_music_toolbox")
        theirs = self.root.stat().st_uid + 1
        with self.assertRaises(apple_source.ToolboxNotFoundError):
            self._locate(None, {}, uid=theirs)

    @posix_filesystem_only
    def test_the_search_skips_a_shared_writable_toolbox_for_a_farther_one(
        self,
    ) -> None:
        trusted = _make_toolbox(self.root / "apple_music_toolbox")
        shared = _make_toolbox(self.root / "checkout" / "apple-music-toolbox")
        os.chmod(shared / "album_stats", 0o777)
        self.assertEqual(self._locate(None, {}), trusted)

    @posix_filesystem_only
    def test_a_group_writable_module_disqualifies_its_toolbox(self) -> None:
        kit = _make_toolbox(self.root / "apple_music_toolbox")
        os.chmod(kit / "recently_added" / "recently_added.py", 0o664)
        with self.assertRaises(apple_source.ToolboxNotFoundError):
            self._locate(None, {})

    @posix_filesystem_only
    def test_a_named_shared_writable_toolbox_is_refused_by_name_only(self) -> None:
        kit = _make_toolbox(self.root / "shared-kit")
        os.chmod(kit, 0o777)
        for option, environ in (
            (str(kit), {}),
            (None, {apple_source.TOOLBOX_ENV: str(kit)}),
        ):
            with self.subTest(option=option):
                with self.assertRaises(apple_source.ToolboxNotFoundError) as raised:
                    self._locate(option, environ)
                message = str(raised.exception)
                self.assertIn("shared-kit", message)
                self.assertIn("writable by other users", message)
                self.assertNotIn(str(self.root), message)

    def test_a_named_toolbox_another_user_owns_is_refused(self) -> None:
        kit = _make_toolbox(self.root / "their-kit")
        with self.assertRaises(apple_source.ToolboxNotFoundError) as raised:
            self._locate(str(kit), {}, uid=self.root.stat().st_uid + 1)
        self.assertIn("their-kit", str(raised.exception))
        self.assertIn("not owned by the current user", str(raised.exception))


class CurrentUserTest(unittest.TestCase):
    def test_windows_has_no_user_id_to_check(self) -> None:
        with mock.patch.object(apple_source.sys, "platform", "win32"):
            self.assertIsNone(apple_source.current_uid())

    @posix_filesystem_only
    def test_posix_answers_the_user_new_files_belong_to(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(apple_source.current_uid(), Path(tmp).stat().st_uid)

    def test_no_user_id_trusts_any_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            kit = _make_toolbox(Path(tmp) / "kit")
            self.assertIsNone(apple_source.untrusted_reason(kit, None))


class OpenToolboxTest(unittest.TestCase):
    def test_a_toolbox_that_fails_to_import_reads_as_not_found(self) -> None:
        with TemporaryDirectory() as tmp:
            kit = _make_toolbox(Path(tmp) / "kit")
            with mock.patch.object(
                importlib, "import_module", side_effect=ImportError("broken")
            ):
                with self.assertRaises(apple_source.ToolboxNotFoundError) as raised:
                    apple_source.open_toolbox(
                        str(kit), environ={}, start=kit, run=no_music
                    )
        self.assertIn("did not import: ImportError", str(raised.exception))

    def test_an_import_failure_names_the_module_never_the_message(self) -> None:
        # An ImportError's message can carry an absolute path; only its module name,
        # or failing that its type, reaches the operator, with the fix beside it.
        leak = ImportError("cannot load /an/invented/dir/x.py", name="album_stats")
        with TemporaryDirectory() as tmp:
            kit = _make_toolbox(Path(tmp) / "kit")
            with mock.patch.object(importlib, "import_module", side_effect=leak):
                with self.assertRaises(apple_source.ToolboxNotFoundError) as raised:
                    apple_source.open_toolbox(
                        str(kit), environ={}, start=kit, run=no_music
                    )
        message = str(raised.exception)
        self.assertIn("did not import: album_stats", message)
        self.assertNotIn("/an/invented", message)
        self.assertIn("--toolbox", message)

    def test_the_opened_toolbox_carries_its_folder_name(self) -> None:
        modules = _fake_modules(takes_min_stars=True)
        with TemporaryDirectory() as tmp:
            kit = _make_toolbox(Path(tmp) / "apple_music_toolbox")
            with mock.patch.object(importlib, "import_module", side_effect=modules.get):
                opened = apple_source.open_toolbox(
                    str(kit), environ={}, start=kit, run=no_music
                )
        self.assertEqual(opened.folder, "apple_music_toolbox")


def _fake_modules(*, takes_min_stars: bool) -> dict[str, types.SimpleNamespace]:
    """The toolbox's two modules, faked: `album_facts` with or without `min_stars`.

    Each call refuses to run, so a case reaching one fails naming the query it made.
    """

    def newest_albums_from_music(count: int, *, run: object) -> list[object]:
        raise AssertionError("the version check ran a query")

    def album_facts(*, artist: str, album: str, run: object) -> None:
        raise AssertionError("the version check ran a query")

    def rated_album_facts(
        *, artist: str, album: str, run: object, min_stars: int | None = None
    ) -> None:
        raise AssertionError("the version check ran a query")

    return {
        "recently_added": types.SimpleNamespace(
            newest_albums_from_music=newest_albums_from_music
        ),
        "album_stats": types.SimpleNamespace(
            album_facts=rated_album_facts if takes_min_stars else album_facts
        ),
    }


class ToolboxVersionTest(unittest.TestCase):
    """A star threshold needs a toolbox whose `album_facts` takes `min_stars`."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kit = _make_toolbox(Path(self._tmp.name) / "kit")

    def _open(self, *, takes_min_stars: bool, min_stars: int | None) -> object:
        modules = _fake_modules(takes_min_stars=takes_min_stars)
        with mock.patch.object(importlib, "import_module", side_effect=modules.get):
            return apple_source.open_toolbox(
                str(self.kit),
                environ={},
                start=self.kit,
                run=no_music,
                min_stars=min_stars,
            )

    def test_a_threshold_against_a_toolbox_predating_it_is_refused(self) -> None:
        with self.assertRaises(apple_source.ToolboxNotFoundError) as raised:
            self._open(takes_min_stars=False, min_stars=3)
        self.assertIn("predates --favorite-min-stars", str(raised.exception))
        self.assertIn("update it", str(raised.exception))

    def test_no_threshold_opens_a_toolbox_predating_it(self) -> None:
        self.assertIsInstance(
            self._open(takes_min_stars=False, min_stars=None), apple_source.Toolbox
        )

    def test_a_threshold_opens_a_toolbox_that_takes_it(self) -> None:
        self.assertIsInstance(
            self._open(takes_min_stars=True, min_stars=3), apple_source.Toolbox
        )

    def _main(self, *extra: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        modules = _fake_modules(takes_min_stars=False)
        with mock.patch.object(importlib, "import_module", side_effect=modules.get):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "--source",
                        "music",
                        "--toolbox",
                        str(self.kit),
                        "--no-artwork",
                        *extra,
                    ],
                    fetch=no_store,
                    run=no_music,
                )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_the_command_line_exits_2_naming_the_fix_not_a_traceback(self) -> None:
        code, stdout, stderr = self._main("--favorite-min-stars", "4")
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn(
            "album_scan: the apple-music-toolbox predates --favorite-min-stars", stderr
        )
        self.assertNotIn("Traceback", stderr)

    def test_a_threshold_with_favorites_declined_needs_no_newer_toolbox(self) -> None:
        # --no-favorites makes no facts query, so the threshold is never sent; the run
        # then fails only on the fake that refuses every query, which is reached.
        with self.assertRaises(AssertionError):
            self._main("--favorite-min-stars", "4", "--no-favorites")


class ToolboxRequiredTest(unittest.TestCase):
    def test_the_music_sources_refuse_to_run_without_a_toolbox(self) -> None:
        for source in ("music", "auto"):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    album_lookups.resolve_albums(source, Path("."), 1, toolbox=None)


class CommandLineToolboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / "An Artist" / "A Record").mkdir(parents=True)
        self.not_a_kit = self.root / "not-a-kit"
        self.not_a_kit.mkdir()

    def _main(self, source: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "--library",
                    str(self.root),
                    "--source",
                    source,
                    "--toolbox",
                    str(self.not_a_kit),
                    "--no-artwork",
                ],
                fetch=no_store,
                run=no_music,
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_a_music_source_with_no_toolbox_exits_2_and_says_how_to_fix_it(
        self,
    ) -> None:
        for source in ("music", "auto"):
            with self.subTest(source=source):
                code, stdout, stderr = self._main(source)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("album_scan: ", stderr)
                self.assertIn("--toolbox", stderr)
                self.assertIn(apple_source.TOOLBOX_ENV, stderr)

    def test_the_folder_source_never_looks_for_the_toolbox(self) -> None:
        code, stdout, _ = self._main("folder")
        self.assertEqual(code, 0)
        self.assertIn('"A Record"', stdout)


if __name__ == "__main__":
    unittest.main()
