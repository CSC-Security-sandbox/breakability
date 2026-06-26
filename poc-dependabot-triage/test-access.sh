#!/bin/bash
# Quick validation: checks if gh CLI can access dependabot alerts for a repo
# Usage: ./test-access.sh owner/repo

set -e

REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)}"

if [ -z "$REPO" ]; then
  echo "❌ No repo specified and not in a git repo with gh access"
  echo "Usage: ./test-access.sh owner/repo"
  exit 1
fi

echo "🔍 Testing access to: $REPO"
echo ""

# Check gh auth
echo "1. Checking gh auth..."
if ! gh auth status &>/dev/null; then
  echo "   ❌ Not authenticated. Run: gh auth login"
  exit 1
fi
echo "   ✅ Authenticated"

# Check repo access
echo "2. Checking repo access..."
if ! gh repo view "$REPO" --json name &>/dev/null; then
  echo "   ❌ Cannot access repo: $REPO"
  exit 1
fi
echo "   ✅ Repo accessible"

# Check dependabot alerts
echo "3. Fetching Dependabot alerts..."
ALERT_COUNT=$(gh api "/repos/$REPO/dependabot/alerts" --jq '[.[] | select(.state=="open")] | length' 2>/dev/null || echo "ERROR")

if [ "$ALERT_COUNT" = "ERROR" ]; then
  echo "   ❌ Cannot access Dependabot alerts (might need admin/security permissions)"
  exit 1
fi

echo "   ✅ Found $ALERT_COUNT open alerts"
echo ""

# Show sample
if [ "$ALERT_COUNT" -gt 0 ]; then
  echo "📋 Top 5 alerts by severity:"
  echo "---"
  gh api "/repos/$REPO/dependabot/alerts" \
    --jq '[.[] | select(.state=="open")] | sort_by(.security_advisory.cvss.score) | reverse | .[0:5] | .[] | "\(.number) | \(.security_advisory.severity) (CVSS \(.security_advisory.cvss.score)) | \(.security_vulnerability.package.name) | \(.security_advisory.summary[0:60])"' 2>/dev/null
  echo "---"
  echo ""
  echo "✅ Ready for triage. Use the agent with: @dependabot-triage triage this repo"
else
  echo "🎉 No open Dependabot alerts — repo is clean!"
fi
