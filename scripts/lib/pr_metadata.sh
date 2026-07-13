#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# pr_metadata.sh — Per-PR metadata setup, enrichment, and CLI pipeline
#
# Provides setup_pr_metadata() which initializes temp files, parses package
# name/versions from PR title, detects ecosystem/dep type/relation, enriches
# CVEs, collects diff, runs usage scan, NestJS peer check, TS pipeline CLI,
# and npm API diff.
#
# Globals read:  PR_NUM, PR_TITLE, PR_BRANCH, PR_BODY, PR_JSON, i,
#                REPO_ROOT, BRK_SCRIPTS, BC_SCRATCH_DIR, RESULTS_FILE,
#                CLI_PATH, DIFF_MAX_LINES, _BC_ALERTS_CACHE
# Globals set:   INSTALL_METHOD, ERROR_CLASS, CASCADE_IMPACT,
#                NESTJS_PEER_WARNING, INSTALL_OK, MERGEABLE_STATUS,
#                NEW_ERRORS, MERGEABLE_JSON, MERGE_STATE, BUILD_VERDICT,
#                ECOSYSTEM, PKG_DIR, PKG, FROM_VER, TO_VER,
#                ADDITIONAL_PACKAGES, BUMP, DEP_TYPE, DEP_RELATION,
#                CVES, CVE_DETAILS, DIFF_FILE, DIFF_LINES, DIFF_TRUNCATED,
#                USAGE_RAW, FILES_IMPORTING, IMPORT_COUNT,
#                ADDITIONAL_IMPORTS, DETERMINISTIC
# ──────────────────────────────────────────────────────────────────────────────

setup_pr_metadata() {
# Initialize vuln-scan outputs (in case code path skips the scan below)
printf 'unknown' > "/tmp/_bc_vuln_status_${PR_NUM}.txt"
printf '' > "/tmp/_bc_vuln_finding_${PR_NUM}.txt"
printf '' > "/tmp/_bc_vuln_new_findings_${PR_NUM}.txt"
printf '0' > "/tmp/_bc_vuln_preexisting_count_${PR_NUM}.txt"
printf '' > "/tmp/_bc_vuln_output_${PR_NUM}.txt"
printf '' > "$BC_SCRATCH_DIR/_bc_go_resolution_command_${PR_NUM}.txt"
printf 'null' > "$BC_SCRATCH_DIR/_bc_go_resolution_exit_${PR_NUM}.txt"
printf '' > "$BC_SCRATCH_DIR/_bc_go_resolution_output_${PR_NUM}.txt"
printf '' > "$BC_SCRATCH_DIR/_bc_go_modsum_diff_${PR_NUM}.txt"
printf '' > "$BC_SCRATCH_DIR/_bc_usage_raw_${PR_NUM}.txt"
printf '' > "$BC_SCRATCH_DIR/_bc_cli_output_${PR_NUM}.txt"
printf '' > "$BC_SCRATCH_DIR/_bc_smoke_output_${PR_NUM}.txt"

INSTALL_METHOD="ci"
ERROR_CLASS=""
CASCADE_IMPACT="[]"
NESTJS_PEER_WARNING=""
INSTALL_OK="false"
MERGEABLE_STATUS="UNKNOWN"
NEW_ERRORS=""

# Check mergeable status — skip deep analysis for conflicted PRs
MERGEABLE_JSON=$(gh pr view "$PR_NUM" --json mergeable,mergeStateStatus 2>/dev/null || echo '{}')
MERGEABLE_STATUS=$(echo "$MERGEABLE_JSON" | jq -r '.mergeable // "UNKNOWN"')
MERGE_STATE=$(echo "$MERGEABLE_JSON" | jq -r '.mergeStateStatus // "UNKNOWN"')
echo "  mergeable: $MERGEABLE_STATUS ($MERGE_STATE)"

# If PR has merge conflicts, record it and skip full analysis
if [[ "$MERGEABLE_STATUS" == "CONFLICTING" ]]; then
  echo "  ⚠️  PR has merge conflicts — skipping build analysis"
  BUILD_VERDICT="conflict"
  # Still need to parse title for package info, then write minimal JSON
fi

# Detect ecosystem
ECOSYSTEM=$(detect_ecosystem "$PR_BRANCH")
echo "  ecosystem: $ECOSYSTEM"

# Detect package subdirectory for monorepos
PKG_DIR=$(cd "$REPO_ROOT" && detect_pkg_dir "$PR_BRANCH" "$ECOSYSTEM")
echo "  pkg_dir: $PKG_DIR"

# Parse package name and versions from title
# Handles: "Bump X from A to B", "Bump X and Y"
PKG=""
FROM_VER=""
TO_VER=""
ADDITIONAL_PACKAGES=""

# Dependabot uses two title styles: legacy "Bump X from A to B" and the
# conventional-commits "build(deps): bump X from A to B" (lowercase, prefixed).
# Match [Bb]ump unanchored so both styles — and scoped names like @nestjs/common
# — are captured. ("build" contains no "bump" substring, so this is unambiguous.)
if [[ "$PR_TITLE" =~ [Bb]ump[[:space:]]+(.+)[[:space:]]+from[[:space:]]+([^ ]+)[[:space:]]+to[[:space:]]+([^ ]+) ]]; then
  PKG="${BASH_REMATCH[1]}"
  FROM_VER="${BASH_REMATCH[2]}"
  TO_VER="${BASH_REMATCH[3]}"
elif [[ "$PR_TITLE" =~ [Bb]ump[[:space:]]+(.+)[[:space:]]+and[[:space:]]+(.*) ]]; then
  # Multi-package PR — take the first package name, record others
  PKG="${BASH_REMATCH[1]}"
  ADDITIONAL_PACKAGES="${BASH_REMATCH[2]}"
  # Clean "in /dir" from additional packages
  ADDITIONAL_PACKAGES=$(echo "$ADDITIONAL_PACKAGES" | sed 's/ in \/.*$//')
  # Try multiple patterns from PR body to find versions:
  FIRST_BUMP_LINE=""
  for pattern in \
    'from \`\?[0-9][0-9.]*\`\? to \`\?[0-9][0-9.]*\`\?' \
    '[Uu]pdates.*from [0-9][0-9.]* to [0-9][0-9.]*' \
    '[Bb]umps.*from [0-9][0-9.]* to [0-9][0-9.]*'; do
    FIRST_BUMP_LINE=$(echo "$PR_BODY" | tr -d '`' | grep -m1 -oE "$pattern" || true)
    [[ -n "$FIRST_BUMP_LINE" ]] && break
  done
  if [[ -n "$FIRST_BUMP_LINE" ]]; then
    FROM_VER=$(echo "$FIRST_BUMP_LINE" | grep -oE '[0-9][0-9.]*' | head -1)
    TO_VER=$(echo "$FIRST_BUMP_LINE" | grep -oE '[0-9][0-9.]*' | tail -1)
  fi
  echo "  multi-package PR: $PKG + $ADDITIONAL_PACKAGES"
fi

# Sanitize: strip any trailing HTML/whitespace from version strings
FROM_VER=$(echo "$FROM_VER" | tr -d '\n\r' | sed 's/[^0-9a-zA-Z._-].*//; s/[[:space:]]//g')
TO_VER=$(echo "$TO_VER" | tr -d '\n\r' | sed 's/[^0-9a-zA-Z._-].*//; s/[[:space:]]//g')

echo "  package: $PKG ($FROM_VER → $TO_VER)"

# Bump type
BUMP="unknown"
if [[ -n "$FROM_VER" && -n "$TO_VER" ]]; then
  BUMP=$(detect_bump_type "$FROM_VER" "$TO_VER")
fi
echo "  bump: $BUMP"

# Update-type risk profile: patch updates are SAFE (no breaking changes by semver).
# Major updates carry HIGH_RISK (semver contract broken, API surface changed).
# Minor updates are MODERATE_RISK (new features, but backwards compatible).

# Dep type
DEP_TYPE="unknown"
case "$ECOSYSTEM" in
  npm)     
    if [[ "$PKG_DIR" != "/" && -f "$PKG_DIR/package.json" ]]; then
      DEP_TYPE=$(detect_dep_type_npm "$PKG" "$PKG_DIR/package.json")
    else
      DEP_TYPE=$(detect_dep_type_npm "$PKG")
    fi
    ;;
  gomod)
    # CR4-7: pass PKG_DIR to scope grep to the affected module
    if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
      DEP_TYPE=$(detect_dep_type_go "$PKG" "$PKG_DIR")
    else
      DEP_TYPE=$(detect_dep_type_go "$PKG")
    fi
    ;;
  pip)     DEP_TYPE="production" ;;
  actions) DEP_TYPE="dev" ;;
  docker)  DEP_TYPE="production" ;;
  maven)   DEP_TYPE="production" ;;

esac
echo "  dep_type: $DEP_TYPE"

# Dep relation (CR4-10: pass correct go.mod path for multi-module repos)
_GO_MOD_PATH="go.mod"
if [[ "$ECOSYSTEM" == "gomod" && "$PKG_DIR" != "/" && -f "${PKG_DIR}/go.mod" ]]; then
  _GO_MOD_PATH="${PKG_DIR}/go.mod"
fi
DEP_RELATION=$(detect_dep_relation "$ECOSYSTEM" "$PKG" "$_GO_MOD_PATH")
echo "  dep_relation: $DEP_RELATION"

# Security / CVEs — from PR body AND Dependabot alerts cache
# Dependabot usually does NOT put CVE/GHSA IDs in PR bodies.
# We enrich from the cached alerts API response.
CVES=$(extract_cves "$PR_BODY")
# Enrich from Dependabot alerts: find alerts matching this package name
# V8 FIX: Also extract severity, CVSS score, and advisory URL for each CVE
CVE_DETAILS="[]"
if [[ -f "$_BC_ALERTS_CACHE" ]]; then
  _CVE_ENRICH=$(python3 "$BRK_SCRIPTS/core/pr_utils.py" cve_enrich --pkg "$PKG" --alerts-cache "$_BC_ALERTS_CACHE" 2>/dev/null)
  ALERT_CVES=$(echo "$_CVE_ENRICH" | head -1)
  CVE_DETAILS=$(echo "$_CVE_ENRICH" | tail -1)
  [[ -z "$CVE_DETAILS" || "$CVE_DETAILS" == "" ]] && CVE_DETAILS="[]"
  # Merge: body CVEs + alert CVEs (deduplicated)
  if [[ -n "$ALERT_CVES" ]]; then
    if [[ -n "$CVES" ]]; then
      CVES=$(echo "$CVES,$ALERT_CVES" | tr "," "\n" | sort -u | tr "\n" "," | sed "s/,$//" )
    else
      CVES="$ALERT_CVES"
    fi
  fi
fi
[[ -n "$CVES" ]] && echo "  cves: $CVES"

# ── Collect diff ────────────────────────────────────────────────
DIFF_FILE="/tmp/pr-${PR_NUM}.diff"
gh pr diff "$PR_NUM" > "$DIFF_FILE" 2>/dev/null || echo "" > "$DIFF_FILE"
DIFF_LINES=$(wc -l < "$DIFF_FILE" | tr -d ' ')
DIFF_TRUNCATED="false"
if [[ "$DIFF_LINES" -gt "$DIFF_MAX_LINES" ]]; then
  DIFF_TRUNCATED="true"
  head -n "$DIFF_MAX_LINES" "$DIFF_FILE" > "${DIFF_FILE}.tmp"
  mv "${DIFF_FILE}.tmp" "$DIFF_FILE"
fi
echo "  diff: $DIFF_LINES lines (truncated=$DIFF_TRUNCATED)"

# ── Usage scan (shell-level) ────────────────────────────────────
USAGE_RAW=""
case "$ECOSYSTEM" in
  npm)   
    # For monorepos, scan from PKG_DIR if available
    if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
      USAGE_RAW=$(cd "$PKG_DIR" && scan_usage_npm "$PKG")
    else
      USAGE_RAW=$(scan_usage_npm "$PKG")
    fi
    ;;
  gomod)
    # CR4-13: scope usage scan to PKG_DIR module to avoid inflating import count
    if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
      USAGE_RAW=$(scan_usage_go "$PKG" "$PKG_DIR")
    else
      USAGE_RAW=$(scan_usage_go "$PKG")
    fi
    ;;
  pip)   USAGE_RAW=$(scan_usage_pip "$PKG") ;;
esac
printf '%s' "$USAGE_RAW" > "$BC_SCRATCH_DIR/_bc_usage_raw_${PR_NUM}.txt"
FILES_IMPORTING=$(format_usage_files "$USAGE_RAW")
IMPORT_COUNT=$(echo "$FILES_IMPORTING" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
echo "  imports found: $IMPORT_COUNT files"

# ── Usage scan for additional packages (multi-package PRs) ──────
ADDITIONAL_IMPORTS="[]"
if [[ -n "$ADDITIONAL_PACKAGES" ]]; then
  for EXTRA_PKG in $(echo "$ADDITIONAL_PACKAGES" | tr ',' ' '); do
    EXTRA_PKG=$(echo "$EXTRA_PKG" | xargs)  # trim whitespace
    [[ -z "$EXTRA_PKG" ]] && continue
    EXTRA_RAW=""
    case "$ECOSYSTEM" in
      npm)
        if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
          EXTRA_RAW=$(cd "$PKG_DIR" && scan_usage_npm "$EXTRA_PKG")
        else
          EXTRA_RAW=$(scan_usage_npm "$EXTRA_PKG")
        fi
        ;;
      gomod)
        if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
          EXTRA_RAW=$(scan_usage_go "$EXTRA_PKG" "$PKG_DIR")
        else
          EXTRA_RAW=$(scan_usage_go "$EXTRA_PKG")
        fi
        ;;
      pip)   EXTRA_RAW=$(scan_usage_pip "$EXTRA_PKG") ;;
    esac
    EXTRA_FILES=$(format_usage_files "$EXTRA_RAW")
    EXTRA_COUNT=$(echo "$EXTRA_FILES" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    echo "  additional pkg $EXTRA_PKG: $EXTRA_COUNT import sites"
    # Merge into ADDITIONAL_IMPORTS as {"package": "...", "files": [...]}
    # Use temp files to avoid shell double-quote parsing issues on 2nd+ iteration
    # (Finding-5.3) and special chars in package names (Finding-5.6).
    printf '%s' "$ADDITIONAL_IMPORTS" > /tmp/_bc_addl_accum.json
    printf '%s' "$EXTRA_FILES" > /tmp/_bc_extra_files.json
    printf '%s' "$EXTRA_PKG" > /tmp/_bc_extra_pkg.txt
    _addl_result=""
    _addl_result=$(python3 2>/dev/null << 'ADDLEOF'
import json
with open('/tmp/_bc_addl_accum.json') as f: existing = json.loads(f.read() or '[]')
with open('/tmp/_bc_extra_files.json') as f: files = json.loads(f.read() or '[]')
with open('/tmp/_bc_extra_pkg.txt') as f: pkg = f.read().strip()
existing.append({'package': pkg, 'files': files, 'count': len(files)})
print(json.dumps(existing))
ADDLEOF
) && ADDITIONAL_IMPORTS="$_addl_result" || true
  done
fi

# ── Cascade impact (shared lib analysis) ────────────────────────
if [[ "$PKG_DIR" == lib/* ]]; then
  CASCADE_IMPACT=$(check_cascade_impact "$PKG_DIR")
  CASCADE_COUNT=$(echo "$CASCADE_IMPACT" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
  echo "  cascade: $CASCADE_COUNT downstream services affected"
fi

# ── NestJS peer group warning ───────────────────────────────────
if [[ "$PKG" == @nestjs/* ]]; then
  NESTJS_PEER_WARNING=$(python3 "$BRK_SCRIPTS/core/pr_utils.py" nestjs_peer_warning --pkg "$PKG" --peer-groups-file /tmp/_bc_peer_groups.json --results-file "$RESULTS_FILE" 2>/dev/null || true)
  [[ -n "$NESTJS_PEER_WARNING" ]] && echo "  $NESTJS_PEER_WARNING"
fi


# ── Run TS pipeline CLI (for npm/gomod/pip) ────────────────────
DETERMINISTIC="{}"
if [[ "$ECOSYSTEM" == "npm" || "$ECOSYSTEM" == "gomod" || "$ECOSYSTEM" == "pip" ]] && [[ -n "$PKG" && -n "$FROM_VER" && -n "$TO_VER" ]]; then
  echo "  running TS pipeline..."
  CLI_ECO="$ECOSYSTEM"
  PR_BODY_FILE="/tmp/_bc_pr_${PR_NUM}.body"
  printf '%s' "$PR_BODY" > "$PR_BODY_FILE"

  # CLI sends logs to stdout mixed with JSON.  Capture all stdout,
  # then extract only the JSON portion (from first '{' to end).
  CLI_OUTPUT_FILE="/tmp/_bc_cli_${PR_NUM}.raw"
  CLI_JSON_FILE="/tmp/_bc_cli_${PR_NUM}.json"
  CLI_ERR_FILE="/tmp/_bc_cli_${PR_NUM}.err"
  # Pre-fetch comprehensive changelogs/release notes and feed them to the CLI so
  # computeMergeRisk sees declared breaking changes; the CLI also persists
  # deterministic.changelogText + deterministic.changelogSignal.
  CLI_CHANGELOG_ARGS=()
  if [[ "$ECOSYSTEM" == "gomod" || "$ECOSYSTEM" == "npm" ]]; then
    CLI_CHANGELOG_FILE="/tmp/_bc_changelog_${PR_NUM}.txt"
    if [[ "$ECOSYSTEM" == "gomod" ]]; then
      fetch_go_changelog_text "$PKG" "$FROM_VER" "$TO_VER" > "$CLI_CHANGELOG_FILE" 2>/dev/null || true
    else
      fetch_npm_changelog_text "$PKG" "$FROM_VER" "$TO_VER" > "$CLI_CHANGELOG_FILE" 2>/dev/null || true
    fi
    if [[ -s "$CLI_CHANGELOG_FILE" ]]; then
      CLI_CHANGELOG_ARGS=(--changelog-file "$CLI_CHANGELOG_FILE")
      echo "  changelog: fetched $(wc -l < "$CLI_CHANGELOG_FILE" | tr -d ' ') line(s) for CLI verdict"
    fi
  fi
  timeout -k 15 180 node "$CLI_PATH" \
    -p "$PKG" -f "$FROM_VER" -t "$TO_VER" \
    -r "$REPO_ROOT" -e "$CLI_ECO" -d "$DEP_TYPE" \
    --pr-body-file "$PR_BODY_FILE" \
    ${CLI_CHANGELOG_ARGS[@]+"${CLI_CHANGELOG_ARGS[@]}"} \
    --json > "$CLI_OUTPUT_FILE" 2>"$CLI_ERR_FILE" || true

  # Extract JSON: find the first line starting with '{' and take everything from there
  sed -n '/^{/,$p' "$CLI_OUTPUT_FILE" > "$CLI_JSON_FILE"

  if python3 -c "import json; json.load(open('$CLI_JSON_FILE'))" 2>/dev/null; then
    DETERMINISTIC=$(BC_FILES_IMPORTING="$FILES_IMPORTING" python3 "$BRK_SCRIPTS/core/pr_utils.py" reconcile_cli --cli-json-file "$CLI_JSON_FILE" --pkg "$PKG" 2>/dev/null || echo "{}")
    echo "  pipeline: classification=$(echo "$DETERMINISTIC" | python3 -c "import json,sys; print(json.load(sys.stdin).get('classification','?'))" 2>/dev/null || echo "?")"
    echo "  pipeline: merge_risk=$(echo "$DETERMINISTIC" | python3 -c "import json,sys; d=json.load(sys.stdin); mr=d.get('merge_risk') or {}; cs=d.get('changelogSignal') or {}; print((mr.get('tag') or '?')+' changelog_status='+str((cs or {}).get('status'))+' bullets='+str(len((cs or {}).get('bullets',[]))))" 2>/dev/null || echo "?")"
  else
    echo "  pipeline: failed to parse CLI output"
    echo "  pipeline-stderr: $(tail -3 "$CLI_ERR_FILE" 2>/dev/null | tr '\n' ' ')"
    DETERMINISTIC="{}"
  fi
  cat "$CLI_OUTPUT_FILE" "$CLI_ERR_FILE" 2>/dev/null | tail -c 4000 > "$BC_SCRATCH_DIR/_bc_cli_output_${PR_NUM}.txt" || true
  rm -f "$CLI_OUTPUT_FILE" "$CLI_JSON_FILE" "$CLI_ERR_FILE"
else
  echo "  pipeline: skipped ($ECOSYSTEM)"
fi

# ── npm semantic API/type-surface diff (mirrors Go apidiff) ───────────────────
# The bundled TS CLI does not compare the dependency's exported type surface, so
# for npm we run a standalone TypeScript-compiler diff of the package's .d.ts at
# the from/to versions (downloaded from the public registry). Results are merged
# into the deterministic block as the api_diff signal. Fails safe: compatible=null
# (UNAVAILABLE) whenever either version ships no types or extraction fails — never
# a false "compatible". A clean compatible result only clears patch/minor bumps;
# the evidence contract still gates major bumps on semver + changelog.
if [[ "$ECOSYSTEM" == "npm" && -n "$PKG" && -n "$FROM_VER" && -n "$TO_VER" ]]; then
  APIDIFF_SCRIPT="$BRK_SCRIPTS/npm_apidiff.mjs"
  if [[ -f "$APIDIFF_SCRIPT" ]] && command -v node >/dev/null 2>&1; then
    echo "  npm api-diff: $PKG $FROM_VER -> $TO_VER ..."
    APIDIFF_JSON=$(timeout -k 15 240 node "$APIDIFF_SCRIPT" "$PKG" "$FROM_VER" "$TO_VER" 2>/dev/null | tail -1 || echo "")
    if [[ -n "$APIDIFF_JSON" ]]; then
      DETERMINISTIC=$(DET_IN="$DETERMINISTIC" AD_IN="$APIDIFF_JSON" python3 "$BRK_SCRIPTS/core/pr_utils.py" merge_apidiff 2>/dev/null || echo "$DETERMINISTIC")
      echo "  npm api-diff: compatible=$(echo "$APIDIFF_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('compatible'))" 2>/dev/null || echo '?') api_changes=$(echo "$APIDIFF_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('apiChanges'))" 2>/dev/null || echo '?')"
    else
      echo "  npm api-diff: no output (unavailable)"
    fi
  fi
fi
}
