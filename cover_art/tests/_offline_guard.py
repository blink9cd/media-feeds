"""The offline tripwire every cover_art test module but the live one carries.

A module binds it by importing the two unittest module hooks:

    from _offline_guard import setUpModule, tearDownModule

While a module's tests run, `socket.create_connection`, which every `http.client`
connection opens through, refuses any host but loopback, and every proxy is gone (a
proxy on loopback would pass the tripwire and tunnel on to the real host). The loopback
server in `test_read_body.py` still works.

**The refusal is not the loud part; the module teardown is.** cover_art's fetch never
raises: any `OSError` becomes a quiet miss, so a refused connection alone would leave a
test that wrongly reached for the network green, and it would only skip the request it
should never have made. Each refusal is therefore also logged, and `tearDownModule`
raises an `AssertionError` naming every host attempted, so unittest reports the module
as errored however thoroughly the code under test swallowed the refusal.

**Why per module, not at import.** `tests/test_live_artwork.py` must reach the network,
and every module here runs in one process under the aggregator's `load_tests`, so an
import-time patch would make the live test skip as "offline" forever. unittest runs one
module's hooks around that module's own classes only, so the live module, which binds
neither hook, runs unguarded.

**What it cannot reach.** The patch lives in this process. A child process is guarded
only if it runs a module that binds these hooks itself.
"""

from __future__ import annotations

import os
import socket
import urllib.request
from collections.abc import Callable
from typing import Any
from unittest import mock

#: The hosts a test may connect to: loopback, by address or by name.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
#: The proxy variables urllib honors, lowercased: every case of each is removed.
_PROXY_VARIABLES = frozenset({"http_proxy", "https_proxy", "all_proxy", "no_proxy"})


def _no_proxies() -> dict[str, str]:
    """`urllib.request.getproxies` while guarded: there are none, wherever it looks."""
    return {}


class OfflineGuard:
    """One start/stop cycle of the tripwire; `attempts` logs each refused (host, port).

    Starting inside another running guard nests: the outer guard's patched connect is
    what this one saves and later restores, so the outer one stays in force after.
    """

    def __init__(self) -> None:
        self.attempts: list[tuple[str, int]] = []
        self._real_connect: Callable[..., socket.socket] = socket.create_connection
        # Any: mock's patcher class is private, so it has no public name to type with.
        self._patches: list[Any] = []
        self._saved_proxies: dict[str, str] = {}

    def start(self) -> None:
        """Refuse every non-loopback connection and remove every proxy until `stop`.

        A step failing midway undoes the steps before it and re-raises: unittest skips
        `tearDownModule` after a `setUpModule` error, so nothing else would.
        """
        self._real_connect = socket.create_connection
        self._saved_proxies = {
            name: value
            for name, value in os.environ.items()
            if name.lower() in _PROXY_VARIABLES
        }
        for name in self._saved_proxies:
            del os.environ[name]
        try:
            for patch in self._patchers():
                patch.start()
                self._patches.append(patch)
        except BaseException:
            self._undo()
            raise

    def stop(self) -> None:
        """Undo every patch, then raise if any connection was refused meanwhile.

        A second `stop` finds nothing to undo and nothing to report, so it does nothing.
        """
        self._undo()
        attempts, self.attempts = self.attempts, []
        if attempts:
            hosts = ", ".join(f"{host}:{port}" for host, port in attempts)
            raise AssertionError(f"an offline test tried to reach the network: {hosts}")

    def _patchers(self) -> list[Any]:
        """The patches `start` applies, in order.

        mock's patchers, not plain assignment: each remembers exactly what it replaced,
        which is what lets a guard nest inside another and restore it.
        """
        return [
            mock.patch.object(socket, "create_connection", self._loopback_only),
            mock.patch.object(urllib.request, "getproxies", _no_proxies),
        ]

    def _undo(self) -> None:
        """Stop the started patches, newest first, and put the removed proxies back."""
        patches, self._patches = self._patches, []
        for patch in reversed(patches):
            patch.stop()
        saved, self._saved_proxies = self._saved_proxies, {}
        os.environ.update(saved)

    def _loopback_only(
        self, address: tuple[str, int], *args: Any, **kwargs: Any
    ) -> socket.socket:
        """`socket.create_connection`, refusing (and logging) every host but loopback."""
        host, port = address[0], address[1]
        if host not in _LOOPBACK_HOSTS:
            self.attempts.append((host, port))
            raise OSError(f"tripwire: an offline cover_art test tried to reach {host}")
        return self._real_connect(address, *args, **kwargs)


#: The one guard the module hooks drive; unittest never runs two modules' hooks at once.
_GUARD = OfflineGuard()


def setUpModule() -> None:  # noqa: N802 - unittest looks the hook up by this name
    """Start the tripwire before the module's first test: the unittest module hook."""
    _GUARD.start()


def tearDownModule() -> None:  # noqa: N802 - unittest looks the hook up by this name
    """Stop the tripwire, failing loudly on any refusal: the unittest module hook."""
    _GUARD.stop()
