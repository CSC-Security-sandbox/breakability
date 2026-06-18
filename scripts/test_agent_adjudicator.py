"""Tests for agent_adjudicator.adjudicate_reachability."""

import unittest

from agent_adjudicator import adjudicate_reachability


def _pr(**kw):
    base = {
        "package": "github.com/example/lib",
        "deterministic": {},
        "build": {"verdict": "pass"},
    }
    base.update(kw)
    return base


class AdjudicatorTests(unittest.TestCase):
    def test_reached_relevant_when_changed_symbol_is_called(self):
        pr = _pr(deterministic={
            "api_changes_detail": [
                {"kind": "type_changed", "symbol": "NewWithInstance"},
                {"kind": "removed", "symbol": "n.Buffer"},
            ]
        })
        ci = {"callsites": [{"symbol": "NewWithInstance", "file": "db/migrate.go", "line": 86, "is_test": False}]}
        out = adjudicate_reachability(pr, ci, {})
        self.assertEqual(out["verdict"], "REACHED_RELEVANT")
        self.assertIn("NewWithInstance", out["matched_symbols"])
        self.assertTrue(out["manual_review_required"])
        self.assertFalse(out.get("recommend_lower"))

    def test_method_receiver_variant_matches(self):
        # changed "Error.Code" should match a called "Error.Code".
        pr = _pr(deterministic={"api_changes_detail": [{"kind": "type_changed", "symbol": "Error.Code"}]})
        ci = {"callsites": [{"symbol": "Error.Code", "file": "x.go", "line": 1, "is_test": False}]}
        out = adjudicate_reachability(pr, ci, {})
        self.assertEqual(out["verdict"], "REACHED_RELEVANT")

    def test_test_only_callsite_does_not_count_as_reached(self):
        pr = _pr(deterministic={"api_changes_detail": [{"kind": "removed", "symbol": "Foo"}]})
        ci = {"callsites": [{"symbol": "Foo", "file": "x_test.go", "line": 1, "is_test": True}]}
        out = adjudicate_reachability(pr, ci, {})
        self.assertNotEqual(out["verdict"], "REACHED_RELEVANT")

    def test_not_reached_when_reachability_absent(self):
        pr = _pr(deterministic={"api_changes_detail": [{"kind": "removed", "symbol": "Gone"}]})
        ci = {"callsites": [], "impact": "NOT_REACHED", "reachability_verdict": "ABSENT"}
        out = adjudicate_reachability(pr, ci, {})
        self.assertEqual(out["verdict"], "NOT_REACHED")
        self.assertFalse(out["manual_review_required"])
        self.assertTrue(out["recommend_lower"])

    def test_not_reached_never_recommends_lower_when_safety_locked(self):
        pr = _pr(cves=["CVE-2025-1"], deterministic={"api_changes_detail": [{"kind": "removed", "symbol": "Gone"}]})
        ci = {"callsites": [], "impact": "NOT_REACHED"}
        out = adjudicate_reachability(pr, ci, {})
        self.assertEqual(out["verdict"], "NOT_REACHED")
        self.assertTrue(out["safety_locked"])
        self.assertFalse(out["recommend_lower"])

    def test_needs_agent_when_unresolved(self):
        pr = _pr(deterministic={"api_changes_detail": [
            {"kind": "removed", "symbol": "SearchV2JQL"},
            {"kind": "signature_changed", "symbol": "SearchOptionsV2"},
        ]})
        ci = {"callsites": [], "impact": "UNCERTAIN", "reachability_verdict": "UNCERTAIN"}
        out = adjudicate_reachability(pr, ci, {})
        self.assertEqual(out["verdict"], "NEEDS_AGENT")
        self.assertTrue(out["manual_review_required"])
        self.assertIsNotNone(out["agent_task"])
        self.assertIn("SearchV2JQL", out["agent_task"])

    def test_build_failure_is_safety_locked(self):
        pr = _pr(build={"verdict": "pre_existing_plus_new"},
                 deterministic={"api_changes_detail": [{"kind": "removed", "symbol": "X"}]})
        ci = {"callsites": [], "impact": "NOT_REACHED"}
        out = adjudicate_reachability(pr, ci, {})
        self.assertTrue(out["safety_locked"])
        self.assertFalse(out["recommend_lower"])

    def test_non_breaking_changes_ignored(self):
        pr = _pr(deterministic={"api_changes_detail": [{"kind": "added", "symbol": "NewThing"}]})
        out = adjudicate_reachability(pr, {"callsites": []}, {})
        self.assertEqual(out["changed_symbols"], [])
        self.assertEqual(out["verdict"], "NEEDS_AGENT")

    def test_package_pseudo_symbol_ignored(self):
        pr = _pr(deterministic={"api_changes_detail": [
            {"kind": "removed", "symbol": "package github.com/lib/pq/cmd/pqlisten"},
            {"kind": "type_changed", "symbol": "Error.Code"},
        ]})
        out = adjudicate_reachability(pr, {"callsites": []}, {})
        self.assertEqual(out["changed_symbols"], ["Error.Code"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
