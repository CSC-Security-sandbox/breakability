#!/usr/bin/env python3
"""Safety test suite for the break-class router (no pytest dependency).

Run: python3 .github/scripts/test_break_class_router.py
Exits non-zero on any failure.

The cardinal invariant under test: a changed default / config / option / knob is NEVER
routed to a probe (call_observable) unless the SAME bullet carries explicit call-local
evidence (return value / error / format / signature). Probing a stateful/load/resource/
timing default constructs fine at both versions and emits a confident FALSE GREEN, so
the router must keep those on the reasoning path (not_observable).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from break_class_router import (  # noqa: E402
    classify_bullet, classify_break,
    NOT_OBSERVABLE, CALL_OBSERVABLE, AMBIGUOUS,
)

# (bullet, expected_class)
BULLET_CASES = [
    # --- FALSE-GREEN class: MUST route NOT_OBSERVABLE (a probe would clear them green) ---
    ("default cardinality limit changed 0 -> 2000", NOT_OBSERVABLE),
    ("changed the default timeout to 30s", NOT_OBSERVABLE),
    ("default request budget changed 100 -> 50", NOT_OBSERVABLE),   # noun NOT in resource list
    ("default retry policy changed", NOT_OBSERVABLE),
    ("default queue size increased", NOT_OBSERVABLE),
    ("new default flush interval", NOT_OBSERVABLE),
    ("changed the default mode", NOT_OBSERVABLE),                   # generic config, no call-local
    ("default connection reuse behavior changed", NOT_OBSERVABLE),
    ("the default consistency level is now eventual", NOT_OBSERVABLE),
    ("default backpressure mode is now drop", NOT_OBSERVABLE),
    ("default cache eviction behaviour changed", NOT_OBSERVABLE),
    # --- GENUINE call-observable: MUST stay CALL_OBSERVABLE (probe is competent) ---
    ("function now returns an error instead of nil", CALL_OBSERVABLE),
    ("default return value is now 0 instead of -1", CALL_OBSERVABLE),
    ("the default formatter output format changed", CALL_OBSERVABLE),
    ("New() signature changed: added a parameter", CALL_OBSERVABLE),
    ("now rejects empty strings with a validation error", CALL_OBSERVABLE),
    ("default serialization now emits RFC3339 format", CALL_OBSERVABLE),
    ("no longer returns nil; returns an empty slice", CALL_OBSERVABLE),
    # --- runtime/load dimension wins outright ---
    ("general performance improvements", NOT_OBSERVABLE),  # "performance" is a load marker
    ("reduced memory usage under load", NOT_OBSERVABLE),
    # --- nothing actionable ---
    ("internal refactor", AMBIGUOUS),
    ("", AMBIGUOUS),
]

# (bullets_list, expected_aggregate_class)
AGG_CASES = [
    # one observable + one stateful -> whole PR NOT_OBSERVABLE (precedence)
    (["function now returns an error", "default cardinality limit 0 -> 2000"], NOT_OBSERVABLE),
    # only observable -> probe
    (["the output format changed to RFC3339"], CALL_OBSERVABLE),
    # only config-without-call-local -> reasoning
    (["changed the default request budget"], NOT_OBSERVABLE),
    # empty -> ambiguous
    ([], AMBIGUOUS),
]


def main():
    fails = 0
    for bullet, expected in BULLET_CASES:
        klass, markers = classify_bullet(bullet)
        ok = klass == expected
        if not ok:
            fails += 1
            print(f"FAIL bullet: got={klass} exp={expected} markers={markers} :: {bullet!r}")
    for bullets, expected in AGG_CASES:
        klass = classify_break(bullets)["class"]
        ok = klass == expected
        if not ok:
            fails += 1
            print(f"FAIL agg: got={klass} exp={expected} :: {bullets!r}")
    total = len(BULLET_CASES) + len(AGG_CASES)
    if fails:
        print(f"\n{fails}/{total} FAILED")
        return 1
    print(f"OK: all {total} router safety cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
