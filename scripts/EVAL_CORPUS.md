# Breakability Evaluation Corpus

Foundation for frozen evaluation/scoring harness enabling policy validation against known ground truth instead of vibes-based tuning.

## Usage

### Validate Corpus

```bash
python3 .github/scripts/breakability_eval.py eval-corpus.seed.json
```

### Score Predictions

Run your policy/merger to generate predictions as JSON, then:

```bash
python3 .github/scripts/breakability_eval.py eval-corpus.seed.json --predict <predictions.json>
```

Output:
- **Metrics**: auto_clear%, human_review% (<=15% target), review%, fix%, abstain% distribution
- **Errors**: false_green_count/rate (predicted safe but ground truth is review/fix), false_block_count/rate (predicted review/fix but ground truth is safe)
- **Gates**: Target validation for 85% dev-work reduction (auto_clear>=85%, zero false-green)
- **Per-case**: Detailed breakdown with error flags

### Run Tests

```bash
python3 .github/scripts/test_breakability_eval.py
```

## Corpus Schema

```json
{
  "cases": [
    {
      "pr_id": "18",
      "ecosystem": "go",
      "package": "github.com/example/api",
      "from_version": "1.0.0",
      "to_version": "2.0.0",
      "expected_label": "true_fix",  // true_safe, true_review, true_fix, abstain
      "expected_evidence_class": "api_break",
      "notes": "Breaking API change"
    }
  ]
}
```

## Seed Corpus

Frozen snapshot from merge plan #122. Each case is conservative ground truth:

- **#18** (true_fix): Breaking API change, build fails, test suite catches it
- **#2** (true_review): Supply-chain signal, requires human verification
- **#8** (true_review): Behavioral changes, not fully covered by probes
- **#28** (true_safe): CVE patch, tests pass, no interface changes
- **#16** (true_review): Test suite exists but not verified clean

## Metrics Explained

- **auto_clear_pct**: Fraction of PRs approved automatically (target: >=85% for 85% dev-work reduction)
- **human_review_pct**: Fraction requiring human review/fix (review_pct + fix_pct, target: <=15%)
- **false_green_count/rate**: Cases predicted as auto_clear but ground truth is true_review/true_fix (highest risk, target: 0)
- **false_block_count/rate**: Cases predicted as review/fix but ground truth is true_safe (dev friction cost)

**Target gates for product readiness**:
- auto_clear >= 85% (enables ~85% dev-work reduction)
- false_green_count == 0 (zero known false-green)
