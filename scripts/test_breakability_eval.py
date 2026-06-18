#!/usr/bin/env python3
"""Test suite for breakability eval corpus validator and scorer (no pytest).

Run: python3 .github/scripts/test_breakability_eval.py
Exits non-zero on any failure.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from breakability_eval import (  # noqa: E402
    CorpusValidator, CorpusCase, Scorer, load_corpus, load_predictions,
)


def test_corpus_case_validation():
    """Test that invalid cases are rejected."""
    # Missing field
    case = CorpusCase({"pr_id": "1"})
    valid, msg = case.validate()
    assert not valid, "Should reject case with missing fields"
    
    # Valid case
    case = CorpusCase({
        "pr_id": "18",
        "ecosystem": "go",
        "package": "github.com/example/api",
        "from_version": "1.0.0",
        "to_version": "2.0.0",
        "expected_label": "true_fix",
        "expected_evidence_class": "api_break",
        "notes": "Breaking API change",
    })
    valid, msg = case.validate()
    assert valid, f"Should accept valid case: {msg}"
    
    # Invalid label
    case = CorpusCase({
        "pr_id": "18",
        "ecosystem": "go",
        "package": "github.com/example/api",
        "from_version": "1.0.0",
        "to_version": "2.0.0",
        "expected_label": "invalid_label",
        "expected_evidence_class": "api_break",
    })
    valid, msg = case.validate()
    assert not valid, "Should reject invalid expected_label"
    
    return True


def test_corpus_validator():
    """Test corpus validation."""
    # Missing cases key
    valid, errors = CorpusValidator.validate_corpus({})
    assert not valid, "Should reject corpus without 'cases' key"
    
    # Cases not a list
    valid, errors = CorpusValidator.validate_corpus({"cases": "not_a_list"})
    assert not valid, "Should reject corpus where cases is not a list"
    
    # Valid corpus
    corpus = {
        "cases": [
            {
                "pr_id": "18",
                "ecosystem": "go",
                "package": "github.com/example/api",
                "from_version": "1.0.0",
                "to_version": "2.0.0",
                "expected_label": "true_fix",
                "expected_evidence_class": "api_break",
                "notes": "Breaking API change",
            }
        ]
    }
    valid, errors = CorpusValidator.validate_corpus(corpus)
    assert valid, f"Should accept valid corpus: {errors}"
    
    return True


def test_scorer_metrics():
    """Test that scorer computes correct metrics."""
    cases = [
        CorpusCase({
            "pr_id": "18",
            "ecosystem": "go",
            "package": "github.com/example/api",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",
            "expected_evidence_class": "api_break",
        }),
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "npm",
            "package": "lodash",
            "from_version": "4.17.0",
            "to_version": "4.18.0",
            "expected_label": "true_review",
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "28",
            "ecosystem": "go",
            "package": "github.com/security/fix",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
    ]
    
    predictions = {
        "18": "fix",
        "2": "review",
        "28": "auto_clear",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    # Check metrics (use approximate equality for floating-point)
    expected = 100 / 3
    assert abs(result["metrics"]["fix_pct"] - expected) < 0.1, f"fix_pct should be ~33.3%, got {result['metrics']['fix_pct']}"
    assert abs(result["metrics"]["review_pct"] - expected) < 0.1, f"review_pct should be ~33.3%, got {result['metrics']['review_pct']}"
    assert abs(result["metrics"]["auto_clear_pct"] - expected) < 0.1, f"auto_clear_pct should be ~33.3%, got {result['metrics']['auto_clear_pct']}"
    assert result["metrics"]["abstain_pct"] == 0, "abstain_pct should be 0%"
    
    # Check no errors in this case
    assert result["errors"]["false_green_count"] == 0, "Should have no false greens"
    assert result["errors"]["false_block_count"] == 0, "Should have no false blocks"
    
    return True


def test_false_green_detection():
    """Test detection of false-green (predicted safe but ground truth is review/fix)."""
    cases = [
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "npm",
            "package": "lodash",
            "from_version": "4.17.0",
            "to_version": "4.18.0",
            "expected_label": "true_review",  # needs review
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "18",
            "ecosystem": "go",
            "package": "github.com/example/api",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",  # needs fixing
            "expected_evidence_class": "api_break",
        }),
    ]
    
    # Predict both as auto_clear (dangerous!)
    predictions = {
        "2": "auto_clear",
        "18": "auto_clear",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    # Both should be flagged as false-green
    assert result["errors"]["false_green_count"] == 2, "Should detect both false-greens"
    assert result["per_case"][0]["error"] == "false_green"
    assert result["per_case"][1]["error"] == "false_green"
    
    return True


def test_false_block_detection():
    """Test detection of false-block (predicted review/fix but ground truth is safe)."""
    cases = [
        CorpusCase({
            "pr_id": "28",
            "ecosystem": "go",
            "package": "github.com/security/fix",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",  # safe
            "expected_evidence_class": "none",
        }),
    ]
    
    # Predict as review (overly conservative)
    predictions = {
        "28": "review",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    # Should be flagged as false-block
    assert result["errors"]["false_block_count"] == 1, "Should detect false-block"
    assert result["per_case"][0]["error"] == "false_block"
    
    return True


def test_false_none_detection():
    """Test detection of false-none (predicted abstain but ground truth is review/fix)."""
    cases = [
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "npm",
            "package": "lodash",
            "from_version": "4.17.0",
            "to_version": "4.18.0",
            "expected_label": "true_review",  # needs review
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "18",
            "ecosystem": "go",
            "package": "github.com/example/api",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",  # needs fixing
            "expected_evidence_class": "api_break",
        }),
    ]
    
    # Predict both as abstain (dangerous - skips action)
    predictions = {
        "2": "abstain",
        "18": "abstain",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    # Both should be flagged as false-none
    assert result["errors"]["false_none_count"] == 2, "Should detect both false-nones"
    assert result["per_case"][0]["error"] == "false_none"
    assert result["per_case"][1]["error"] == "false_none"
    
    return True


def test_false_safe_rate_combined():
    """Test that false_safe_pct combines false_green and false_none rates."""
    cases = [
        CorpusCase({
            "pr_id": "1",
            "ecosystem": "go",
            "package": "pkg1",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",
            "expected_evidence_class": "api_break",
        }),
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "go",
            "package": "pkg2",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_review",
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "3",
            "ecosystem": "go",
            "package": "pkg3",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
        CorpusCase({
            "pr_id": "4",
            "ecosystem": "go",
            "package": "pkg4",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
    ]
    
    # One false-green, one false-none, rest correct
    predictions = {
        "1": "auto_clear",  # FALSE GREEN (should be fix)
        "2": "abstain",      # FALSE NONE (should be review)
        "3": "auto_clear",   # correct
        "4": "auto_clear",   # correct
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    assert result["errors"]["false_green_count"] == 1
    assert result["errors"]["false_none_count"] == 1
    assert result["errors"]["false_safe_count"] == 2, "false_safe should be sum of false_green + false_none"
    assert abs(result["errors"]["false_safe_pct"] - 50.0) < 0.1, f"false_safe_pct should be 50%, got {result['errors']['false_safe_pct']}"
    
    return True


def test_false_rates_percentages():
    """Test that all error rate percentages are computed correctly."""
    cases = [
        CorpusCase({
            "pr_id": "1",
            "ecosystem": "go",
            "package": "pkg1",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",
            "expected_evidence_class": "api_break",
        }),
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "go",
            "package": "pkg2",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_review",
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "3",
            "ecosystem": "go",
            "package": "pkg3",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
        CorpusCase({
            "pr_id": "4",
            "ecosystem": "go",
            "package": "pkg4",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
    ]
    
    # 1 false-green, 1 false-none, 1 false-block, 1 correct
    predictions = {
        "1": "auto_clear",  # FALSE GREEN
        "2": "abstain",      # FALSE NONE
        "3": "review",       # FALSE BLOCK
        "4": "auto_clear",   # correct
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    # Verify percentages sum to reasonable values
    assert result["errors"]["false_green_count"] == 1
    assert result["errors"]["false_none_count"] == 1
    assert result["errors"]["false_safe_count"] == 2
    assert result["errors"]["false_block_count"] == 1
    
    assert abs(result["errors"]["false_green_pct"] - 25.0) < 0.1
    assert abs(result["errors"]["false_none_pct"] - 25.0) < 0.1
    assert abs(result["errors"]["false_safe_pct"] - 50.0) < 0.1
    assert abs(result["errors"]["false_block_pct"] - 25.0) < 0.1
    
    return True





def test_mixed_predictions():
    """Test mixed prediction scenario."""
    cases = [
        CorpusCase({
            "pr_id": "18",
            "ecosystem": "go",
            "package": "github.com/example/api",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",
            "expected_evidence_class": "api_break",
        }),
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "npm",
            "package": "lodash",
            "from_version": "4.17.0",
            "to_version": "4.18.0",
            "expected_label": "true_review",
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "28",
            "ecosystem": "go",
            "package": "github.com/security/fix",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
        CorpusCase({
            "pr_id": "16",
            "ecosystem": "python",
            "package": "requests",
            "from_version": "2.27.0",
            "to_version": "2.28.0",
            "expected_label": "true_review",
            "expected_evidence_class": "test_coverage",
        }),
    ]
    
    # One false-green, one false-block, one correct
    predictions = {
        "18": "fix",           # correct
        "2": "auto_clear",     # FALSE GREEN (should be review)
        "28": "review",        # FALSE BLOCK (should be auto_clear)
        "16": "review",        # correct
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    assert result["errors"]["false_green_count"] == 1
    assert result["errors"]["false_block_count"] == 1
    assert result["metrics"]["auto_clear_pct"] == 25, f"auto_clear_pct should be 25%, got {result['metrics']['auto_clear_pct']}"
    assert result["metrics"]["review_pct"] == 50
    assert result["metrics"]["fix_pct"] == 25
    
    return True


def test_human_review_pct():
    """Test that human_review_pct correctly sums review_pct and fix_pct."""
    cases = [
        CorpusCase({
            "pr_id": "18",
            "ecosystem": "go",
            "package": "github.com/example/api",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",
            "expected_evidence_class": "api_break",
        }),
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "npm",
            "package": "lodash",
            "from_version": "4.17.0",
            "to_version": "4.18.0",
            "expected_label": "true_review",
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "28",
            "ecosystem": "go",
            "package": "github.com/security/fix",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
    ]
    
    predictions = {
        "18": "fix",
        "2": "review",
        "28": "auto_clear",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    # human_review_pct should be review_pct + fix_pct
    expected_human_review = result["metrics"]["review_pct"] + result["metrics"]["fix_pct"]
    assert abs(result["metrics"]["human_review_pct"] - expected_human_review) < 0.01, \
        f"human_review_pct should equal review_pct + fix_pct"
    assert abs(result["metrics"]["human_review_pct"] - 66.67) < 0.1, \
        f"human_review_pct should be ~66.7%, got {result['metrics']['human_review_pct']}"
    
    return True


def test_false_green_rate():
    """Test that false_green_rate is correctly calculated as percentage."""
    cases = [
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "npm",
            "package": "lodash",
            "from_version": "4.17.0",
            "to_version": "4.18.0",
            "expected_label": "true_review",
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "18",
            "ecosystem": "go",
            "package": "github.com/example/api",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",
            "expected_evidence_class": "api_break",
        }),
    ]
    
    # Predict both as auto_clear (both false-green)
    predictions = {
        "2": "auto_clear",
        "18": "auto_clear",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    assert result["errors"]["false_green_count"] == 2
    assert result["errors"]["false_green_rate"] == 100.0, \
        f"false_green_rate should be 100%, got {result['errors']['false_green_rate']}"
    
    return True


def test_false_block_rate():
    """Test that false_block_rate is correctly calculated as percentage."""
    cases = [
        CorpusCase({
            "pr_id": "28",
            "ecosystem": "go",
            "package": "github.com/security/fix",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
        CorpusCase({
            "pr_id": "35",
            "ecosystem": "python",
            "package": "requests",
            "from_version": "2.28.0",
            "to_version": "2.29.0",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
    ]
    
    # Predict both as review (both false-block)
    predictions = {
        "28": "review",
        "35": "fix",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    assert result["errors"]["false_block_count"] == 2
    assert result["errors"]["false_block_rate"] == 100.0, \
        f"false_block_rate should be 100%, got {result['errors']['false_block_rate']}"
    
    return True


def test_gates_pass_all():
    """Test gates pass when auto_clear >=85% and zero false-green."""
    cases = [
        CorpusCase({
            "pr_id": "28",
            "ecosystem": "go",
            "package": "github.com/security/fix",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
        CorpusCase({
            "pr_id": "35",
            "ecosystem": "python",
            "package": "requests",
            "from_version": "2.28.0",
            "to_version": "2.29.0",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
    ]
    
    # Perfect predictions: all auto_clear, both safe
    predictions = {
        "28": "auto_clear",
        "35": "auto_clear",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    assert result["gates"]["auto_clear_gte_85pct"] == True, "Should pass auto_clear gate (100%)"
    assert result["gates"]["zero_false_green"] == True, "Should pass zero_false_green gate"
    assert result["gates"]["pass"] == True, "Overall gates should pass"
    
    return True


def test_gates_fail_auto_clear():
    """Test gates fail when auto_clear <85%."""
    cases = [
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "npm",
            "package": "lodash",
            "from_version": "4.17.0",
            "to_version": "4.18.0",
            "expected_label": "true_review",
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "18",
            "ecosystem": "go",
            "package": "github.com/example/api",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",
            "expected_evidence_class": "api_break",
        }),
        CorpusCase({
            "pr_id": "28",
            "ecosystem": "go",
            "package": "github.com/security/fix",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
    ]
    
    # Only 1 auto_clear out of 3 = 33.3% < 85%
    predictions = {
        "2": "review",
        "18": "fix",
        "28": "auto_clear",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    assert result["metrics"]["auto_clear_pct"] < 85.0
    assert result["gates"]["auto_clear_gte_85pct"] == False, "Should fail auto_clear gate"
    assert result["gates"]["pass"] == False, "Overall gates should fail"
    
    return True


def test_gates_fail_false_green():
    """Test gates fail when false_green_count > 0."""
    cases = [
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "npm",
            "package": "lodash",
            "from_version": "4.17.0",
            "to_version": "4.18.0",
            "expected_label": "true_review",
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "28",
            "ecosystem": "go",
            "package": "github.com/security/fix",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
    ]
    
    # 50% auto_clear but 1 is false-green
    predictions = {
        "2": "auto_clear",  # FALSE GREEN
        "28": "auto_clear",
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    assert result["gates"]["zero_false_green"] == False, "Should fail zero_false_green gate"
    assert result["gates"]["pass"] == False, "Overall gates should fail"
    
    return True


def test_report_shape():
    """Test that report has correct structure with all required fields."""
    cases = [
        CorpusCase({
            "pr_id": "18",
            "ecosystem": "go",
            "package": "github.com/example/api",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",
            "expected_evidence_class": "api_break",
        }),
    ]
    
    predictions = {"18": "fix"}
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    # Check top-level keys
    assert "metrics" in result, "Report must have 'metrics' key"
    assert "errors" in result, "Report must have 'errors' key"
    assert "gates" in result, "Report must have 'gates' key"
    assert "per_case" in result, "Report must have 'per_case' key"
    
    # Check metrics keys
    assert "auto_clear_pct" in result["metrics"]
    assert "human_review_pct" in result["metrics"]
    assert "review_pct" in result["metrics"]
    assert "fix_pct" in result["metrics"]
    assert "abstain_pct" in result["metrics"]
    
    # Check errors keys
    assert "false_green_count" in result["errors"]
    assert "false_green_rate" in result["errors"]
    assert "false_block_count" in result["errors"]
    assert "false_block_rate" in result["errors"]
    
    # Check gates keys
    assert "auto_clear_gte_85pct" in result["gates"]
    assert "zero_false_green" in result["gates"]
    assert "pass" in result["gates"]
    
    # Check per_case structure
    assert len(result["per_case"]) == 1
    assert "pr_id" in result["per_case"][0]
    assert "expected" in result["per_case"][0]
    assert "predicted" in result["per_case"][0]
    assert "error" in result["per_case"][0]
    
    return True

    """Test mixed prediction scenario."""
    cases = [
        CorpusCase({
            "pr_id": "18",
            "ecosystem": "go",
            "package": "github.com/example/api",
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "expected_label": "true_fix",
            "expected_evidence_class": "api_break",
        }),
        CorpusCase({
            "pr_id": "2",
            "ecosystem": "npm",
            "package": "lodash",
            "from_version": "4.17.0",
            "to_version": "4.18.0",
            "expected_label": "true_review",
            "expected_evidence_class": "supply_chain",
        }),
        CorpusCase({
            "pr_id": "28",
            "ecosystem": "go",
            "package": "github.com/security/fix",
            "from_version": "1.0.0",
            "to_version": "1.0.1",
            "expected_label": "true_safe",
            "expected_evidence_class": "none",
        }),
        CorpusCase({
            "pr_id": "16",
            "ecosystem": "python",
            "package": "requests",
            "from_version": "2.27.0",
            "to_version": "2.28.0",
            "expected_label": "true_review",
            "expected_evidence_class": "test_coverage",
        }),
    ]
    
    # One false-green, one false-block, one correct
    predictions = {
        "18": "fix",           # correct
        "2": "auto_clear",     # FALSE GREEN (should be review)
        "28": "review",        # FALSE BLOCK (should be auto_clear)
        "16": "review",        # correct
    }
    
    scorer = Scorer(cases)
    result = scorer.score(predictions)
    
    assert result["errors"]["false_green_count"] == 1
    assert result["errors"]["false_block_count"] == 1
    assert result["metrics"]["auto_clear_pct"] == 25, f"auto_clear_pct should be 25%, got {result['metrics']['auto_clear_pct']}"
    assert result["metrics"]["review_pct"] == 50
    assert result["metrics"]["fix_pct"] == 25
    
    return True


def test_load_corpus_from_file():
    """Test loading corpus from JSON file."""
    corpus_data = {
        "cases": [
            {
                "pr_id": "18",
                "ecosystem": "go",
                "package": "github.com/example/api",
                "from_version": "1.0.0",
                "to_version": "2.0.0",
                "expected_label": "true_fix",
                "expected_evidence_class": "api_break",
                "notes": "Breaking API change",
            }
        ]
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(corpus_data, f)
        temp_file = f.name
    
    try:
        valid, cases, msg = load_corpus(temp_file)
        assert valid, f"Should load valid corpus: {msg}"
        assert len(cases) == 1
        assert cases[0].pr_id == "18"
    finally:
        os.unlink(temp_file)
    
    return True


def test_load_predictions_from_file():
    """Test loading predictions from JSON file."""
    predictions_data = {
        "18": "fix",
        "2": "review",
        "28": "auto_clear",
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(predictions_data, f)
        temp_file = f.name
    
    try:
        valid, predictions, msg = load_predictions(temp_file)
        assert valid, f"Should load valid predictions: {msg}"
        assert predictions["18"] == "fix"
    finally:
        os.unlink(temp_file)
    
    return True


def main():
    tests = [
        ("corpus_case_validation", test_corpus_case_validation),
        ("corpus_validator", test_corpus_validator),
        ("scorer_metrics", test_scorer_metrics),
        ("false_green_detection", test_false_green_detection),
        ("false_none_detection", test_false_none_detection),
        ("false_block_detection", test_false_block_detection),
        ("false_safe_rate_combined", test_false_safe_rate_combined),
        ("false_rates_percentages", test_false_rates_percentages),
        ("mixed_predictions", test_mixed_predictions),
        ("load_corpus_from_file", test_load_corpus_from_file),
        ("load_predictions_from_file", test_load_predictions_from_file),
        ("human_review_pct", test_human_review_pct),
        ("false_green_rate", test_false_green_rate),
        ("false_block_rate", test_false_block_rate),
        ("gates_pass_all", test_gates_pass_all),
        ("gates_fail_auto_clear", test_gates_fail_auto_clear),
        ("gates_fail_false_green", test_gates_fail_false_green),
        ("report_shape", test_report_shape),
    ]
    
    fails = 0
    for name, test_fn in tests:
        try:
            if test_fn():
                print(f"✓ {name}")
            else:
                print(f"✗ {name}")
                fails += 1
        except AssertionError as e:
            print(f"✗ {name}: {e}")
            fails += 1
        except Exception as e:
            print(f"✗ {name}: {type(e).__name__}: {e}")
            fails += 1
    
    total = len(tests)
    if fails:
        print(f"\n{fails}/{total} FAILED")
        return 1
    print(f"\nOK: all {total} eval tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
