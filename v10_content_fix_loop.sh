#!/bin/bash
set -euo pipefail

echo "🔍 INVESTIGATING: Why AI content is missing (sections present but empty)"
echo ""

cd ~/code/ndm-fresh-breakability

# 1. Check which PRs got behavioral probe
echo "1. PRs that got behavioral probe in run 28147777734:"
gh run view 28147777734 --log 2>&1 | grep "differential-probe.*PR" | head -15

echo ""
echo "2. Checking build-results.json for PR #67 data..."
# Download artifacts if available
gh run view 28147777734 --json artifacts --jq '.artifacts[] | select(.name == "build-results") | .url'

echo ""
echo "3. Checking verdict_contract.py - does it run for all PRs?"
grep -A 10 "def main\|for pr in" .github/scripts/verdict_contract.py | head -20

echo ""
echo "4. Checking differential-probe.py - does it skip NOT REACHED PRs?"
grep -A 10 "NOT REACHED\|if.*reachable\|skip" .github/scripts/differential-probe.py | head -30

