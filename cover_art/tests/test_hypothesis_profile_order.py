"""Each module of property tests loads its Hypothesis profile before its `@given` tests.

A `@given` test snapshots its settings when it is decorated, which happens at import,
while its class body (or, for a module-level test, its `def`) runs. So a profile loaded
after them changes nothing for the tests above it: they keep Hypothesis's defaults, the `too_slow` health check
and the 200 ms per-example deadline included. The first draw of a text strategy builds
Hypothesis's unicode tables once per storage directory, and on a fresh clone that build
used to land inside the `too_slow` allowance, so a misordered profile failed there and
nowhere else. That failure depends on how slow the build is on a given Hypothesis
release and machine, which no test can pin, so the order is read off each module's
syntax tree instead, where it is the same on every machine.

The module list is derived, not written down: every test module here holding a
`@given` decorator anywhere, spelled `given` or `hypothesis.given`. A new property module is therefore checked from the day it is
added, and a list that comes back empty fails rather than passing over nothing.

`ProfileDeadlineTest` pins the other half in-process: each profile sets
`deadline=None`, because under Hypothesis's default 200 ms per-example deadline a
loaded machine that slows a passing property's first run but not its retry fails it
with `FlakyFailure: Unreliable test timings`.
"""

from __future__ import annotations

import ast
import importlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import settings


_TESTS_DIR = Path(__file__).resolve().parent

# This folder, for the offline tripwire every offline module carries.
sys.path[:0] = [str(_TESTS_DIR)]
from _offline_guard import setUpModule, tearDownModule  # noqa: E402, F401 - the bootstrap above; unittest calls the hooks by name

#: The `settings` calls a property module must make at top level, before its first
#: `@given` test.
_REGISTER = "register_profile"
_LOAD = "load_profile"

#: Fewer property modules than this means the discovery rule stopped finding one,
#: which would pass over that module silently. Restated, not counted off the folder:
#: a count taken from discovery agrees with discovery whatever it finds.
_MIN_PROPERTY_MODULES = 2

#: The problem a source with no `@given` test at all reports.
_NO_GIVEN_TEST = "no statement holds a @given test, so there is no order to check"


def _is_given(decorator: ast.expr) -> bool:
    """Whether a decorator is Hypothesis's `given`, called or bare.

    By name (`@given`, `@given(...)`) or by attribute (`@hypothesis.given`,
    `@hypothesis.given(...)`, or `given` on any other module alias).
    """
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute):
        return target.attr == "given"
    return isinstance(target, ast.Name) and target.id == "given"


def _holds_given_test(tree: ast.AST) -> bool:
    """Whether anything in `tree`, at any depth, carries a `@given` decorator."""
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and any(_is_given(decorator) for decorator in node.decorator_list)
        for node in ast.walk(tree)
    )


def _settings_call(node: ast.stmt) -> tuple[str, str | None] | None:
    """A top-level `settings.<method>(name, ...)` statement's method and profile name.

    The name is None when the first argument is not a string literal. Anything that
    is not such a statement gives None.
    """
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return None
    func = node.value.func
    if not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "settings"
    ):
        return None
    args = node.value.args
    first = args[0] if args else None
    name = first.value if isinstance(first, ast.Constant) else None
    return func.attr, name if isinstance(name, str) else None


def _first_index(calls: list[tuple[int, str, str | None]], method: str) -> int | None:
    """The statement index of the first `settings.<method>` call, if there is one."""
    return next((index for index, called, _ in calls if called == method), None)


def profile_order_problems(source: str) -> list[str]:
    """Every way `source` fails to load its profile before its first `@given` test.

    Empty when the module registers and loads a profile at top level, by name, ahead
    of the first top-level statement holding a `@given` decorator at any depth: a
    class of them, a class nesting one, or a module-level function. Only top-level
    `settings` calls count, because only they are sure to run at import, in order,
    before the tests are decorated.
    """
    body = ast.parse(source).body
    first_given = next(
        (index for index, node in enumerate(body) if _holds_given_test(node)), None
    )
    if first_given is None:
        return [_NO_GIVEN_TEST]
    calls = [
        (index, *found)
        for index, node in enumerate(body)
        if (found := _settings_call(node)) is not None
    ]
    problems: list[str] = []
    for method in (_REGISTER, _LOAD):
        index = _first_index(calls, method)
        if index is None:
            problems.append(f"no top-level settings.{method}(...) call")
        elif index > first_given:
            problems.append(
                f"settings.{method} sits after line {body[first_given].lineno}'s "
                "@given tests, which snapshotted their settings first"
            )
    registered = {name for _, called, name in calls if called == _REGISTER}
    for _, called, name in calls:
        if called != _LOAD:
            continue
        if name is None:
            problems.append(
                f"settings.{_LOAD} is not given its profile's name as a string "
                "literal, so which profile it loads cannot be checked"
            )
        elif name not in registered:
            problems.append(
                f"settings.{_LOAD} names {name!r}, which this module never registers"
            )
    return problems


def property_modules(folder: Path) -> list[Path]:
    """Every test module directly in `folder` holding a `@given` test anywhere."""
    return sorted(
        path
        for path in folder.glob("test_*.py")
        if _holds_given_test(ast.parse(path.read_text(encoding="utf-8")))
    )


_WELL_ORDERED = """
from hypothesis import given, settings, strategies as st
import unittest

settings.register_profile("demo", deadline=None)
settings.load_profile("demo")


class Plain(unittest.TestCase):
    def test_plain(self) -> None:
        pass


class Prop(unittest.TestCase):
    @given(st.integers())
    def test_prop(self, n: int) -> None:
        pass
"""

# The defect itself: both calls sit below the class whose tests they were meant for.
_LOADED_AFTER_THE_CLASS = """
from hypothesis import given, settings, strategies as st
import unittest


class Prop(unittest.TestCase):
    @given(st.integers())
    def test_prop(self, n: int) -> None:
        pass


settings.register_profile("demo", deadline=None)
settings.load_profile("demo")
"""


#: `_LOADED_AFTER_THE_CLASS`'s class, for swapping in other homes for its `@given` test.
_PROP_CLASS = """class Prop(unittest.TestCase):
    @given(st.integers())
    def test_prop(self, n: int) -> None:
        pass
"""

_NESTED_PROP_CLASS = """class Outer:
    class Prop(unittest.TestCase):
        @given(st.integers())
        def test_prop(self, n: int) -> None:
            pass
"""

_MODULE_LEVEL_PROP = """@given(st.integers())
def test_prop(n: int) -> None:
    pass
"""


class ProfileOrderCheckTest(unittest.TestCase):
    """The ordering check, over sources written here to fail in one way each."""

    def test_a_profile_loaded_before_the_first_given_class_passes(self) -> None:
        self.assertEqual(profile_order_problems(_WELL_ORDERED), [])

    def test_a_profile_loaded_after_the_given_class_is_flagged(self) -> None:
        problems = profile_order_problems(_LOADED_AFTER_THE_CLASS)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn(f"settings.{_REGISTER}", problems[0])
        self.assertIn(f"settings.{_LOAD}", problems[1])
        self.assertIn("after", problems[1])

    def test_a_module_that_never_loads_a_profile_is_flagged(self) -> None:
        source = _WELL_ORDERED.replace('settings.load_profile("demo")\n', "")
        self.assertEqual(
            profile_order_problems(source),
            [f"no top-level settings.{_LOAD}(...) call"],
        )

    def test_a_module_that_never_registers_a_profile_is_flagged(self) -> None:
        source = _WELL_ORDERED.replace(
            'settings.register_profile("demo", deadline=None)\n', ""
        )
        problems = profile_order_problems(source)
        self.assertIn(f"no top-level settings.{_REGISTER}(...) call", problems)

    def test_loading_a_profile_the_module_did_not_register_is_flagged(self) -> None:
        source = _WELL_ORDERED.replace('load_profile("demo")', 'load_profile("other")')
        self.assertEqual(
            profile_order_problems(source),
            ["settings.load_profile names 'other', which this module never registers"],
        )

    def test_a_profile_named_by_anything_but_a_string_literal_is_flagged(
        self,
    ) -> None:
        for call in ("load_profile(PROFILE)", "load_profile()", "load_profile(42)"):
            with self.subTest(call=call):
                source = _WELL_ORDERED.replace('load_profile("demo")', call)
                problems = profile_order_problems(source)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("string literal", problems[0])

    def test_other_top_level_calls_are_not_mistaken_for_the_profile(self) -> None:
        # The profile's own method names on another receiver, and a bare call, sit
        # where the profile would: only the `settings.` receiver makes them the profile.
        source = _LOADED_AFTER_THE_CLASS.replace(
            "import unittest\n",
            "import unittest\n"
            'other.register_profile("demo")\n'
            'other.load_profile("demo")\n'
            "print()\n",
        )
        self.assertEqual(len(profile_order_problems(source)), 2)

    def test_a_load_nested_under_a_statement_does_not_count(self) -> None:
        # Only a top-level call runs unconditionally at import, ahead of the classes.
        source = _WELL_ORDERED.replace(
            'settings.load_profile("demo")',
            'if True:\n    settings.load_profile("demo")',
        )
        self.assertIn(
            f"no top-level settings.{_LOAD}(...) call", profile_order_problems(source)
        )

    def test_a_bare_or_attribute_given_marks_the_class(self) -> None:
        # `@given` without a call, and `@hypothesis.given(...)`, are the same decorator.
        for spelling in ("@given", "@hypothesis.given(st.integers())"):
            with self.subTest(spelling=spelling):
                source = _LOADED_AFTER_THE_CLASS.replace(
                    "@given(st.integers())", spelling
                )
                self.assertEqual(len(profile_order_problems(source)), 2)

    def test_a_given_test_in_a_nested_class_is_found(self) -> None:
        # The class holding the test sits inside another top-level class.
        source = _LOADED_AFTER_THE_CLASS.replace(_PROP_CLASS, _NESTED_PROP_CLASS)
        problems = profile_order_problems(source)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("after", problems[1])

    def test_a_module_level_given_function_is_found(self) -> None:
        # A `@given` function at top level snapshots its settings just the same.
        source = _LOADED_AFTER_THE_CLASS.replace(_PROP_CLASS, _MODULE_LEVEL_PROP)
        problems = profile_order_problems(source)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("after", problems[1])

    def test_a_module_with_no_given_test_is_flagged(self) -> None:
        source = _WELL_ORDERED.replace("@given(st.integers())", "@staticmethod")
        self.assertEqual(profile_order_problems(source), [_NO_GIVEN_TEST])


class PropertyModuleDiscoveryTest(unittest.TestCase):
    """Discovery picks the test modules holding a `@given` test, however spelled."""

    def test_only_test_modules_holding_a_given_test_are_found(self) -> None:
        test = "def test_prop(n: int) -> None:\n    pass\n"
        # Held apart from the newline before it, which the PII scan would otherwise
        # read, with the decorator, as an address.
        attribute_given = "@hypothesis.given()"
        files = {
            "test_imports_given.py": f"from hypothesis import given\n@given()\n{test}",
            "test_plain_import.py": f"import hypothesis\n{attribute_given}\n{test}",
            "test_bare_given.py": f"from hypothesis import given\n@given\n{test}",
            "test_nested_given.py": "from hypothesis import given\n"
            "class Outer:\n"
            "    class Inner:\n"
            "        @given()\n"
            "        def test_prop(self) -> None:\n"
            "            pass\n",
            "test_imports_only.py": "from hypothesis import given\n",
            "test_settings_only.py": "from hypothesis import settings\n",
            "test_other_decorator.py": f"@staticmethod\n{test}",
            "helper_with_given.py": f"from hypothesis import given\n@given()\n{test}",
        }
        with TemporaryDirectory() as raw:
            root = Path(raw)
            for name, text in files.items():
                (root / name).write_text(text, encoding="utf-8", newline="\n")
            found = [path.name for path in property_modules(root)]
        expected = [
            "test_bare_given.py",
            "test_imports_given.py",
            "test_nested_given.py",
            "test_plain_import.py",
        ]
        self.assertEqual(found, expected)


class PropertyModuleOrderTest(unittest.TestCase):
    """Every real property module here passes the ordering check."""

    def test_every_property_module_loads_its_profile_before_its_given_classes(
        self,
    ) -> None:
        modules = property_modules(_TESTS_DIR)
        self.assertGreaterEqual(
            len(modules),
            _MIN_PROPERTY_MODULES,
            f"found {[path.name for path in modules]}: the discovery of modules "
            "holding a @given test missed one, so its order goes unchecked",
        )
        for path in modules:
            with self.subTest(module=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertEqual(profile_order_problems(source), [])


class ProfileDeadlineTest(unittest.TestCase):
    """Each profile the property classes load sets no per-example wall-clock deadline.

    Read from Hypothesis's process-wide registry, which each module of properties fills
    at import. `setUpClass` imports every discovered property module itself, so the
    registry is filled whether this file runs alone or under the aggregator; under the
    aggregator the names are already in `sys.modules`, so nothing imports twice. Sound
    only while each name is registered nowhere else: the registry keeps the last
    registration of a name, so a shared name would read whichever module registered it
    last.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """Import each property module by name, registering its profile."""
        # `_TESTS_DIR` is on sys.path (the bootstrap at the top of this file).
        for path in property_modules(_TESTS_DIR):
            importlib.import_module(path.stem)

    def test_the_url_property_profile_carries_no_deadline(self) -> None:
        # test_artwork_url_properties.py's profile.
        self.assertIsNone(settings.get_profile("cover_art_properties").deadline)

    def test_the_script_property_profile_carries_no_deadline(self) -> None:
        # test_artwork_match_scripts.py's profile.
        profile = settings.get_profile("artwork_match_scripts_properties")
        self.assertIsNone(profile.deadline)

    def test_the_symbol_property_profile_carries_no_deadline(self) -> None:
        # test_artwork_match_symbols.py's profile.
        profile = settings.get_profile("artwork_match_symbols_properties")
        self.assertIsNone(profile.deadline)


if __name__ == "__main__":
    unittest.main()
