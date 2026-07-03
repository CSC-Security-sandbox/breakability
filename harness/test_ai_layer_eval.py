#!/usr/bin/env python3
"""Offline AI-layer regression eval.

Locks in two properties of the AI adjudication layer against frozen fixtures so they
can be verified in milliseconds, with NO model calls, NO network, NO agent CLI:

  1. The gate ingests the adjudicator's normalized verdict schema
     ({final_verdict,...}) as well as the legacy schema ({reachable,recommendation,...})
     and maps both through ONE falsifiability contract.
  2. Feeding the corpus-aligned AI verdicts to the gate keeps FALSE_GREEN=0 and
     GOLDEN_REGRESSIONS=0 -- i.e. the recorded AI layer never green-lights a PR the
     ground-truth corpus says is a review/fix.

Run: pytest test_ai_layer_eval.py
"""

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_gate

FIX = os.path.join(HERE, "fixtures")
ARTIFACT = os.path.join(FIX, "build_results_3pr.json")
AI_VERDICTS = os.path.join(FIX, "ai_verdicts_corpus.json")
CORPUS = os.path.join(HERE, "corpus.json")
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))


def _run_gate(ai=None):
    cmd = [sys.executable, os.path.join(HERE, "run_gate.py"), ARTIFACT, CORPUS,
           "--repo", REPO_ROOT]
    if ai:
        cmd += ["--ai", ai]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    metrics = {}
    for line in out.splitlines():
        if ":" in line and line.split(":", 1)[0].isupper():
            k, v = line.split(":", 1)
            metrics[k.strip()] = v.strip()
    return metrics, out


class NormalizeSchemaTests(unittest.TestCase):
    def test_normalized_safe_maps_to_downgradeable(self):
        n = run_gate._normalize_ai_verdict(
            {"final_verdict": "safe", "citation": "x", "proof": "ran grep"})
        self.assertEqual(n["recommendation"], "safe")
        self.assertIs(n["reachable"], False)

    def test_normalized_escalate_stays_review(self):
        n = run_gate._normalize_ai_verdict(
            {"final_verdict": "escalate", "citation": "x"})
        self.assertEqual(n["recommendation"], "review")
        self.assertIs(n["reachable"], True)

    def test_needs_change_stays_review(self):
        n = run_gate._normalize_ai_verdict({"final_verdict": "needs_change"})
        self.assertEqual(n["recommendation"], "review")

    def test_legacy_schema_passes_through(self):
        v = {"reachable": False, "recommendation": "safe", "citation": "x"}
        self.assertEqual(run_gate._normalize_ai_verdict(v), v)


class GateZeroFalseGreenTests(unittest.TestCase):
    def test_baseline_no_ai_zero_false_green(self):
        m, _ = _run_gate()
        self.assertEqual(m.get("FALSE_GREEN"), "0")
        self.assertEqual(m.get("GOLDEN_REGRESSIONS"), "0")

    def test_with_ai_verdicts_zero_false_green(self):
        self.assertTrue(os.path.exists(AI_VERDICTS))
        m, out = _run_gate(ai=AI_VERDICTS)
        self.assertEqual(m.get("FALSE_GREEN"), "0",
                         "AI layer must never produce a false-green\n" + out)
        self.assertEqual(m.get("GOLDEN_REGRESSIONS"), "0", out)
        self.assertEqual(m.get("AI_REJECTED"), "0",
                         "frozen AI verdicts must pass the falsifiability contract\n" + out)

    def test_ai_verdict_for_review_pr_is_not_a_downgrade(self):
        # PR#10 is true_review in corpus; the frozen verdict must NOT auto-clear it.
        ai = json.load(open(AI_VERDICTS))
        self.assertIn("10", ai)
        self.assertNotEqual(ai["10"].get("final_verdict"), "safe",
                            "PR#10 is true_review; clearing it would be a false-green")


if __name__ == "__main__":
    unittest.main()
