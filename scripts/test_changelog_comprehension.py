#!/usr/bin/env python3
"""Tests for M8 changelog comprehension. Deterministic Tier A is exercised directly;
Tier B (AI) is exercised through ai_backend replay cassettes (offline, sub-second)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import changelog_comprehension as m8
import ai_backend


def _syms(claims):
    return {c["symbol"] for c in claims}


def _by_symbol(claims, sym):
    for c in claims:
        if c["symbol"] == sym:
            return c
    return None


class DeterministicTierTests(unittest.TestCase):
    def test_removed_symbol(self):
        claims = m8.extract_deterministic(
            ["BREAKING: removed `Search` and `SearchWithContext`"], "")
        self.assertIn("Search", _syms(claims))
        c = _by_symbol(claims, "Search")
        self.assertEqual(c["kind"], m8.KIND_REMOVED)
        self.assertEqual(c["severity"], m8.SEV_HIGH)
        self.assertEqual(c["old"], "Search")

    def test_rename_extracts_old_and_new(self):
        claims = m8.extract_deterministic(
            ["Breaking change: renamed `Client.Do` to `Client.Execute`"], "")
        c = _by_symbol(claims, "Client.Do")
        self.assertIsNotNone(c)
        self.assertEqual(c["kind"], m8.KIND_RENAMED)
        self.assertEqual(c["old"], "Client.Do")
        self.assertEqual(c["new"], "Client.Execute")

    def test_is_now_called_rename(self):
        claims = m8.extract_deterministic(
            ["Breaking: `OldName` is now called `NewName`"], "")
        c = _by_symbol(claims, "OldName")
        self.assertIsNotNone(c)
        self.assertEqual(c["new"], "NewName")

    def test_signature_change(self):
        claims = m8.extract_deterministic(
            ["Breaking: changed the signature of `Connect`"], "")
        c = _by_symbol(claims, "Connect")
        self.assertIsNotNone(c)
        self.assertEqual(c["kind"], m8.KIND_SIGNATURE)

    def test_deprecated_is_low_when_not_breaking(self):
        claims = m8.extract_deterministic([], "Deprecated `LegacyFetch`; behavior changed.")
        c = _by_symbol(claims, "LegacyFetch")
        self.assertIsNotNone(c)
        self.assertEqual(c["kind"], m8.KIND_DEPRECATED)

    def test_non_breaking_text_yields_no_claims(self):
        claims = m8.extract_deterministic(
            ["Fixed a typo in docs", "Improved performance of internal cache"], "")
        self.assertEqual(claims, [])

    def test_breaking_without_symbol_keeps_one_behavioral_claim(self):
        # the break must never be silently dropped
        claims = m8.extract_deterministic(
            ["This release contains a breaking change to wire format"], "")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["kind"], m8.KIND_BEHAVIORAL)
        self.assertEqual(claims[0]["severity"], m8.SEV_HIGH)

    def test_stopwords_not_treated_as_symbols(self):
        claims = m8.extract_deterministic(["Breaking: removed support for the old API"], "")
        # "support"/"API"/"the" are stopwords; no fake symbol claim
        self.assertNotIn("support", _syms(claims))
        self.assertNotIn("API", _syms(claims))

    def test_lowercase_unexported_not_a_symbol(self):
        claims = m8.extract_deterministic(["Breaking: removed `doThing`"], "")
        # backtick path still allows it only if it looks exported; doThing is lower -> dropped,
        # but the breaking line still yields a behavioral fallback claim
        self.assertTrue(all(c["symbol"] != "doThing" for c in claims))


class ComprehendTests(unittest.TestCase):
    def test_comprehend_uses_pr_changelog_fields(self):
        pr = {"deterministic": {"changelogSignal": {
            "bullets": ["BREAKING: removed `Search`"]}}}
        res = m8.comprehend(pr, pr_id="10", use_ai=False)
        self.assertTrue(res["available"])
        self.assertEqual(res["max_severity"], m8.SEV_HIGH)
        self.assertIn("Search", _syms(res["breaking_claims"]))
        self.assertEqual(res["tier"], "det")

    def test_comprehend_empty_changelog(self):
        res = m8.comprehend({}, pr_id="x", use_ai=False)
        self.assertFalse(res["available"])
        self.assertEqual(res["breaking_claims"], [])
        self.assertEqual(res["max_severity"], "none")


class AiEnrichmentReplayTests(unittest.TestCase):
    """Tier B through the ai_backend replay cassette -> deterministic + offline."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["BRK_AGENT_MODE"] = "replay"
        os.environ["BRK_CASSETTE_DIR"] = self.dir

    def tearDown(self):
        os.environ.pop("BRK_AGENT_MODE", None)
        os.environ.pop("BRK_CASSETTE_DIR", None)

    def _write_cassette(self, key, response):
        import json
        path = ai_backend.cassette_path("changelog", "", key=key, cassette_dir=self.dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"response": response}, fh)

    def test_ai_adds_old_new_for_rename_regex_missed(self):
        prose = "The fetch entrypoint was reworked this cycle."  # no regex rename
        det = []  # deterministic found nothing structured
        self._write_cassette(
            "pr-99",
            '[{"symbol":"Fetch","kind":"renamed","old":"Fetch","new":"FetchV2",'
            '"severity":"high","source":"reworked"}]')
        claims = m8.enrich_with_ai(prose, "99", det, enable=True)
        c = _by_symbol(claims, "Fetch")
        self.assertIsNotNone(c)
        self.assertEqual(c["new"], "FetchV2")

    def test_ai_miss_falls_back_to_deterministic(self):
        det = [m8._claim("Search", m8.KIND_REMOVED, m8.SEV_HIGH, "removed Search", old="Search")]
        # no cassette for pr-77 -> replay miss -> "" -> keep deterministic
        claims = m8.enrich_with_ai("removed Search", "77", det, enable=True)
        self.assertEqual(_syms(claims), {"Search"})

    def test_ai_cannot_delete_deterministic_breaking_claim(self):
        det = [m8._claim("Search", m8.KIND_REMOVED, m8.SEV_HIGH, "removed Search", old="Search")]
        self._write_cassette("pr-55", "[]")  # AI returns nothing
        claims = m8.enrich_with_ai("removed Search", "55", det, enable=True)
        self.assertIn("Search", _syms(claims))  # deterministic break preserved

    def test_ai_disabled_returns_deterministic(self):
        det = [m8._claim("X", m8.KIND_REMOVED, m8.SEV_HIGH, "s")]
        self.assertEqual(m8.enrich_with_ai("p", "1", det, enable=False), det)


class FrozenArtifactRegressionTests(unittest.TestCase):
    """Locks the corpus #10 fix: M8 must extract the ACTUAL breaking symbols from the
    go-jira changelog prose, not just the '### Breaking Changes' heading."""

    FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "breakability", "harness", "fixtures",
                       "build_results_3pr.json")

    def test_pr10_extracts_search_symbols(self):
        if not os.path.exists(self.FIX):
            self.skipTest("frozen artifact fixture not present")
        import json
        prs = json.load(open(self.FIX)).get("prs", {})
        res = m8.comprehend(prs["10"], pr_id="10", use_ai=False)
        syms = {c["symbol"] for c in res["breaking_claims"]}
        self.assertIn("IssueService.Search", syms)
        self.assertIn("IssueService.SearchWithContext", syms)
        self.assertTrue(res["available"])


if __name__ == "__main__":
    unittest.main()
