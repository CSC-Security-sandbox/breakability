#!/usr/bin/env python3
"""Unit tests for verdict_contract.py — the single authoritative verdict source."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verdict_contract import (  # noqa: E402
    BUCKET_BLOCKED,
    BUCKET_REVIEW,
    BUCKET_SAFE,
    PRED_AUTO_CLEAR,
    PRED_FIX,
    PRED_REVIEW,
    StageNoOpError,
    assert_stage_did_work,
    authoritative_verdict,
    map_policy_decision,
    prediction_for_pr,
)


def _pr(decision=None, **extra):
    pr = dict(extra)
    if decision is not None:
        pr["policy_lowering"] = {"decision": decision}
    return pr


class TestActionToBucketMapping(unittest.TestCase):
    def test_fix_maps_to_blocked(self):
        self.assertEqual(map_policy_decision({"verdict": "FIX"})["verdict"], BUCKET_BLOCKED)

    def test_review_maps_to_review(self):
        self.assertEqual(map_policy_decision({"verdict": "REVIEW"})["verdict"], BUCKET_REVIEW)

    def test_abstain_maps_to_review(self):
        self.assertEqual(map_policy_decision({"verdict": "ABSTAIN"})["verdict"], BUCKET_REVIEW)

    def test_merge_maps_to_safe(self):
        self.assertEqual(map_policy_decision({"verdict": "MERGE"})["verdict"], BUCKET_SAFE)

    def test_glance_maps_to_safe_THE_FIX(self):
        # The #121->#128 regression: GLANCE must be SAFE, not REVIEW.
        out = map_policy_decision({"verdict": "GLANCE"})
        self.assertEqual(out["verdict"], BUCKET_SAFE)
        self.assertEqual(out["severity"], "low")

    def test_unknown_action_returns_none(self):
        self.assertIsNone(map_policy_decision({"verdict": "WAT"}))
        self.assertIsNone(map_policy_decision({}))
        self.assertIsNone(map_policy_decision(None))

    def test_explicit_severity_preserved(self):
        out = map_policy_decision({"verdict": "REVIEW", "severity": "high"})
        self.assertEqual(out["severity"], "high")
        self.assertEqual(out["priority"], "P1")

    def test_fix_priority_is_p0(self):
        self.assertEqual(map_policy_decision({"verdict": "FIX"})["priority"], "P0")


class TestAuthoritativeVerdictPrecedence(unittest.TestCase):
    def test_hard_fix_floor_build_fail_wins_over_everything(self):
        pr = _pr({"verdict": "MERGE"}, build={"verdict": "fail"},
                 verdict_v2={"verdict": "SAFE"},
                 ai_adjudication={"applied": "downgrade_to_safe", "evidence": "x"})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_BLOCKED)
        self.assertEqual(v["source"], "hard_fix_floor")

    def test_security_introduced_is_hard_floor(self):
        pr = _pr({"verdict": "MERGE", "reason_code": "security:introduced"})
        self.assertEqual(authoritative_verdict(pr)["verdict"], BUCKET_BLOCKED)

    def test_ai_downgrade_to_safe(self):
        pr = _pr({"verdict": "REVIEW"},
                 ai_adjudication={"applied": "downgrade_to_safe", "evidence": "not imported"})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_SAFE)
        self.assertEqual(v["source"], "ai:downgrade_to_safe")

    def test_ai_needs_change_keeps_review(self):
        pr = _pr({"verdict": "MERGE"},
                 ai_adjudication={"applied": "needs_change", "evidence": "behaviour changed"})
        self.assertEqual(authoritative_verdict(pr)["verdict"], BUCKET_REVIEW)

    def test_materialised_v2_used_when_present(self):
        pr = _pr({"verdict": "MERGE"}, verdict_v2={"verdict": "REVIEW", "severity": "medium"})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_REVIEW)
        self.assertEqual(v["source"], "verdict_v2")

    def test_invalid_v2_ignored_falls_through_to_policy(self):
        pr = _pr({"verdict": "MERGE"}, verdict_v2={"verdict": ""})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_SAFE)
        self.assertEqual(v["source"], "policy_lowering")

    def test_policy_used_when_no_v2(self):
        # The exact "0 PRs at reconcile time" scenario: v2 absent, policy present.
        pr = _pr({"verdict": "GLANCE"})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_SAFE)
        self.assertEqual(v["source"], "policy_lowering")

    def test_fail_closed_to_review_when_nothing(self):
        v = authoritative_verdict({})
        self.assertEqual(v["verdict"], BUCKET_REVIEW)
        self.assertEqual(v["source"], "fail_closed")


class TestBreakingChangelogReachableFloor(unittest.TestCase):
    """Rule 4: breaking changelog + reachable + no passing tests → REVIEW minimum."""

    def test_pr66_breaking_reachable_no_tests_gets_review(self):
        """PR #66 scenario: major, changelog=breaking, reachable=1 file, tests=skip."""
        pr = _pr({"verdict": "MERGE"},
                 deterministic={"changelogSignal": "### Breaking Changes\nRemoved callback API"},
                 files_importing=["src/auth.ts"],
                 test={"ran": False})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_REVIEW)
        self.assertEqual(v["source"], "breaking_changelog_reachable_floor")

    def test_breaking_reachable_tests_pass_stays_safe(self):
        """If tests ran and passed, the floor does NOT fire."""
        pr = _pr({"verdict": "MERGE"},
                 deterministic={"changelogSignal": "### Breaking Changes\nAPI removed"},
                 files_importing=["src/auth.ts"],
                 test={"ran": True, "exit": 0})
        v = authoritative_verdict(pr)
        self.assertNotEqual(v["source"], "breaking_changelog_reachable_floor")

    def test_no_breaking_changelog_stays_safe(self):
        """Non-breaking changelog does not trigger the floor."""
        pr = _pr({"verdict": "MERGE"},
                 deterministic={"changelogSignal": "Bug fixes and improvements"},
                 files_importing=["src/auth.ts"],
                 test={"ran": False})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_SAFE)

    def test_not_reachable_stays_safe(self):
        """Breaking changelog but no files import → stays SAFE."""
        pr = _pr({"verdict": "MERGE"},
                 deterministic={"changelogSignal": "### Breaking Changes\nRemoved API"},
                 files_importing=[],
                 test={"ran": False})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_SAFE)

    def test_actions_fast_path_exits_before_floor(self):
        """PR #59 scenario: actions ecosystem exits at fast-path before floor check."""
        pr = _pr({"verdict": "MERGE"},
                 ecosystem="actions",
                 build={"verdict": "pass"},
                 deterministic={"changelogSignal": "### Breaking Changes\nRenamed action"},
                 files_importing=["src/ci.ts"],
                 test={"ran": False})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_SAFE)
        self.assertEqual(v["source"], "actions_fast_path")

    def test_hard_fix_floor_exits_before_breaking_floor(self):
        """PR #68 scenario: test failure → BLOCKED via hard_fix_floor before this check."""
        pr = _pr({"verdict": "FIX"},
                 build={"verdict": "pass"},
                 test={"ran": True, "exit": 1, "verdict": "fail"},
                 deterministic={"changelogSignal": "### Breaking Changes\nAPI removed"},
                 files_importing=["src/auth.ts"])
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_BLOCKED)
        self.assertEqual(v["source"], "hard_fix_floor")

    def test_deprecation_triggers_floor(self):
        """Deprecation notices also trigger the floor."""
        pr = _pr({"verdict": "MERGE"},
                 deterministic={"changelogSignal": "Deprecated: old API will be removed in v5"},
                 files_importing=["lib/client.js"],
                 test={"ran": False})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_REVIEW)
        self.assertEqual(v["source"], "breaking_changelog_reachable_floor")


class TestPreExistingTestFailure(unittest.TestCase):
    """Rule 1b: pre-existing test failures (same exit code on main) get REVIEW, not BLOCKED."""

    def test_new_test_failure_gets_blocked(self):
        pr = _pr({"verdict": "MERGE"},
                 build={"verdict": "pass"},
                 test={"ran": True, "exit": 1, "main_test_exit": 0, "output_tail": "FAILED"})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_BLOCKED)
        self.assertEqual(v["source"], "hard_fix_floor")

    def test_preexisting_test_failure_gets_review(self):
        """VCP PR#23 scenario: test fails on both PR and main with same exit code."""
        pr = _pr({"verdict": "REVIEW"},
                 build={"verdict": "pass"},
                 test={"ran": True, "exit": 1, "main_test_exit": 1, "output_tail": "FAILED"})
        v = authoritative_verdict(pr)
        self.assertNotEqual(v["verdict"], BUCKET_BLOCKED)
        self.assertNotEqual(v["source"], "hard_fix_floor")

    def test_preexisting_test_failure_no_main_exit_gets_blocked(self):
        """If main_test_exit is absent, assume new failure → BLOCKED."""
        pr = _pr({"verdict": "MERGE"},
                 build={"verdict": "pass"},
                 test={"ran": True, "exit": 1, "output_tail": "FAILED"})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_BLOCKED)
        self.assertEqual(v["source"], "hard_fix_floor")

    def test_different_exit_codes_gets_blocked(self):
        """Different exit codes means the upgrade changed the failure mode → BLOCKED."""
        pr = _pr({"verdict": "MERGE"},
                 build={"verdict": "pass"},
                 test={"ran": True, "exit": 2, "main_test_exit": 1, "output_tail": "FAILED"})
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_BLOCKED)
        self.assertEqual(v["source"], "hard_fix_floor")


class TestAIDowngradeBreakingChangelog(unittest.TestCase):
    """Rule 4+6 via _probe_escalation: AI downgrade_to_safe must not bypass
    breaking changelog + reachable + no passing tests."""

    def test_probe_escalation_with_breaking_changelog_stays_review(self):
        """NDM PR#66 fixture: AI says SAFE, but breaking changelog + reachable + no tests.
        Rule 4 guard at authoritative_verdict line 369 fires first (higher precedence)."""
        pr = _pr(
            {"verdict": "REVIEW"},
            ai_adjudication={"applied": "downgrade_to_safe", "evidence": "API still exported"},
            behavioral_grade={"same_behavior": True},
            deterministic={"changelogSignal": "breaking change: removed callback API"},
            files_importing=["src/auth/JwtService.ts"],
            test={"ran": False},
        )
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_REVIEW)
        self.assertIn("breaking_changelog", v.get("source", ""))

    def test_ai_downgrade_safe_when_no_breaking_changelog(self):
        """AI downgrade stays SAFE when changelog is clean."""
        pr = _pr(
            {"verdict": "REVIEW"},
            ai_adjudication={"applied": "downgrade_to_safe", "evidence": "not imported"},
            deterministic={"changelogSignal": "bug fixes only"},
            files_importing=[],
            test={"ran": False},
        )
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_SAFE)

    def test_ai_downgrade_safe_when_tests_pass(self):
        """AI downgrade stays SAFE when tests passed (even with breaking changelog)."""
        pr = _pr(
            {"verdict": "REVIEW"},
            ai_adjudication={"applied": "downgrade_to_safe", "evidence": "tests pass"},
            deterministic={"changelogSignal": "### Breaking Changes\nRemoved old API"},
            files_importing=["src/auth.ts"],
            test={"ran": True, "exit": 0},
        )
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_SAFE)

    def test_probe_different_plus_breaking_changelog(self):
        """Both probe mismatch and breaking changelog → REVIEW via Rule 4 (higher precedence)."""
        pr = _pr(
            {"verdict": "REVIEW"},
            ai_adjudication={"applied": "downgrade_to_safe", "evidence": "minor"},
            behavioral_grade={"same_behavior": False, "behavior_changed": True},
            deterministic={"changelogSignal": "breaking change: API removed"},
            files_importing=["src/auth.ts"],
            test={"ran": False},
        )
        v = authoritative_verdict(pr)
        self.assertEqual(v["verdict"], BUCKET_REVIEW)
        self.assertIn("breaking_changelog", v.get("source", ""))


class TestPrediction(unittest.TestCase):
    def test_glance_predicts_auto_clear(self):
        self.assertEqual(prediction_for_pr(_pr({"verdict": "GLANCE"})), PRED_AUTO_CLEAR)

    def test_merge_predicts_auto_clear(self):
        self.assertEqual(prediction_for_pr(_pr({"verdict": "MERGE"})), PRED_AUTO_CLEAR)

    def test_review_predicts_review(self):
        self.assertEqual(prediction_for_pr(_pr({"verdict": "REVIEW"})), PRED_REVIEW)

    def test_build_fail_predicts_fix(self):
        self.assertEqual(prediction_for_pr(_pr({"verdict": "FIX"}, build={"verdict": "fail"})), PRED_FIX)

    def test_fix_action_predicts_fix(self):
        self.assertEqual(prediction_for_pr(_pr({"verdict": "FIX"})), PRED_FIX)


class TestStageAssertion(unittest.TestCase):
    def test_zero_processed_with_input_raises(self):
        with self.assertRaises(StageNoOpError):
            assert_stage_did_work("reconcile", input_count=5, processed_count=0)

    def test_zero_processed_allowed_when_opted_in(self):
        assert_stage_did_work("reconcile", input_count=5, processed_count=0, allow_empty=True)

    def test_no_input_is_fine(self):
        assert_stage_did_work("reconcile", input_count=0, processed_count=0)

    def test_work_done_is_fine(self):
        assert_stage_did_work("reconcile", input_count=5, processed_count=3)


if __name__ == "__main__":
    unittest.main()
