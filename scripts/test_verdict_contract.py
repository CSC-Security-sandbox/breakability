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
