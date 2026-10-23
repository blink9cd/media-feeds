"""Grouped suite: this repository's sys.path bootstrap never reaches above its own root.

Each tool puts the vendored libraries and its sibling tool folders on ``sys.path`` from
ONE fixed place: directly under the repository root, derived from the tool's own
``__file__``. Nothing here searches the ancestors, so no folder above a checkout can be
imported in place of the real one. ``stdio_utf8.md``, "How this project's tools import
it", says why.

Run through ``../test_stdio_utf8.py``'s ``load_tests`` hook, which is how this file runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

#: This repository's root: two levels above this file, which sits in stdio_utf8/tests/.
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every file carrying a sys.path bootstrap, relative to the root, with the arguments
#: that run it to a clean exit. film_parse is a module rather than a CLI, and cover_art
#: a library: run bare, each bootstraps, imports its siblings, and exits.
_BOOTSTRAPPED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("film_scan/film_scan.py", ("--help",)),
    ("book_scan/book_scan.py", ("--help",)),
    ("wikidata_director/wikidata_director.py", ("--help",)),
    ("film_scan/src/film_parse.py", ()),
    ("album_scan/album_scan.py", ("--help",)),
    ("cover_art/cover_art.py", ()),
)

#: A generous ceiling for each child: a hung tool must fail this suite, not hang it.
_SUBPROCESS_TIMEOUT = 120


class TestBootstrapStaysInsideTheRepository(unittest.TestCase):
    """No tool's sys.path bootstrap may reach above this repository's root.

    Each tool puts the vendored libraries and its sibling tool folders on sys.path from
    the repository root. A search that walked every ancestor instead would put any
    directory above the checkout that holds a same-named folder onto sys.path ahead of
    the standard library, so a module planted there would be imported in place of the
    real one. These copy the repository under a parent holding exactly such a folder and
    run each tool in a real subprocess; the planted modules record that they ran.
    """

    def _plant_decoys(self, parent: Path) -> Path:
        """Plant a folder the old search would have taken, holding two shadowing modules.

        ``feed_read/feed_read.py`` is what makes an ancestor search take the folder.
        Two decoys, because a module already imported before a bootstrap runs is never
        looked up again: ``json`` fires for a tool that first imports it through
        feed_read, and the ``xml`` package for wikidata_director, which imports json
        itself before its bootstrap. Returns the path either decoy creates when it runs.
        """
        marker = parent / "decoy-ran"
        decoy = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        folder = parent / "feed_read"
        (folder / "xml").mkdir(parents=True)
        (folder / "feed_read.py").write_text("", encoding="utf-8", newline="\n")
        (folder / "json.py").write_text(decoy, encoding="utf-8", newline="\n")
        (folder / "xml" / "__init__.py").write_text(
            decoy, encoding="utf-8", newline="\n"
        )
        return marker

    def _run(self, script: Path, *args: str) -> int:
        """Run *script* to exit and return its code, with no inherited PYTHON* setting.

        Nothing is captured: the verdict is the marker file and the exit code.
        """
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        # reliability: ignore[RL005] DEVNULL sinks, not pipes: no grandchild can hold one open
        return subprocess.run(
            [sys.executable, str(script), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        ).returncode

    def _staged_copy(self, parent: Path) -> Path:
        """This repository's tool folders copied to ``<parent>/repo``, without caches.

        A tool folder is one holding a module named after it -- the layout every
        bootstrap relies on -- so nothing else at the root (a virtualenv, say) is
        copied on every run.
        """
        repo = parent / "repo"
        for folder in _REPO_ROOT.iterdir():
            if (folder / f"{folder.name}.py").is_file():
                shutil.copytree(
                    folder,
                    repo / folder.name,
                    ignore=shutil.ignore_patterns(".*", "__pycache__"),
                )
        return repo

    def test_no_tool_imports_a_module_planted_above_the_root(self) -> None:
        for script, args in _BOOTSTRAPPED:
            with self.subTest(script=script), tempfile.TemporaryDirectory() as td:
                parent = Path(td)
                marker = self._plant_decoys(parent)
                code = self._run(self._staged_copy(parent) / script, *args)
                self.assertFalse(marker.exists(), f"{script} ran a planted module")
                # Also proves the tool really ran: a wrong path exits 2, marker-free.
                self.assertEqual(code, 0, f"{script} did not exit cleanly")

    def test_the_decoys_fire_for_an_unbounded_search(self) -> None:
        # The control: the old walk-every-ancestor bootstrap, run from the same staged
        # layout, does run a decoy -- so a clean result above is not a dead decoy.
        probe = (
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path[:0] = [\n"
            "    str(folder)\n"
            "    for ancestor in Path(__file__).resolve().parents\n"
            '    for folder in (ancestor / "feed_read",)\n'
            '    if (folder / "feed_read.py").is_file()\n'
            "]\n"
            "import feed_read  # noqa: E402, F401\n"
        )
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            marker = self._plant_decoys(parent)
            script = self._staged_copy(parent) / "probe" / "probe.py"
            script.parent.mkdir()
            script.write_text(probe, encoding="utf-8", newline="\n")
            self._run(script)
            self.assertTrue(marker.exists(), "the decoy never ran; the test is blind")
