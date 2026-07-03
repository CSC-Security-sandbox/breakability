#!/usr/bin/env python3
"""Structural guard against the #121 -> #128 merge-plan "review wall" regression.

#121 (GOOD) categorised 33 PRs into a product-useful plan with non-empty SAFE lanes:
  * "Safe to Merge — Tests Pass (L4 ...)"
  * "Build Passes — Review Recommended (L2/L3 ...)"
  * "Optional Glance — Low Breakability"
A typed-verdict rewrite (#128, BROKEN) collapsed both SAFE lanes to 0 and ballooned
"Review recommended (Medium)" — a review wall — because GLANCE was mapped to REVIEW.

This test pins the structural invariant a healthy merge plan MUST satisfy. It runs against
the committed #121 golden (proving the invariant encodes the good baseline) and asserts the
#128 broken sample VIOLATES it (proving the guard actually catches the regression). Wire
``assert_plan_not_a_review_wall`` to the live-rendered merge plan in CI to block recurrence.
"""
import os
import re
import unittest

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

# Summary-table rows that prove SAFE lanes exist. A flat review wall zeroes these.
SAFE_LANE_ROWS = [
    r"Safe to merge\s*—\s*tests pass \(L4\)\s*\|\s*(\d+)",
    r"Build passes\s*—\s*review recommended \(L2/L3\)\s*\|\s*(\d+)",
]


def _counts(body, patterns):
    out = []
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        out.append(int(m.group(1)) if m else None)
    return out


def assert_plan_not_a_review_wall(body):
    """Raise AssertionError if the rendered merge plan collapsed its SAFE lanes.

    Invariant: at least one of the SAFE verification lanes ("Safe to merge (L4)",
    "Build passes (L2/L3)") must be present AND not all-zero. A plan where every SAFE
    lane is 0 is the #128 review wall.
    """
    counts = _counts(body, SAFE_LANE_ROWS)
    present = [c for c in counts if c is not None]
    if not present:
        raise AssertionError("merge plan has no SAFE verification lanes at all (#128 review wall)")
    if all(c == 0 for c in present):
        raise AssertionError(
            "all SAFE lanes are 0 (Safe-to-merge L4 + Build-passes L2/L3) — this is the "
            "#128 review wall; clean-build PRs are being forced into REVIEW"
        )


class TestMergePlanReviewWallGuard(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(GOLDEN_DIR, name), encoding="utf-8") as fh:
            return fh.read()

    def test_121_golden_passes_invariant(self):
        body = self._read("merge_plan_121.golden.md")
        # Should not raise — #121 is the healthy baseline.
        assert_plan_not_a_review_wall(body)
        l4, l23 = _counts(body, SAFE_LANE_ROWS)
        self.assertEqual(l4, 3)
        self.assertEqual(l23, 5)

    def test_128_broken_violates_invariant(self):
        body = self._read("merge_plan_128.broken.md")
        with self.assertRaises(AssertionError):
            assert_plan_not_a_review_wall(body)

    def test_121_has_optional_glance_lane(self):
        body = self._read("merge_plan_121.golden.md")
        self.assertRegex(body, r"Optional glance \(Low\)")


if __name__ == "__main__":
    unittest.main()
