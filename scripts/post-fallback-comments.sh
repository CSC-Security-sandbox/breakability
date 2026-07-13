#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# post-fallback-comments.sh — Deterministic build analysis comments + merge plan
#
# PRIMARY comment system: posts structured build analysis comments for every PR
# and generates a merge plan issue. Comments are generated directly from the
# deterministic JSON results — no AI required. If the AI agent already posted
# richer comments (<!-- breakability-agent -->), those PRs are skipped.
# CR5-1/M3: This is the primary system, not a fallback. The AI agent is optional.
#
# Comment quality by scenario:
#   - Trivially safe (patch/transitive/actions/docker): brief SAFE one-liner
#   - Build pass with new errors or major bump: BUILD ANALYSIS with details
#   - Build fail: BUILD_FAILS with error excerpt and remediation note
#   - Build not run (skip/infra_error): REVIEW with infrastructure context
#   - Security fix: SECURITY FIX with severity and MERGE NOW recommendation
#   - Pre-existing (L1, zero new errors): LIKELY SAFE
# ──────────────────────────────────────────────────────────────────────────────
set -u
set -o pipefail
export LC_ALL=en_US.UTF-8
unset GH_TOKEN

# ── Local dry-run mode ────────────────────────────────────────────────────────
# When DRY_RUN=1, never post or delete anything on GitHub. Instead render each
# PR's comment body to $DRY_RUN_DIR/pr-<N>.md so we can iterate on the comment
# content locally in seconds (see .github/scripts/run-local.sh). Destructive gh
# calls are routed through these guards.
DRY_RUN="${DRY_RUN:-0}"
DRY_RUN_DIR="${DRY_RUN_DIR:-/tmp/breakability-local/comments}"
BRK_SCRIPTS="${BREAKABILITY_SCRIPTS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
if [[ "$DRY_RUN" == "1" ]]; then
  mkdir -p "$DRY_RUN_DIR"
fi
source "$BRK_SCRIPTS/lib/comment_helpers.sh"
source "$BRK_SCRIPTS/lib/comment_blocks.sh"
source "$BRK_SCRIPTS/lib/comment_templates.sh"

RESULTS_FILE="/tmp/build-results.json"
CLI_PATH="${CLI_PATH:-.github/actions/breakability-check/index.js}"

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

# EU-18: Actions run link for verifiability
RUN_LINK=""
if [[ -n "${GITHUB_RUN_ID:-}" ]]; then
  RUN_LINK="
🔗 [View analysis run](${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID})"
fi

echo "Posting deterministic analysis comments (mode: $BC_MODE)..."

# V8 FIX (C2): Detect discovered-but-not-analyzed PRs (cancelled batch timeout).
# Compare the discover list (all open Dependabot PRs) against the build-results.json.
# Post a "Skipped — batch was cancelled" comment for missing PRs.
echo "Checking for cancelled/missing PRs..."
DISCOVERED_PRS=$(gh pr list --label "dependencies" --state open \
  --json number --jq '.[].number' --limit 500 2>/dev/null | sort -n || echo "")
ANALYZED_PRS=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
for num in sorted(data.get('prs', {}).keys(), key=int):
    print(num)
" 2>/dev/null || echo "")
REQUESTED_SUBSET_PRS=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
meta = data.get('metadata', {})
if meta.get('subset_requested'):
    for num in meta.get('requested_pr_numbers', []):
        print(num)
" 2>/dev/null || echo "")

if [[ -n "$REQUESTED_SUBSET_PRS" ]]; then
  _FILTERED_DISCOVERED=""
  for _disc_pr in $DISCOVERED_PRS; do
    for _req_pr in $REQUESTED_SUBSET_PRS; do
      if [[ "$_disc_pr" == "$_req_pr" ]]; then
        _FILTERED_DISCOVERED="$_FILTERED_DISCOVERED $_disc_pr"
        break
      fi
    done
  done
  DISCOVERED_PRS="$_FILTERED_DISCOVERED"
  echo "Subset run: missing-PR check limited to requested PRs: $(echo "$REQUESTED_SUBSET_PRS" | tr '\n' ',' | sed 's/,$//')"
fi

# Find PRs in discovered list but NOT in analyzed results
CANCELLED_PRS=""
for _disc_pr in $DISCOVERED_PRS; do
  _found=false
  for _anal_pr in $ANALYZED_PRS; do
    if [[ "$_disc_pr" == "$_anal_pr" ]]; then
      _found=true
      break
    fi
  done
  if [[ "$_found" == "false" ]]; then
    CANCELLED_PRS="$CANCELLED_PRS $_disc_pr"
  fi
done

# Post "Skipped" comments for cancelled PRs and add them to results JSON
for _CANCEL_PR in $CANCELLED_PRS; do
  [[ -z "$_CANCEL_PR" ]] && continue
  # Check if we already have a recent comment — don't spam
  _HAS_RECENT=$(gh api "repos/$OWNER/$REPO/issues/$_CANCEL_PR/comments" \
    --jq '[.[] | select(.body | contains("<!-- breakability-check -->")) | select(.body | contains("batch was cancelled"))] | length' \
    2>/dev/null || echo "0")
  if [[ "$_HAS_RECENT" -gt 0 ]]; then
    continue
  fi
  # Delete old deterministic comments before posting new one
  _OLD_IDS=$(gh api "repos/$OWNER/$REPO/issues/$_CANCEL_PR/comments" \
    --jq '.[] | select(.body | contains("<!-- breakability-check -->")) | select(.body | contains("<!-- breakability-agent -->") | not) | .id' \
    2>/dev/null || true)
  for _CID in $_OLD_IDS; do
    gh_delete_comment "$OWNER" "$REPO" "$_CID"
  done
  _CANCEL_TITLE=$(gh pr view "$_CANCEL_PR" --json title --jq '.title' 2>/dev/null || echo "Unknown")
  _CANCEL_COMMENT="<!-- breakability-check -->
## ⚠️ SKIPPED — Analysis incomplete (batch was cancelled)

This PR was discovered but the analysis batch was cancelled or timed out before it could be processed.

**What to do:** Re-run the analysis: \`gh workflow run breakability-agent.yml\`

${RUN_LINK}
> 🔬 *Deterministic analysis — batch incomplete*"
  gh_pr_comment "$_CANCEL_PR" "$_CANCEL_COMMENT" && \
    echo "  Posted 'cancelled' comment for PR #$_CANCEL_PR" || true

  # Add to results JSON so merge plan picks it up
  python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
data['prs']['$_CANCEL_PR'] = {
    'package': '$(echo "$_CANCEL_TITLE" | sed "s/'/\\\\'/g")',
    'from': '?', 'to': '?', 'ecosystem': 'unknown', 'bump': 'unknown',
    'dep_type': 'unknown', 'dep_relation': 'unknown', 'cves': [],
    'build': {'verdict': 'cancelled', 'main_exit': -1, 'pr_exit': -1,
              'output_tail': '', 'new_errors': [], 'install_method': 'none', 'error_class': ''},
    'test': {'ran': False, 'exit': None, 'output_tail': ''},
    'files_importing': [], 'pkg_dir': '/', 'install_ok': False,
    'verification_level': -1, 'verification_label': 'NA_cancelled',
    'verification_steps': [], 'skip_reason': 'batch cancelled/timed out'
}
with open('$RESULTS_FILE', 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null || true
done
if [[ -n "$CANCELLED_PRS" ]]; then
  echo "  Cancelled PRs:$CANCELLED_PRS"
else
  echo "  No cancelled PRs detected"
fi

if [[ -f "$BRK_SCRIPTS/policy_lowering.py" ]]; then
  if python3 "$BRK_SCRIPTS/policy_lowering.py" "$RESULTS_FILE" --enrich -o /tmp/build-results.policy.json 2>/tmp/policy-lowering.err; then
    mv /tmp/build-results.policy.json "$RESULTS_FILE"
  else
    echo "[warn] policy lowering unavailable; using legacy verdict map"
    cat /tmp/policy-lowering.err 2>/dev/null || true
  fi
fi

node "$CLI_PATH" verdict-map "$RESULTS_FILE" >/dev/null 2>&1 || echo "[warn] verdict-map unavailable; rendering will fail-closed to REVIEW"

# ── Re-assert AI adjudication as the LAST word ────────────────────────────────
# policy_lowering.py --enrich and `verdict-map` (above) rebuild verdict_v2 / the
# policy decision from raw deterministic evidence, clobbering any AI downgrade the
# reconcile step applied. The AI arbiter is authoritative for the break-reachable
# residue it resolved, so re-apply its decision here, after the clobbering steps
# and before the overlay. Without this, a verified false-positive the AI cleared
# (e.g. dep not imported in the bumped module) snaps back to REVIEW.
python3 "$BRK_SCRIPTS/core/ai_reassert.py" "$RESULTS_FILE" || echo "[warn] ai re-assertion skipped"

python3 "$BRK_SCRIPTS/core/policy_overlay.py" "$RESULTS_FILE" || echo "[warn] policy lowering overlay unavailable; using legacy verdict_v2"

# Get all PR numbers from build-results.json
PR_NUMBERS=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
for num in sorted(data.get('prs', {}).keys(), key=int):
    print(num)
")

# ── Pre-create the merge-plan issue so per-PR comments link the LIVE plan ─────
# The plan BODY is generated after the per-PR loop, but the comments posted inside
# that loop must link the plan THIS run produces — not the previous run's still-open
# issue. So close stale plans and create a fresh placeholder NOW, capture its number,
# and EDIT it with the real body at the end. (Fixes stale "Merge plan: #NNN" links.)
_MP_LABEL="breakability-merge-plan"
if [[ "$DRY_RUN" == "1" ]]; then
  MERGE_PLAN_NUM="LOCAL"
  export MERGE_PLAN_NUM
  echo "  [dry-run] skipping merge-plan issue close/create (number=LOCAL)"
else
# Ensure the merge-plan label exists — `gh issue create --label` hard-fails if the
# label is absent (a fresh repo has none), which previously emptied MERGE_PLAN_NUM and
# cascaded into "Failed to create merge plan issue". Create it idempotently.
gh label create "$_MP_LABEL" --color "0e8a16" --description "Breakability merge plan" >/dev/null 2>&1 || true
_MP_OLD=$(gh issue list --label "$_MP_LABEL" --state open --json number -q '.[].number' 2>/dev/null || echo "")
_MP_OLD_LEGACY=$(gh issue list --label "dependencies" --state open --json number,title \
  -q '.[] | select(.title | test("📋.*[Mm]erge [Pp]lan|[Dd]ependabot [Mm]erge [Pp]lan|[Bb]reakability [Mm]erge [Pp]lan")) | .number' 2>/dev/null || echo "")
_MP_OLD_UNLABELED=$(gh issue list --state open --json number,title,labels \
  -q '.[] | select((.labels | length) == 0) | select(.title | test("[Dd]ependabot [Mm]erge [Pp]lan|📋.*[Mm]erge [Pp]lan")) | .number' 2>/dev/null || echo "")
for _OLD_NUM in $_MP_OLD $_MP_OLD_LEGACY $_MP_OLD_UNLABELED; do
  [[ -z "$_OLD_NUM" ]] && continue
  gh issue close "$_OLD_NUM" --comment "Superseded by new merge plan run at $(date -u '+%Y-%m-%d %H:%M UTC')." 2>/dev/null && \
    echo "  Closed old merge plan issue #$_OLD_NUM" || true
done
_MP_PLACEHOLDER_URL=$(gh issue create \
  --title "📋 Breakability Merge Plan $(date -u '+%Y-%m-%d %H:%M UTC') (generating…)" \
  --body "⏳ Merge plan is being generated from the latest build results — this issue updates momentarily." \
  --label "$_MP_LABEL" 2>/dev/null || echo "")
MERGE_PLAN_NUM=$(echo "$_MP_PLACEHOLDER_URL" | grep -oE '[0-9]+$' || echo "")
export MERGE_PLAN_NUM
if [[ -n "$MERGE_PLAN_NUM" ]]; then
  echo "  Reserved merge plan issue #$MERGE_PLAN_NUM"
else
  echo "  ⚠️  Could not pre-create merge plan issue — comments will omit the plan link"
fi
fi

for PR_NUM in $PR_NUMBERS; do
  # Per-PR atomic comment management (A3-9):
  # 1. Check for existing AI agent comments (preserve those)
  # 2. Delete old deterministic comments (<!-- breakability-check --> without <!-- breakability-agent -->)
  # 3. Post new deterministic comment
  # This avoids the race where merge-results.sh deletes comments before this script posts.
  # V9.8 iter6 (D): only preserve AGENT comments from THIS run (or within last 2 hours).
  # Stale agent comments from previous runs were surviving forever, causing dual-comment contradictions.
  _WF_STARTED_AT="${GITHUB_RUN_STARTED_AT:-}"
  if [[ -z "$_WF_STARTED_AT" ]]; then
    # Fallback: anything created in the last 2 hours is "current run"
    _CUTOFF=$(date -u -d "2 hours ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-2H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "1970-01-01T00:00:00Z")
  else
    _CUTOFF="$_WF_STARTED_AT"
  fi
  HAS_AGENT_COMMENT=$(gh api "repos/$OWNER/$REPO/issues/$PR_NUM/comments" \
    --jq "[.[] | select(.body | contains(\"<!-- breakability-agent -->\")) | select(.created_at >= \"$_CUTOFF\")] | length" \
    2>/dev/null || echo "0")

  if [[ "$HAS_AGENT_COMMENT" -gt 0 ]]; then
    # AI agent already posted a richer comment IN THIS RUN — skip deterministic fallback.
    # Still delete stale pre-cutoff agent comments so only the current one remains.
    STALE_AGENT_IDS=$(gh api "repos/$OWNER/$REPO/issues/$PR_NUM/comments" \
      --jq ".[] | select(.body | contains(\"<!-- breakability-agent -->\")) | select(.created_at < \"$_CUTOFF\") | .id" \
      2>/dev/null || true)
    for CID in $STALE_AGENT_IDS; do
      gh_delete_comment "$OWNER" "$REPO" "$CID"
    done
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # No current-run agent comment → delete ALL previous breakability comments (both markers)
  # so the new deterministic comment is the single source of truth.
  OLD_COMMENT_IDS=$(gh api "repos/$OWNER/$REPO/issues/$PR_NUM/comments" \
    --jq '.[] | select(.body | contains("<!-- breakability-check -->") or (.body | contains("<!-- breakability-agent -->"))) | .id' \
    2>/dev/null || true)
  for CID in $OLD_COMMENT_IDS; do
    gh_delete_comment "$OWNER" "$REPO" "$CID"
  done

  # Extract all fields for this PR in one python call
  PR_FIELDS=$(python3 "$BRK_SCRIPTS/rendering/pr_fields.py" extract --results-file "$RESULTS_FILE" --pr-num "$PR_NUM" 2>/dev/null || echo '{}')

  # Extract all fields in a single Python call instead of 13 separate spawns (CR2-7).
  # This reduces per-PR Python process spawns from 15 to 3 (fields + CVE extraction).
  _FIELDS_EXTRACTED=$(echo "$PR_FIELDS" | python3 "$BRK_SCRIPTS/rendering/pr_fields.py" format_vars 2>/dev/null || echo "")
  # Parse the output into shell variables
  PKG=$(echo "$_FIELDS_EXTRACTED" | grep '^PKG=' | cut -d= -f2-)
  FROM=$(echo "$_FIELDS_EXTRACTED" | grep '^FROM=' | cut -d= -f2-)
  TO=$(echo "$_FIELDS_EXTRACTED" | grep '^TO=' | cut -d= -f2-)
  BUMP=$(echo "$_FIELDS_EXTRACTED" | grep '^BUMP=' | cut -d= -f2-)
  # 0.x semver: only flag 0.x major bumps, not real v1→v2 upgrades
  FROM_MAJOR="${FROM%%.*}"
  FROM_MAJOR="${FROM_MAJOR#v}"
  if [[ "$BUMP" == "major" && "$FROM_MAJOR" == "0" ]]; then
    BUMP_DISPLAY="major ⚠️ (0.x unstable — treat as breaking)"
  else
    BUMP_DISPLAY="$BUMP"
  fi
  DEP_TYPE=$(echo "$_FIELDS_EXTRACTED" | grep '^DEP_TYPE=' | cut -d= -f2-)
  DEP_REL=$(echo "$_FIELDS_EXTRACTED" | grep '^DEP_REL=' | cut -d= -f2-)
  ECOSYSTEM=$(echo "$_FIELDS_EXTRACTED" | grep '^ECOSYSTEM=' | cut -d= -f2-)
  # ── CI review tier ───────────────────────────────────────────────────────────
  # "CI-only" is NOT automatically "safe". Classify CI (actions/docker) deps into:
  #   secsens — handles tokens/creds/registry/cloud auth, code signing, OR deploy/publish.
  #             A breaking/compromised release here is a supply-chain risk -> security review.
  #   ""      — benign CI dep -> auto-safe changelog glance. Majorness alone is NOT a review
  #             trigger (a major setup-* bump is still a glance per the breakability oracle).
  # MUST stay in sync with ci_classifier.py (the policy-layer source of truth).
  _CI_TIER=""
  if printf '%s' "$PKG" | grep -qiE 'token|credential|secret|password|login|oauth|oidc|/auth|-auth|ssh-agent|import-gpg|gpg|cosign|sigstore|vault|kms|aws-actions|azure/login|google-github-actions/auth|configure-aws-credentials|registry|ghcr|ecr|gcr|deploy|release|publish|pages'; then
    _CI_TIER="secsens"
  fi
  VERDICT=$(echo "$_FIELDS_EXTRACTED" | grep '^VERDICT=' | cut -d= -f2-)
  INSTALL_METHOD=$(echo "$_FIELDS_EXTRACTED" | grep '^INSTALL_METHOD=' | cut -d= -f2-)
  INSTALL_OK=$(echo "$_FIELDS_EXTRACTED" | grep '^INSTALL_OK=' | cut -d= -f2-)
  VER_LABEL=$(echo "$_FIELDS_EXTRACTED" | grep '^VER_LABEL=' | cut -d= -f2-)
  NEW_ERR_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^NEW_ERR_COUNT=' | cut -d= -f2-)
  FILES_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^FILES_COUNT=' | cut -d= -f2-)
  PKG_DIR=$(echo "$_FIELDS_EXTRACTED" | grep '^PKG_DIR=' | cut -d= -f2-)
  ERROR_CLASS=$(echo "$_FIELDS_EXTRACTED" | grep '^ERROR_CLASS=' | cut -d= -f2-)
  OOM_OVERRIDE=$(echo "$_FIELDS_EXTRACTED" | grep '^OOM_OVERRIDE=' | cut -d= -f2-)
  OOM_PACKAGES=$(echo "$_FIELDS_EXTRACTED" | grep '^OOM_PACKAGES=' | cut -d= -f2-)
  GOSUM_NEW_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^GOSUM_NEW_COUNT=' | cut -d= -f2-)
  GOSUM_NEW_NAMES=$(echo "$_FIELDS_EXTRACTED" | grep '^GOSUM_NEW_NAMES=' | cut -d= -f2-)
  GOSUM_TOTAL_PR=$(echo "$_FIELDS_EXTRACTED" | grep '^GOSUM_TOTAL_PR=' | cut -d= -f2-)
  GOSUM_TOTAL_MAIN=$(echo "$_FIELDS_EXTRACTED" | grep '^GOSUM_TOTAL_MAIN=' | cut -d= -f2-)
  VULN_STATUS=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_STATUS=' | cut -d= -f2-)
  VULN_FINDING=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_FINDING=' | cut -d= -f2-)
  VULN_NEW_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_NEW_COUNT=' | cut -d= -f2-)
  VULN_NEW_LIST=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_NEW_LIST=' | cut -d= -f2-)
  VULN_PREEXISTING_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_PREEXISTING_COUNT=' | cut -d= -f2-)
  TEST_FAIL_DETAIL=$(echo "$_FIELDS_EXTRACTED" | grep '^TEST_FAIL_DETAIL=' | cut -d= -f2-)
  BUILD_EXIT_CODE=$(echo "$_FIELDS_EXTRACTED" | grep '^BUILD_EXIT=' | cut -d= -f2-)
  PR_BUILD_EXIT=$(echo "$_FIELDS_EXTRACTED" | grep '^PR_BUILD_EXIT=' | cut -d= -f2-)
  MAIN_BUILD_EXIT=$(echo "$_FIELDS_EXTRACTED" | grep '^MAIN_BUILD_EXIT=' | cut -d= -f2-)
  # P1 (reviewer): a timeout/OOM-killed build (exit 124/137) cannot be trusted for an
  # "errors are identical" comparison — compilation was killed before all packages
  # were checked. Surface this caveat wherever we'd otherwise say "LIKELY SAFE".
  _TIMEOUT_CAVEAT=""
  if [[ "$PR_BUILD_EXIT" == "124" || "$PR_BUILD_EXIT" == "137" || "$MAIN_BUILD_EXIT" == "124" || "$MAIN_BUILD_EXIT" == "137" ]]; then
    _TIMEOUT_CAVEAT=" ⚠️ **Build was killed (timeout/OOM, exit ${PR_BUILD_EXIT}/${MAIN_BUILD_EXIT}) — the error comparison is INCOMPLETE.** Packages after the kill point were never compiled, so new type errors there would not be detected. Treat as inconclusive, not verified-safe."
  fi
  TEST_EXIT_CODE=$(echo "$_FIELDS_EXTRACTED" | grep '^TEST_EXIT_CODE=' | cut -d= -f2-)
  TEST_RAN=$(echo "$_FIELDS_EXTRACTED" | grep '^TEST_RAN=' | cut -d= -f2-)
  MAIN_TEST_EXIT=$(echo "$_FIELDS_EXTRACTED" | grep '^MAIN_TEST_EXIT=' | cut -d= -f2-)
  # ── Single-source, HONEST test-result framing (one derivation feeds BOTH the signals
  # table and the "how we checked" block, so the same fact can never read alarming in one
  # place and exculpatory in another — PR#16). We only call a failure "pre-existing" when
  # we can PROVE main also fails (upstream classified it via TEST_FAIL_DETAIL, or
  # MAIN_TEST_EXIT>0). When main never tested (its build broke -> main_test_exit=-1) we say
  # "could not confirm" — we do NOT fabricate "same failure on main". Safe direction:
  # underclaim pre-existing.
  _TESTS_CLEAN=0
  _TEST_FAILED=0
  if [[ "${TEST_RAN:-False}" == "True" && "${TEST_EXIT_CODE:-}" == "0" ]]; then
    _TESTS_CLEAN=1
  elif [[ "${TEST_RAN:-False}" == "True" && -n "${TEST_EXIT_CODE:-}" && "${TEST_EXIT_CODE}" != "-1" ]]; then
    _TEST_FAILED=1
  fi
  _TEST_PREEXIST_VERIFIED=0
  if [[ -n "${TEST_FAIL_DETAIL:-}" ]]; then
    _TEST_PREEXIST_VERIFIED=1
  elif [[ "${MAIN_TEST_EXIT:-}" =~ ^[0-9]+$ && "${MAIN_TEST_EXIT}" -gt 0 ]]; then
    _TEST_PREEXIST_VERIFIED=1
  fi
  # Shared phrases (pipe-safe for the markdown table; backticks escaped so bash never runs them).
  _TEST_SIGNAL_CELL=""
  _TEST_HOWCHECKED=""
  if [[ "$_TEST_FAILED" == "1" ]]; then
    if [[ "$_TEST_PREEXIST_VERIFIED" == "1" ]]; then
      _TEST_SIGNAL_CELL="⚠️ tests fail (classified pre-existing — \`main\` tests also fail, not introduced by this PR)"
      _TEST_HOWCHECKED="⚙️ Automated tests fail — classified pre-existing: \`main\` tests also fail, so this PR did not introduce them"
    else
      _TEST_SIGNAL_CELL="⚠️ tests fail (exit ${TEST_EXIT_CODE}) — could not confirm against \`main\` (its build/tests did not run clean); NOT verified pre-existing"
      _TEST_HOWCHECKED="⚙️ Automated tests fail (exit ${TEST_EXIT_CODE}) — could NOT confirm against \`main\` (its build/tests did not run clean); not verified as pre-existing — treat as unresolved"
    fi
  fi
  BUILD_EVIDENCE=$(echo "$_FIELDS_EXTRACTED" | grep '^BUILD_EVIDENCE=' | cut -d= -f2-)
  BUILD_DIRS=$(echo "$_FIELDS_EXTRACTED" | grep '^BUILD_DIRS=' | cut -d= -f2-)
  MERGE_RISK_TAG=$(echo "$_FIELDS_EXTRACTED" | grep '^MERGE_RISK_TAG=' | cut -d= -f2-)
  MERGE_RISK_REASON=$(echo "$_FIELDS_EXTRACTED" | grep '^MERGE_RISK_REASON=' | cut -d= -f2-)
  MERGE_RISK_EVIDENCE=$(echo "$_FIELDS_EXTRACTED" | grep '^MERGE_RISK_EVIDENCE=' | cut -d= -f2-)
  MERGE_RISK_BUILD_VERIFICATION=$(echo "$_FIELDS_EXTRACTED" | grep '^MERGE_RISK_BUILD_VERIFICATION=' | cut -d= -f2-)
  # The deterministic merge-risk reason is built BEFORE the behavioral oracle runs, so it
  # ends in a "verify against the release notes" punt. Once the oracle has committed a CITED
  # grade, that punt is stale — the oracle already read the notes and graded the exposure.
  # Replace the punt tail with a pointer to the graded verdict + the oracle's runtime check,
  # and pick a non-punt tail for the High-branch REVIEW_WHY. Fail-open keeps the honest punt.
  eval "$(get_behavioral_grade "$PR_NUM")"
  _BG_SRC_LC=$(printf '%s' "${BG_SOURCE:-}" | tr '[:upper:]' '[:lower:]')
  _BG_CITED=0
  if [[ "${BG_OK:-0}" == "1" && ( "$_BG_SRC_LC" == "reasoning" || "$_BG_SRC_LC" == "probe" ) \
        && ( -n "${BG_RATIONALE:-}" || -n "${BG_GUIDANCE:-}" || -n "${BG_EVIDENCE:-}" ) ]]; then
    _BG_CITED=1
  fi
  _BG_CONF_LC=$(printf '%s' "${BG_CONFIDENCE:-}" | tr '[:upper:]' '[:lower:]')
  if [[ "$_BG_CITED" == "1" ]]; then
    case "$_BG_CONF_LC" in
      low|medium|high) MERGE_RISK_ORACLE_CONFIDENCE="$_BG_CONF_LC" ;;
      *) MERGE_RISK_ORACLE_CONFIDENCE="cited" ;;
    esac
  else
    MERGE_RISK_ORACLE_CONFIDENCE="not available"
  fi
  _REVIEW_WHY_TAIL=" Verify the affected behavior against the release notes before merging."
  if [[ "$_BG_CITED" == "1" ]]; then
    _REVIEW_WHY_TAIL=" The behavioral verdict below grades your actual exposure."
    if [[ "$MERGE_RISK_REASON" == *"verify against the release notes"* ]]; then
      _BG_LABEL=$(printf '%s' "${BG_GRADE:-medium}" | awk '{print toupper(substr($0,1,1)) substr($0,2)}')
      _RS="$MERGE_RISK_REASON"
      _RS="${_RS/ — verify against the release notes/}"
      _RS="${_RS/; verify against the release notes/}"
      _RS="${_RS/, but verify against the release notes/}"
      _RS="${_RS/ verify against the release notes/}"
      _BG_TAIL=" — the behavioral oracle graded your actual exposure **${_BG_LABEL}** (see the verdict above)"
      [[ -n "${BG_GUIDANCE:-}" ]] && _BG_TAIL+=": ${BG_GUIDANCE}"
      MERGE_RISK_REASON="${_RS}${_BG_TAIL}"
    fi
  fi
  VULN_EVIDENCE=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_EVIDENCE=' | cut -d= -f2-)
  TEST_SUMMARY=$(echo "$_FIELDS_EXTRACTED" | grep '^TEST_SUMMARY=' | cut -d= -f2-)
  FILES_LIST=$(echo "$_FIELDS_EXTRACTED" | grep '^FILES_LIST=' | cut -d= -f2-)

  build_cve_blocks

  build_evidence_blocks
  build_checklist_blocks

  HOW_CHECKED=""
  case "$VER_LABEL" in
    L4*)
      HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ✅ Build passes${_EV_BUILD} — exit 0, $NEW_ERR_COUNT new error(s)
- ✅ Tests pass (exit=$TEST_EXIT_CODE)${_EV_TEST} — no regressions vs main
- ✅ Diffed error output: PR introduces 0 new diagnostics${_TRANSITIVE_NOTE}${_VULN_NOTE}
</details>${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}${_FILES_DETAIL_BLOCK}${_GO_RESOLUTION_BLOCK}${_BUILD_STDOUT_BLOCK}${_TEST_STDOUT_BLOCK}${_NO_TEST_CONFIDENCE_BLOCK}${CHANGELOG_LINK}"
      ;;
    L3*)
      HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ✅ Build passes${_EV_BUILD} — exit 0, $NEW_ERR_COUNT new error(s)
- ⬜ Tests not configured or not run (no behavioral-probe mitigation assumed)
- ✅ Diffed error output: PR introduces 0 new diagnostics${_TRANSITIVE_NOTE}${_VULN_NOTE}
</details>${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}${_FILES_DETAIL_BLOCK}${_GO_RESOLUTION_BLOCK}${_BUILD_STDOUT_BLOCK}${_NO_TEST_CONFIDENCE_BLOCK}${CHANGELOG_LINK}"
      ;;
    L2*)
      # V9.3 FIX (P1-2): BUILD_FAILS PRs must NOT use the "builds clean" checklist.
      # Split on verdict: fail gets a failure-specific checklist, pass/pre_existing gets the original.
      if [[ "$VERDICT" == "fail" || "$VERDICT" == "pre_existing_plus_new" ]]; then
        # Build failed — show failure-specific checklist
        if [[ "$OOM_OVERRIDE" == "True" ]]; then
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ⚙️ Build hit OOM (\`signal: killed\`) on unrelated sub-packages — not caused by this upgrade
- ✅ PR's targeted packages are not affected
- ✅ No new type errors introduced vs. main
</details>"
        else
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ❌ Project build fails on PR branch
- ✅ Build passes on main — errors are introduced by this upgrade
- ⬜ Tests not run (build must pass first)
</details>"
        fi
      else
        # Build passed — original L2 checklist
        TEST_EXIT_RAW=$(echo "$PR_FIELDS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('test_exit',-1))" 2>/dev/null || echo "-1")
        TEST_RAN_RAW=$(echo "$PR_FIELDS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('test_ran',False))" 2>/dev/null || echo "False")
        if [[ "$TEST_RAN_RAW" == "True" && "$TEST_EXIT_RAW" != "0" && "$TEST_EXIT_RAW" != "-1" ]]; then
          _TEST_DETAIL_NOTE=""
          if [[ -n "$TEST_FAIL_DETAIL" ]]; then
            _TEST_DETAIL_NOTE=" ($TEST_FAIL_DETAIL)"
          fi
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ✅ Build passes${_EV_BUILD} — exit 0, $NEW_ERR_COUNT new error(s)
- ${_TEST_HOWCHECKED:-⚙️ Automated tests fail${_TEST_DETAIL_NOTE} — see test output}
- ✅ Diffed error output: PR introduces 0 new diagnostics${_TRANSITIVE_NOTE}${_VULN_NOTE}
</details>${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}${_FILES_DETAIL_BLOCK}${_GO_RESOLUTION_BLOCK}${_BUILD_STDOUT_BLOCK}${_NO_TEST_CONFIDENCE_BLOCK}${CHANGELOG_LINK}"
        else
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ✅ Build passes${_EV_BUILD} — exit 0, $NEW_ERR_COUNT new error(s)
- ⬜ Tests not configured or not run (no behavioral-probe mitigation assumed)
- ✅ Diffed error output: PR introduces 0 new diagnostics${_TRANSITIVE_NOTE}${_VULN_NOTE}
</details>${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}${_FILES_DETAIL_BLOCK}${_GO_RESOLUTION_BLOCK}${_BUILD_STDOUT_BLOCK}${_NO_TEST_CONFIDENCE_BLOCK}${CHANGELOG_LINK}"
        fi
      fi
      ;;
    L1*)
      # V8 FIX (C3): L1 comments must include WHAT failed and WHERE, not just
      # "Build verification limited". Extract module and error excerpt from build output.
      _L1_MAIN_EXIT=$(echo "$PR_FIELDS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('main_exit',-1))" 2>/dev/null || echo "-1")
      _L1_MAIN_CLASS=""
      case "$_L1_MAIN_EXIT" in
        124) _L1_MAIN_CLASS=" (timeout)" ;;
        137) _L1_MAIN_CLASS=" (OOM/killed)" ;;
      esac
      # V9.3: Enhanced excerpt + module attribution for OOM errors.
      # Identifies which sub-packages had errors and whether they're related to the PR's package.
      _L1_EXCERPT_AND_ATTR=$(echo "$PR_FIELDS" | _BC_PKG="$PKG" python3 "$BRK_SCRIPTS/rendering/pr_fields.py" l1_excerpt 2>/dev/null || echo "")
      _L1_ATTR=$(echo "$_L1_EXCERPT_AND_ATTR" | head -1)
      _L1_EXCERPT=$(echo "$_L1_EXCERPT_AND_ATTR" | sed '1,/^---EXCERPT---$/d')
      _L1_EXCERPT_BLOCK=""
      if [[ -n "$_L1_EXCERPT" ]]; then
        _L1_EXCERPT_BLOCK="
\`\`\`
${_L1_EXCERPT}
\`\`\`"
      fi
      _L1_MODULE_NOTE=""
      if [[ -n "$PKG_DIR" && "$PKG_DIR" != "/" ]]; then
        _L1_MODULE_NOTE="
- PR targets module: \`$PKG_DIR\`"
      fi
      # V9.3: Add OOM attribution note if errors are in unrelated sub-packages
      _L1_OOM_NOTE=""
      if echo "$_L1_ATTR" | grep -q "OOM_PKGS="; then
        _OOM_PKG_NAMES=$(echo "$_L1_ATTR" | sed 's/OOM_PKGS=//;s/|.*//')
        _OOM_PKG_SHORT=$(echo "$_OOM_PKG_NAMES" | tr ',' '\n' | while read -r p; do echo "$p" | rev | cut -d/ -f1 | rev; done | tr '\n' ',' | sed 's/,$//')
        if echo "$_L1_ATTR" | grep -q "UNRELATED"; then
          _L1_OOM_NOTE="
- ⚙️ Pre-existing failure is in \`${_OOM_PKG_SHORT}\` — **unrelated** to this PR's package (\`$PKG\`)"
        fi
      fi
      HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ⚠️ Build fails on both \`main\` (exit=${_L1_MAIN_EXIT}${_L1_MAIN_CLASS}) and PR branch — same errors${_L1_MODULE_NOTE}${_L1_OOM_NOTE}
- ✅ No NEW errors introduced by this upgrade

**Pre-existing build errors:**${_L1_EXCERPT_BLOCK}

Fix these on \`main\` to unlock full L2+ verification.
</details>"
      ;;
    *)
      if [[ -n "$VER_LABEL" ]]; then
        # End-user feedback: "Limited verification performed" is misleading when the
        # tool DID compare builds and found zero new errors. Show what we actually did.
        if [[ "$VERDICT" == "pre_existing" && "$NEW_ERR_COUNT" -eq 0 ]]; then
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

- ⚙️ Built both \`main\` and PR branch
- ✅ Compared build errors — **zero new errors** from this upgrade
- ⚠️ Baseline build has pre-existing failures (not caused by upgrade)
</details>"
        else
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

- ⬜ Build verification limited by infrastructure issues
</details>"
        fi
      fi
      ;;
  esac

  if [[ "$ECOSYSTEM" == "gomod" && -n "$HOW_CHECKED" ]]; then
    case "$HOW_CHECKED" in
      *"Go dependency-resolution output"*|*"go.mod / go.sum diff"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${_GO_RESOLUTION_BLOCK}" ;;
    esac
    case "$HOW_CHECKED" in
      *"Confidence without tests"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${_NO_TEST_CONFIDENCE_BLOCK}" ;;
    esac
    case "$HOW_CHECKED" in
      *"API diff signal"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${_API_DIFF_TOOL_BLOCK}" ;;
    esac
    case "$HOW_CHECKED" in
      *"Changelog signals"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${CHANGELOG_LINK}" ;;
    esac
    case "$HOW_CHECKED" in
      *"BREAK-reachability context"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}" ;;
    esac
  fi

  # Avoid double-rendering the changelog: the gomod HOW_CHECKED enrichment (above) may already
  # embed the "### Changelog signals" block inside the "How we checked" details. Templates that
  # ALSO insert ${CHANGELOG_LINK} inline must use ${CHANGELOG_INLINE} so the inline copy is
  # suppressed when HOW_CHECKED already carries it (non-gomod keeps the inline copy).
  CHANGELOG_INLINE="$CHANGELOG_LINK"
  case "$HOW_CHECKED" in
    *"Changelog signals"*) CHANGELOG_INLINE="" ;;
  esac

  # Prepend govulncheck header badge (if status is failure/vulns_found) so it sits
  # right above the HOW_CHECKED collapsible — visible without expanding details.
  if [[ -n "$_VULN_HEADER_BADGE" && -n "$HOW_CHECKED" ]]; then
    HOW_CHECKED="
${_VULN_HEADER_BADGE}${HOW_CHECKED}"
  fi

  # Excerpt of build output (first 10 lines of errors for context)
  BUILD_EXCERPT=$(echo "$PR_FIELDS" | python3 "$BRK_SCRIPTS/rendering/pr_fields.py" build_excerpt 2>/dev/null || echo "")

  # Use the plan issue reserved at the start of this run, so the link always points
  # at THIS run's plan (never a stale previous-run number).
  PLAN_LINE=""
  if [[ -n "${MERGE_PLAN_NUM:-}" ]]; then
    PLAN_LINE="
📋 Merge plan: #$MERGE_PLAN_NUM"
  fi

  # ── Classify and generate comment ─────────────────────────────────────────
  dispatch_comment_template


  # ── CVE version-gating for the SECURITY FIX body ─────────────────────────────
  # The PR-body `cves` field (CVE_LIST/CVE_COUNT) is an UNVERIFIED claim: it does NOT
  # prove the resulting (incl. transitive) version actually reaches the advisory's
  # fixed-in version. Dependabot-matched fixes (fixes_cves -> _FIXES_CVE_DATA) ARE
  # version-gated (build-check.sh bumped_modules + first_patched_version gate in
  # merge-results.sh). Credit only the version-verified set as "resolved"; render the
  # rest as "claimed (not version-verified)" so the per-PR body can never over-credit a
  # CVE the bump does not actually deliver — keeping it consistent with the merge-plan
  # orphan table (e.g. PR#23 CVE-2026-39883 fixed-in 1.43 while the PR reaches only 1.42;
  # PR#10 CVE-2025-30204 with no Dependabot match).
  # Reconcile the SECURITY-FIX recommendation with the committed behavioral grade so the body
  # cannot say "MERGE IMMEDIATELY" while the headline/merge-plan say "REVIEW THEN MERGE" (PR#23).
  # BG_* are available here (eval'd at get_behavioral_grade above); V2_VERDICT is not yet set.
  _BEHAV_BREAK=0
  if [[ "${BG_OK:-0}" == "1" ]]; then
    _bg_src_lc_cve="$(printf '%s' "${BG_SOURCE:-}" | tr '[:upper:]' '[:lower:]')"
    _bg_grade_lc_cve="$(printf '%s' "${BG_GRADE:-}" | tr '[:upper:]' '[:lower:]')"
    if [[ ( "$_bg_src_lc_cve" == "reasoning" || "$_bg_src_lc_cve" == "probe" ) \
          && ( -n "${BG_RATIONALE:-}" || -n "${BG_GUIDANCE:-}" || -n "${BG_EVIDENCE:-}" ) \
          && ( "$_bg_grade_lc_cve" == "high" || "$_bg_grade_lc_cve" == "medium" ) ]]; then
      _BEHAV_BREAK=1
    fi
  fi
  if [[ -n "${_FIXES_CVE_DATA:-}" ]]; then
    _CVE_VERIFIED=1
    _CVE_HEADING="$CVE_COUNT CVE(s) resolved (version-verified): $_FIXES_CVE_DATA"
    if [[ "$_BEHAV_BREAK" == "1" ]]; then
      _CVE_RECOMMEND="**REVIEW THEN MERGE.** It resolves ${CVE_COUNT} version-verified known CVE(s) (the resulting version reaches each advisory's fixed-in version) with zero new build errors — but the behavioral oracle graded a **${_bg_grade_lc_cve}** breaking-change exposure (see the graded call sites below). Confirm those call sites, then merge to clear the CVE."
    elif [[ "$_TESTS_CLEAN" == "1" ]]; then
      _CVE_RECOMMEND="**MERGE NOW.** It resolves ${CVE_COUNT} version-verified known CVE(s) (the resulting version reaches each advisory's fixed-in version), introduces zero new build errors, and the test suite passes."
    elif [[ "$_TEST_FAILED" == "1" && "$_TEST_PREEXIST_VERIFIED" == "1" ]]; then
      _CVE_RECOMMEND="**REVIEW THEN MERGE.** It resolves ${CVE_COUNT} version-verified known CVE(s) with zero new build errors, but the test suite is **also failing on \`main\`** (pre-existing). Confirm the failures are unrelated to this upgrade, then merge to clear the CVE."
    elif [[ "$_TEST_FAILED" == "1" ]]; then
      _CVE_RECOMMEND="**REVIEW THEN MERGE.** It resolves ${CVE_COUNT} version-verified known CVE(s) with zero new build errors, but the test suite is **currently failing** and we could NOT confirm the failures pre-date this PR (\`main\` did not build/test clean). Verify the failures are unrelated before merging."
    else
      _CVE_RECOMMEND="**PRIORITIZE — review then merge.** It resolves ${CVE_COUNT} version-verified known CVE(s) with zero new build errors; the build and type-check pass, but **tests were not run**, so safety is not fully verified. Prioritize it, give it a quick review, then merge to clear the CVE."
    fi
  else
    _CVE_VERIFIED=0
    _CVE_HEADING="$CVE_COUNT CVE(s) claimed by the PR body — ⚠️ NOT version-verified against the resulting (incl. transitive) go.mod/lockfile version: $CVE_LIST"
    _CVE_RECOMMEND="**Merge to clear these advisories — but the fix is NOT version-verified.** Confirm the resulting (incl. transitive) version reaches each advisory's fixed-in version before relying on this as a security fix; merging is still the path to remediate. No new build errors were introduced."
  fi

  # CVE override: when a PR fixes CVEs, escalate the presentation regardless of verdict.
  # CR5-8: Fire for ALL verdicts, not just pass/pre_existing. A HIGH CVE must be visually
  # distinct and recommend immediate merge even when the build has issues. The developer
  # needs to know: "This PR fixes a HIGH CVE but may also need build fixes."
  # End-user feedback: PR #10 (HIGH CVE) was indistinguishable from 27 other PRs.
  if [[ "$CVE_COUNT" -gt 0 && "$CVE_COUNT" != "0" ]]; then
    _SEV_BADGE=""
    if [[ -n "$CVE_MAX_SEVERITY" ]]; then
      _SEV_BADGE=" ($CVE_MAX_SEVERITY)"
    fi
    if [[ "$NEW_ERR_COUNT" -eq 0 ]]; then
      # No new errors — recommend immediate merge regardless of baseline state
      COMMENT="<!-- breakability-check -->
## 🔴 SECURITY FIX${_SEV_BADGE} — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

### ⚠️ $_CVE_HEADING
$(if [[ -n "$CVE_MAX_SEVERITY" ]]; then echo "**Severity: ${CVE_MAX_SEVERITY}** — This PR fixes a known security vulnerability."; fi)

**Build Impact:** No new errors introduced by this upgrade.${MODULE_LINE}
$(if [[ "$VERDICT" == "pre_existing" ]]; then echo "Baseline build has pre-existing failures (not related to this package)."; elif [[ "$VERDICT" == "pass" ]]; then echo "Build passes on PR branch."; else echo "Build status: \`$VERDICT\` — no new errors detected."; fi)

### Heads-up: CVE reachability (hint only)
No reachable path found by a scanner is **not** safe-to-ignore evidence. Patch regardless: this PR resolves the advisory.

### Recommendation
$_CVE_RECOMMEND
Security fixes should be prioritized over routine dependency upgrades.
$(if [[ "$VERDICT" == "pre_existing" && "$VER_LABEL" == L0* ]]; then echo "> If baseline build failures concern you, verify locally before merging. The security fix is independent of the baseline issue."; fi)

Verification: **${VER_LABEL:-L0}**${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
    else
      # Has new errors BUT also fixes CVEs — show both facts prominently
      COMMENT="<!-- breakability-check -->
## 🔴 SECURITY FIX (BUILD ISSUES)${_SEV_BADGE} — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

### ⚠️ $_CVE_HEADING
$(if [[ -n "$CVE_MAX_SEVERITY" ]]; then echo "**Severity: ${CVE_MAX_SEVERITY}** — This PR fixes a known security vulnerability."; fi)

**Build Impact:** ❌ $NEW_ERR_COUNT new error(s) introduced by this upgrade.${MODULE_LINE}

### Heads-up: CVE reachability (hint only)
No reachable path found by a scanner is **not** safe-to-ignore evidence. Patch regardless once the build break is fixed.

### Recommendation
$(if [[ "$_CVE_VERIFIED" == "1" ]]; then echo "**This PR fixes a version-verified $CVE_MAX_SEVERITY CVE but also introduces build errors.** Fix the build errors, then merge immediately."; else echo "**This PR claims to fix a $CVE_MAX_SEVERITY CVE (not version-verified) and also introduces build errors.** Fix the build errors and confirm the resulting version reaches the advisory's fixed-in version, then merge."; fi)
Do not delay — the security fix is critical.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
    fi
  fi

  if [[ -n "$COMMENT" ]]; then
    COMMENT=$(COMMENT_BODY="$COMMENT" MERGE_RISK_LINE="$MERGE_RISK_LINE" python3 "$BRK_SCRIPTS/rendering/pr_fields.py" inject_merge_risk 2>/dev/null || printf '%s' "$COMMENT")
    COMMENT=$(COMMENT_BODY="$COMMENT" PR_FIELDS="$PR_FIELDS" PR_NUM="$PR_NUM" RESULTS_FILE="$RESULTS_FILE" MERGE_RISK_LINE="$MERGE_RISK_LINE" PLAN_LINE="${PLAN_LINE:-}" RUN_LINK="${RUN_LINK:-}" ADVISORY_FOOTER="${ADVISORY_FOOTER:-}" python3 "$BRK_SCRIPTS/rendering/comment_builder.py")
    eval "$(get_verdict_v2 "$PR_NUM")"
    eval "$(get_behavioral_grade "$PR_NUM")"
    # Reset per-PR so a prior PR's residual evidence can never leak onto this one.
    _V2_RESIDUAL_BLOCK=""
    # ── Behavioral/oracle confidence, DISTINCT from build-verification tier ──
    # V2_CONF is an L0-L5 build/test verification tier from the verdict mapper. Do not show it
    # as behavioral confidence. Behavioral/oracle confidence comes from behavioral_grade.confidence.
    # BLOCKED -> High; SAFE -> None; REVIEW -> the committed behavioral grade (the
    # differential probe / break-class router) if present, else Medium. This replaces
    # the "review the release notes yourself" punt with a graded answer.
    # Match the Python _bg_cited_grade() condition exactly so the headline grade and the
    # merge-plan effective_risk_tag() can never diverge: a committed grade only counts when
    # it is CITED (source reasoning/probe AND has rationale/guidance/evidence). BG_OK alone
    # is set even for uncited default grades.
    _BG_CITED=0
    if [[ "${BG_OK:-0}" == "1" ]]; then
      _bg_src_lc="$(printf '%s' "${BG_SOURCE:-}" | tr '[:upper:]' '[:lower:]')"
      if [[ ( "$_bg_src_lc" == "reasoning" || "$_bg_src_lc" == "probe" ) \
            && ( -n "${BG_RATIONALE:-}" || -n "${BG_GUIDANCE:-}" || -n "${BG_EVIDENCE:-}" ) ]]; then
        _BG_CITED=1
      fi
    fi
    _BG_CONF_LC="$(printf '%s' "${BG_CONFIDENCE:-}" | tr '[:upper:]' '[:lower:]')"
    if [[ "$_BG_CITED" == "1" ]]; then
      case "$_BG_CONF_LC" in
        low|medium|high) _BEHAVIORAL_CONF_LABEL="$_BG_CONF_LC" ;;
        *) _BEHAVIORAL_CONF_LABEL="cited" ;;
      esac
    else
      _BEHAVIORAL_CONF_LABEL="not available"
    fi
    # Normalise the mapper's deterministic severity (none/low/medium/high) as the fallback grade
    # when no CITED behavioral oracle grade exists — this is the SAME severity the merge-plan tiers
    # use, so the per-PR headline and the merge-plan bucket can never diverge. A cited probe/
    # reasoning grade still wins (it did real work and may legitimately raise/lower the tier).
    _V2_SEV="$(printf '%s' "${V2_SEVERITY:-medium}" | tr '[:upper:]' '[:lower:]')"
    case "$_V2_SEV" in high|medium|low|none) ;; *) _V2_SEV="medium" ;; esac
    case "${V2_VERDICT:-REVIEW}" in
      BLOCKED) _GRADE="high" ;;
      SAFE)    if [[ "$_BG_CITED" == "1" ]]; then _GRADE="${BG_GRADE:-$_V2_SEV}"; else _GRADE="$_V2_SEV"; fi ;;
      *)       # REVIEW: prefer the CITED behavioral grade (the probe/reasoning oracle did real
               # work and may lower/raise the tier); otherwise fall back to the mapper severity so
               # stable major/0.x bumps read as Low (optional glance), not a blanket Medium.
               if [[ "$_BG_CITED" == "1" ]]; then
                 _GRADE="${BG_GRADE:-medium}"
               else
                 _GRADE="$_V2_SEV"
               fi
               case "$_GRADE" in high|medium|low|none) ;; *) _GRADE="medium" ;; esac
               ;;
    esac
    # CI review-tier floor: a security-sensitive CI action (auth/token/registry/deploy) must not
    # headline "Low · optional glance" while its body asks for a supply-chain review. Floor it to
    # Medium so headline and body agree. Non-sensitive majors stay Low (= "optional glance",
    # matching the changelog-glance body). A cited behavioral grade, if any, still wins.
    if [[ ( "$ECOSYSTEM" == "actions" || "$ECOSYSTEM" == "docker" ) && "$_CI_TIER" == "secsens" && "$_BG_CITED" != "1" ]]; then
      case "$_GRADE" in high|medium) ;; *) _GRADE="medium" ;; esac
    fi
    # ── Breakability grade headline (decisive verdicts) ────────────────────────
    # Map V2_BREAK_GRADE (from verdict_contract.py) to decisive emoji+title
    # SAFE → ✅ SAFE
    # LOW_BREAKING → 🟡 BREAKING - LOW breakability
    # MEDIUM_BREAKING → 🟠 BREAKING - MEDIUM breakability
    # HIGH_BREAKING → 🔴 BREAKING - HIGH breakability
    case "${V2_BREAK_GRADE:-MEDIUM_BREAKING}" in
      SAFE) 
        _BRK_EMOJI="✅"
        _BRK_TITLE="SAFE"
        _BRK_DESC="safe to merge"
        ;;
      LOW_BREAKING)
        _BRK_EMOJI="🟡"
        _BRK_TITLE="BREAKING - LOW breakability"
        _BRK_DESC="quick review recommended"
        ;;
      MEDIUM_BREAKING)
        _BRK_EMOJI="🟠"
        _BRK_TITLE="BREAKING - MEDIUM breakability"
        _BRK_DESC="careful review required"
        ;;
      HIGH_BREAKING)
        _BRK_EMOJI="🔴"
        _BRK_TITLE="BREAKING - HIGH breakability"
        _BRK_DESC="fix required before merge"
        ;;
      *)
        _BRK_EMOJI="🟠"
        _BRK_TITLE="BREAKING - MEDIUM breakability"
        _BRK_DESC="review required"
        ;;
    esac
    
    # Legacy grade-based headline (keep for backward compatibility, but prefer decisive breakability_grade)
    case "$_GRADE" in
      high)   _V2_HEADLINE="🔴 Breakability: High · review required · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}" ;;
      medium) _V2_HEADLINE="🟠 Breakability: Medium · review recommended · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}" ;;
      low)    _V2_HEADLINE="🟡 Breakability: Low · optional glance · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}" ;;
      *)      _GRADE="none"; _V2_HEADLINE="🟢 Breakability: None · safe to merge · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}" ;;
    esac
    
    # Use decisive breakability_grade headline as primary (user-requested format)
    _V2_HEADLINE_DECISIVE="${_BRK_EMOJI} ${_BRK_TITLE} · Oracle: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}"
    # ── CVE-aware headline floor ───────────────────────────────────────────────
    # A PR that fixes a known CVE must headline the SECURITY action, not a
    # breakability/changelog punt ("re-run", "skim the release notes"). Otherwise
    # the body says "MERGE THIS PR IMMEDIATELY" while the headline buries it, and
    # the merge plan says "SAFE — merge now" — a self-contradiction. Derive this
    # from the SAME committed signals the body uses: PR-body CVEs (CVE_COUNT) OR a
    # Dependabot/govulncheck-matched fix (_FIXES_CVE_DATA), plus NEW_ERR_COUNT and
    # V2_VERDICT. Security fixes are P0.
    _HAS_CVE_FIX=0
    _GATED_CVE_FIX=0
    if [[ -n "${_FIXES_CVE_DATA:-}" ]]; then
      # Version-gated Dependabot match (merge-results.sh confirmed resulting version >= fixed-in).
      _HAS_CVE_FIX=1; _GATED_CVE_FIX=1
    elif [[ "${CVE_COUNT:-0}" =~ ^[0-9]+$ && "${CVE_COUNT:-0}" -gt 0 ]]; then
      # PR-body CVE claim only — NOT confirmed against the resulting version.
      _HAS_CVE_FIX=1
    fi
    if [[ "$_HAS_CVE_FIX" == "1" ]]; then
      _SEC_SEV="${CVE_MAX_SEVERITY:+${CVE_MAX_SEVERITY} }"
      _SEC_CVE_N="${CVE_COUNT:-0}"
      [[ "$_SEC_CVE_N" =~ ^[0-9]+$ ]] || _SEC_CVE_N=0
      [[ "$_SEC_CVE_N" -gt 0 ]] && _SEC_CVE_DESC="${_SEC_CVE_N} ${_SEC_SEV}CVE(s)" || _SEC_CVE_DESC="known ${_SEC_SEV}vulnerabilit(ies)"
      # Only a version-gated fix earns the confident "resolves"/"MERGE NOW". A raw PR-body claim
      # is shown as "claims to fix (not version-verified)" and never forced to MERGE NOW, so the
      # headline can't over-credit a CVE the resulting version doesn't actually reach.
      if [[ "$_GATED_CVE_FIX" == "1" ]]; then _SEC_VERB="resolves"; else _SEC_VERB="claims to fix (not version-verified)"; fi
      if [[ "${NEW_ERR_COUNT:-0}" =~ ^[0-9]+$ && "${NEW_ERR_COUNT:-0}" -gt 0 ]]; then
        _V2_HEADLINE="🔴 Security fix · BLOCKED — ${_SEC_VERB} ${_SEC_CVE_DESC} but introduces build errors · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0 (fix build, then merge)"
      elif [[ "$_GRADE" == "high" ]]; then
        # The PR's OWN deterministic/behavioral break grade is High. An incidental (often
        # transitive) CVE must NOT bury that — keep the breaking-change identity as the lead
        # so the dev sees the dominant risk first, with the CVE noted as a secondary benefit.
        _V2_HEADLINE="🔴 Breakability: High · REVIEW THEN MERGE — also ${_SEC_VERB} ${_SEC_CVE_DESC}; the breaking change is the dominant risk (verify the call sites below), merging still clears the CVE · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      elif [[ "$_GATED_CVE_FIX" != "1" || "${V2_VERDICT:-REVIEW}" == "BLOCKED" || "${V2_VERDICT:-REVIEW}" == "REVIEW" || "$_GRADE" == "medium" ]]; then
        # A CVE-fixing PR that the body routes to REVIEW (breaking change flagged, or a
        # committed behavioral grade of high/medium), OR an unverified PR-body claim, must NOT
        # headline "MERGE NOW" — that contradicts the body/plan. Say REVIEW THEN MERGE so the
        # security urgency is preserved without over-promising safety.
        _V2_HEADLINE="🔴 Security fix · REVIEW THEN MERGE — ${_SEC_VERB} ${_SEC_CVE_DESC}; verify the version/breaking-change note below, but merging is the path to clear the CVE · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      elif [[ "$_TESTS_CLEAN" == "1" ]]; then
        # Build clean AND the test suite actually ran green — the only state that earns the
        # confident "MERGE NOW". Urgency never bypasses verification.
        _V2_HEADLINE="🔴 Security fix · MERGE NOW — ${_SEC_VERB} ${_SEC_CVE_DESC}; build clean and tests pass · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      elif [[ "$_TEST_FAILED" == "1" && "$_TEST_PREEXIST_VERIFIED" == "1" ]]; then
        # Tests fail, but they ALSO fail on main (proven pre-existing). Prioritize, but the dev
        # must confirm the failures are unrelated before merging — never "merge now" over red.
        _V2_HEADLINE="🔴 Security fix · REVIEW THEN MERGE — ${_SEC_VERB} ${_SEC_CVE_DESC}; the test suite also fails on \`main\` (pre-existing) — confirm it is unrelated, then merge to clear the CVE · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      elif [[ "$_TEST_FAILED" == "1" ]]; then
        # Tests fail and we could NOT prove they pre-date this PR. Highest-caution security verb.
        _V2_HEADLINE="🔴 Security fix · REVIEW THEN MERGE — ${_SEC_VERB} ${_SEC_CVE_DESC}, but the test suite is currently failing and not confirmed pre-existing — verify before merging · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      else
        # Build clean, no behavioral break, but tests were not run — prioritize, glance, merge.
        _V2_HEADLINE="🔴 Security fix · PRIORITIZE — review then merge — ${_SEC_VERB} ${_SEC_CVE_DESC}; build clean but tests were not run (safety not fully verified) · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      fi
    fi
    case "${V2_VERDICT:-REVIEW}" in
      SAFE)
        # None/Low with positive evidence => no residual block needed.
        [[ "$_GRADE" == "none" ]] && _V2_RESIDUAL_BLOCK=""
        ;;
      BLOCKED)
        ;;
      *)
        V2_VERDICT="REVIEW"
        ;;
    esac
    # When the committed behavioral grade carries a reasoned rationale, surface THAT
    # (concrete, committed) instead of the generic "what to check" residual.
    if [[ "${BG_OK:-0}" == "1" && -n "${BG_RATIONALE:-}" ]]; then
      _BG_BODY="Why: ${BG_RATIONALE}"
      [[ -n "${BG_GUIDANCE:-}" ]] && _BG_BODY="${_BG_BODY}
→ ${BG_GUIDANCE}"
      [[ -n "${BG_EVIDENCE:-}" ]] && _BG_BODY="${_BG_BODY}
Evidence: ${BG_EVIDENCE}"
      [[ -n "${BG_CALLSITE:-}" ]] && _BG_BODY="${_BG_BODY}
Reachable at: ${BG_CALLSITE}"
      _V2_RESIDUAL_BLOCK="$_BG_BODY"
    fi
    if [[ "${BG_OK:-0}" != "1" && ( "${V2_VERDICT:-REVIEW}" == "REVIEW" || "${V2_VERDICT:-REVIEW}" == "BLOCKED" ) ]]; then
      _V2_RESIDUAL_SUMMARY_RAW="${V2_RESIDUAL_SUMMARY:-${V2_REASON:-manual review required}}"
      _V2_RESIDUAL_CHECK_RAW="${V2_RESIDUAL_CHECK:-Review the deterministic evidence below before merging.}"
      _V2_RESIDUAL_CHANGELOG_RAW="${V2_RESIDUAL_CHANGELOG:-}"
      _V2_RESIDUAL_REACH_RAW="${V2_RESIDUAL_REACH:-}"
      _V2_RESIDUAL_BLOCK=$(V2_RESIDUAL_SUMMARY="$_V2_RESIDUAL_SUMMARY_RAW" V2_RESIDUAL_CHECK="$_V2_RESIDUAL_CHECK_RAW" V2_RESIDUAL_CHANGELOG="$_V2_RESIDUAL_CHANGELOG_RAW" V2_RESIDUAL_REACH="$_V2_RESIDUAL_REACH_RAW" R_DEP_TYPE="${DEP_TYPE:-?}" R_BUMP="${BUMP:-?}" R_FROM="${FROM:-?}" R_TO="${TO:-?}" R_USAGE_SIG="${V2_SIG_usage:-UNAVAILABLE}" R_CHANGELOG_SIG="${V2_SIG_changelog:-UNAVAILABLE}" python3 "$BRK_SCRIPTS/rendering/residual_risk.py" synthesize 2>/dev/null || printf 'What to check: %s\n→ %s' "$_V2_RESIDUAL_SUMMARY_RAW" "$_V2_RESIDUAL_CHECK_RAW")
    fi
    # CI review-tier residual — surface the "why not auto-safe" in the VISIBLE per-PR comment
    # (the detailed body is collapsed into <details>). Only when no cited behavioral grade exists.
    if [[ ( "$ECOSYSTEM" == "actions" || "$ECOSYSTEM" == "docker" ) && -n "$_CI_TIER" && "$_BG_CITED" != "1" ]]; then
      if [[ "$_CI_TIER" == "secsens" ]]; then
        _V2_RESIDUAL_BLOCK="What to check: this CI dependency handles tokens, credentials, registry/cloud auth, signing, or deployment — \"CI-only\" does not make it auto-safe.
→ Pin to a full commit SHA, and review the changelog for changed permissions / token scopes / inputs before merging."
      else
        _V2_RESIDUAL_BLOCK="What to check: major version bump of a CI action — inputs, runtime defaults, or outputs may have changed and could break your workflow (no application code is affected).
→ Skim the release notes for breaking changes before merging."
      fi
    fi
    _COMPANION_BANNER=$(PR_NUM="$PR_NUM" RESULTS_FILE="$RESULTS_FILE" python3 "$BRK_SCRIPTS/rendering/residual_risk.py" companion_banner 2>/dev/null || true)
    _V2_SIGNALS_TABLE="
### Signals checked
| Signal | Result |
|---|---|
| Resolve | $(v2_signal_label "${V2_SIG_resolve:-UNAVAILABLE}") |
| Build | $(v2_signal_label "${V2_SIG_build:-UNAVAILABLE}") |
| Test | $(if [[ "$_TEST_FAILED" == "1" ]]; then printf '%s' "$_TEST_SIGNAL_CELL"; elif [[ "$VERDICT" == "pre_existing" || -n "${TEST_FAIL_DETAIL:-}" ]]; then printf '⚠️ pre-existing failures — tests did not re-verify clean'; elif [[ "${TEST_RAN:-False}" != "True" ]]; then printf '· not run (no behavioral-probe mitigation assumed)'; else v2_signal_label "${V2_SIG_test:-UNAVAILABLE}"; fi) |
| API diff | $(v2_signal_label "${V2_SIG_api_diff:-UNAVAILABLE}") |
| Usage | $(v2_signal_label "${V2_SIG_usage:-UNAVAILABLE}") |
| Vulnerability | $(v2_signal_label "${V2_SIG_vuln:-UNAVAILABLE}") |
| Changelog | $(v2_signal_label "${V2_SIG_changelog:-UNAVAILABLE}") |"
    if [[ "${BREAKABILITY_DEBUG_WRAP_LEGACY:-0}" == "1" ]]; then
    COMMENT=$(COMMENT_BODY="$COMMENT" V2_HEADLINE="$_V2_HEADLINE" V2_COMPANION_BANNER="${_COMPANION_BANNER:-}" V2_RESIDUAL_BLOCK="${_V2_RESIDUAL_BLOCK:-}" V2_SIGNALS_TABLE="$_V2_SIGNALS_TABLE" python3 "$BRK_SCRIPTS/rendering/residual_risk.py" debug_wrap 2>/dev/null || printf '%s' "$COMMENT")
    fi
    if gh_pr_comment "$PR_NUM" "$COMMENT"; then
      echo "  Posted comment for PR #$PR_NUM ($PKG ${FROM}→${TO}, $VERDICT)"
      POSTED=$((POSTED + 1))
    else
      echo "  ⚠️  Failed to post comment for PR #$PR_NUM"
      FAILED=$((FAILED + 1))
    fi
  fi
done

echo ""
echo "Deterministic comments: posted $POSTED, skipped $SKIPPED (AI agent already commented), failed $FAILED"

# ── Regenerate merge plan issue ──────────────────────────────────────────────
# The merge plan must always reflect the latest build-results.json.
# If the AI agent generated a previous plan, it may be stale after a
# deterministic rerun. This section creates/updates the merge plan issue
# from the current data so PR comments and the plan never contradict.
echo ""
echo "════════════ MERGE PLAN ════════════"

MERGE_PLAN_BODY=$(python3 "$BRK_SCRIPTS/rendering/merge_plan.py")

if [[ -n "$MERGE_PLAN_BODY" && "$MERGE_PLAN_BODY" != *"Traceback"* ]]; then
  _MP_LABEL="breakability-merge-plan"

  # Count analyzed PRs for the title
  _MP_PR_COUNT=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
print(len(data.get('prs', {})))
" 2>/dev/null || echo "?")
  _MP_TITLE="📋 Breakability Merge Plan $(date -u '+%Y-%m-%d %H:%M UTC') (${_MP_PR_COUNT} PRs)"

  # GitHub caps issue/comment bodies at 65536 chars. On large repos the rendered plan
  # (per-PR sections + long CVE lists) can exceed that, which previously made
  # `gh issue create` fail silently ("Failed to create merge plan issue") and post
  # nothing. Build a truncated copy for the GitHub post (the full body is still written
  # to disk as the dry-run/CI artifact). The plan is front-loaded (Developer Action
  # Summary, security CVEs, per-PR review), so head-truncation preserves the
  # actionable content.
  MERGE_PLAN_BODY_POST="$MERGE_PLAN_BODY"
  if [[ "${#MERGE_PLAN_BODY}" -gt 65000 ]]; then
    MERGE_PLAN_BODY_POST=$(MP_FULL="$MERGE_PLAN_BODY" python3 "$BRK_SCRIPTS/rendering/truncate_plan.py")
    echo "  Merge plan body exceeded 65536 chars — truncated to fit GitHub limit"
  fi

  # The plan issue was reserved (and stale plans closed) BEFORE the per-PR loop so
  # comments could link the live number. Update THAT issue in place with the final
  # title + body. Only if reservation failed do we create one now (fallback).
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '%s\n' "$MERGE_PLAN_BODY" > "$DRY_RUN_DIR/merge-plan.md"
    echo "  [dry-run] wrote merge plan -> $DRY_RUN_DIR/merge-plan.md"
  elif [[ -n "${MERGE_PLAN_NUM:-}" ]]; then
    if gh issue edit "$MERGE_PLAN_NUM" --title "$_MP_TITLE" --body "$MERGE_PLAN_BODY_POST" >/dev/null 2>&1; then
      echo "  Updated merge plan issue #$MERGE_PLAN_NUM"
    else
      echo "  ⚠️  Failed to update reserved merge plan issue #$MERGE_PLAN_NUM"
    fi
  else
    NEW_ISSUE=$(gh issue create \
      --title "$_MP_TITLE" \
      --body "$MERGE_PLAN_BODY_POST" \
      --label "$_MP_LABEL" 2>/dev/null || echo "")
    # Retry without the label if the labeled create failed (e.g. label missing).
    if [[ -z "$NEW_ISSUE" ]]; then
      NEW_ISSUE=$(gh issue create \
        --title "$_MP_TITLE" \
        --body "$MERGE_PLAN_BODY_POST" 2>/dev/null || echo "")
    fi
    if [[ -n "$NEW_ISSUE" ]]; then
      echo "  Created merge plan issue: $NEW_ISSUE"
    else
      echo "  ⚠️  Failed to create merge plan issue"
    fi
  fi
else
  echo "  ⚠️  Merge plan generation failed — skipping issue update"
fi
