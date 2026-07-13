#!/usr/bin/env python3
"""Tests for the build-results.json schema contract."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from core.build_results_schema import (
    validate, validate_pr, validate_file,
    normalize_pr, normalize_top_level,
    ALIASES, TOP_LEVEL_ALIASES,
    SchemaValidationError,
)


class TestNormalizePR(unittest.TestCase):
    def test_import_files_alias(self):
        pr = {"import_files": ["a.go:5"], "package": "foo"}
        normalize_pr(pr)
        self.assertEqual(pr["files_importing"], ["a.go:5"])
        self.assertNotIn("import_files", pr)

    def test_importFiles_alias(self):
        pr = {"importFiles": ["b.go:10"], "package": "foo"}
        normalize_pr(pr)
        self.assertEqual(pr["files_importing"], ["b.go:10"])
        self.assertNotIn("importFiles", pr)

    def test_from_version_alias(self):
        pr = {"from_version": "1.0.0", "to_version": "2.0.0", "package": "foo"}
        normalize_pr(pr)
        self.assertEqual(pr["from"], "1.0.0")
        self.assertEqual(pr["to"], "2.0.0")
        self.assertNotIn("from_version", pr)
        self.assertNotIn("to_version", pr)

    def test_canonical_names_not_overwritten(self):
        pr = {"files_importing": ["canonical"], "import_files": ["legacy"], "package": "foo"}
        normalize_pr(pr)
        self.assertEqual(pr["files_importing"], ["canonical"])

    def test_build_stdout_alias(self):
        pr = {"build": {"stdout": "output text"}, "package": "foo"}
        normalize_pr(pr)
        self.assertEqual(pr["build"]["output_tail"], "output text")
        self.assertNotIn("stdout", pr["build"])

    def test_test_exit_code_alias(self):
        pr = {"test": {"exit_code": 1}, "package": "foo"}
        normalize_pr(pr)
        self.assertEqual(pr["test"]["exit"], 1)
        self.assertNotIn("exit_code", pr["test"])

    def test_merge_risk_from_deterministic(self):
        pr = {"deterministic": {"merge_risk": {"tag": "High"}}, "package": "foo"}
        normalize_pr(pr)
        self.assertEqual(pr["merge_risk"]["tag"], "High")

    def test_files_importing_from_deterministic(self):
        pr = {"deterministic": {"files_importing": ["c.go"]}, "package": "foo"}
        normalize_pr(pr)
        self.assertEqual(pr["files_importing"], ["c.go"])

    def test_changed_behavior_alias(self):
        pr = {"changed_behavior": "yes it changed", "package": "foo"}
        normalize_pr(pr)
        self.assertEqual(pr["behavior_changed"], "yes it changed")
        self.assertNotIn("changed_behavior", pr)


class TestNormalizeTopLevel(unittest.TestCase):
    def test_pr_results_alias(self):
        data = {"pr_results": {"1": {"package": "foo"}}}
        normalize_top_level(data)
        self.assertIn("prs", data)
        self.assertNotIn("pr_results", data)

    def test_results_array_to_prs_dict(self):
        data = {"results": [
            {"pr_num": 42, "package": "foo"},
            {"pr_num": 43, "package": "bar"},
        ]}
        normalize_top_level(data)
        self.assertIn("prs", data)
        self.assertIn("42", data["prs"])
        self.assertIn("43", data["prs"])
        self.assertEqual(data["prs"]["42"]["package"], "foo")

    def test_prs_not_overwritten_by_results(self):
        data = {"prs": {"1": {"package": "canonical"}}, "results": [{"pr_num": 1, "package": "legacy"}]}
        normalize_top_level(data)
        self.assertEqual(data["prs"]["1"]["package"], "canonical")


class TestValidatePR(unittest.TestCase):
    def _clean_pr(self, **overrides):
        pr = {
            "package": "example.com/lib",
            "ecosystem": "gomod",
            "from": "1.0.0",
            "to": "1.1.0",
            "bump": "minor",
            "dep_type": "production",
            "dep_relation": "direct",
        }
        pr.update(overrides)
        return pr

    def test_valid_pr(self):
        errors = validate_pr("1", self._clean_pr())
        self.assertEqual(errors, [])

    def test_missing_package(self):
        errors = validate_pr("1", self._clean_pr(package=""))
        self.assertTrue(any("missing 'package'" in e for e in errors))

    def test_invalid_ecosystem(self):
        errors = validate_pr("1", self._clean_pr(ecosystem="rust"))
        self.assertTrue(any("invalid ecosystem" in e for e in errors))

    def test_invalid_bump(self):
        errors = validate_pr("1", self._clean_pr(bump="huge"))
        self.assertTrue(any("invalid bump" in e for e in errors))

    def test_invalid_dep_type(self):
        errors = validate_pr("1", self._clean_pr(dep_type="optional"))
        self.assertTrue(any("invalid dep_type" in e for e in errors))

    def test_invalid_dep_relation(self):
        errors = validate_pr("1", self._clean_pr(dep_relation="peer"))
        self.assertTrue(any("invalid dep_relation" in e for e in errors))

    def test_invalid_build_verdict(self):
        errors = validate_pr("1", self._clean_pr(build={"verdict": "maybe"}))
        self.assertTrue(any("invalid build.verdict" in e for e in errors))

    def test_invalid_verdict_v2(self):
        errors = validate_pr("1", self._clean_pr(verdict_v2={"verdict": "MAYBE"}))
        self.assertTrue(any("invalid verdict_v2.verdict" in e for e in errors))

    def test_invalid_verification_label(self):
        errors = validate_pr("1", self._clean_pr(verification_label="L99_impossible"))
        self.assertTrue(any("invalid verification_label" in e for e in errors))

    def test_legacy_field_detected(self):
        pr = self._clean_pr()
        pr["import_files"] = ["a.go"]
        errors = validate_pr("1", pr)
        self.assertTrue(any("legacy field 'import_files'" in e for e in errors))

    def test_skip_entry_valid(self):
        pr = {"package": "skipped-pkg", "skip_reason": "breakability:skip label"}
        errors = validate_pr("1", pr, strict=True)
        self.assertEqual(errors, [])

    def test_strict_missing_from(self):
        pr = self._clean_pr()
        del pr["from"]
        errors = validate_pr("1", pr, strict=True)
        self.assertTrue(any("missing 'from'" in e for e in errors))

    def test_valid_ecosystems(self):
        for eco in ["npm", "gomod", "pip", "actions", "docker", "maven"]:
            errors = validate_pr("1", self._clean_pr(ecosystem=eco))
            self.assertEqual(errors, [], f"ecosystem '{eco}' should be valid")

    def test_valid_verdicts(self):
        for v in ["SAFE", "REVIEW", "BLOCKED", "GLANCE"]:
            errors = validate_pr("1", self._clean_pr(verdict_v2={"verdict": v}))
            self.assertEqual(errors, [], f"verdict '{v}' should be valid")


class TestValidate(unittest.TestCase):
    def test_empty_prs(self):
        errors = validate({"prs": {}})
        self.assertEqual(errors, [])

    def test_missing_prs_and_results(self):
        errors = validate({})
        self.assertTrue(any("Missing both" in e for e in errors))

    def test_prs_not_dict(self):
        errors = validate({"prs": "invalid"})
        self.assertTrue(any("should be dict" in e for e in errors))

    def test_cross_pr_deps_not_list(self):
        errors = validate({"prs": {}, "cross_pr_deps": "invalid"})
        self.assertTrue(any("should be list" in e for e in errors))

    def test_full_valid_document(self):
        data = {
            "metadata": {"repo": "owner/repo", "mode": "advisory"},
            "prs": {
                "1": {
                    "package": "example.com/lib",
                    "ecosystem": "gomod",
                    "from": "1.0.0",
                    "to": "1.1.0",
                    "bump": "minor",
                    "dep_type": "production",
                    "dep_relation": "direct",
                    "build": {"verdict": "pass"},
                    "verdict_v2": {"verdict": "SAFE"},
                    "verification_label": "L4_tests_pass",
                },
            },
            "cross_pr_deps": [],
            "security_posture": {},
            "govulncheck": {},
        }
        errors = validate(data)
        self.assertEqual(errors, [])


class TestValidateFile(unittest.TestCase):
    def test_validate_file_with_normalization(self):
        data = {
            "results": [
                {"pr_num": 1, "package": "foo", "import_files": ["a.go"], "from_version": "1.0", "to_version": "2.0"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            try:
                errors = validate_file(f.name, normalize=True)
                self.assertEqual(errors, [])
            finally:
                os.unlink(f.name)

    def test_validate_file_without_normalization_catches_legacy(self):
        data = {
            "prs": {"1": {"package": "foo", "import_files": ["a.go"]}},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            try:
                errors = validate_file(f.name, normalize=False)
                self.assertTrue(any("legacy field" in e for e in errors))
            finally:
                os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
