#!/usr/bin/env python3
"""Unit tests for dynamic_probe_runner.py."""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dynamic_probe_runner import ProbeClassification, ProbeSpec, run_probe  # noqa: E402
from evidence_contract import SignalStatus  # noqa: E402


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
SCRATCH = SCRIPT_DIR / ".probe-work-tests"


PRINT_SOURCE = '''package main

import (
    "fmt"
    "os"
)

func main() {
    fmt.Println(os.Getenv("DYNAMIC_PROBE_VERSION"))
}
'''


class DynamicProbeRunnerTests(unittest.TestCase):
    def spec(self, **overrides):
        data = {
            "ecosystem": "go",
            "package": "std",
            "from_version": "same",
            "to_version": "same",
            "source": PRINT_SOURCE,
            "command": "go run .",
            "timeout": 20,
        }
        data.update(overrides)
        return ProbeSpec(**data)

    def test_same_output_classifies_same_behavior(self):
        result = run_probe(self.spec(), scratch_root=SCRATCH)
        self.assertEqual(result.classification, ProbeClassification.SAME_BEHAVIOR, result.to_dict())
        evidence = result.to_evidence_record()
        self.assertEqual(evidence.status, SignalStatus.PASS)
        self.assertIs(evidence.same_behavior, True)
        self.assertIs(evidence.relevant, False)

    def test_changed_output_classifies_changed_behavior(self):
        result = run_probe(self.spec(from_version="old", to_version="new"), scratch_root=SCRATCH)
        self.assertEqual(result.classification, ProbeClassification.CHANGED_BEHAVIOR, result.to_dict())
        evidence = result.to_evidence_record()
        self.assertEqual(evidence.status, SignalStatus.FAIL)
        self.assertIs(evidence.same_behavior, False)
        self.assertIs(evidence.relevant, True)

    def test_timeout_classifies_probe_failed(self):
        source = '''package main

import "time"

func main() { time.Sleep(2 * time.Second) }
'''
        result = run_probe(self.spec(source=source, timeout=0.1), scratch_root=SCRATCH)
        self.assertEqual(result.classification, ProbeClassification.PROBE_FAILED, result.to_dict())
        self.assertEqual(result.to_evidence_record().status, SignalStatus.UNAVAILABLE)
        self.assertTrue(result.to_evidence_record().tool_failure)

    def test_command_failure_classifies_probe_failed(self):
        source = '''package main

import "os"

func main() { os.Exit(7) }
'''
        result = run_probe(self.spec(source=source), scratch_root=SCRATCH)
        self.assertEqual(result.classification, ProbeClassification.PROBE_FAILED, result.to_dict())

    def test_output_truncation(self):
        source = '''package main

import "fmt"

func main() { for i := 0; i < 200; i++ { fmt.Print("x") } }
'''
        result = run_probe(self.spec(source=source), scratch_root=SCRATCH, output_limit=50)
        self.assertEqual(result.classification, ProbeClassification.SAME_BEHAVIOR, result.to_dict())
        self.assertLessEqual(len(result.old.first.stdout), 80)
        self.assertIn("truncated", result.old.first.stdout)

    def test_repeated_output_difference_classifies_nondeterministic(self):
        source = '''package main

import (
    "fmt"
    "os"
)

func main() { fmt.Println(os.Getpid()) }
'''
        result = run_probe(self.spec(source=source), scratch_root=SCRATCH)
        self.assertEqual(result.classification, ProbeClassification.NONDETERMINISTIC, result.to_dict())

    def test_go_test_snippet(self):
        source = '''package probe

import "testing"

func TestProbe(t *testing.T) {}
'''
        result = run_probe(self.spec(source=source, command="go test ./..."), scratch_root=SCRATCH)
        self.assertEqual(result.classification, ProbeClassification.SAME_BEHAVIOR, result.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
