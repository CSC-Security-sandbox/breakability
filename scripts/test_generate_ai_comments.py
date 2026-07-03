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
    _normalize_verdict_text,
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
                      has_reachability=True):
        parts = ["<!-- breakability-check -->", "## SAFE — lodash"]
        if has_table:
            parts.append("| Layer | Signal | Detail |")
        if has_subsection:
            parts.append("### How we checked")
        if has_numbered:
            parts.append("1. Review the changelog")
        if has_bash:
            parts.append("```bash")
            parts.append("npm test")
            parts.append("```")
        if has_reachability:
            parts.append("**Reachability** confirms the package is imported by 3 files")
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
        comment = self._make_comment(has_subsection=False)
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

    def test_diagnostics_has_all_eight_criteria(self):
        comment = self._make_comment(lines=170)
        _, diag = _validate_comment(comment, "42")
        expected = {"line_count", "has_h2", "has_signal_table", "has_h3",
                    "has_mode_footer", "has_numbered_list", "has_bash_block", "has_reachability"}
        self.assertEqual(set(diag.keys()), expected)

    def test_verdict_mismatch_detected(self):
        """When AI says SAFE but contract says REVIEW, validation fails with verdict_mismatch."""
        comment = self._make_comment(lines=170)
        pr = {
            "package": "jwks-rsa", "build": {"verdict": "pass"},
            "test": {"ran": False},
            "behavioral_grade": {"same_behavior": False, "behavior_changed": True},
            "files_importing": ["src/auth.ts"],
            "policy_lowering": {"decision": {"verdict": "MERGE"}},
        }
        passed, diag = _validate_comment(comment, "42", pr)
        self.assertFalse(passed)
        self.assertIn("verdict_mismatch", diag)
        self.assertFalse(diag["verdict_mismatch"]["passed"])
        self.assertIn("AI=SAFE", diag["verdict_mismatch"]["value"])

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
                   "has_mode_footer", "has_numbered_list", "has_bash_block", "has_reachability"]
        diag = {}
        for c in checks:
            if c == "line_count":
                diag[c] = {"passed": c not in failures, "value": line_count}
            else:
                diag[c] = {"passed": c not in failures, "value": c not in failures}
        return diag

    def test_long_comment_one_failure_accepted(self):
        diag = self._make_diag(line_count=381, failures={"has_h3"})
        self.assertTrue(_near_valid(diag))

    def test_short_comment_one_failure_rejected(self):
        diag = self._make_diag(line_count=100, failures={"has_h3"})
        self.assertFalse(_near_valid(diag))

    def test_long_comment_two_failures_rejected(self):
        diag = self._make_diag(line_count=400, failures={"has_h3", "has_bash_block"})
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

    def test_review_verdict_without_verdict_v2(self):
        pr = {**SAMPLE_PR, "build": {"verdict": "pass", "pr_exit": 0},
              "test": {"ran": True, "exit": 0}}
        del pr["verdict_v2"]
        comment = _fallback_comment(pr, "42", None, None, "claude-sonnet-4.5")
        self.assertIn("REVIEW", comment)
        self.assertIn("⚠️", comment)


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
        parts.append("1. Review the changelog")
        parts.append("```bash")
        parts.append("npm test")
        parts.append("```")
        parts.append("**Reachability** confirms the package is imported by 3 files")
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


if __name__ == "__main__":
    unittest.main()
