#!/usr/bin/env python3
"""Tests for generate_ai_comments.py"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate_ai_comments import (
    _build_per_pr_prompt,
    _validate_comment,
    _near_valid,
    _fallback_comment,
    _ensure_marker,
    _extract_pr_data,
    _enforce_verdict_floor,
    _rewrite_noncanonical_arbiter,
    _strip_merge_encouraging,
    _fix_body_verdict_contradictions,
    _enforce_merge_risk_tag,
    _normalize_verdict_text,
    _inject_verdict_logic,
    _strip_agent_narration,
    _strip_govulncheck,
    _sanitize_comment,
    _downgrade_mismatched_probe,
    _guard_empty_build_output,
    _strip_wrong_ecosystem_refs,
    _validate_merge_risk_tag,
    _inject_merge_risk,
)


SAMPLE_PR = {
    "pr_num": "42",
    "package": "lodash",
    "from": "4.17.20",
    "to": "4.17.21",
    "bump": "patch",
    "dep_type": "production",
    "build": {"verdict": "pass", "pr_exit": 0},
    "test": {"ran": True, "exit": 0},
    "deterministic": {"api_changes": 0, "changelogSignal": "clean"},
    "verdict_v2": {"verdict": "SAFE", "severity": "low", "confidence": "L4", "priority": "P3"},
}


class TestValidateComment(unittest.TestCase):
    def _make_comment(self, lines=170, has_table=True, has_subsection=True,
                      has_footer=True, has_numbered=True, has_bash=True,
                      has_reachability=True, has_sha256=True,
                      has_policy=True, has_confidence=True,
                      has_h3_sections=True, has_merge_plan=True):
        parts = ["<!-- breakability-check -->", "## SAFE — lodash"]
        if has_table:
            parts.append("| Layer | Signal | Detail |")
        if has_subsection:
            parts.append("### How we checked")
        if has_h3_sections:
            parts.append("### Build Analysis")
            parts.append("### Test Analysis")
        if has_numbered:
            parts.append("1. Review the changelog")
        if has_bash:
            parts.append("```bash")
            parts.append("npm test")
            parts.append("```")
        if has_reachability:
            parts.append("**Reachability** confirms the package is imported by 3 files")
        if has_sha256:
            parts.append("SHA256: abc123def456")
        if has_policy:
            parts.append("### Verdict Logic")
            parts.append("IF build.verdict = pass AND test.exit = 0")
            parts.append("THEN verdict = SAFE")
        if has_confidence:
            parts.append("**Confidence:** HIGH — Build passed cleanly")
        if has_merge_plan:
            parts.append("Merge plan: #42")
        body_needed = lines - len(parts) - (1 if has_footer else 0)
        if body_needed > 0:
            parts.extend([f"Line {i}" for i in range(body_needed)])
        if has_footer:
            parts.append("Mode: Deterministic + Behavioral Probe")
        return "\n".join(parts)

    def test_valid_comment_passes(self):
        comment = self._make_comment(lines=170)
        passed, diag = _validate_comment(comment, "42")
        self.assertTrue(passed)
        self.assertTrue(all(d["passed"] for d in diag.values()))

    def test_too_short_fails(self):
        comment = self._make_comment(lines=50)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["line_count"]["passed"])

    def test_missing_table_fails(self):
        comment = self._make_comment(has_table=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_signal_table"]["passed"])

    def test_missing_subsection_fails(self):
        comment = self._make_comment(has_subsection=False, has_h3_sections=False, has_policy=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_h3"]["passed"])

    def test_missing_footer_fails(self):
        comment = self._make_comment(has_footer=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_mode_footer"]["passed"])

    def test_missing_numbered_recommendations_fails(self):
        comment = self._make_comment(has_numbered=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_numbered_list"]["passed"])

    def test_missing_bash_commands_fails(self):
        comment = self._make_comment(has_bash=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_bash_block"]["passed"])

    def test_missing_reachability_fails(self):
        comment = self._make_comment(has_reachability=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_reachability"]["passed"])

    def test_diagnostics_has_all_thirteen_criteria(self):
        comment = self._make_comment(lines=170)
        _, diag = _validate_comment(comment, "42")
        expected = {"line_count", "has_h2", "has_signal_table", "has_h3",
                    "has_mode_footer", "has_numbered_list", "has_bash_block", "has_reachability",
                    "has_sha256", "has_policy_pseudocode", "has_confidence_reasoning",
                    "has_h3_narrative_sections", "has_merge_plan_link"}
        self.assertEqual(set(diag.keys()), expected)

    def test_missing_sha256_fails(self):
        comment = self._make_comment(has_sha256=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_sha256"]["passed"])

    def test_missing_policy_pseudocode_fails(self):
        comment = self._make_comment(has_policy=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_policy_pseudocode"]["passed"])

    def test_missing_confidence_fails(self):
        comment = self._make_comment(has_confidence=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_confidence_reasoning"]["passed"])

    def test_missing_h3_sections_fails(self):
        comment = self._make_comment(has_h3_sections=False, has_subsection=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_h3_narrative_sections"]["passed"])

    def test_missing_merge_plan_link_fails(self):
        comment = self._make_comment(has_merge_plan=False)
        passed, diag = _validate_comment(comment, "42")
        self.assertFalse(passed)
        self.assertFalse(diag["has_merge_plan_link"]["passed"])

    def test_verdict_mismatch_detected_as_warning(self):
        """When AI says SAFE but contract says REVIEW, verdict_mismatch is a warning (passed=True)."""
        comment = self._make_comment(lines=170)
        pr = {
            "package": "jwks-rsa", "build": {"verdict": "pass"},
            "test": {"ran": False},
            "behavioral_grade": {"same_behavior": False, "behavior_changed": True},
            "files_importing": ["src/auth.ts"],
            "policy_lowering": {"decision": {"verdict": "MERGE"}},
        }
        passed, diag = _validate_comment(comment, "42", pr)
        self.assertIn("verdict_mismatch", diag)
        self.assertTrue(diag["verdict_mismatch"]["passed"])
        self.assertIn("AI=SAFE", diag["verdict_mismatch"]["value"])
        self.assertIn("warning", diag["verdict_mismatch"]["value"])

    def test_no_verdict_mismatch_when_agreement(self):
        """When AI and contract agree, no verdict_mismatch diagnostic."""
        comment = self._make_comment(lines=170)
        pr = {
            "package": "lodash", "build": {"verdict": "pass"},
            "test": {"ran": True, "exit": 0},
            "verdict_v2": {"verdict": "SAFE", "severity": "low", "confidence": "L4", "priority": "P3"},
        }
        passed, diag = _validate_comment(comment, "42", pr)
        self.assertNotIn("verdict_mismatch", diag)


class TestNearValid(unittest.TestCase):
    """_near_valid accepts long comments with at most 1 failing check."""

    def _make_diag(self, line_count=350, failures=None):
        failures = failures or set()
        checks = ["line_count", "has_h2", "has_signal_table", "has_h3",
                   "has_mode_footer", "has_numbered_list", "has_bash_block", "has_reachability",
                   "has_sha256", "has_policy_pseudocode", "has_confidence_reasoning",
                   "has_h3_narrative_sections", "has_merge_plan_link"]
        diag = {}
        for c in checks:
            if c == "line_count":
                diag[c] = {"passed": c not in failures, "value": line_count}
            elif c == "has_h3_narrative_sections":
                diag[c] = {"passed": c not in failures, "value": 5 if c not in failures else 1}
            else:
                diag[c] = {"passed": c not in failures, "value": c not in failures}
        return diag

    def test_long_comment_one_failure_accepted(self):
        diag = self._make_diag(line_count=381, failures={"has_h3"})
        self.assertTrue(_near_valid(diag))

    def test_short_comment_one_failure_rejected(self):
        diag = self._make_diag(line_count=100, failures={"has_h3"})
        self.assertFalse(_near_valid(diag))

    def test_long_comment_two_failures_accepted(self):
        diag = self._make_diag(line_count=400, failures={"has_h3", "has_bash_block"})
        self.assertTrue(_near_valid(diag))

    def test_long_comment_three_failures_rejected(self):
        diag = self._make_diag(line_count=400, failures={"has_h3", "has_bash_block", "has_sha256"})
        self.assertFalse(_near_valid(diag))

    def test_all_passing_long_is_near_valid(self):
        diag = self._make_diag(line_count=350, failures=set())
        self.assertTrue(_near_valid(diag))

    def test_line_count_fail_below_300_rejected(self):
        diag = self._make_diag(line_count=120, failures={"line_count"})
        self.assertFalse(_near_valid(diag))

    def test_line_count_fail_at_300_accepted(self):
        diag = self._make_diag(line_count=300, failures=set())
        diag["line_count"] = {"passed": False, "value": 300}
        self.assertTrue(_near_valid(diag))

    def test_h3_failure_rejects_even_with_long_comment(self):
        """T005: H3 narrative sections must be non-bypassable."""
        diag = self._make_diag(line_count=350, failures={"has_h3_narrative_sections"})
        diag["has_h3_narrative_sections"] = {"passed": False, "value": 1}
        self.assertFalse(_near_valid(diag))

    def test_h3_passing_still_allows_near_valid(self):
        """T005: H3 passing with other failure still accepted."""
        diag = self._make_diag(line_count=350, failures={"has_bash_block"})
        self.assertTrue(_near_valid(diag))


class TestInjectVerdictLogicRegex(unittest.TestCase):
    """T006: _inject_verdict_logic insertion point regex covers VCP Go PR headings."""

    def test_inserts_before_steps_heading(self):
        comment = "## REVIEW — test-pkg\n\nSome analysis.\n\n### Steps\n1. Do something"
        pr = {"build": {"verdict": "pass"}, "dep_type": "production",
              "policy_lowering": {"decision": {"verdict": "REVIEW"}}}
        result = _inject_verdict_logic(comment, pr, "99")
        self.assertIn("### Verdict Logic", result)
        self.assertLess(result.index("### Verdict Logic"), result.index("### Steps"))

    def test_inserts_before_what_to_do_next(self):
        comment = "## REVIEW — test-pkg\n\nAnalysis.\n\n### What To Do Next\n1. Check"
        pr = {"build": {"verdict": "pass"}, "dep_type": "production",
              "policy_lowering": {"decision": {"verdict": "REVIEW"}}}
        result = _inject_verdict_logic(comment, pr, "99")
        self.assertIn("### Verdict Logic", result)
        self.assertLess(result.index("### Verdict Logic"), result.index("### What To Do Next"))

    def test_inserts_before_developer_actions(self):
        comment = "## REVIEW — test-pkg\n\nAnalysis.\n\n### Developer Actions\n1. Review"
        pr = {"build": {"verdict": "pass"}, "dep_type": "production",
              "policy_lowering": {"decision": {"verdict": "REVIEW"}}}
        result = _inject_verdict_logic(comment, pr, "99")
        self.assertIn("### Verdict Logic", result)
        self.assertLess(result.index("### Verdict Logic"), result.index("### Developer Actions"))


class TestFallbackComment(unittest.TestCase):
    def test_fallback_includes_package(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("lodash", comment)
        self.assertIn("4.17.20", comment)
        self.assertIn("4.17.21", comment)

    def test_fallback_includes_marker(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("<!-- breakability-check -->", comment)

    def test_fallback_includes_run_url(self):
        comment = _fallback_comment(SAMPLE_PR, "42", "https://example.com/run/1", None, "claude-sonnet-4.5")
        self.assertIn("https://example.com/run/1", comment)

    def test_fallback_includes_merge_plan(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, "99", "claude-sonnet-4.5")
        self.assertIn("#99", comment)


class TestBuildPrompt(unittest.TestCase):
    def test_prompt_contains_pr_data(self):
        prompt = _build_per_pr_prompt(
            base_prompt="Base instructions here",
            pr=SAMPLE_PR, pr_num="42",
            metadata={"repo": "test/repo", "mode": "advisory"},
            run_url="https://example.com/run", merge_plan_issue="10",
            model_name="claude-sonnet-4.5", cross_deps=[], top_level={},
        )
        self.assertIn("PR #42", prompt)
        self.assertIn("lodash", prompt)
        self.assertIn("#10", prompt)
        self.assertIn("https://example.com/run", prompt)

    def test_prompt_includes_cross_deps(self):
        deps = [{"pr_a": "42", "pr_b": "43", "reason": "shared dep"}]
        prompt = _build_per_pr_prompt(
            base_prompt="Base", pr=SAMPLE_PR, pr_num="42",
            metadata={}, run_url=None, merge_plan_issue=None,
            model_name="test", cross_deps=deps, top_level={},
        )
        self.assertIn("Cross-PR Dependencies", prompt)
        self.assertIn("shared dep", prompt)


class TestEnsureMarker(unittest.TestCase):
    def test_adds_marker(self):
        result = _ensure_marker("## Some comment")
        self.assertTrue(result.startswith("<!-- breakability-check -->"))

    def test_preserves_existing_marker(self):
        text = "<!-- breakability-check -->\n## Comment"
        result = _ensure_marker(text)
        self.assertEqual(result.count("breakability-check"), 1)


class TestExtractPrData(unittest.TestCase):
    def test_serializes_pr(self):
        result = _extract_pr_data(SAMPLE_PR)
        data = json.loads(result)
        self.assertEqual(data["package"], "lodash")


class TestFallbackVerdictDisplay(unittest.TestCase):
    """_fallback_comment must read authoritative_verdict and display correct verdict."""

    def test_safe_verdict_for_passing_build(self):
        pr = {**SAMPLE_PR, "build": {"verdict": "pass", "pr_exit": 0},
              "test": {"ran": True, "exit": 0},
              "verdict_v2": {"verdict": "SAFE", "severity": "low", "confidence": "L4", "priority": "P3"}}
        comment = _fallback_comment(pr, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("SAFE", comment)
        self.assertIn("✅", comment)
        self.assertNotIn("BLOCKED", comment)

    def test_blocked_verdict_for_build_fail(self):
        pr = {**SAMPLE_PR, "build": {"verdict": "fail", "pr_exit": 1},
              "test": {"ran": False}}
        comment = _fallback_comment(pr, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("BLOCKED", comment)
        self.assertIn("🚫", comment)
        self.assertNotIn("✅ SAFE", comment)

    def test_blocked_verdict_for_test_fail(self):
        pr = {**SAMPLE_PR, "build": {"verdict": "pass", "pr_exit": 0},
              "test": {"ran": True, "exit": 1, "output_tail": "FAILED tests"}}
        comment = _fallback_comment(pr, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("BLOCKED", comment)
        self.assertNotIn("✅ SAFE", comment)

    def test_safe_verdict_for_actions_ecosystem(self):
        pr = {**SAMPLE_PR, "ecosystem": "actions",
              "build": {"verdict": "pass"}, "test": {"ran": False}}
        comment = _fallback_comment(pr, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("SAFE", comment)
        self.assertIn("✅", comment)

    def test_safe_verdict_without_verdict_v2_when_deterministic_safe(self):
        pr = {**SAMPLE_PR, "build": {"verdict": "pass", "pr_exit": 0},
              "test": {"ran": True, "exit": 0}}
        del pr["verdict_v2"]
        comment = _fallback_comment(pr, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("SAFE", comment)

    def test_review_verdict_without_verdict_v2_when_imported(self):
        pr = {**SAMPLE_PR, "build": {"verdict": "pass", "pr_exit": 0},
              "test": {"ran": True, "exit": 0},
              "files_importing": ["src/auth.ts"]}
        del pr["verdict_v2"]
        comment = _fallback_comment(pr, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("REVIEW", comment)


class TestEnforceVerdictFloor(unittest.TestCase):
    """_enforce_verdict_floor must override AI verdict when contract disagrees."""

    def test_safe_overridden_to_review_when_probe_different(self):
        comment = "<!-- breakability-check -->\n## ✅ SAFE — `jwks-rsa` 3.2.2 → 4.1.0\nBody here"
        pr = {
            "package": "jwks-rsa", "from": "3.2.2", "to": "4.1.0",
            "build": {"verdict": "pass"}, "test": {"ran": False},
            "behavioral_grade": {"same_behavior": False, "behavior_changed": True},
            "files_importing": ["src/auth.ts"],
            "policy_lowering": {"decision": {"verdict": "MERGE"}},
        }
        result = _enforce_verdict_floor(comment, pr, "66")
        self.assertIn("REVIEW", result)
        self.assertNotIn("✅ SAFE", result)

    def test_review_not_overridden_when_contract_agrees(self):
        comment = "<!-- breakability-check -->\n## ⚠️ REVIEW — `lodash` 4.17.20 → 4.17.21\nBody"
        pr = {
            "package": "lodash", "build": {"verdict": "pass"},
            "test": {"ran": True, "exit": 0},
            "policy_lowering": {"decision": {"verdict": "REVIEW"}},
        }
        result = _enforce_verdict_floor(comment, pr, "42")
        self.assertIn("REVIEW", result)

    def test_safe_stays_safe_when_contract_agrees(self):
        comment = "<!-- breakability-check -->\n## ✅ SAFE — `lodash` 4.17.20 → 4.17.21\nBody"
        pr = {
            "package": "lodash", "build": {"verdict": "pass"},
            "test": {"ran": True, "exit": 0},
            "verdict_v2": {"verdict": "SAFE", "severity": "low", "confidence": "L4", "priority": "P3"},
        }
        result = _enforce_verdict_floor(comment, pr, "42")
        self.assertIn("SAFE", result)

    def test_blocked_never_downgraded(self):
        comment = "<!-- breakability-check -->\n## 🚫 BLOCKED — `pkg` 1.0 → 2.0\nBody"
        pr = {
            "package": "pkg", "build": {"verdict": "fail"},
            "test": {"ran": False},
        }
        result = _enforce_verdict_floor(comment, pr, "99")
        self.assertIn("BLOCKED", result)

    def test_review_corrected_to_safe_when_contract_says_safe(self):
        """C4: PR#105 scenario — AI generates REVIEW but verdict_v2 is SAFE."""
        comment = "<!-- breakability-check -->\n## ⚠️ REVIEW — `pkg` 1.0 → 2.0\nBody"
        pr = {
            "package": "pkg", "build": {"verdict": "pass"},
            "test": {"ran": True, "exit": 0},
            "verdict_v2": {"verdict": "SAFE", "severity": "low", "confidence": "L4", "priority": "P3"},
        }
        result = _enforce_verdict_floor(comment, pr, "105")
        self.assertIn("SAFE", result)
        self.assertNotIn("⚠️ REVIEW", result)


class TestEnforceMergeRiskTag(unittest.TestCase):
    """C6: _enforce_merge_risk_tag corrects comment body contradicting merge_risk.tag."""

    def test_high_tag_corrects_low_in_body(self):
        comment = "## ⚠️ REVIEW — pkg\n\nMerge Risk: Low\nSafe to merge"
        pr = {"merge_risk": {"tag": "High"}, "verdict_v2": {"verdict": "REVIEW"}}
        result = _enforce_merge_risk_tag(comment, pr, "36")
        self.assertIn("Merge Risk: High", result)
        self.assertNotIn("Merge Risk: Low", result)

    def test_low_tag_not_changed(self):
        comment = "## ✅ SAFE — pkg\n\nMerge Risk: Low\nSafe to merge"
        pr = {"merge_risk": {"tag": "Low"}, "verdict_v2": {"verdict": "SAFE"}}
        result = _enforce_merge_risk_tag(comment, pr, "100")
        self.assertIn("Merge Risk: Low", result)

    def test_no_merge_risk_field_passes_through(self):
        comment = "## ✅ SAFE — pkg\n\nSome content"
        pr = {"verdict_v2": {"verdict": "SAFE"}}
        result = _enforce_merge_risk_tag(comment, pr, "101")
        self.assertEqual(result, comment)


class TestAllStubsDetection(unittest.TestCase):
    """When all PRs fall back to stubs, main() must exit non-zero (code 2)."""

    def _write_build_results(self, path, prs_dict):
        data = {"metadata": {"repo": "test/repo"}, "prs": prs_dict}
        with open(path, "w") as f:
            json.dump(data, f)

    def _write_dummy_prompt(self, path):
        with open(path, "w") as f:
            f.write("# Dummy prompt for testing\n")

    def test_all_stubs_exits_nonzero(self):
        """When Backend returns empty for all PRs, main() should return 2."""
        import tempfile
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = os.path.join(tmpdir, "build-results.json")
            prompt_path = os.path.join(tmpdir, "prompt.md")
            self._write_build_results(results_path, {
                "42": {**SAMPLE_PR, "pr_num": "42"},
                "43": {**SAMPLE_PR, "pr_num": "43", "package": "express"},
            })
            self._write_dummy_prompt(prompt_path)

            mock_backend = MagicMock()
            mock_backend.model = "test-model"
            mock_backend.invoke.return_value = ""

            with patch("generate_ai_comments.Backend") as MockBackend:
                MockBackend.from_env.return_value = mock_backend
                with patch("sys.argv", ["prog", results_path, "--prompt", prompt_path]):
                    from generate_ai_comments import main
                    ret = main()
                    self.assertEqual(ret, 2)

    def test_partial_stubs_exits_zero(self):
        """When Backend succeeds for some PRs, main() should return 0."""
        import tempfile
        from unittest.mock import patch, MagicMock

        valid_comment = self._make_valid_ai_comment()

        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = os.path.join(tmpdir, "build-results.json")
            prompt_path = os.path.join(tmpdir, "prompt.md")
            self._write_build_results(results_path, {
                "42": {**SAMPLE_PR, "pr_num": "42"},
                "43": {**SAMPLE_PR, "pr_num": "43", "package": "express"},
            })
            self._write_dummy_prompt(prompt_path)

            mock_backend = MagicMock()
            mock_backend.model = "test-model"
            mock_backend.invoke.side_effect = [valid_comment, "", ""]

            with patch("generate_ai_comments.Backend") as MockBackend:
                MockBackend.from_env.return_value = mock_backend
                with patch("sys.argv", ["prog", results_path, "--prompt", prompt_path]):
                    from generate_ai_comments import main
                    ret = main()
                    self.assertEqual(ret, 0)

    def test_all_ai_success_exits_zero(self):
        """When Backend succeeds for all PRs, main() should return 0."""
        import tempfile
        from unittest.mock import patch, MagicMock

        valid_comment = self._make_valid_ai_comment()

        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = os.path.join(tmpdir, "build-results.json")
            prompt_path = os.path.join(tmpdir, "prompt.md")
            self._write_build_results(results_path, {
                "42": {**SAMPLE_PR, "pr_num": "42"},
            })
            self._write_dummy_prompt(prompt_path)

            mock_backend = MagicMock()
            mock_backend.model = "test-model"
            mock_backend.invoke.return_value = valid_comment

            with patch("generate_ai_comments.Backend") as MockBackend:
                MockBackend.from_env.return_value = mock_backend
                with patch("sys.argv", ["prog", results_path, "--prompt", prompt_path]):
                    from generate_ai_comments import main
                    ret = main()
                    self.assertEqual(ret, 0)

    def _make_valid_ai_comment(self):
        parts = ["<!-- breakability-check -->", "## SAFE — lodash"]
        parts.append("| Layer | Signal | Detail |")
        parts.append("### How we checked")
        parts.append("### Build Analysis")
        parts.append("### Test Analysis")
        parts.append("1. Review the changelog")
        parts.append("```bash")
        parts.append("npm test")
        parts.append("```")
        parts.append("**Reachability** confirms the package is imported by 3 files")
        parts.append("SHA256: abc123def456")
        parts.append("verdict = SAFE")
        parts.append("build = PASS")
        parts.append("tests = PASS")
        parts.append("**Confidence:** HIGH — Build passed cleanly")
        parts.append("Merge plan: #42")
        parts.extend([f"Line {i}" for i in range(150)])
        parts.append("Mode: Deterministic + Behavioral Probe")
        return "\n".join(parts)


class TestNormalizeVerdictText(unittest.TestCase):
    """_normalize_verdict_text maps non-standard H2 verdicts to SAFE/REVIEW/BLOCKED."""

    def test_unverified_maps_to_review(self):
        comment = "<!-- breakability-check -->\n## ❓ UNVERIFIED — `pkg` 1.0 → 2.0\nBody"
        result = _normalize_verdict_text(comment, "77")
        self.assertIn("REVIEW", result)
        self.assertNotIn("UNVERIFIED", result)

    def test_build_fails_maps_to_blocked(self):
        comment = "<!-- breakability-check -->\n## ❌ BUILD_FAILS — `pkg` 1.0 → 2.0\nBody"
        result = _normalize_verdict_text(comment, "99")
        self.assertIn("BLOCKED", result)
        self.assertNotIn("BUILD_FAILS", result)

    def test_safe_unchanged(self):
        comment = "<!-- breakability-check -->\n## ✅ SAFE — `lodash` 4.17.20 → 4.17.21\nBody"
        result = _normalize_verdict_text(comment, "42")
        self.assertIn("SAFE", result)

    def test_review_unchanged(self):
        comment = "<!-- breakability-check -->\n## ⚠️ REVIEW — `pkg` 1.0 → 2.0\nBody"
        result = _normalize_verdict_text(comment, "42")
        self.assertIn("REVIEW", result)

    def test_inconclusive_maps_to_review(self):
        comment = "<!-- breakability-check -->\n## ❓ INCONCLUSIVE — `pkg` 1.0 → 2.0\nBody"
        result = _normalize_verdict_text(comment, "42")
        self.assertIn("REVIEW", result)
        self.assertNotIn("INCONCLUSIVE", result)


class TestEnrichedFallbackComment(unittest.TestCase):
    """Enriched fallback must have signal summary table and >= 40 lines."""

    def test_fallback_line_count(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "claude-sonnet-4.5")
        line_count = len(comment.strip().splitlines())
        self.assertGreaterEqual(line_count, 40, f"Fallback is only {line_count} lines")

    def test_fallback_has_signal_table(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("| Layer", comment)
        table_rows = [l for l in comment.splitlines() if l.startswith("| ") and "---" not in l]
        self.assertGreaterEqual(len(table_rows), 5, f"Only {len(table_rows)} table rows")

    def test_fallback_has_verdict_logic(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("Authoritative verdict", comment)
        self.assertIn("Breakability grade", comment)

    def test_fallback_has_recommendation(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("### Recommendation", comment)
        self.assertIn("1.", comment)

    def test_fallback_with_files_importing(self):
        pr = {**SAMPLE_PR, "files_importing": ["src/auth.ts", "src/api.ts"]}
        comment = _fallback_comment(pr, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("src/auth.ts", comment)
        self.assertIn("Files importing", comment)

    def test_fallback_with_probe_hashes(self):
        pr = {**SAMPLE_PR, "behavioral_grade": {"same_behavior": True, "hashes": {"before": "abc123", "after": "abc123"}}}
        comment = _fallback_comment(pr, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("abc123", comment)
        self.assertIn("SHA256", comment)

    def test_fallback_has_ai_fallback_marker(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("<!-- ai-fallback -->", comment)


class TestDiagnosticLogging(unittest.TestCase):
    """Diagnostic JSON written when AI generation fails."""

    def test_diagnostics_written_on_failure(self):
        import tempfile
        from unittest.mock import patch, MagicMock

        diag_path = "/tmp/ai-comment-diagnostics.json"
        if os.path.exists(diag_path):
            os.remove(diag_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = os.path.join(tmpdir, "build-results.json")
            prompt_path = os.path.join(tmpdir, "prompt.md")
            data = {"metadata": {"repo": "test/repo"}, "prs": {"42": {**SAMPLE_PR, "pr_num": "42"}}}
            with open(results_path, "w") as f:
                json.dump(data, f)
            with open(prompt_path, "w") as f:
                f.write("# Dummy prompt\n")

            mock_backend = MagicMock()
            mock_backend.model = "test-model"
            mock_backend.invoke.return_value = ""

            with patch("generate_ai_comments.Backend") as MockBackend:
                MockBackend.from_env.return_value = mock_backend
                from generate_ai_comments import generate_comments
                generate_comments(data, prompt_path, model="test")

            self.assertTrue(os.path.exists(diag_path), "Diagnostics file not written")
            with open(diag_path) as f:
                records = json.load(f)
            self.assertGreater(len(records), 0)
            self.assertEqual(records[0]["pr_num"], "42")
            self.assertIn("gate_results", records[0])


class TestGoldenFeatureValidation(unittest.TestCase):
    """Validates all 13 golden features are present in a golden-standard comment."""

    GOLDEN_COMMENT = "\n".join([
        "<!-- breakability-check -->",
        "## ✅ SAFE — `lodash` 4.17.20 → 4.17.21 · production · patch",
        "",
        "| Layer | Signal | Detail | Confidence |",
        "|-------|--------|--------|------------|",
        "| Build | ✅ pass | exit 0 | HIGH — Definitive exit code |",
        "| Tests | ✅ pass | exit 0 | HIGH — Test suite passed |",
        "| API Diff | ✅ clean | 0 symbols | MEDIUM — No changes found |",
        "| Changelog | ✅ clean | clean | MEDIUM — No breaking markers |",
        "| Reachability | ✅ not reached | 0 imports | HIGH — Not referenced |",
        "| Probe | ✅ same | behavior unchanged | HIGH — Probe confirmed same |",
        "| AI Arbiter | ⬜ not run | — |",
        "",
        "### How we checked",
        "",
        "- **Build**: Installed `lodash@4.17.21` and ran full build pipeline",
        "- **Tests**: Ran project test suite",
        "",
        "### ✅ Build Analysis",
        "**Status:** ✅ **PASS** | **Verification Level:** HIGH",
        "**Confidence:** **HIGH** — Definitive exit code from full build pipeline",
        "",
        "### ✅ Test Analysis",
        "**Status:** ✅ **PASS** | **Verification Level:** HIGH",
        "**Confidence:** **HIGH** — Test suite ran and passed",
        "",
        "### ✅ API Diff Analysis",
        "**Status:** ✅ **0 change(s)** | **Verification Level:** MEDIUM",
        "",
        "### ✅ Changelog Analysis",
        "**Status:** ✅ **CLEAN** | **Verification Level:** MEDIUM",
        "",
        "### ✅ Reachability Analysis",
        "**Status:** ✅ **NOT REACHED** | **Verification Level:** HIGH",
        "- ✅ Scanned project source files for imports of `lodash`",
        "",
        "### ✅ Behavioral Probe Analysis",
        "**Status:** ✅ **SAME** | **Verification Level:** HIGH",
        "**Confidence:** **HIGH** — Behavioral probe ran and reported same",
        "",
        "### Recommendation",
        "",
        "1. Safe to merge — no action required",
        "2. Merge when confident",
        "",
        "<details><summary>Verdict logic</summary>",
        "",
        "```",
        "build      = PASS",
        "tests      = PASS",
        "probe      = SAME",
        "reachable  = FALSE",
        "changelog  = CLEAN",
        "verdict    = SAFE",
        "```",
        "",
        "</details>",
        "",
        "<details><summary>Verification commands</summary>",
        "",
        "```bash",
        "npm install lodash@4.17.21",
        "npm run build",
        "",
        "npm test",
        "```",
        "",
        "</details>",
        "",
        "### Authoritative verdict",
        "Breakability grade: A",
        "",
        "Old SHA256: abc123",
        "New SHA256: abc123",
        "",
        "**Reachability** confirms the package is not imported by production code.",
        "",
        "Merge plan: #42",
        "",
    ] + [f"Padding line {i}" for i in range(80)] + [
        "---",
        "Mode: Deterministic + Behavioral Probe · Model: golden-test · Analyzed: 2026-01-01",
    ])

    def test_f01_signal_summary_table(self):
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_signal_table"]["passed"])

    def test_f02_per_layer_narrative_sections(self):
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_h3_narrative_sections"]["passed"])
        self.assertGreaterEqual(diag["has_h3_narrative_sections"]["value"], 3)

    def test_f03_what_we_checked(self):
        self.assertIn("How we checked", self.GOLDEN_COMMENT)

    def test_f04_stdout_in_code_blocks(self):
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_bash_block"]["passed"])

    def test_f05_confidence_reasoning(self):
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_confidence_reasoning"]["passed"])

    def test_f06_sha256_hashes(self):
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_sha256"]["passed"])

    def test_f07_reachability(self):
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_reachability"]["passed"])

    def test_f08_policy_pseudocode(self):
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_policy_pseudocode"]["passed"])

    def test_f09_numbered_recommendations(self):
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_numbered_list"]["passed"])

    def test_f10_verification_commands(self):
        self.assertIn("Verification commands", self.GOLDEN_COMMENT)
        self.assertIn("npm install", self.GOLDEN_COMMENT)

    def test_f11_merge_plan_link(self):
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_merge_plan_link"]["passed"])

    def test_f12_analysis_run_link(self):
        self.assertIn("Mode:", self.GOLDEN_COMMENT)
        _, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(diag["has_mode_footer"]["passed"])

    def test_f13_model_attribution_footer(self):
        self.assertIn("Model:", self.GOLDEN_COMMENT)
        self.assertIn("Analyzed:", self.GOLDEN_COMMENT)

    def test_all_13_features_pass(self):
        passed, diag = _validate_comment(self.GOLDEN_COMMENT, "42")
        self.assertTrue(passed, f"Failed checks: {[k for k, v in diag.items() if not v['passed']]}")

    def test_minimal_comment_fails(self):
        minimal = "## SAFE\nShort comment."
        passed, _ = _validate_comment(minimal, "42")
        self.assertFalse(passed)


class TestChangelogStatusRendering(unittest.TestCase):
    """C1: Changelog table row must read changelogSignal.status."""

    def _make_pr(self, changelog_signal):
        pr = dict(SAMPLE_PR)
        pr["deterministic"] = {"changelogSignal": changelog_signal}
        return pr

    def test_breaking_status_shows_warning(self):
        pr = self._make_pr({"status": "breaking", "bullets": ["removed X"]})
        comment = _fallback_comment(pr, "42", None, None, "test-model")
        self.assertIn("⚠️ Breaking changes detected", comment)
        self.assertNotIn("No breaking changes", comment)

    def test_missing_status_shows_unavailable(self):
        pr = self._make_pr({"status": "missing"})
        comment = _fallback_comment(pr, "42", None, None, "test-model")
        self.assertIn("⏭️ Unavailable", comment)

    def test_clean_status_shows_no_breaking(self):
        pr = self._make_pr({"status": "clean"})
        comment = _fallback_comment(pr, "42", None, None, "test-model")
        self.assertIn("✅ No breaking changes", comment)
        self.assertNotIn("low confidence", comment)

    def test_none_status_shows_low_confidence(self):
        pr = self._make_pr({"status": "none"})
        comment = _fallback_comment(pr, "42", None, None, "test-model")
        self.assertIn("low confidence", comment)

    def test_null_signal_shows_unknown(self):
        pr = dict(SAMPLE_PR)
        pr["deterministic"] = {}
        comment = _fallback_comment(pr, "42", None, None, "test-model")
        self.assertIn("⏭️ Unknown", comment)

    def test_breaking_bullets_rendered(self):
        pr = self._make_pr({"status": "breaking", "bullets": ["removed foo", "changed bar"]})
        comment = _fallback_comment(pr, "42", None, None, "test-model")
        self.assertIn("removed foo", comment)
        self.assertIn("changed bar", comment)


class TestCVEApplicabilityInComment(unittest.TestCase):
    """C2: CVE section must check version applicability before claiming 'remediates'."""

    def _make_pr(self, from_ver, vuln_range, cve_ids):
        pr = dict(SAMPLE_PR)
        pr["from"] = from_ver
        pr["deterministic"] = {
            "security": {
                "isSecurity": True,
                "cveIds": cve_ids,
                "cvssScore": 9.8,
                "vulnerableVersionRange": vuln_range,
            }
        }
        return pr

    def test_vulnerable_version_says_remediates(self):
        pr = self._make_pr("0.45.0", "< 0.52.0", ["CVE-2024-1234"] * 26)
        comment = _fallback_comment(pr, "109", None, None, "test-model")
        self.assertIn("remediates 26 CVE(s)", comment)

    def test_non_vulnerable_version_says_historical(self):
        pr = self._make_pr("11.1.18", ">= 11.0.0, < 11.0.16", ["CVE-2024-5678"])
        comment = _fallback_comment(pr, "16", None, None, "test-model")
        self.assertIn("Historical advisory", comment)
        self.assertNotIn("remediates", comment)

    def test_equal_range_outside(self):
        pr = self._make_pr("2.17.4", "= 2.17.3", ["CVE-2024-9999"] * 10)
        comment = _fallback_comment(pr, "28", None, None, "test-model")
        self.assertIn("Historical advisory", comment)


class TestIssueNumberPlaceholder(unittest.TestCase):
    """C3: ISSUE_NUMBER placeholder must never appear in comments."""

    def test_no_issue_number_without_merge_plan(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "test-model")
        self.assertNotIn("ISSUE_NUMBER", comment)
        self.assertNotIn("Merge plan:", comment)

    def test_merge_plan_shown_when_provided(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, "123", "test-model")
        self.assertIn("#123", comment)
        self.assertIn("Merge plan:", comment)


class TestPipelineProvenanceFooter(unittest.TestCase):
    """C6: Footer must say 'template-fallback' when AI didn't run."""

    def test_fallback_footer_no_model_name(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "claude-sonnet-5")
        self.assertIn("template-fallback", comment)
        self.assertIn("no AI analysis performed", comment)
        self.assertNotIn("claude-sonnet-5 (fallback)", comment)


class TestApiDiffFabricationNone(unittest.TestCase):
    """C1: When api_diff_tool is None, render Unavailable instead of No changes."""

    def test_api_diff_tool_none_shows_unavailable(self):
        pr = {**SAMPLE_PR, "deterministic": {"api_diff_tool": None, "api_changes": 0}}
        comment = _fallback_comment(pr, "16", None, None, "test-model")
        self.assertIn("Unavailable", comment)
        self.assertNotIn("No changes", comment)

    def test_api_diff_tool_semantic_with_zero_changes_shows_no_changes(self):
        pr = {**SAMPLE_PR, "deterministic": {
            "api_diff_tool": {"status": "semantic"}, "api_changes": 0
        }}
        comment = _fallback_comment(pr, "43", None, None, "test-model")
        self.assertIn("No changes", comment)
        self.assertNotIn("Unavailable", comment.split("API Diff")[1].split("\n")[0])

    def test_api_diff_tool_unavailable_status_shows_unavailable(self):
        pr = {**SAMPLE_PR, "deterministic": {
            "api_diff_tool": {"status": "unavailable"}, "api_changes": None
        }}
        comment = _fallback_comment(pr, "37", None, None, "test-model")
        self.assertIn("Unavailable", comment)


class TestBlockedVerdictEvidence(unittest.TestCase):
    """C2: BLOCKED verdicts must cite actual error text."""

    def test_blocked_with_new_errors_shows_build_errors(self):
        pr = {
            **SAMPLE_PR,
            "build": {"verdict": "fail", "pr_exit": 1, "new_errors": ["error TS2882: Cannot find module"]},
            "test": {"ran": False},
            "verdict_v2": {"verdict": "BLOCKED"},
        }
        comment = _fallback_comment(pr, "103", None, None, "test-model")
        self.assertIn("Build Errors", comment)
        self.assertIn("TS2882", comment)

    def test_blocked_with_new_failures_shows_test_failures(self):
        pr = {
            **SAMPLE_PR,
            "build": {"verdict": "fail", "pr_exit": 1},
            "test": {"ran": True, "exit": 1, "new_failures": ["TestObservability"]},
            "verdict_v2": {"verdict": "BLOCKED"},
        }
        comment = _fallback_comment(pr, "68", None, None, "test-model")
        self.assertIn("Test Failures", comment)
        self.assertIn("TestObservability", comment)

    def test_blocked_with_output_tail_shows_excerpt(self):
        pr = {
            **SAMPLE_PR,
            "build": {"verdict": "fail", "pr_exit": 1, "new_errors": [],
                       "output_tail": "npm error ERESOLVE could not resolve"},
            "test": {"ran": False},
            "verdict_v2": {"verdict": "BLOCKED"},
        }
        comment = _fallback_comment(pr, "38", None, None, "test-model")
        self.assertIn("ERESOLVE", comment)


class TestConfidenceNotHardcoded(unittest.TestCase):
    """C3: Confidence column must reflect actual data, not hardcoded MEDIUM."""

    def test_probe_confidence_high_shown(self):
        pr = {**SAMPLE_PR, "behavioral_grade": {"same_behavior": True, "confidence": "high"}}
        comment = _fallback_comment(pr, "16", None, None, "test-model")
        probe_row = [l for l in comment.splitlines() if "Behavioral Probe" in l][0]
        self.assertIn("HIGH", probe_row)
        self.assertNotIn("MEDIUM", probe_row)

    def test_probe_unavailable_shows_dash(self):
        pr = {**SAMPLE_PR, "behavioral_grade": {"rationale": "go executable not found"}}
        comment = _fallback_comment(pr, "68", None, None, "test-model")
        probe_row = [l for l in comment.splitlines() if "Behavioral Probe" in l][0]
        self.assertIn("—", probe_row)

    def test_changelog_missing_shows_dash(self):
        pr = {**SAMPLE_PR, "deterministic": {"changelogSignal": {"status": "missing"}}}
        comment = _fallback_comment(pr, "16", None, None, "test-model")
        cl_row = [l for l in comment.splitlines() if "Changelog" in l and "|" in l][0]
        self.assertIn("—", cl_row)

    def test_api_diff_unavailable_shows_dash(self):
        pr = {**SAMPLE_PR, "deterministic": {"api_diff_tool": None, "api_changes": 0}}
        comment = _fallback_comment(pr, "16", None, None, "test-model")
        api_row = [l for l in comment.splitlines() if "API Diff" in l][0]
        self.assertIn("—", api_row)


class TestSafePreExistingExplanation(unittest.TestCase):
    """C4: SAFE + pre_existing build must have bridging explanation."""

    def test_safe_with_pre_existing_has_explanation(self):
        pr = {
            **SAMPLE_PR,
            "build": {"verdict": "pre_existing", "pr_exit": 2},
            "verdict_v2": {"verdict": "SAFE"},
        }
        comment = _fallback_comment(pr, "16", None, None, "test-model")
        self.assertIn("pre-existing", comment)
        self.assertIn("not caused by this upgrade", comment)

    def test_safe_without_pre_existing_no_explanation(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "test-model")
        self.assertNotIn("not caused by this upgrade", comment)


class TestMergeRiskRendering(unittest.TestCase):
    """C5: merge_risk.tag and reason must be visible in comment."""

    def test_merge_risk_medium_rendered(self):
        pr = {
            **SAMPLE_PR,
            "merge_risk": {"tag": "Medium", "reason": "major version bump"},
        }
        comment = _fallback_comment(pr, "9", None, None, "test-model")
        self.assertIn("Merge Risk", comment)
        self.assertIn("Medium", comment)
        self.assertIn("major version bump", comment)

    def test_no_merge_risk_no_section(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "test-model")
        self.assertNotIn("Merge Risk", comment)


class TestCrossPrDepsRendering(unittest.TestCase):
    """C6: cross_pr_deps must appear in per-PR comments."""

    def test_cross_pr_deps_rendered(self):
        pr = {**SAMPLE_PR}
        deps = [
            {"pr_a": 24, "pr_b": 42, "reason": "plugin/parser pair must stay in lockstep",
             "merge_order": "merge together"},
        ]
        comment = _fallback_comment(pr, "42", None, None, "test-model", cross_pr_deps=deps)
        self.assertIn("Coordinated Upgrades", comment)
        self.assertIn("PR #24", comment)
        self.assertIn("lockstep", comment)
        self.assertIn("merge together", comment)

    def test_no_cross_pr_deps_no_section(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "test-model", cross_pr_deps=[])
        self.assertNotIn("Coordinated Upgrades", comment)

    def test_unrelated_deps_not_shown(self):
        deps = [
            {"pr_a": 100, "pr_b": 200, "reason": "unrelated pair"},
        ]
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "test-model", cross_pr_deps=deps)
        self.assertNotIn("Coordinated Upgrades", comment)


class TestFooterDateFromMetadata(unittest.TestCase):
    """C7: Footer date must use metadata.timestamp, not today's date."""

    def test_footer_uses_metadata_timestamp(self):
        meta = {"timestamp": "2026-07-17T05:06:14Z"}
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "test-model", metadata=meta)
        self.assertIn("2026-07-17", comment)
        self.assertNotIn("T05:06:14Z", comment)

    def test_footer_falls_back_to_today_without_metadata(self):
        comment = _fallback_comment(SAMPLE_PR, "42", None, None, "test-model")
        self.assertIn("Analyzed:", comment)


class TestEnforceVerdictFloorBodyText(unittest.TestCase):
    """C1/C2: _enforce_verdict_floor must rewrite body text, not just header."""

    def test_merge_immediately_rewritten_for_blocked(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## 🚨 REVIEW RISK — `pkg` 1.0 → 2.0\n"
            "Merge immediately.\n"
            "Verdict: MERGE IMMEDIATELY\n"
        )
        pr = {"package": "pkg", "build": {"verdict": "fail"}, "test": {"ran": False}}
        result = _enforce_verdict_floor(comment, pr, "54")
        self.assertNotIn("MERGE IMMEDIATELY", result)
        self.assertNotIn("Merge immediately", result)
        self.assertIn("BLOCKED", result)

    def test_review_risk_rewritten_to_blocked(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## 🚨 REVIEW RISK — `pkg` 1.0 → 2.0\n"
            "Some REVIEW RISK text here.\n"
        )
        pr = {"package": "pkg", "build": {"verdict": "fail"}, "test": {"ran": False}}
        result = _enforce_verdict_floor(comment, pr, "9")
        self.assertNotIn("REVIEW RISK", result)
        self.assertIn("BLOCKED", result)

    def test_body_verdict_references_corrected(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## ⚠️ REVIEW — `pkg` 1.0 → 2.0\n"
            "verdict = REVIEW\n"
            "AI Arbiter: REVIEW\n"
        )
        pr = {"package": "pkg", "build": {"verdict": "fail"}, "test": {"ran": False}}
        result = _enforce_verdict_floor(comment, pr, "23")
        self.assertIn("verdict = BLOCKED", result)
        self.assertIn("AI Arbiter: BLOCKED", result)

    def test_correct_verdict_not_touched(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## 🚫 BLOCKED — `pkg` 1.0 → 2.0\n"
            "verdict = BLOCKED\n"
        )
        pr = {"package": "pkg", "build": {"verdict": "fail"}, "test": {"ran": False}}
        result = _enforce_verdict_floor(comment, pr, "32")
        self.assertEqual(result, comment)

    def test_positive_control_pr52_blocked_stays_correct(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## 🚫 BLOCKED — `golang.org/x/crypto` 0.46.0 → 0.52.0\n"
            "verdict = BLOCKED\n"
        )
        pr = {
            "package": "golang.org/x/crypto", "build": {"verdict": "fail"},
            "test": {"ran": False},
        }
        result = _enforce_verdict_floor(comment, pr, "52")
        self.assertIn("BLOCKED", result)
        self.assertNotIn("REVIEW", result)


class TestSanitizeComment(unittest.TestCase):
    """C3: _sanitize_comment strips fabricated URLs and QA notes."""

    def test_strips_qa_notes(self):
        comment = "## REVIEW — pkg\nContent.\n**Character count:** ~15,800\n**Line count:** ~420 lines"
        result = _sanitize_comment(comment, {}, "8")
        self.assertNotIn("Character count", result)
        self.assertNotIn("Line count", result)

    def test_fixes_nvg_to_nvd(self):
        comment = "See https://nvg.nist.gov/vuln/detail/CVE-2026-29181"
        result = _sanitize_comment(comment, {}, "8")
        self.assertIn("nvd.nist.gov", result)
        self.assertNotIn("nvg.nist.gov", result)

    def test_strips_fabricated_run_id(self):
        comment = "Run: actions/runs/12345 completed."
        result = _sanitize_comment(comment, {}, "8")
        self.assertNotIn("actions/runs/12345", result)

    def test_strips_your_org_placeholder(self):
        comment = "Link: https://github.com/your-org/your-repo/issues/68"
        result = _sanitize_comment(comment, {}, "8")
        self.assertNotIn("your-org/your-repo", result)


class TestStripAgentNarration(unittest.TestCase):
    """C3: _strip_agent_narration removes leaked LLM self-narration."""

    def test_strips_now_let_me(self):
        comment = "Now let me create the complete PR comment for PR #8:\n\n---\n## REVIEW — pkg"
        result = _strip_agent_narration(comment)
        self.assertNotIn("Now let me", result)
        self.assertIn("## REVIEW", result)

    def test_strips_ill_create(self):
        comment = "I'll create the analysis now.\n## SAFE — pkg"
        result = _strip_agent_narration(comment)
        self.assertNotIn("I'll create", result)

    def test_preserves_normal_content(self):
        comment = "## REVIEW — pkg\nThis is a normal comment."
        result = _strip_agent_narration(comment)
        self.assertIn("normal comment", result)


class TestProbeMismatchDowngrade(unittest.TestCase):
    """C6: PACKAGE-MISMATCH in reconciliation_note → LOW confidence in fallback."""

    def test_mismatch_shows_low_in_fallback(self):
        pr = {
            **SAMPLE_PR,
            "behavioral_grade": {
                "same_behavior": True, "confidence": "high",
                "reconciliation_note": "PACKAGE-MISMATCH: probe analyzed wrong package. Grade should be re-evaluated.",
            },
        }
        comment = _fallback_comment(pr, "8", None, None, "test-model")
        probe_row = [l for l in comment.splitlines() if "Behavioral Probe" in l][0]
        self.assertIn("LOW", probe_row)
        self.assertIn("mismatch", probe_row.lower())

    def test_no_mismatch_keeps_actual_confidence(self):
        pr = {
            **SAMPLE_PR,
            "behavioral_grade": {"same_behavior": True, "confidence": "high"},
        }
        comment = _fallback_comment(pr, "7", None, None, "test-model")
        probe_row = [l for l in comment.splitlines() if "Behavioral Probe" in l][0]
        self.assertIn("HIGH", probe_row)
        self.assertNotIn("mismatch", probe_row.lower())

    def test_downgrade_in_ai_comment(self):
        comment = (
            "<!-- breakability-check -->\n"
            "| Behavioral Probe | ✅ STABLE | High confidence |\n"
        )
        pr = {
            "behavioral_grade": {
                "same_behavior": True, "confidence": "high",
                "reconciliation_note": "PACKAGE-MISMATCH: wrong pkg",
            },
        }
        result = _downgrade_mismatched_probe(comment, pr, "8")
        self.assertIn("Low confidence", result)
        self.assertIn("package mismatch", result)
        self.assertNotIn("High confidence", result)


class TestCveFloorBlockedBanner(unittest.TestCase):
    """C7: CVE-floor BLOCKED PRs get security urgency banner in fallback."""

    def test_cve_floor_blocked_has_banner(self):
        pr = {
            **SAMPLE_PR,
            "build": {"verdict": "fail", "pr_exit": 1},
            "test": {"ran": False},
            "cve_details": [{"cve_id": "CVE-2026-33815", "severity": "critical", "cvss_score": 9.8}],
            "deterministic": {
                "security": {
                    "isSecurity": True, "cveIds": ["CVE-2026-33815", "CVE-2026-33816", "CVE-2026-41889"],
                    "cvssScore": 9.8,
                },
            },
        }
        comment = _fallback_comment(pr, "32", None, None, "test-model")
        self.assertIn("CRITICAL SECURITY UPDATE", comment)
        self.assertIn("BLOCKED", comment)

    def test_non_cve_blocked_no_banner(self):
        pr = {
            **SAMPLE_PR,
            "build": {"verdict": "fail", "pr_exit": 1},
            "test": {"ran": False},
        }
        comment = _fallback_comment(pr, "42", None, None, "test-model")
        self.assertNotIn("CRITICAL SECURITY UPDATE", comment)


class TestGovulncheckStripping(unittest.TestCase):
    """C11: govulncheck recommendations stripped from comments."""

    def test_strips_govulncheck_install(self):
        comment = "## REVIEW\n```bash\ngo install golang.org/x/vuln/cmd/govulncheck@latest\ngovulncheck ./...\n```"
        result = _strip_govulncheck(comment)
        self.assertNotIn("govulncheck", result)

    def test_preserves_other_content(self):
        comment = "## REVIEW\n```bash\ngo build ./...\ngo test ./...\n```"
        result = _strip_govulncheck(comment)
        self.assertIn("go build", result)
        self.assertIn("go test", result)


class TestDenyListInPrompt(unittest.TestCase):
    """C11: AI prompt includes govulncheck deny list."""

    def test_prompt_contains_deny_list(self):
        prompt = _build_per_pr_prompt(
            base_prompt="Base instructions",
            pr=SAMPLE_PR, pr_num="42",
            metadata={}, run_url=None, merge_plan_issue=None,
            model_name="test", cross_deps=[], top_level={},
        )
        self.assertIn("govulncheck", prompt)
        self.assertIn("DENY LIST", prompt)


class TestEnforceVerdictFloorAIFormats(unittest.TestCase):
    """C1: _enforce_verdict_floor must handle AI output formats with compound verdict words."""

    def test_review_risk_rewritten_to_blocked_in_header(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## 🚨 REVIEW RISK — `github.com/jackc/pgx/v5` 5.7.4 → 5.9.2\n"
            "Body text here"
        )
        pr = {"package": "github.com/jackc/pgx/v5", "build": {"verdict": "fail"}, "test": {"ran": False}}
        result = _enforce_verdict_floor(comment, pr, "23")
        self.assertIn("## 🚫 BLOCKED", result)
        self.assertNotIn("REVIEW RISK", result)

    def test_security_risk_rewritten_to_blocked_in_header(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## 🚨 SECURITY_RISK — `pkg` 1.0 → 2.0\n"
            "verdict = SECURITY_RISK\n"
        )
        pr = {"package": "pkg", "build": {"verdict": "fail"}, "test": {"ran": False}}
        result = _enforce_verdict_floor(comment, pr, "23")
        self.assertIn("## 🚫 BLOCKED", result)
        self.assertNotIn("SECURITY_RISK", result)

    def test_governance_override_stripped_for_blocked(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## 🚨 REVIEW RISK — `pkg` 1.0 → 2.0\n"
            "**Rule applied:** SECURITY OVERRIDE (Rule 0.5) — merge for CVE.\n"
            "security_override = MERGE_REQUIRED\n"
        )
        pr = {"package": "pkg", "build": {"verdict": "fail"}, "test": {"ran": False}}
        result = _enforce_verdict_floor(comment, pr, "23")
        self.assertNotIn("SECURITY OVERRIDE", result)
        self.assertNotIn("MERGE_REQUIRED", result)
        self.assertIn("human review", result)

    def test_must_be_merged_rewritten_for_blocked(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## 🚨 REVIEW RISK — `pkg` 1.0 → 2.0\n"
            "This PR MUST be merged for security reasons.\n"
        )
        pr = {"package": "pkg", "build": {"verdict": "fail"}, "test": {"ran": False}}
        result = _enforce_verdict_floor(comment, pr, "54")
        self.assertNotIn("MUST be merged", result)

    def test_positive_control_fallback_blocked_unchanged(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## 🚫 BLOCKED — `pgx/v5` 5.7.4 → 5.9.2\n"
            "verdict = BLOCKED\n"
        )
        pr = {"package": "pgx/v5", "build": {"verdict": "fail"}, "test": {"ran": False}}
        result = _enforce_verdict_floor(comment, pr, "9")
        self.assertIn("## 🚫 BLOCKED", result)

    def test_review_verdict_pr_keeps_merge_recommendation(self):
        """Positive control: REVIEW PRs must keep their merge recommendations."""
        comment = (
            "<!-- breakability-check -->\n"
            "## ⚠️ REVIEW — `pkg` 1.0 → 2.0\n"
            "Merge recommended after review.\n"
        )
        pr = {
            "package": "pkg", "build": {"verdict": "pass"},
            "test": {"ran": True, "exit": 0},
            "policy_lowering": {"decision": {"verdict": "REVIEW"}},
        }
        result = _enforce_verdict_floor(comment, pr, "41")
        self.assertIn("Merge recommended after review", result)


class TestGuardEmptyBuildOutput(unittest.TestCase):
    """C3: Strip fabricated infra causes when build.output_tail is empty."""

    def test_strips_go_toolchain_unavailable(self):
        comment = "Build not run (Go toolchain unavailable)\nGo is not installed on runner"
        pr = {"build": {"output_tail": "", "pr_exit": -1}}
        result = _guard_empty_build_output(comment, pr, "23")
        self.assertNotIn("Go toolchain unavailable", result)
        self.assertNotIn("Go is not installed", result)
        self.assertIn("no diagnostic output captured", result)

    def test_strips_missing_path(self):
        comment = "Build failed: missing PATH configuration for Go"
        pr = {"build": {"output_tail": "\n", "pr_exit": -1}}
        result = _guard_empty_build_output(comment, pr, "54")
        self.assertIn("no diagnostic output captured", result)

    def test_preserves_when_output_tail_present(self):
        comment = "Build failed: Go toolchain unavailable"
        pr = {"build": {"output_tail": "no space left on device", "pr_exit": -1}}
        result = _guard_empty_build_output(comment, pr, "10")
        self.assertIn("Go toolchain unavailable", result)

    def test_preserves_when_pr_exit_not_negative_one(self):
        comment = "Build failed: Go toolchain unavailable"
        pr = {"build": {"output_tail": "", "pr_exit": 1}}
        result = _guard_empty_build_output(comment, pr, "42")
        self.assertIn("Go toolchain unavailable", result)

    def test_positive_control_disk_space_kept(self):
        """PR#10/#11 must keep correct disk-space diagnosis."""
        comment = "Build failed: no space left on device"
        pr = {"build": {"output_tail": "no space left on device", "pr_exit": 1}}
        result = _guard_empty_build_output(comment, pr, "10")
        self.assertIn("no space left on device", result)


class TestStripWrongEcosystemRefs(unittest.TestCase):
    """C5: Strip Node.js/npm references from Go-only repo comments."""

    def test_strips_npm_install_from_gomod(self):
        comment = (
            "## REVIEW\n"
            "Verify that Node.js 20 is installed and npm cache is restored correctly\n"
            "```bash\nnpm install pkg@1.0\nnpm run build\n```\n"
        )
        pr = {
            "ecosystem": "gomod",
            "_top_level": {"codebase_context": {"has_package_json": False}},
        }
        result = _strip_wrong_ecosystem_refs(comment, pr, "20")
        self.assertNotIn("Node.js 20 is installed", result)
        self.assertNotIn("npm install", result)

    def test_strips_npm_from_actions_go_only(self):
        comment = "Verify that Node.js 20 is installed and npm cache is restored correctly\n"
        pr = {
            "ecosystem": "actions",
            "_top_level": {"codebase_context": {"has_package_json": False}},
        }
        result = _strip_wrong_ecosystem_refs(comment, pr, "4")
        self.assertNotIn("Node.js 20 is installed", result)

    def test_keeps_npm_for_mixed_repo(self):
        comment = "```bash\nnpm install pkg@1.0\n```"
        pr = {
            "ecosystem": "gomod",
            "_top_level": {"codebase_context": {"has_package_json": True}},
        }
        result = _strip_wrong_ecosystem_refs(comment, pr, "42")
        self.assertIn("npm install", result)

    def test_keeps_npm_for_npm_ecosystem(self):
        comment = "```bash\nnpm install pkg@1.0\n```"
        pr = {
            "ecosystem": "npm",
            "_top_level": {"codebase_context": {"has_package_json": True}},
        }
        result = _strip_wrong_ecosystem_refs(comment, pr, "42")
        self.assertIn("npm install", result)


class TestValidateMergeRiskTag(unittest.TestCase):
    """C7: Validate merge_risk tag against enum {Low,Medium,High,None}."""

    def test_invalid_tag_replaced_with_ground_truth(self):
        comment = "## REVIEW\n\nMerge Risk: BLOCKED\nSome content"
        pr = {"merge_risk": {"tag": "High"}}
        result = _validate_merge_risk_tag(comment, pr, "52")
        self.assertIn("Merge Risk: High", result)
        self.assertNotIn("Merge Risk: BLOCKED", result)

    def test_valid_tag_unchanged(self):
        comment = "## REVIEW\n\nMerge Risk: High\nSome content"
        pr = {"merge_risk": {"tag": "High"}}
        result = _validate_merge_risk_tag(comment, pr, "53")
        self.assertIn("Merge Risk: High", result)

    def test_no_merge_risk_in_comment_unchanged(self):
        comment = "## REVIEW\nSome content"
        pr = {"merge_risk": {"tag": "High"}}
        result = _validate_merge_risk_tag(comment, pr, "42")
        self.assertEqual(result, comment)


class TestSanitizeCommentFabricatedSHA(unittest.TestCase):
    """C4: Strip fabricated 40-char hex strings (commit SHAs) not in PR metadata."""

    def test_strips_fabricated_sha(self):
        comment = "Commit fa0a91b85d4f404e444e00e005971372dc801d16 introduced changes"
        pr = {"commit_sha": "", "base_sha": ""}
        result = _sanitize_comment(comment, pr, "4")
        self.assertNotIn("fa0a91b85d4f404e444e00e005971372dc801d16", result)

    def test_keeps_real_sha(self):
        real_sha = "abcdef1234567890abcdef1234567890abcdef12"
        comment = f"Commit {real_sha} introduced changes"
        pr = {"commit_sha": real_sha}
        result = _sanitize_comment(comment, pr, "4")
        self.assertIn(real_sha, result)


class TestMergeRiskReasonEnrichment(unittest.TestCase):
    """C8: merge_risk.reason must include reachability/probe evidence."""

    def test_zero_imports_in_reason(self):
        from verdict_contract import authoritative_verdict
        pr = {
            "package": "some-pkg", "from": "1.0", "to": "2.0",
            "build": {"verdict": "pass"}, "test": {"ran": False},
            "files_importing": [], "usages": [],
            "deterministic": {
                "merge_risk": {"tag": "Medium", "reason": "missing or unparsable changelog; default caution"},
            },
        }
        av = authoritative_verdict(pr)
        self.assertIn("not imported by application code", av.get("reason", ""))

    def test_nonzero_imports_in_reason(self):
        from verdict_contract import authoritative_verdict
        pr = {
            "package": "some-pkg", "from": "1.0", "to": "2.0",
            "build": {"verdict": "pass"}, "test": {"ran": False},
            "files_importing": ["src/auth.go", "src/api.go"], "usages": [],
            "deterministic": {
                "merge_risk": {"tag": "Medium", "reason": "missing or unparsable changelog; default caution"},
            },
        }
        av = authoritative_verdict(pr)
        self.assertIn("imported by 2 file(s)", av.get("reason", ""))

    def test_positive_control_nonempty_usages_no_double_annotation(self):
        """PRs with non-empty usages must NOT get 'not imported' annotation."""
        from verdict_contract import authoritative_verdict
        pr = {
            "package": "lodash", "from": "4.17.20", "to": "4.17.21",
            "build": {"verdict": "pass"}, "test": {"ran": True, "exit": 0},
            "files_importing": ["src/utils.ts"], "usages": ["debounce", "throttle"],
            "verdict_v2": {"verdict": "SAFE", "severity": "low", "confidence": "L4", "priority": "P3"},
        }
        av = authoritative_verdict(pr)
        self.assertNotIn("not imported", av.get("reason", ""))


class TestStripMergeEncouraging(unittest.TestCase):
    """Tests for C2: structural merge-encouraging language stripping."""

    def test_strips_merge_immediately(self):
        comment = "Header line\nMerge immediately for security.\nOther text."
        result = _strip_merge_encouraging(comment, "9")
        self.assertNotIn("Merge immediately", result)
        self.assertIn("Other text", result)

    def test_strips_merge_recommended(self):
        comment = "Merge recommended despite verification limitations.\nSafe text."
        result = _strip_merge_encouraging(comment, "52")
        self.assertNotIn("Merge recommended", result)
        self.assertIn("Safe text", result)

    def test_strips_must_be_merged(self):
        comment = "This upgrade must be merged for security.\nMore text."
        result = _strip_merge_encouraging(comment, "54")
        self.assertNotIn("must be merged", result)
        self.assertIn("More text", result)

    def test_strips_merge_without_delay(self):
        comment = "Once verified, merge without delay.\nEnd."
        result = _strip_merge_encouraging(comment, "9")
        self.assertNotIn("merge without delay", result)

    def test_strips_strongly_recommended_merge(self):
        comment = "Strongly recommended for immediate merge.\nKeep."
        result = _strip_merge_encouraging(comment, "54")
        self.assertNotIn("merge", result.lower())
        self.assertIn("Keep", result)

    def test_strips_merge_this_pr_with_recommendation(self):
        """Merge + should in same sentence gets stripped."""
        comment = "You should merge this PR promptly.\nKeep this."
        result = _strip_merge_encouraging(comment, "53")
        self.assertNotIn("merge this PR", result)
        self.assertIn("Keep this", result)


class TestRewriteNoncanonicalArbiter(unittest.TestCase):
    """Tests for C3: validate AI Arbiter tokens against canonical enum."""

    def test_rewrites_merge_recommended(self):
        comment = "| AI Arbiter | 🚨 MERGE RECOMMENDED | HIGH |"
        result = _rewrite_noncanonical_arbiter(comment, "BLOCKED", "53")
        self.assertNotIn("MERGE RECOMMENDED", result)
        self.assertIn("BLOCKED", result)

    def test_keeps_canonical_blocked(self):
        comment = "| AI Arbiter | 🚨 BLOCKED | PARTIAL |"
        result = _rewrite_noncanonical_arbiter(comment, "BLOCKED", "23")
        self.assertIn("BLOCKED", result)

    def test_keeps_canonical_safe(self):
        comment = "| AI Arbiter | ✅ SAFE | HIGH |"
        result = _rewrite_noncanonical_arbiter(comment, "SAFE", "42")
        self.assertIn("SAFE", result)

    def test_rewrites_security_risk(self):
        comment = "| AI Arbiter | 🚨 SECURITY RISK | HIGH |"
        result = _rewrite_noncanonical_arbiter(comment, "BLOCKED", "54")
        self.assertNotIn("SECURITY RISK", result)
        self.assertIn("BLOCKED", result)


class TestGuardEmptyBuildOutputCodeBlocks(unittest.TestCase):
    """Tests for C4: citation grounding — fabricated code blocks."""

    def test_strips_fabricated_buffer_ids(self):
        comment = (
            "Build output:\n```\ncompile: writing output: "
            "write $WORK/b109/_pkg_.a: no space left on device\n```\nEnd."
        )
        pr = {"build": {"output_tail": "\n", "pr_exit": -1}}
        result = _guard_empty_build_output(comment, pr, "23")
        self.assertNotIn("$WORK/b109", result)
        self.assertIn("no diagnostic output captured", result)

    def test_preserves_real_output(self):
        comment = (
            "Build output:\n```\ncompile: writing output: "
            "write $WORK/b109/_pkg_.a: no space left on device\n```\n"
        )
        pr = {"build": {"output_tail": "real disk space error output", "pr_exit": 1}}
        result = _guard_empty_build_output(comment, pr, "11")
        self.assertIn("$WORK/b109", result)

    def test_preserves_non_build_code_blocks(self):
        comment = "```bash\ngo test ./...\n```\n"
        pr = {"build": {"output_tail": "", "pr_exit": -1}}
        result = _guard_empty_build_output(comment, pr, "52")
        self.assertIn("go test", result)


class TestInjectMergeRisk(unittest.TestCase):
    """Tests for C5: inject Merge Risk section when missing from AI comments."""

    def test_injects_when_missing(self):
        comment = "## BLOCKED — pkg\nSome analysis.\n### Recommendation\nSteps."
        pr = {
            "merge_risk": {"tag": "High", "reason": "CVE floor applied"},
            "build": {"verdict": "fail"}, "test": {"ran": False},
        }
        result = _inject_merge_risk(comment, pr, "9")
        self.assertIn("Merge Risk", result)
        self.assertIn("High", result)
        self.assertIn("CVE floor", result)

    def test_no_duplicate_when_present(self):
        comment = "## BLOCKED\n### Merge Risk\n**🔴 High**\n### Recommendation"
        pr = {
            "merge_risk": {"tag": "High", "reason": "CVE floor"},
            "build": {"verdict": "fail"}, "test": {"ran": False},
        }
        result = _inject_merge_risk(comment, pr, "23")
        self.assertEqual(result.count("Merge Risk"), 1)

    def test_no_injection_when_no_tag(self):
        comment = "## REVIEW\nAnalysis."
        pr = {"merge_risk": {}, "build": {"verdict": "pass"}, "test": {"ran": True, "exit": 0}}
        result = _inject_merge_risk(comment, pr, "7")
        self.assertNotIn("Merge Risk", result)


class TestStripYamlCodeBlockNpm(unittest.TestCase):
    """Tests for C6: npm/yarn substitution inside YAML code blocks."""

    def test_replaces_npm_ci_in_yaml(self):
        comment = (
            "```yaml\nname: ci\nsteps:\n"
            "      - run: npm ci\n"
            "      - run: npm test\n```\n"
        )
        pr = {
            "ecosystem": "actions",
            "_top_level": {"codebase_context": {"has_package_json": False}},
        }
        result = _strip_wrong_ecosystem_refs(comment, pr, "22")
        self.assertNotIn("npm ci", result)
        self.assertNotIn("npm test", result)
        self.assertIn("go build", result)
        self.assertIn("go test", result)

    def test_keeps_npm_for_node_repo(self):
        comment = "```yaml\n      - run: npm ci\n      - run: npm test\n```\n"
        pr = {
            "ecosystem": "actions",
            "_top_level": {"codebase_context": {"has_package_json": True}},
        }
        result = _strip_wrong_ecosystem_refs(comment, pr, "22")
        self.assertIn("npm ci", result)
        self.assertIn("npm test", result)


class TestStripRuleCitations(unittest.TestCase):
    """Tests for C7: fabricated Rule N citations."""

    def test_strips_rule_numbers(self):
        comment = (
            "Analysis:\n"
            "Rule 0.5 (Security Override) applies.\n"
            "See Rule 23 for coordinated upgrades.\n"
            "Normal text here.\n"
        )
        pr = {"package": "pkg", "build": {"verdict": "pass"}, "test": {"ran": True, "exit": 0}}
        result = _sanitize_comment(comment, pr, "23")
        self.assertNotIn("Rule 0.5", result)
        self.assertNotIn("Rule 23", result)
        self.assertIn("Normal text", result)

    def test_strips_rule_with_colon(self):
        comment = "Rule 8: Actions PRs default SAFE.\nEnd."
        pr = {"package": "pkg", "build": {"verdict": "pass"}, "test": {"ran": True, "exit": 0}}
        result = _sanitize_comment(comment, pr, "4")
        self.assertNotIn("Rule 8", result)
        self.assertIn("End", result)


class TestVerdictReviewSafeContradiction(unittest.TestCase):
    """Tests for C8: SAFE/REVIEW contradiction in body text."""

    def test_review_verdict_strips_safe_language(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## ✅ SAFE — `go.opentelemetry.io/otel/sdk` 1.39.0 → 1.43.0\n"
            "| AI Arbiter | ✅ SAFE | MEDIUM |\n"
            "SAFE to merge with coordination.\n"
            "Why SAFE: all signals green.\n"
            "we keep SAFE and add coordination.\n"
        )
        pr = {
            "package": "go.opentelemetry.io/otel/sdk",
            "build": {"verdict": "pass", "pr_exit": 0},
            "test": {"ran": False},
            "behavioral_grade": {"same_behavior": True, "confidence": "high"},
            "verdict_v2": {"verdict": "REVIEW", "severity": "medium"},
            "dep_type": "dev", "bump": "minor",
        }
        result = _enforce_verdict_floor(comment, pr, "7")
        self.assertIn("REVIEW", result)
        self.assertNotIn("AI Arbiter | ✅ SAFE", result)
        self.assertNotIn("SAFE to merge", result)
        self.assertNotIn("Why SAFE", result)
        self.assertNotIn("we keep SAFE", result)

    def test_safe_verdict_keeps_safe_language(self):
        comment = (
            "<!-- breakability-check -->\n"
            "## ✅ SAFE — `lodash` 4.17.20 → 4.17.21\n"
            "| AI Arbiter | ✅ SAFE | HIGH |\n"
            "SAFE to merge.\n"
        )
        pr = {
            "package": "lodash",
            "build": {"verdict": "pass", "pr_exit": 0},
            "test": {"ran": True, "exit": 0},
            "verdict_v2": {"verdict": "SAFE", "severity": "low"},
        }
        result = _enforce_verdict_floor(comment, pr, "42")
        self.assertIn("SAFE to merge", result)
        self.assertIn("AI Arbiter | ✅ SAFE", result)


if __name__ == "__main__":
    unittest.main()
