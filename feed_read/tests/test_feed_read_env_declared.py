"""Every environment variable this project reads by name is declared to the gate cache.

Each `*_OFFLINE` switch skips a live boundary test, and each identity variable skips one
when it is absent, so every one of them changes a gate verdict without changing one file.
The workspace gate's verdict cache folds in only the variables that `[tool.gate_cache]
env` in this project's pyproject.toml declares. An undeclared one lets a verdict recorded
with it unset be replayed into a run with it set, and the reverse -- an online sweep
replaying an offline PASS never runs the live tests at all.

So this test fails on a read nothing declares, or one whose name it cannot resolve, which stops a new switch landing without
its declaration, and on a declaration nothing reads, which stops a renamed switch leaving
a stale name behind. It sits under feed_read, the library every adapter shares, only
because a test must live under one tool; it scans every tool folder in the project,
and skips the vendored shared libraries for the reason recorded beside `_VENDORED`. The
``test_feed_read_`` prefix is load-bearing: one mypy pass covers every tool folder in the
project, so test basenames must be unique across it.
"""

from __future__ import annotations

import ast
import tomllib
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# How a read is spelled here, compared as unparsed source so `os.getenv` and a bare
# `getenv` imported from os are both seen.
_GETTERS = frozenset({"os.environ.get", "os.getenv", "environ.get", "getenv"})
_MAPPINGS = frozenset({"os.environ", "environ"})
# A switch the scan must find, so a scanner that silently stopped matching cannot pass
# by finding nothing on either side.
_KNOWN_SWITCH = "FILM_SCAN_OFFLINE"

# The vendored shared libraries, skipped by the scan below. Their environment use is
# test-fixture plumbing -- a pid-file path a test exports for one child and reads back --
# not a switch that changes which tests run, so declaring it would say something untrue.
# Two of those reads name a variable no static scan can resolve (a function parameter,
# and a constant read through its module), which is exactly the shape this scan refuses
# in this project's OWN code. Excluded by name rather than by listing the tool folders,
# so a tool folder added later is scanned by default -- the safe direction.
_VENDORED = frozenset(
    {
        "stdio_utf8",
        "help_formatter",
        "bounded_fetch",
        "library_walk",
        "bounded_run",
        "live_pid_guard",
    }
)


def _read_key(node: ast.AST) -> ast.AST | None:
    """The key expression *node* reads the environment with, else None."""
    if isinstance(node, ast.Call) and node.args and ast.unparse(node.func) in _GETTERS:
        return node.args[0]
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.ctx, ast.Load)
        and ast.unparse(node.value) in _MAPPINGS
    ):
        return node.slice
    if (
        isinstance(node, ast.Compare)
        and isinstance(node.ops[0], (ast.In, ast.NotIn))
        and ast.unparse(node.comparators[0]) in _MAPPINGS
    ):
        return node.left
    return None


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` bindings, plain or annotated.

    The ``USERNAME_ENV = "..."`` style reads a variable through a named constant, and a
    scan that saw only literals would miss every such read.
    """
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.update(
                (t.id, value.value) for t in targets if isinstance(t, ast.Name)
            )
    return found


def _names_read(source: str) -> tuple[set[str], list[str]]:
    """Every name *source* reads from the environment, and each read it cannot name.

    A key resolves when it is a string literal or a module-level constant bound to one.
    Anything else comes back as ``"<line>: <read>"`` so the test FAILS on it: skipping a
    read the scan cannot name would reopen the gap it exists to close.
    """
    tree = ast.parse(source)
    constants = _string_constants(tree)
    names: set[str] = set()
    unresolved: list[str] = []
    for node in ast.walk(tree):
        key = _read_key(node)
        if key is None:
            continue
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            names.add(key.value)
        elif isinstance(key, ast.Name) and key.id in constants:
            names.add(constants[key.id])
        else:
            unresolved.append(f"{getattr(node, 'lineno', 0)}: {ast.unparse(node)}")
    return names, unresolved


def _project_reads() -> tuple[set[str], list[str]]:
    """Every environment read in this project's own tools, caches and vendored skipped."""
    names: set[str] = set()
    unresolved: list[str] = []
    for path in sorted(_PROJECT_ROOT.rglob("*.py")):
        relative = path.relative_to(_PROJECT_ROOT)
        if relative.parts[0] in _VENDORED or any(
            part.startswith(".") or part == "__pycache__" for part in relative.parts
        ):
            continue
        found, unnamed = _names_read(path.read_text(encoding="utf-8"))
        names |= found
        unresolved += [f"{relative.as_posix()}:{entry}" for entry in unnamed]
    return names, unresolved


def _declared() -> set[str]:
    """The names this project's pyproject.toml declares under [tool.gate_cache] env."""
    text = (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return set(tomllib.loads(text)["tool"]["gate_cache"]["env"])


class TestEnvDeclared(unittest.TestCase):
    """The declaration and the reads are the same set, in both directions."""

    def test_the_scanner_sees_each_read_form_and_no_write(self) -> None:
        source = (
            'NAMED_ENV = "E"\n'
            'os.environ.get("A")\n'
            'os.getenv("B")\n'
            'os.environ["C"]\n'
            'if "D" in os.environ: pass\n'
            "os.environ.get(NAMED_ENV)\n"
            'os.environ["W"] = "1"\n'
            'patch.dict(os.environ, {"P": "1"})\n'
            'os.environ.pop("Q", None)\n'
        )
        self.assertEqual(_names_read(source), ({"A", "B", "C", "D", "E"}, []))

    def test_a_read_the_scanner_cannot_name_is_reported(self) -> None:
        """Skipping it would reopen the gap: the name could be a switch nobody declared."""
        _, unresolved = _names_read("def f(name):\n    return os.environ.get(name)\n")
        self.assertEqual(unresolved, ["2: os.environ.get(name)"])

    def test_every_environment_read_is_declared_and_nothing_else(self) -> None:
        read, unresolved = _project_reads()
        self.assertEqual(
            unresolved,
            [],
            "an environment read whose name this scan cannot resolve: spell the name "
            "as a string literal or a module-level constant so it can be checked",
        )
        self.assertIn(
            _KNOWN_SWITCH, read, "the scan found no switch; it is not looking"
        )
        self.assertEqual(
            sorted(read),
            sorted(_declared()),
            "[tool.gate_cache] env in this project's pyproject.toml must list exactly "
            "the variables its code reads by name, or the gate cache replays a verdict "
            "across a change in one of them",
        )
