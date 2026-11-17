"""The offline tripwire: what it refuses, what it lets through, and who carries it.

`_offline_guard` is the suite-wide guard against a real connection. These tests drive
fresh `OfflineGuard` instances rather than the module's own hooks, because this module
carries the guard too: stopping the module's guard mid-run would leave the rest of
this file unguarded. A fresh instance started inside the module's guard nests over it
and restores it on stop, which the tests below also pin.

Every host named here is an RFC 5737 documentation address or under `.invalid`, and the
guard refuses before name resolution, so nothing in this file reaches a network. That
holds only while the guard is correct: a mutant of `_offline_guard` (a mutation_gate run,
say) can let these calls through to a real resolver and a real connect attempt.
"""

from __future__ import annotations

import ast
import contextlib
import os
import socket
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

_TESTS_DIR = Path(__file__).resolve().parent
_TOOL_ROOT = _TESTS_DIR.parent
sys.path[:0] = [str(_TESTS_DIR)]

import _offline_guard  # noqa: E402 - import follows the sys.path bootstrap above
from _offline_guard import (  # noqa: E402 - import follows the sys.path bootstrap above
    OfflineGuard,
    setUpModule,
    tearDownModule,
)

#: The hooks unittest looks up on a module, which the guard must supply.
_HOOKS = frozenset({"setUpModule", "tearDownModule"})
#: The one file that must reach the network, so the one file the guard must not wrap.
_LIVE_FILE = _TESTS_DIR / "test_live_artwork.py"
#: Hosts no test may reach: documentation addresses and a reserved TLD.
_DOC_ADDRESS = "192.0.2.1"
_INVALID_HOST = "cover.invalid"
_PROXY = "http://198.51.100.7:3128"
#: What a system proxy setting looks like to urllib, which reads it outside the env.
_SYSTEM_PROXIES = {"https": "http://192.0.2.9:3128"}
_CONNECT_TIMEOUT_SECONDS = 5.0


def _hook_bindings(path: Path) -> dict[str, list[str]]:
    """For each unittest module hook, how the module's top level binds it.

    Each binding is `import:<module>.<name>` for a `from` import, or `def`/`assign`
    for a local definition, so a later local definition shadowing the import shows
    up as a second binding rather than passing silently.
    """
    bindings: dict[str, list[str]] = {hook: [] for hook in _HOOKS}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        for name, how in _bound_names(node):
            if name in bindings:
                bindings[name].append(how)
    return bindings


def _bound_names(node: ast.stmt) -> list[tuple[str, str]]:
    """The top-level names one statement binds, each with how it binds them."""
    if isinstance(node, ast.ImportFrom):
        return [
            (alias.asname or alias.name, f"import:{node.module}.{alias.name}")
            for alias in node.names
        ]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [(node.name, "def")]
    if isinstance(node, ast.Assign):
        return [
            (target.id, "assign")
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
    return []


def _guarded_files() -> list[Path]:
    """Every test module in the tool folder and tests/, but the live one."""
    return sorted(
        path
        for folder in (_TOOL_ROOT, _TESTS_DIR)
        for path in folder.glob("test_*.py")
        if path != _LIVE_FILE
    )


class GuardWiringTest(unittest.TestCase):
    """Every offline module binds the guard's hooks, and the live module does not.

    Read from each file's syntax tree, not by importing it: importing would run the
    live module and the property modules' import-time Hypothesis profile
    registrations, and an identity check against `_offline_guard`'s functions would
    depend on which name each file had been imported under. The tree says what the
    file binds, whatever imported it.
    """

    def test_every_offline_module_binds_the_guard_hooks_and_nothing_else(
        self,
    ) -> None:
        files = _guarded_files()
        self.assertGreater(len(files), 2, "the offline module list came back empty")
        for path in files:
            with self.subTest(module=path.name):
                bindings = _hook_bindings(path)
                for hook in sorted(_HOOKS):
                    self.assertEqual(bindings[hook], [f"import:_offline_guard.{hook}"])

    def test_the_live_module_binds_neither_hook(self) -> None:
        self.assertEqual(_hook_bindings(_LIVE_FILE), {hook: [] for hook in _HOOKS})

    def test_the_module_hooks_drive_the_shared_guard(self) -> None:
        # The hooks this file and every other binds start and stop one instance.
        # Faked here, because the real ones would stop this module's own guard.
        guard = _offline_guard._GUARD
        self.assertIsInstance(guard, OfflineGuard)
        with (
            mock.patch.object(guard, "start") as start,
            mock.patch.object(guard, "stop") as stop,
        ):
            setUpModule()
            tearDownModule()
        start.assert_called_once_with()
        stop.assert_called_once_with()


class TripwireTest(unittest.TestCase):
    """A fresh guard refuses every non-loopback host, loudly, and lets loopback through.

    Every guard a test starts is also stopped by a cleanup, so a test failing midway
    cannot leave its patches over the rest of the run. `stop` is a no-op on a guard
    already stopped, so the cleanup neither repeats nor masks a test's own `stop`.
    """

    def setUp(self) -> None:
        self.guard = OfflineGuard()
        self.addCleanup(self.guard.stop)
        self.connect_before = socket.create_connection
        self.getproxies_before = urllib.request.getproxies

    def test_a_swallowed_refusal_still_fails_at_stop_naming_each_host(self) -> None:
        self.guard.start()
        # Both swallowed, the way cover_art's fetch turns any OSError into a miss.
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(
                f"http://{_DOC_ADDRESS}/", timeout=_CONNECT_TIMEOUT_SECONDS
            )
        with self.assertRaisesRegex(OSError, "tripwire"):
            socket.create_connection(
                (_INVALID_HOST, 443), timeout=_CONNECT_TIMEOUT_SECONDS
            )
        with self.assertRaises(AssertionError) as caught:
            self.guard.stop()
        report = str(caught.exception)
        self.assertIn(f"{_DOC_ADDRESS}:80", report)
        self.assertIn(f"{_INVALID_HOST}:443", report)

    def test_stop_undoes_every_patch_even_when_it_raises(self) -> None:
        self.guard.start()
        self.assertIsNot(socket.create_connection, self.connect_before)
        with self.assertRaises(OSError):
            socket.create_connection(
                (_INVALID_HOST, 443), timeout=_CONNECT_TIMEOUT_SECONDS
            )
        with self.assertRaises(AssertionError):
            self.guard.stop()
        self.assertIs(socket.create_connection, self.connect_before)
        self.assertIs(urllib.request.getproxies, self.getproxies_before)

    def test_the_report_is_cleared_so_the_next_run_starts_clean(self) -> None:
        self.guard.start()
        with self.assertRaises(OSError):
            socket.create_connection(
                (_INVALID_HOST, 443), timeout=_CONNECT_TIMEOUT_SECONDS
            )
        # The refusal report is what the first test asserts; here only its clearing is.
        with contextlib.suppress(AssertionError):
            self.guard.stop()
        self.guard.start()
        self.guard.stop()
        self.assertEqual(self.guard.attempts, [])

    def test_a_loopback_connection_is_allowed_and_not_reported(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.guard.start()
            try:
                with socket.create_connection(
                    ("127.0.0.1", port), timeout=_CONNECT_TIMEOUT_SECONDS
                ):
                    pass
            finally:
                self.guard.stop()
        self.assertEqual(self.guard.attempts, [])

    def test_proxies_are_cleared_while_running_and_restored_after(self) -> None:
        # Two cases of the name, because urllib honors either. And a stand-in for a
        # system proxy setting, which urllib reads outside the environment: without
        # it, a machine with no proxy would pass this test with `getproxies` unpatched.
        proxies = {"HTTP_PROXY": _PROXY, "https_proxy": _PROXY}

        def system_proxies() -> dict[str, str]:
            return dict(_SYSTEM_PROXIES)

        with (
            mock.patch.dict(os.environ, proxies),
            mock.patch.object(urllib.request, "getproxies", system_proxies),
        ):
            self.guard.start()
            # Each name spelled out, never read through a loop variable: an export's
            # environment-read scan (feed_read's) must be able to resolve every name
            # this tree reads, and refuses the whole export over one it cannot.
            self.assertIsNone(os.environ.get("HTTP_PROXY"))
            self.assertIsNone(os.environ.get("https_proxy"))
            self.assertEqual(urllib.request.getproxies(), {})
            self.guard.stop()
            self.assertEqual(os.environ.get("HTTP_PROXY"), _PROXY)
            self.assertEqual(os.environ.get("https_proxy"), _PROXY)
            self.assertIs(urllib.request.getproxies, system_proxies)
        self.assertIs(urllib.request.getproxies, self.getproxies_before)

    def test_a_second_stop_is_a_no_op(self) -> None:
        with mock.patch.dict(os.environ, {"HTTP_PROXY": _PROXY}):
            self.guard.start()
            self.guard.stop()
            del os.environ["HTTP_PROXY"]
            self.guard.stop()
            # The proxy the first stop restored is not restored a second time.
            # Read as one key, never `assertNotIn(..., os.environ)`: its failure
            # message would print the whole environment into the test log.
            self.assertIsNone(os.environ.get("HTTP_PROXY"))
        self.assertIs(socket.create_connection, self.connect_before)

    def test_a_start_that_fails_midway_rolls_back_what_it_did(self) -> None:
        # unittest skips tearDownModule after a setUpModule error, so a half-started
        # guard must undo itself. The first patcher is real, so its undoing shows.
        started = mock.patch.object(socket, "create_connection", mock.Mock())
        failing = mock.Mock()
        failing.start.side_effect = RuntimeError("the second patch failed")
        with (
            mock.patch.dict(os.environ, {"HTTP_PROXY": _PROXY}),
            mock.patch.object(self.guard, "_patchers", return_value=[started, failing]),
        ):
            with self.assertRaisesRegex(RuntimeError, "second patch"):
                self.guard.start()
            self.assertIs(socket.create_connection, self.connect_before)
            self.assertEqual(os.environ.get("HTTP_PROXY"), _PROXY)
        failing.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
