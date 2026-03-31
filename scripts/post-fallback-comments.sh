#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# post-fallback-comments.sh — Graceful degradation for breakability analysis
#
# If the AI agent crashes or misses PRs, this script posts structured build
# analysis comments for every PR that lacks one. Comments are generated
# directly from the deterministic JSON results — no AI required.
#
# Comment quality by scenario:
#   - Trivially safe (patch/transitive/actions/docker): brief SAFE one-liner
#   - Build pass with new errors or major bump: BUILD ANALYSIS with details
#   - Build fail: BUILD_FAILS with error excerpt and remediation note
#   - Build not run (skip/infra_error): REVIEW with infrastructure context
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RESULTS_FILE="/tmp/build-results.json"

if [[ ! -f "$RESULTS_FILE" ]]; then
  echo "No build-results.json found — nothing to do"
  exit 0
fi

OWNER_REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null || echo "unknown/unknown")
OWNER="${OWNER_REPO%%/*}"
REPO="${OWNER_REPO##*/}"

POSTED=0
SKIPPED=0
FAILED=0

# Read advisory mode from results metadata
BC_MODE=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
print(data.get('metadata', {}).get('mode', 'advisory'))
" 2>/dev/null || echo "advisory")

ADVISORY_FOOTER=""
if [[ "$BC_MODE" == "advisory" ]]; then
  ADVISORY_FOOTER="
> 🔬 **Advisory mode** — This analysis is informational. No merges are blocked."
fi

echo "Checking for PRs that need fallback comments (mode: $BC_MODE)..."

# Get all PR numbers from build-results.json
PR_NUMBERS=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
for num in sorted(data.get('prs', {}).keys(), key=int):
    print(num)
")

for PR_NUM in $PR_NUMBERS; do
  # Skip if PR already has a breakability comment
  HAS_COMMENT=$(gh api "repos/$OWNER/$REPO/issues/$PR_NUM/comments" \
    --jq '[.[] | select(.body | contains("<!-- breakability-check -->") or contains("<!-- breakability-agent -->"))] | length' \
    2>/dev/null || echo "0")

  if [[ "$HAS_COMMENT" -gt 0 ]]; then
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # Extract all fields for this PR in one python call
  PR_FIELDS=$(python3 -c "
import json, sys
with open('$RESULTS_FILE') as f:
    data = json.load(f)
pr = data['prs'].get('$PR_NUM', {})
build = pr.get('build', {})
test = pr.get('test', {})
print(json.dumps({
    'package':      pr.get('package', '?'),
    'from':         pr.get('from', '?'),
    'to':           pr.get('to', '?'),
    'bump':         pr.get('bump', '?'),
    'dep_type':     pr.get('dep_type', '?'),
    'dep_relation': pr.get('dep_relation', '?'),
    'ecosystem':    pr.get('ecosystem', '?'),
    'verdict':      build.get('verdict', '?'),
    'install_method': build.get('install_method', ''),
    'install_ok':   build.get('install_ok', False),
    'new_errors':   build.get('new_errors', []),
    'output_tail':  build.get('output_tail', ''),
    'test_ran':     test.get('ran', False),
    'test_exit':    test.get('exit', -1),
    'verification_label': pr.get('verification_label', ''),
    'files_importing': pr.get('files_importing', []),
}))
" 2>/dev/null || echo '{}')

  PKG=$(echo "$PR_FIELDS"     | python3 -c "import json,sys; print(json.load(sys.stdin).get('package','?'))")
  FROM=$(echo "$PR_FIELDS"    | python3 -c "import json,sys; print(json.load(sys.stdin).get('from','?'))")
  TO=$(echo "$PR_FIELDS"      | python3 -c "import json,sys; print(json.load(sys.stdin).get('to','?'))")
  BUMP=$(echo "$PR_FIELDS"    | python3 -c "import json,sys; print(json.load(sys.stdin).get('bump','?'))")
  DEP_TYPE=$(echo "$PR_FIELDS"  | python3 -c "import json,sys; print(json.load(sys.stdin).get('dep_type','?'))")
  DEP_REL=$(echo "$PR_FIELDS"   | python3 -c "import json,sys; print(json.load(sys.stdin).get('dep_relation','?'))")
  ECOSYSTEM=$(echo "$PR_FIELDS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ecosystem','?'))")
  VERDICT=$(echo "$PR_FIELDS"   | python3 -c "import json,sys; print(json.load(sys.stdin).get('verdict','?'))")
  INSTALL_METHOD=$(echo "$PR_FIELDS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('install_method',''))")
  INSTALL_OK=$(echo "$PR_FIELDS"     | python3 -c "import json,sys; print(json.load(sys.stdin).get('install_ok',False))")
  VER_LABEL=$(echo "$PR_FIELDS"      | python3 -c "import json,sys; print(json.load(sys.stdin).get('verification_label',''))")
  NEW_ERR_COUNT=$(echo "$PR_FIELDS"  | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('new_errors',[])))")
  FILES_COUNT=$(echo "$PR_FIELDS"    | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('files_importing',[])))")

  # Excerpt of build output (first 10 lines of errors for context)
  BUILD_EXCERPT=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
tail = d.get('output_tail', '')
lines = [l for l in tail.splitlines() if 'error' in l.lower() or 'Error' in l][:8]
if lines:
    print('\n'.join(lines))
else:
    print(tail[:300] if tail else '')
" 2>/dev/null || echo "")

  # Find merge plan issue number
  MERGE_PLAN_NUM=$(gh issue list --label "merge-plan" --state open --json number -q '.[0].number' 2>/dev/null || echo "")
  PLAN_LINE=""
  if [[ -n "$MERGE_PLAN_NUM" ]]; then
    PLAN_LINE="
📋 Merge plan: #$MERGE_PLAN_NUM"
  fi

  # ── Classify and generate comment ─────────────────────────────────────────
  COMMENT=""

  # PRs with breakability:skip label were intentionally excluded from analysis.
  # Don't post any comment — the developer already opted out of analysis.
  if [[ "$VERDICT" == "skipped" ]]; then
    echo "  PR #$PR_NUM has breakability:skip label — skipping fallback comment"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  if [[ "$ECOSYSTEM" == "actions" ]]; then
    # GitHub Actions — always safe, no app code affected
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · dev (CI) · $BUMP

GitHub Actions workflow dependency. No application code affected.

Verification: **NA** — CI-only change${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR*"

  elif [[ "$ECOSYSTEM" == "docker" && "$BUMP" != "major" ]]; then
    # Docker non-major — typically safe
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · production · $BUMP

Docker base image $BUMP bump. No application source changes.${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR*"

  elif [[ "$VERDICT" == "pass" && "$BUMP" == "patch" && "$FILES_COUNT" -lt 5 ]]; then
    # Patch bump, build passes, low usage surface — simple safe
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · patch

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)

$BUMP bump with passing build. No new type errors introduced.${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR*"

  elif [[ "$VERDICT" == "pass" && "$DEP_REL" == "transitive" ]]; then
    # Transitive dep, build passes
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · transitive · $BUMP

Build: ✅ passes · Verification: **${VER_LABEL:-L1}**

Transitive dependency — your code does not import it directly. Build passes.${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR*"

  elif [[ "$VERDICT" == "pass" ]]; then
    # Build passes — general case
    NEW_ERR_NOTE=""
    if [[ "$NEW_ERR_COUNT" -gt 0 ]]; then
      NEW_ERR_NOTE=" · ⚠️ $NEW_ERR_COUNT new error(s) found"
    fi
    COMMENT="<!-- breakability-check -->
## 🔍 BUILD ANALYSIS — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)$NEW_ERR_NOTE

### Summary (deterministic fallback — no AI analysis)
- Package: \`$PKG\` $FROM → $TO ($BUMP bump)
- Type: $DEP_TYPE / $DEP_REL
- Build passes on PR branch
- New type errors: $NEW_ERR_COUNT

**Recommendation:** Review changelog for $BUMP bump breaking changes. Build passes — merge when ready.${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR. Full AI analysis was not performed.*"

  elif [[ "$VERDICT" == "fail" ]]; then
    # Build fails — most important fallback to get right
    EXCERPT_BLOCK=""
    if [[ -n "$BUILD_EXCERPT" ]]; then
      EXCERPT_BLOCK="
\`\`\`
${BUILD_EXCERPT}
\`\`\`"
    fi
    COMMENT="<!-- breakability-check -->
## ❌ BUILD_FAILS — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP

Build: ❌ fails on PR branch, ✅ passes on main · Usage: $FILES_COUNT file(s)

### Build errors (excerpt)$EXCERPT_BLOCK

### What to do
1. Check the full build output in the Actions run for this PR
2. Review the \`$PKG\` $FROM → $TO changelog for breaking changes
3. Fix type errors or update your code to match the new API
4. Re-run the breakability analysis after your fix

**Do not merge — build is broken.** ($BUMP bump)${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR.*"

  elif [[ "$VERDICT" == "pre_existing" ]]; then
    # Pre-existing failures
    COMMENT="<!-- breakability-check -->
## ⚙️ UNVERIFIED — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP

Build: ⚙️ same errors on main and PR branch — pre-existing failure, not caused by this upgrade

### What this means
The build fails on both \`main\` and this PR with the same errors. This upgrade does **not** introduce new failures. However, build verification could not confirm compatibility because the baseline is broken.

**Recommendation:** Fix pre-existing build failures on \`main\` first, then re-analyze. This upgrade is likely safe but unconfirmed.${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR.*"

  elif [[ "$VERDICT" == "pre_existing_plus_new" ]]; then
    # Pre-existing + new errors
    COMMENT="<!-- breakability-check -->
## ❌ BUILD_FAILS — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP

Build: ❌ new errors introduced by this PR (on top of pre-existing failures)

This upgrade introduces **$NEW_ERR_COUNT new error(s)** not present on \`main\`. Fix required before merging.${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR.*"

  elif [[ "$INSTALL_METHOD" == "infra_error" ]]; then
    # Infrastructure blocked analysis
    COMMENT="<!-- breakability-check -->
## 🔍 REVIEW — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP

Build: ⚠️ blocked by infrastructure error — build verification could not run

### What happened
The build check was blocked by an infrastructure issue (private registry, network timeout, or missing dependency not caused by this upgrade). **This is not a build failure from the upgrade.**

**Recommendation:** Verify infrastructure health, then re-run. If infrastructure is healthy, review manually.${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR.*"

  else
    # Catch-all: skip/unknown verdict
    COMMENT="<!-- breakability-check -->
## 🔍 REVIEW — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP

Build analysis status: \`$VERDICT\` (verification: ${VER_LABEL:-unknown})

Automated build analysis was not conclusive for this PR. Manual review recommended.${PLAN_LINE}${ADVISORY_FOOTER}
> ⚠️ *Fallback comment — AI agent did not run or did not cover this PR.*"
  fi

  if [[ -n "$COMMENT" ]]; then
    if gh pr comment "$PR_NUM" --body "$COMMENT" 2>/dev/null; then
      echo "  Posted fallback for PR #$PR_NUM ($PKG $FROM→$TO, $VERDICT)"
      POSTED=$((POSTED + 1))
    else
      echo "  ⚠️  Failed to post fallback for PR #$PR_NUM"
      FAILED=$((FAILED + 1))
    fi
  fi
done

echo ""
echo "Fallback: posted $POSTED, skipped $SKIPPED (already had comments), failed $FAILED"
