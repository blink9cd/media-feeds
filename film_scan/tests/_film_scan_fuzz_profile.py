"""The one deterministic Hypothesis profile every ``@given`` suite in ``film_scan`` runs under.

Importing this module registers and loads the ``deterministic`` profile. A suite
holding a property imports it, before its first ``@given``, for its side effect alone, so
the suite still imports and runs alone and never relies on another having loaded the
profile first: a ``@given`` property snapshots its settings when it is decorated, and the
order a runner imports two suites in is not a contract.

It is a profile, never an ``@settings`` decorator, because a fuzzing campaign may
re-apply its own settings object to each property, and Hypothesis refuses a second one
on a function already carrying one. A suite's own per-test ``@settings`` (an example
count, a deadline) still applies on top, and inherits everything it does not set from
here. ``derandomize`` fixes the example sequence so a test run is reproducible;
``deadline=None`` and the ``too_slow`` suppression keep a loaded machine's timing from
failing a property that holds.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

#: The profile name a presence check recognizes as the deterministic one.
PROFILE = "deterministic"

settings.register_profile(
    PROFILE,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(PROFILE)
