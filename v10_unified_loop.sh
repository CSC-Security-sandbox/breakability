#!/bin/bash
# V10 Unified Loop - Fix AI pipeline gap (PR #67 has NO AI analysis)

set -euo pipefail

MAX_ITERS=5
ITER=0
TARGET_SCORE=85

# CRITICAL ISSUE: PR #67 shows deterministic worked (reached=False ✅) 
# BUT entire AI layer missing - no changelog, no behavioral, no breaking changes!
# Gold standard has rich AI analysis - we have NONE

echo "🔄 V10 UNIFIED LOOP - AI PIPELINE GAP FIX"
echo "Critical: PR #67 has deterministic but NO AI analysis layer"
echo ""

while [ $ITER -lt $MAX_ITERS ]; do
  ITER=$((ITER + 1))
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🔄 ITERATION $ITER - $(date '+%H:%M:%S')"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  # REVIEW PHASE - 6 reviewers focus on AI pipeline
  echo ""
  echo "👥 REVIEW PHASE (6 reviewers)"
  
  # Repo 1: NDM (PR #67 - AI gap)
  echo ""
  echo "📍 NDM Reviewer 1: Root Cause (AI Pipeline)"
  gh copilot -m claude-opus-4.8 << 'REVIEWER1'
You are an adversarial reviewer finding why AI layer didn't run.

CRITICAL BUG: PR #67 deterministic works (reached=False ✅) but ENTIRE AI LAYER MISSING:
- ❌ No changelog analysis
- ❌ No breaking changes section  
- ❌ No AI assessment
- ❌ No behavioral grade
- ❌ No migration notes

Compare:
- Gold: https://github.com/CSC-Security-sandbox/ndm-breakability-test/pull/208#issuecomment-4737308189
  Has: Breaking Changes, Changelog Analysis, Migration Notes, AI assessment
- Ours: https://github.com/CSC-Security-sandbox/ndm-fresh-breakability/pull/67#issuecomment-4788675570
  Has: Only deterministic (6 lines), no AI content!

Check workflow .github/workflows/breakability-agent.yml:
- Line 261: verdict_contract.py - did it run?
- Line 270: differential-probe.py - did it run?
- Line 280: policy_lowering.py - did it enrich?
- Line 342: breakability_analyst.py - did it get AI data?

Find:
1. Which step failed/skipped in validation run 28147777734?
2. Why is build-results.json missing AI fields?
3. Why does analyst.py not render changelog/behavioral sections?

Check ~/code/ndm-fresh-breakability for workflow logs, build-results.json schema.

SCORE: [0-100] based on how well you identify the root cause
REVIEWER1

  echo ""
  echo "📍 NDM Reviewer 2: Code Review (Analyst Renderer)"
  gh copilot -m claude-opus-4.8 << 'REVIEWER2'
You are a code reviewer checking why analyst.py doesn't render AI sections.

File: ~/code/ndm-fresh-breakability/.github/scripts/breakability_analyst.py

The analyst renders 11 sections but MISSING from PR #67:
- Breaking Changes (should show from changelog)
- Changelog Analysis (should show AI summary)
- Behavioral Grade (should show from differential-probe)
- Migration Notes (should show from AI)

Check:
1. Lines 200-400: Section rendering logic
2. What keys does analyst.py expect in pr dict?
3. Are sections conditional (if field missing, skip)?
4. Compare gold standard rendering vs ours

Find which sections are gated by missing data and what fields are missing.

Check ~/code/ndm-fresh-breakability/.github/scripts/breakability_analyst.py

SCORE: [0-100] based on findings
REVIEWER2

  echo ""
  echo "📍 NDM Reviewer 3: End User (Gold Standard Gap)"
  gh copilot -m claude-opus-4.8 << 'REVIEWER3'
You are an end user comparing our output to gold standard.

Gold standard (42 lines, rich AI content):
https://github.com/CSC-Security-sandbox/ndm-breakability-test/pull/208#issuecomment-4737308189

Our output (248 lines but MISSING AI):
https://github.com/CSC-Security-sandbox/ndm-fresh-breakability/pull/67#issuecomment-4788675570

We have MORE lines but WORSE quality - all fluff, no substance!
- We have 11 sections vs gold's 1 section
- But gold has Breaking Changes, we have nothing
- Gold has Changelog Analysis, we have nothing  
- Gold has Migration Notes, we have nothing

This is like having a 248-page book with no content vs 42-page book with insights.

What's the user impact? How does this fail the 80-85% dev time reduction goal?

SCORE: [0-100] for quality gap severity
REVIEWER3

  # Check logs for both repos
  cd ~/code/ndm-fresh-breakability
  RUN_ID=$(gh run list --workflow=breakability-agent.yml --limit 1 --json databaseId --jq '.[0].databaseId')
  
  echo ""
  echo "📍 Workflow Status Check"
  gh run view $RUN_ID --log 2>&1 | grep -E "verdict_contract|differential-probe|policy_lowering|ERROR|SKIP" | head -20
  
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🔧 FIX PHASE"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  gh copilot -m claude-opus-4.8 << 'FIXER'
You are a fix agent. Based on reviewer findings, fix the AI pipeline gap.

Context from reviewers:
- Reviewer 1 found: [which step failed/skipped]
- Reviewer 2 found: [analyst.py conditional rendering]
- Reviewer 3 found: [quality gap impact]

Your job:
1. Fix the workflow step that's skipping/failing
2. Ensure verdict_contract.py, differential-probe.py, policy_lowering.py ALL run
3. Ensure analyst.py gets AI data and renders all sections
4. Test locally if possible, otherwise commit and trigger CI

Files to fix (in ~/code/ndm-fresh-breakability):
- .github/workflows/breakability-agent.yml (workflow orchestration)
- .github/scripts/breakability_analyst.py (rendering)  
- .github/scripts/verdict_contract.py (AI enrichment)
- .github/scripts/policy_lowering.py (data merging)

Make surgical changes, commit with clear message, push.
FIXER

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🧪 CI VALIDATION"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  cd ~/code/ndm-fresh-breakability
  git push origin main || true
  
  echo "Waiting for CI to start..."
  sleep 30
  
  RUN_ID=$(gh run list --workflow=breakability-agent.yml --limit 1 --json databaseId --jq '.[0].databaseId')
  echo "Monitoring run $RUN_ID..."
  
  # Wait for completion (max 40 min)
  gh run watch $RUN_ID --interval 60 || true
  
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📊 SCORING & GATE CHECK"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  # Check PR #67 again
  echo "Checking PR #67 for AI content..."
  CHANGELOG=$(gh pr view 67 --json comments --jq '.comments[-1].body' | grep -c "Changelog Analysis" || echo 0)
  BREAKING=$(gh pr view 67 --json comments --jq '.comments[-1].body' | grep -c "Breaking Changes" || echo 0)
  BEHAVIORAL=$(gh pr view 67 --json comments --jq '.comments[-1].body' | grep -c "Behavioral" || echo 0)
  
  SCORE=$(( (CHANGELOG * 30) + (BREAKING * 40) + (BEHAVIORAL * 30) ))
  
  echo ""
  echo "🎯 Iteration $ITER Score: $SCORE/100"
  echo "   - Changelog Analysis: $CHANGELOG/1 (30 pts)"
  echo "   - Breaking Changes: $BREAKING/1 (40 pts)"
  echo "   - Behavioral Grade: $BEHAVIORAL/1 (30 pts)"
  echo ""
  
  if [ $SCORE -ge $TARGET_SCORE ]; then
    echo "✅ SUCCESS! Score $SCORE >= $TARGET_SCORE"
    echo "🏆 AI pipeline working, gold standard reached!"
    exit 0
  else
    echo "⚠️  Score $SCORE < $TARGET_SCORE, continuing..."
  fi
  
  sleep 10
done

echo ""
echo "❌ MAX_ITERS ($MAX_ITERS) reached without convergence"
echo "Last score: $SCORE/100 (target: $TARGET_SCORE)"
exit 1
