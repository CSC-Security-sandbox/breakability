#!/usr/bin/env python3
"""Tests for generate_ai_merge_plan.py"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate_ai_merge_plan import (
    _categorize_prs,
    _get_verdict,
    _pr_row,
    _parse_all_open_prs,
    _strip_preamble,
    _is_meta_description,
    _fix_section_membership,
    _validate_ai_package_names,
    _build_merge_plan_prompt,
    generate_template_plan,
    generate_merge_plan,
)


class TestStripPreamble(unittest.TestCase):
    def test_strips_conversational_preamble(self):
        text = "Sure, here is the merge plan:\n# Breakability Merge Plan\n\nContent here"
        result = _strip_preamble(text)
        self.assertTrue(result.startswith("# Breakability Merge Plan"))

    def test_strips_code_fences(self):
        text = "```markdown\n# Breakability Merge Plan\n\nContent\n```"
        result = _strip_preamble(text)
        self.assertTrue(result.startswith("# Breakability Merge Plan"))
        self.assertNotIn("```", result)

    def test_preserves_clean_response(self):
        text = "# Breakability Merge Plan\n\nContent here"
        result = _strip_preamble(text)
        self.assertEqual(result, text)

    def test_strips_multiline_preamble(self):
        text = "I'll create the plan.\nHere it is:\n# Breakability Merge Plan\nLine 2"
        result = _strip_preamble(text)
        self.assertTrue(result.startswith("# Breakability Merge Plan"))
        self.assertIn("Line 2", result)

    def test_no_heading_returns_as_is(self):
        text = "This response has no heading at all"
        result = _strip_preamble(text)
        self.assertEqual(result, text)

    def test_strips_preamble_with_hash_in_text(self):
        text = "Perfect! I now have all the data for PR #11.\n\n# Breakability Merge Plan\nContent"
        result = _strip_preamble(text)
        self.assertTrue(result.startswith("# Breakability Merge Plan"))

    def test_strips_preamble_before_h2(self):
        text = "Here is the plan:\n## Summary\nContent"
        result = _strip_preamble(text)
        self.assertTrue(result.startswith("## Summary"))


class TestCategorizePrs(unittest.TestCase):
    def test_empty_dict(self):
        result = _categorize_prs({})
        for key in ("safe", "review", "blocked", "unverified", "skipped"):
            self.assertEqual(result[key], [])

    def test_safe_verdict(self):
        prs = {"10": {"verdict_v2": {"verdict": "SAFE"}}}
        result = _categorize_prs(prs)
        self.assertEqual(len(result["safe"]), 1)
        self.assertEqual(result["safe"][0][0], "10")

    def test_blocked_verdict(self):
        prs = {"5": {"verdict_v2": {"verdict": "BLOCKED"}}}
        result = _categorize_prs(prs)
        self.assertEqual(len(result["blocked"]), 1)

    def test_build_fails_goes_to_blocked(self):
        prs = {"3": {"verdict_v2": {"verdict": "BUILD_FAILS"}}}
        result = _categorize_prs(prs)
        self.assertEqual(len(result["blocked"]), 1)

    def test_unverified_verdict(self):
        prs = {"7": {"verdict_v2": {"verdict": "UNVERIFIED"}}}
        result = _categorize_prs(prs)
        self.assertEqual(len(result["unverified"]), 1)

    def test_skipped_build_verdict(self):
        prs = {"2": {"build": {"verdict": "skipped"}}}
        result = _categorize_prs(prs)
        self.assertEqual(len(result["skipped"]), 1)

    def test_review_as_default(self):
        prs = {"1": {"verdict_v2": {"verdict": "REVIEW"}}}
        result = _categorize_prs(prs)
        self.assertEqual(len(result["review"]), 1)

    def test_unknown_verdict_falls_to_review(self):
        prs = {"1": {"verdict_v2": {"verdict": "SOMETHING_ELSE"}}}
        result = _categorize_prs(prs)
        self.assertEqual(len(result["review"]), 1)

    def test_mixed_verdicts(self):
        prs = {
            "1": {"verdict_v2": {"verdict": "SAFE"}},
            "2": {"verdict_v2": {"verdict": "REVIEW"}},
            "3": {"verdict_v2": {"verdict": "BLOCKED"}},
            "4": {"verdict_v2": {"verdict": "UNVERIFIED"}},
            "5": {"build": {"verdict": "skipped"}},
            "6": {"verdict_v2": {"verdict": "BUILD_FAILS"}},
        }
        result = _categorize_prs(prs)
        self.assertEqual(len(result["safe"]), 1)
        self.assertEqual(len(result["review"]), 1)
        self.assertEqual(len(result["blocked"]), 2)  # BLOCKED + BUILD_FAILS
        self.assertEqual(len(result["unverified"]), 1)
        self.assertEqual(len(result["skipped"]), 1)

    def test_sorted_by_pr_number(self):
        prs = {
            "20": {"verdict_v2": {"verdict": "SAFE"}},
            "3": {"verdict_v2": {"verdict": "SAFE"}},
            "15": {"verdict_v2": {"verdict": "SAFE"}},
        }
        result = _categorize_prs(prs)
        nums = [n for n, _ in result["safe"]]
        self.assertEqual(nums, ["3", "15", "20"])

    def test_authoritative_verdict_overrides_raw(self):
        """authoritative_verdict() should override raw verdict_v2 when build signals differ."""
        pr = {
            "package": "foo",
            "build": {"verdict": "fail", "pr_exit": 1, "main_exit": 0},
            "test": {"ran": False},
            "verdict_v2": {"verdict": "SAFE"},
        }
        verdict = _get_verdict(pr)
        self.assertEqual(verdict, "BLOCKED")

    def test_categorize_uses_authoritative_verdict(self):
        """Categorize should use authoritative verdict, not raw verdict_v2."""
        prs = {
            "11": {
                "package": "go-jose",
                "build": {"verdict": "fail", "pr_exit": 1, "main_exit": 0},
                "test": {"ran": False},
                "verdict_v2": {"verdict": "SAFE"},
            },
        }
        result = _categorize_prs(prs)
        self.assertEqual(len(result["blocked"]), 1)
        self.assertEqual(len(result["safe"]), 0)


class TestPrRow(unittest.TestCase):
    def test_basic_row(self):
        pr = {
            "package": "lodash",
            "from": "4.17.20",
            "to": "4.17.21",
            "bump": "patch",
            "dep_type": "production",
            "verification_label": "L5",
        }
        row = _pr_row("42", pr)
        self.assertIn("| #42 |", row)
        self.assertIn("`lodash`", row)
        self.assertIn("4.17.20 → 4.17.21", row)
        self.assertIn("patch", row)
        self.assertIn("production", row)
        self.assertIn("L5", row)

    def test_missing_fields_default_to_question_mark(self):
        row = _pr_row("1", {})
        self.assertEqual(row.count("?"), 6)

    def test_long_package_name(self):
        pr = {"package": "@opentelemetry/instrumentation-http", "from": "0.51.0", "to": "0.52.0",
              "bump": "minor", "dep_type": "production", "verification_label": "L3"}
        row = _pr_row("99", pr)
        self.assertIn("`@opentelemetry/instrumentation-http`", row)


class TestParseAllOpenPrs(unittest.TestCase):
    def test_empty_env(self):
        with patch.dict(os.environ, {"ALL_OPEN_PRS": ""}):
            result = _parse_all_open_prs()
            self.assertEqual(result, {})

    def test_missing_env(self):
        env = os.environ.copy()
        env.pop("ALL_OPEN_PRS", None)
        with patch.dict(os.environ, env, clear=True):
            result = _parse_all_open_prs()
            self.assertEqual(result, {})

    def test_tab_separated_input(self):
        with patch.dict(os.environ, {"ALL_OPEN_PRS": "10\tBump lodash\n20\tBump express\n"}):
            result = _parse_all_open_prs()
            self.assertEqual(result, {"10": "Bump lodash", "20": "Bump express"})

    def test_malformed_lines_skipped(self):
        with patch.dict(os.environ, {"ALL_OPEN_PRS": "not a number\ttitle\n10\tValid PR\n\n"}):
            result = _parse_all_open_prs()
            self.assertEqual(result, {"10": "Valid PR"})

    def test_whitespace_stripped(self):
        with patch.dict(os.environ, {"ALL_OPEN_PRS": " 5 \t  Bump foo  \n"}):
            result = _parse_all_open_prs()
            self.assertEqual(result, {"5": "Bump foo"})


class TestBuildMergePlanPrompt(unittest.TestCase):
    def test_contains_required_sections(self):
        data = {
            "prs": {"1": {"verdict_v2": {"verdict": "SAFE"}, "package": "lodash"}},
            "metadata": {"repo": "test/repo", "mode": "advisory"},
        }
        prompt = _build_merge_plan_prompt("BASE PROMPT", data, "https://run.url", "test-model")
        self.assertIn("BASE PROMPT", prompt)
        self.assertIn("MERGE PLAN GENERATION TASK", prompt)
        self.assertIn("PR Summary", prompt)
        self.assertIn("All PRs Data", prompt)
        self.assertIn("OUTPUT INSTRUCTIONS", prompt)
        self.assertIn("test/repo", prompt)

    def test_includes_cross_deps(self):
        data = {
            "prs": {},
            "cross_pr_deps": [{"pr_a": "1", "pr_b": "2", "reason": "peer"}],
        }
        prompt = _build_merge_plan_prompt("BASE", data, None, "m")
        self.assertIn("Cross-PR Dependencies", prompt)

    def test_includes_security_posture(self):
        data = {"prs": {}, "security_posture": {"total_open_alerts": 3}}
        prompt = _build_merge_plan_prompt("BASE", data, None, "m")
        self.assertIn("Security Posture", prompt)

    def test_excludes_govulncheck(self):
        data = {"prs": {}, "govulncheck": {"prs_with_new_vulns": 1}}
        prompt = _build_merge_plan_prompt("BASE", data, None, "m")
        self.assertNotIn("govulncheck", prompt)


class TestGenerateTemplatePlan(unittest.TestCase):
    def test_basic_template(self):
        data = {
            "prs": {
                "1": {"verdict_v2": {"verdict": "SAFE"}, "package": "lodash",
                      "from": "4.0", "to": "4.1", "bump": "minor",
                      "dep_type": "production", "verification_label": "L5"},
            },
            "metadata": {"repo": "test/repo", "mode": "advisory"},
        }
        plan = generate_template_plan(data)
        self.assertIn("# Breakability Merge Plan", plan)
        self.assertIn("test/repo", plan)
        self.assertIn("Safe to Merge", plan)
        self.assertIn("`lodash`", plan)

    def test_advisory_mode_banner(self):
        data = {"prs": {}, "metadata": {"mode": "advisory"}}
        plan = generate_template_plan(data)
        self.assertIn("Advisory mode", plan)

    def test_run_url_included(self):
        data = {"prs": {}, "metadata": {}}
        plan = generate_template_plan(data, run_url="https://example.com/run/1")
        self.assertIn("https://example.com/run/1", plan)


class TestGenerateMergePlan(unittest.TestCase):
    def test_template_fallback_when_no_prompt(self):
        data = {
            "prs": {"1": {"verdict_v2": {"verdict": "SAFE"}, "package": "x",
                          "from": "1", "to": "2", "bump": "minor",
                          "dep_type": "dev", "verification_label": "L1"}},
            "metadata": {"repo": "r", "mode": "advisory"},
        }
        result = generate_merge_plan(data, prompt_path=None)
        self.assertIn("# Breakability Merge Plan", result)

    def test_template_fallback_when_prompt_missing(self):
        data = {"prs": {}, "metadata": {}}
        result = generate_merge_plan(data, prompt_path="/nonexistent/path.md")
        self.assertIn("# Breakability Merge Plan", result)

    @patch("generate_ai_merge_plan.Backend")
    def test_ai_path_returns_ai_response(self, mock_backend_cls):
        mock_backend = MagicMock()
        ai_lines = ["# Breakability Merge Plan", ""] + [f"Line {i}" for i in range(25)]
        mock_backend.invoke.return_value = "\n".join(ai_lines)
        mock_backend_cls.from_env.return_value = mock_backend

        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test prompt")
            prompt_path = f.name

        try:
            data = {"prs": {"1": {"verdict_v2": {"verdict": "SAFE"}}}, "metadata": {}}
            result = generate_merge_plan(data, prompt_path=prompt_path, model="test-model")
            self.assertIn("# Breakability Merge Plan", result)
            mock_backend.invoke.assert_called_once()
        finally:
            os.unlink(prompt_path)

    @patch("generate_ai_merge_plan.Backend")
    def test_ai_failure_falls_back_to_template(self, mock_backend_cls):
        mock_backend_cls.from_env.side_effect = Exception("API error")

        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test prompt")
            prompt_path = f.name

        try:
            data = {"prs": {}, "metadata": {"repo": "fallback/repo"}}
            result = generate_merge_plan(data, prompt_path=prompt_path)
            self.assertIn("# Breakability Merge Plan", result)
            self.assertIn("fallback/repo", result)
        finally:
            os.unlink(prompt_path)


class TestFixSectionMembership(unittest.TestCase):
    """T003: _fix_section_membership moves PRs to correct sections."""

    def _make_data(self, verdicts):
        prs = {}
        for num, v in verdicts.items():
            prs[num] = {"verdict_v2": {"verdict": v}, "build": {"verdict": "pass"}}
        return {"prs": prs}

    def test_moves_review_pr_from_safe_section(self):
        text = (
            "## ✅ Safe to Merge\n"
            "| PR | Package |\n|---|---|\n"
            "| #1 | lodash |\n"
            "| #66 | express |\n\n"
            "## ⚠️ Review Needed\n"
            "| PR | Package |\n|---|---|\n"
            "| #3 | chalk |\n"
        )
        data = self._make_data({"1": "SAFE", "66": "REVIEW", "3": "REVIEW"})
        result = _fix_section_membership(text, data)
        safe_idx = result.index("Safe to Merge")
        review_idx = result.index("Review Needed")
        pr66_idx = result.index("#66")
        self.assertGreater(pr66_idx, review_idx)

    def test_moves_blocked_pr_from_safe_section(self):
        text = (
            "## ✅ Safe to Merge\n"
            "| PR | Package |\n|---|---|\n"
            "| #1 | lodash |\n"
            "| #68 | axios |\n\n"
            "## ❌ Fix Required\n"
            "| PR | Package |\n|---|---|\n"
            "| #5 | chalk |\n"
        )
        data = self._make_data({"1": "SAFE", "68": "BLOCKED", "5": "BLOCKED"})
        result = _fix_section_membership(text, data)
        fix_idx = result.index("Fix Required")
        pr68_idx = result.index("#68")
        self.assertGreater(pr68_idx, fix_idx)

    def test_correct_placement_unchanged(self):
        text = (
            "## ✅ Safe to Merge\n"
            "| PR | Package |\n|---|---|\n"
            "| #1 | lodash |\n\n"
            "## ⚠️ Review Needed\n"
            "| PR | Package |\n|---|---|\n"
            "| #2 | express |\n"
        )
        data = self._make_data({"1": "SAFE", "2": "REVIEW"})
        result = _fix_section_membership(text, data)
        self.assertEqual(result, text)


class TestMetaDescriptionDetection(unittest.TestCase):
    """T008: _is_meta_description detects AI responses that describe the plan."""

    def test_meta_description_with_key_sections_detected(self):
        response = (
            "## Key Sections:\n1. Header with repo info\n2. Developer Action Summary\n"
            "3. Safe to Merge table\n\nThe file is saved at merge-plan.md and is ready to be posted.\n"
            + "\n".join(f"Line {i}" for i in range(25))
        )
        data = {"prs": {"1": {}, "2": {}, "3": {}, "4": {}}}
        self.assertTrue(_is_meta_description(response, data))

    def test_valid_response_with_pr_tables_not_detected(self):
        response = (
            "# Breakability Merge Plan\n\n## Safe to Merge\n"
            "| PR | Package | From | To |\n|---|---|---|---|\n"
            "| #1 | lodash | 4.17.20 | 4.17.21 |\n"
            "| #2 | express | 4.18.0 | 4.18.1 |\n"
            "| #3 | chalk | 5.0.0 | 5.1.0 |\n"
            "| #4 | axios | 1.0.0 | 1.1.0 |\n"
        )
        data = {"prs": {"1": {}, "2": {}, "3": {}, "4": {}}}
        self.assertFalse(_is_meta_description(response, data))

    def test_response_with_insufficient_pr_refs_detected(self):
        response = (
            "# Breakability Merge Plan\n\n## Safe to Merge\n"
            "| PR | Package |\n|---|---|\n| #1 | lodash |\n"
        )
        data = {"prs": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "6": {}}}
        self.assertTrue(_is_meta_description(response, data))


class TestCveFixesTable(unittest.TestCase):
    """CVE Fixes table must render when security_posture.cve_fixes is present."""

    def test_template_plan_renders_cve_fixes_table(self):
        data = {
            "prs": {
                "9": {"package": "pgx/v5", "from": "5.7.4", "to": "5.9.2",
                      "bump": "minor", "dep_type": "production",
                      "verification_label": "BLOCKED",
                      "cve_details": [
                          {"cve_id": "CVE-2026-33815", "severity": "critical"},
                          {"cve_id": "CVE-2026-33816", "severity": "critical"},
                      ]},
            },
            "security_posture": {
                "total_open_alerts": 5,
                "severity_counts": {"critical": 2, "high": 1},
                "cve_fixes": [
                    {"pr": "9", "cve_id": "CVE-2026-33815", "severity": "critical",
                     "package": "pgx/v5", "first_patched_version": "5.9.0"},
                    {"pr": "9", "cve_id": "CVE-2026-33816", "severity": "critical",
                     "package": "pgx/v5", "first_patched_version": "5.9.0"},
                ],
                "orphan_alerts": [
                    {"package": "golang.org/x/net", "severity": "medium"},
                ],
            },
        }
        plan = generate_template_plan(data)
        self.assertIn("CVE Fixes in This Batch", plan)
        self.assertIn("CVE-2026-33815", plan)
        self.assertIn("CVE-2026-33816", plan)
        self.assertIn("critical", plan)
        self.assertIn("1 open alert(s)", plan)

    def test_pr_row_includes_cve_badges(self):
        pr = {
            "package": "pgx/v5", "from": "5.7.4", "to": "5.9.2",
            "bump": "minor", "dep_type": "production", "verification_label": "BLOCKED",
            "cve_details": [{"cve_id": "CVE-2026-33815", "severity": "critical"}],
        }
        row = _pr_row("9", pr)
        self.assertIn("CVE-2026-33815", row)
        self.assertIn("🔴", row)

    def test_pr_row_no_cves_shows_dash(self):
        pr = {"package": "lodash", "from": "1.0", "to": "2.0",
              "bump": "major", "dep_type": "production", "verification_label": "SAFE"}
        row = _pr_row("1", pr)
        self.assertIn("—", row)


class TestPrRowCveFallback(unittest.TestCase):
    """C8: _pr_row must fall back to deterministic.security.cveIds when cve_details is empty."""

    def test_cve_fallback_from_deterministic_security(self):
        pr = {
            "package": "golang.org/x/crypto", "from": "0.29.0", "to": "0.36.0",
            "bump": "minor", "dep_type": "production", "verification_label": "L2",
            "cve_details": [],
            "deterministic": {"security": {
                "isSecurity": True,
                "cveIds": ["CVE-2025-22869", "CVE-2025-22868"],
                "cvssScore": 10.0,
            }},
        }
        row = _pr_row("109", pr)
        self.assertIn("2 CVE(s)", row)
        self.assertIn("🔴", row)

    def test_no_cve_data_shows_dash(self):
        pr = {
            "package": "lodash", "from": "1.0", "to": "2.0",
            "bump": "major", "dep_type": "production", "verification_label": "SAFE",
            "cve_details": [],
            "deterministic": {"security": {}},
        }
        row = _pr_row("1", pr)
        self.assertIn("—", row)

    def test_low_cvss_uses_yellow_emoji(self):
        pr = {
            "package": "test-pkg", "from": "1.0", "to": "2.0",
            "bump": "major", "dep_type": "production", "verification_label": "L2",
            "cve_details": [],
            "deterministic": {"security": {
                "isSecurity": True, "cveIds": ["CVE-2025-1234"], "cvssScore": 5.0,
            }},
        }
        row = _pr_row("50", pr)
        self.assertIn("🟡", row)


if __name__ == "__main__":
    unittest.main()
