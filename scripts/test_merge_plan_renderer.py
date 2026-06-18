#!/usr/bin/env python3
"""Regression tests for the merge-plan renderer embedded in post-fallback-comments.sh."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("post-fallback-comments.sh")


def extract_merge_plan_python():
    body = SCRIPT.read_text()
    start = body.index("MERGE_PLAN_BODY=$(python3 << 'PYEOF'")
    start = body.index("\n", start) + 1
    end = body.index("\nPYEOF\n)", start)
    return body[start:end]


def extract_policy_overlay_python():
    body = SCRIPT.read_text()
    marker = 'python3 - "$RESULTS_FILE" <<\'PYEOF\' || echo "[warn] policy lowering overlay unavailable; using legacy verdict_v2"'
    start = body.index(marker)
    start = body.index("\n", start) + 1
    end = body.index("\nPYEOF\n\nget_verdict_v2", start)
    return body[start:end]


def base_pr(**overrides):
    pr = {
        "package": "example.com/lib",
        "ecosystem": "go",
        "from": "1.0.0",
        "to": "1.0.1",
        "bump": "patch",
        "dep_type": "production",
        "install_ok": True,
        "pkg_dir": "/",
        "verification_label": "L2_type_checked",
        "build": {"verdict": "pass", "new_errors": [], "main_exit": 0},
        "cves": [],
        "verdict_v2": {
            "verdict": "REVIEW",
            "severity": "medium",
            "confidence": "L2",
            "priority": "P2",
            "reason": "review needed",
            "residual": {"summary": "review needed", "check": "review:default"},
        },
    }
    pr.update(overrides)
    return pr


def render_plan(results):
    code = extract_merge_plan_python()
    with tempfile.TemporaryDirectory() as td:
        results_path = Path(td) / "build-results.json"
        results_path.write_text(json.dumps(results))
        code = code.replace('open("/tmp/build-results.json")', f'open({str(results_path)!r})')
        rendered = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "GH_TOKEN": ""},
        )
        return rendered.stdout


def apply_policy_overlay(results):
    code = extract_policy_overlay_python()
    with tempfile.TemporaryDirectory() as td:
        results_path = Path(td) / "build-results.json"
        results_path.write_text(json.dumps(results))
        subprocess.run(
            [sys.executable, "-c", code, str(results_path)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return json.loads(results_path.read_text())


def section(markdown, heading):
    _, rest = markdown.split(heading, 1)
    return rest.split("\n## ", 1)[0]


def has_pr(markdown, number):
    return re.search(rf"PR #{number}(?!\d)", markdown) is not None


class MergePlanRendererTests(unittest.TestCase):
    def test_policy_glance_can_lower_legacy_medium_review(self):
        out = apply_policy_overlay({
            "prs": {
                "1": base_pr(
                    policy_lowering={
                        "decision": {
                            "verdict": "GLANCE",
                            "severity": "low",
                            "confidence": "medium",
                            "reason_code": "glance:clean-missing-release-notes",
                            "display_reason": "build, tests, and API diff are clean; changelog is unavailable",
                        },
                        "bundle": {
                            "signals": {
                                "build": {"status": "pass"},
                                "test": {"status": "pass"},
                                "api_diff": {"status": "pass"},
                                "release_notes": {"status": "unavailable"},
                                "security": {"status": "not_applicable"},
                            },
                        },
                    },
                ),
                "2": base_pr(
                    verdict_v2={
                        "verdict": "REVIEW",
                        "severity": "high",
                        "confidence": "L4",
                        "priority": "P1",
                        "reason": "high-risk review",
                        "residual": {"summary": "high-risk review", "check": "review:declared-break"},
                    },
                    policy_lowering={
                        "decision": {
                            "verdict": "GLANCE",
                            "severity": "low",
                            "confidence": "medium",
                            "reason_code": "glance:clean-missing-release-notes",
                            "display_reason": "build, tests, and API diff are clean; changelog is unavailable",
                        },
                        "bundle": {"signals": {"build": {"status": "pass"}}},
                    },
                ),
            },
        })

        low = out["prs"]["1"]["verdict_v2"]
        # #121 semantics (the fix): a GLANCE decision = clean build/tests/api-diff with only
        # missing-changelog uncertainty -> SAFE/Low (Safe to merge / optional glance), NOT a
        # REVIEW. Mapping GLANCE->REVIEW here was the #128 review-wall regression.
        self.assertEqual(low["verdict"], "SAFE")
        self.assertEqual(low["severity"], "low")
        self.assertEqual(low["residual"]["check"], "glance:clean-missing-release-notes")

        high = out["prs"]["2"]["verdict_v2"]
        self.assertEqual(high["severity"], "high")
        self.assertEqual(high["residual"]["check"], "review:declared-break")

    def test_glance_review_rows_move_out_of_manual_review(self):
        plan = render_plan({
            "prs": {
                "1": base_pr(
                    package="example.com/low",
                    verdict_v2={
                        "verdict": "REVIEW",
                        "severity": "low",
                        "confidence": "L3",
                        "priority": "P3",
                        "reason": "build, tests, and API diff are clean; changelog is unavailable",
                        "residual": {
                            "summary": "build, tests, and API diff are clean; changelog is unavailable",
                            "check": "glance:clean-missing-release-notes",
                        },
                    },
                ),
                "2": base_pr(
                    package="example.com/security",
                    cves=["CVE-2026-0001"],
                    verdict_v2={
                        "verdict": "REVIEW",
                        "severity": "low",
                        "confidence": "L3",
                        "priority": "P0",
                        "reason": "security-sensitive update requires human review",
                        "residual": {
                            "summary": "security-sensitive update requires human review",
                            "check": "glance:clean-missing-release-notes",
                        },
                    },
                ),
                "3": base_pr(package="example.com/medium"),
            },
            "metadata": {},
            "cross_pr_deps": [],
            "security_posture": {},
        })

        optional = section(plan, "## 🟡 Optional Glance — Low Breakability")
        manual = section(plan, "## ⚠️ Manual Review Needed")

        self.assertIn("GLANCE then MERGE — low breakability:** #1", plan)
        self.assertTrue(has_pr(optional, "1"))
        self.assertFalse(has_pr(manual, "1"))
        self.assertNotIn("Merge Risk: Medium", optional)

        self.assertFalse(has_pr(optional, "2"))
        self.assertTrue(has_pr(manual, "2"))
        self.assertTrue(has_pr(manual, "3"))


if __name__ == "__main__":
    unittest.main()
