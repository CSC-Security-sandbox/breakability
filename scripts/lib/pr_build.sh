#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# pr_build.sh — Per-PR build execution and verdict determination
#
# Provides run_pr_build() which creates a worktree for the PR branch, runs
# ecosystem-specific builds (npm ci + tsc, go mod tidy + build + vet,
# pip install + import, maven, docker, actions), compares against baseline,
# and determines BUILD_VERDICT.
#
# Globals read:  ECOSYSTEM, PR_NUM, PKG, PKG_DIR, REPO_ROOT, BRK_SCRIPTS,
#                WORKTREE_BASE, TIMEOUT, GO_TIMEOUT, GO_AVAILABLE, PR_BRANCH,
#                FROM_VER, TO_VER, BUMP, FILES_IMPORTING, BC_SCRATCH_DIR,
#                MAIN_DIR, MERGEABLE_STATUS, _GO_MULTI_MODULE,
#                main_go_exit, main_go_output, main_pip_exit, main_pip_output
# Globals set:   BUILD_EXIT, BUILD_OUTPUT, BUILD_VERDICT, NEW_ERRORS,
#                ERROR_CLASS, INSTALL_OK, INSTALL_METHOD, PR_TSC_EXIT,
#                PR_INSTALL_EXIT, PR_WORKTREE, AUDIT_CRITICAL, AUDIT_HIGH,
#                AUDIT_JSON, MAIN_GO_TEST_EXIT_PR, MAIN_NPM_TEST_EXIT_PR,
#                EVIDENCE_DEP_COMMAND, EVIDENCE_BUILD_COMMAND,
#                EVIDENCE_TEST_COMMAND, EVIDENCE_SMOKE_COMMAND,
#                EVIDENCE_SMOKE_OUTPUT, EVIDENCE_SMOKE_EXIT
# ──────────────────────────────────────────────────────────────────────────────

run_pr_build() {
# ── Build check on PR branch ───────────────────────────────────
BUILD_EXIT="-1"
BUILD_OUTPUT=""
BUILD_VERDICT="skip"
PR_WORKTREE="${WORKTREE_BASE}-${PR_NUM}"
AUDIT_CRITICAL=0
AUDIT_HIGH=0
# Initialize PR-level variables BEFORE the worktree check — if worktree creation
# fails (BUILD_VERDICT="error"), these are used in the Python heredoc at line ~2626.
# Without initialization, set -u would abort the script (Finding-2.12).
PR_TSC_EXIT=-1
PR_INSTALL_EXIT=0
MAIN_GO_TEST_EXIT_PR=-1
MAIN_NPM_TEST_EXIT_PR=-1
EVIDENCE_DEP_COMMAND=""
EVIDENCE_BUILD_COMMAND=""
EVIDENCE_TEST_COMMAND=""
EVIDENCE_SMOKE_COMMAND=""
EVIDENCE_SMOKE_OUTPUT=""
EVIDENCE_SMOKE_EXIT="null"

# Re-check MERGEABLE_STATUS (conflict verdict was set at line 1526 but BUILD_VERDICT
# was just reset to "skip" — so we must check the source of truth, not the overwritten var)
if [[ "$MERGEABLE_STATUS" == "CONFLICTING" ]]; then
  BUILD_VERDICT="conflict"
  echo "  Skipping build — PR has merge conflicts"
elif [[ "$ECOSYSTEM" == "npm" || "$ECOSYSTEM" == "gomod" || "$ECOSYSTEM" == "pip" ]]; then
  rm -rf "$PR_WORKTREE" 2>/dev/null || true
  git worktree remove "$PR_WORKTREE" --force 2>/dev/null || true
  git worktree prune 2>/dev/null || true
  git worktree add "$PR_WORKTREE" "origin/$PR_BRANCH" --quiet 2>"$BC_SCRATCH_DIR/_bc_wt_err_${PR_NUM}.txt" || {
    echo "  worktree: failed to create for $PR_BRANCH"
    echo "  worktree-err: $(tail -2 "$BC_SCRATCH_DIR/_bc_wt_err_${PR_NUM}.txt" 2>/dev/null | tr '\n' ' ')"
    BUILD_VERDICT="error"
  }

  if [[ -d "$PR_WORKTREE" ]]; then
    PR_INSTALL_EXIT=0
    PR_TSC_EXIT=-1
    case "$ECOSYSTEM" in
      npm)
        # For monorepos, build in the specific service/lib directory
        BUILD_DIR="$PR_WORKTREE"
        [[ "$PKG_DIR" != "/" && -d "$PR_WORKTREE/$PKG_DIR" ]] && BUILD_DIR="$PR_WORKTREE/$PKG_DIR"
        echo "  build: npm ci + tsc in ${BUILD_DIR#$PR_WORKTREE/}..."
        # Set up private registry auth if configured
        setup_private_registries "$BUILD_DIR"
        EVIDENCE_DEP_COMMAND="npm ci --ignore-scripts"
        BUILD_OUTPUT=$(cd "$BUILD_DIR" && retry_cmd 3 5 timeout $TIMEOUT npm ci --ignore-scripts 2>&1)
        PR_INSTALL_EXIT=$?
        INSTALL_METHOD="ci"
        if [[ "$PR_INSTALL_EXIT" -ne 0 ]]; then
          ERROR_CLASS=$(classify_npm_error "$BUILD_OUTPUT")
          echo "  npm ci failed ($ERROR_CLASS)"
          if [[ "$ERROR_CLASS" == "lockfile_desync" ]]; then
            echo "  trying npm install fallback..."
            rewrite_private_deps_to_local "$BUILD_DIR" "$PR_WORKTREE"
            FALLBACK_OUT=$(cd "$BUILD_DIR" && timeout $TIMEOUT npm install --ignore-scripts --legacy-peer-deps 2>&1)
            _FALLBACK_RC=$?
            EVIDENCE_DEP_COMMAND="npm ci --ignore-scripts; npm install --ignore-scripts --legacy-peer-deps"
            if [[ "$_FALLBACK_RC" -eq 0 ]]; then
              echo "  npm install fallback: SUCCESS"
              PR_INSTALL_EXIT=0
              INSTALL_METHOD="install_fallback"
              BUILD_OUTPUT="npm ci failed with ${ERROR_CLASS}; npm install fallback succeeded.
--- npm install fallback (successful) ---
$FALLBACK_OUT"
            else
              BUILD_OUTPUT="$BUILD_OUTPUT
--- npm install fallback (failed) ---
$FALLBACK_OUT"
            fi
          elif [[ "$ERROR_CLASS" == "infra_error" ]]; then
            # ── Workspace-local fallback ──
            # If the infra_error is from a private registry for packages that
            # exist locally in the monorepo (e.g., @org/auth-lib → lib/auth-lib/),
            # rewrite those deps to file: links so npm resolves them locally.
            echo "  INFRA_ERROR: trying workspace-local fallback..."
            rewrite_private_deps_to_local "$BUILD_DIR" "$PR_WORKTREE"
            FALLBACK_OUT=$(cd "$BUILD_DIR" && timeout $TIMEOUT npm install --ignore-scripts --legacy-peer-deps 2>&1)
            _FALLBACK_RC=$?
            EVIDENCE_DEP_COMMAND="npm ci --ignore-scripts; npm install --ignore-scripts --legacy-peer-deps"
            if [[ "$_FALLBACK_RC" -eq 0 ]]; then
              echo "  workspace-local fallback: SUCCESS"
              PR_INSTALL_EXIT=0
              INSTALL_METHOD="local_fallback"
              BUILD_OUTPUT="npm ci failed with ${ERROR_CLASS}; workspace-local npm install fallback succeeded.
--- npm install fallback (successful) ---
$FALLBACK_OUT"
            else
              BUILD_OUTPUT="$BUILD_OUTPUT
--- npm install fallback (failed) ---
$FALLBACK_OUT"
              INSTALL_METHOD="infra_error"
              echo "  INFRA_ERROR: registry auth failure (workspace fallback also failed)"
            fi
          fi
        fi
        BUILD_EXIT=$PR_INSTALL_EXIT
        # Track whether the package was actually installed (for confidence calibration)
        [[ "$PR_INSTALL_EXIT" -eq 0 ]] && INSTALL_OK="true"

        # npm audit — run after successful install to get security data
        AUDIT_JSON=""
        AUDIT_CRITICAL=0
        AUDIT_HIGH=0
        if [[ "$PR_INSTALL_EXIT" -eq 0 ]]; then
          AUDIT_JSON=$(cd "$BUILD_DIR" && timeout 30 npm audit --json --production 2>/dev/null || echo '{}')
          AUDIT_CRITICAL=$(echo "$AUDIT_JSON" | jq -r '.metadata.vulnerabilities.critical // 0' 2>/dev/null | awk '{s+=$1} END{print s+0}')
          AUDIT_HIGH=$(echo "$AUDIT_JSON" | jq -r '.metadata.vulnerabilities.high // 0' 2>/dev/null | awk '{s+=$1} END{print s+0}')
          [[ "$AUDIT_CRITICAL" -gt 0 || "$AUDIT_HIGH" -gt 0 ]] && echo "  npm audit: ${AUDIT_CRITICAL} critical, ${AUDIT_HIGH} high"
        fi

        if [[ "$PR_INSTALL_EXIT" -eq 0 && -f "$BUILD_DIR/tsconfig.json" ]]; then
          build_npm_workspace_libs "$PR_WORKTREE" "$TIMEOUT"
          EVIDENCE_BUILD_COMMAND="npx tsc --noEmit"
          TSC_OUT=$(cd "$BUILD_DIR" && timeout $TIMEOUT npx tsc --noEmit 2>&1)
          PR_TSC_EXIT=$?
          BUILD_EXIT=$PR_TSC_EXIT
          BUILD_OUTPUT="$BUILD_OUTPUT
--- tsc ---
$TSC_OUT"
        fi
        ;;
      gomod)
        # Sanitize Go env before each PR build (A3-3/CR3-1/CR4-2/A4-2)
        # Clear both shell env AND Go's persistent env file (root cause of V7 failure)
        unset GOSUMDB 2>/dev/null || true
        unset GONOSUMCHECK 2>/dev/null || true
        go env -u GOSUMDB 2>/dev/null || true
        go env -u GONOSUMCHECK 2>/dev/null || true
        go env -u GONOSUMDB 2>/dev/null || true
        if [[ -n "${GOPRIVATE:-}" ]]; then
          export GONOSUMDB="${GOPRIVATE}"
          go env -w GONOSUMDB="${GOPRIVATE}" 2>/dev/null || true
        fi
        if [[ "$GO_AVAILABLE" == "false" ]]; then
          echo "  build: SKIP — Go is not installed on this runner"
          BUILD_OUTPUT="SKIPPED: Go not available (go version returned error or Go not found)"
          BUILD_EXIT=0
          INSTALL_OK="true"
        elif [[ -f "$PR_WORKTREE/go.work" ]]; then
          echo "  build: go.work workspace — sync + targeted build..."
          # Supply chain integrity ensured by go.sum + default GOSUMDB (sum.golang.org).
          # Do NOT set GOSUMDB=off or GOPROXY=direct — see baseline comments for rationale.
          # Separate sync (dependency resolution) from build so INSTALL_OK tracks deps correctly.
          _GO_SYNC_OUT=""
          _GO_SYNC_EXIT=0
          EVIDENCE_DEP_COMMAND="go work sync"
          _GO_SYNC_OUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 go work sync 2>&1) || _GO_SYNC_EXIT=$?
          if [[ "$_GO_SYNC_EXIT" -eq 0 ]]; then
            INSTALL_OK="true"
            EVIDENCE_BUILD_COMMAND="go_targeted_build (timeout ${GO_TIMEOUT} go build -o /dev/null <affected packages>)"
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && {
              _BUILD_RC=0
              go_targeted_build "$FILES_IMPORTING" || _BUILD_RC=$?
              if [[ $_BUILD_RC -eq 0 ]]; then go_targeted_vet "$FILES_IMPORTING"; fi
              exit $_BUILD_RC
            } 2>&1)
            BUILD_EXIT=$?
            # Cache corruption retry: if build failed due to stale cache, clean and retry
            if [[ "$BUILD_EXIT" -ne 0 ]] && [[ "$(classify_go_error "$BUILD_OUTPUT")" == "cache_corruption" ]]; then
              echo "  ⚠ Go build cache corruption detected — cleaning cache and retrying..."
              (cd "$PR_WORKTREE" && go clean -cache 2>/dev/null || true)
              BUILD_OUTPUT=$(cd "$PR_WORKTREE" && {
                _BUILD_RC=0
                go_targeted_build "$FILES_IMPORTING" || _BUILD_RC=$?
                if [[ $_BUILD_RC -eq 0 ]]; then go_targeted_vet "$FILES_IMPORTING"; fi
                exit $_BUILD_RC
              } 2>&1)
              BUILD_EXIT=$?
              echo "  cache-clean retry: exit=$BUILD_EXIT"
            fi
          else
            # Sync failed — dependency resolution failed
            echo "  go work sync failed after 3 retries"
            BUILD_OUTPUT="$_GO_SYNC_OUT"
            BUILD_EXIT=$_GO_SYNC_EXIT
          fi
        else
          echo "  build: go mod tidy + build + vet..."
          # Supply chain integrity ensured by go.sum + default GOSUMDB (sum.golang.org).
          # Do NOT set GOSUMDB=off or GOPROXY=direct — see baseline comments for rationale.
          # Separate tidy from build so we can track dependency resolution independently.
          # go mod tidy succeeding = dependency resolution passed (INSTALL_OK=true).
          # This decouples L0 (install failed) from L2 (build failed but deps resolved).
          #
          # Multi-module fix (A2-4): run go mod tidy in the correct module directory
          # based on PKG_DIR, not always in the worktree root. In a multi-module repo,
          # the Dependabot PR modifies a sub-module's go.mod (e.g., cicd/go.mod), so
          # tidy must run there to resolve the correct dependencies.
          # Multi-module fix (A3-7): run go mod tidy in ALL modules that have
          # files importing the changed package, not just the PKG_DIR module.
          # go_targeted_build builds across all affected modules, so each needs
          # its go.sum updated by tidy. Otherwise modules that didn't get tidy'd
          # will fail on checksum verification during go build.
          _GO_TIDY_EXIT=0
          _GO_TIDY_OUT=""
          EVIDENCE_DEP_COMMAND="go mod tidy"
          if [[ "$_GO_MULTI_MODULE" == "true" ]]; then
            # Find all go.mod files and tidy each module that has importing files
            _TIDY_MODULES=$(_BC_IMPORT_JSON="$FILES_IMPORTING" _BC_PKG_DIR="$PKG_DIR" python3 "$BRK_SCRIPTS/core/pr_utils.py" tidy_modules 2>/dev/null)
            # _BC_PKG_DIR is now passed inline to the python3 invocation above (CR4-1/A4-6)
            # Run tidy in each affected module
            if [[ -n "$_TIDY_MODULES" ]]; then
              # Accumulate ALL tidy output across modules (CR4-3/CR4-4).
              # Previous code only kept the last failure's output, losing earlier errors.
              _GO_TIDY_ALL_OUT=""
              while IFS= read -r _tidy_mod; do
                [[ -z "$_tidy_mod" ]] && continue
                _tidy_dir="$PR_WORKTREE/$_tidy_mod"
                [[ "$_tidy_mod" == "." ]] && _tidy_dir="$PR_WORKTREE"
                if [[ -f "$_tidy_dir/go.mod" ]]; then
                  echo "  multi-module: go mod tidy in $_tidy_mod"
                  _mod_tidy_out=""
                  _mod_tidy_rc=0
                  _mod_tidy_out=$(cd "$_tidy_dir" && retry_cmd 3 5 timeout -k 15 120 go mod tidy 2>&1) || _mod_tidy_rc=$?
                  if [[ "$_mod_tidy_rc" -ne 0 ]]; then
                    _GO_TIDY_EXIT=$_mod_tidy_rc
                    echo "  ⚠ go mod tidy failed in $_tidy_mod (exit=$_mod_tidy_rc)"
                  fi
                  _GO_TIDY_ALL_OUT="${_GO_TIDY_ALL_OUT}
--- go mod tidy: ${_tidy_mod} (exit=${_mod_tidy_rc}) ---
${_mod_tidy_out}"
                fi
              done <<< "$_TIDY_MODULES"
              _GO_TIDY_OUT="$_GO_TIDY_ALL_OUT"
            else
              # Fallback: tidy in PKG_DIR or root
              _GO_TIDY_DIR="$PR_WORKTREE"
              if [[ "$PKG_DIR" != "/" && -f "$PR_WORKTREE/$PKG_DIR/go.mod" ]]; then
                _GO_TIDY_DIR="$PR_WORKTREE/$PKG_DIR"
                echo "  multi-module: running go mod tidy in $PKG_DIR (not root)"
              fi
              _GO_TIDY_OUT=$(cd "$_GO_TIDY_DIR" && retry_cmd 3 5 timeout -k 15 120 go mod tidy 2>&1) || _GO_TIDY_EXIT=$?
            fi
          else
            # Single-module: tidy in worktree root
            _GO_TIDY_OUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 timeout -k 15 120 go mod tidy 2>&1) || _GO_TIDY_EXIT=$?
          fi
          if [[ "$_GO_TIDY_EXIT" -eq 0 ]]; then
            INSTALL_OK="true"
            EVIDENCE_BUILD_COMMAND="go_targeted_build (timeout ${GO_TIMEOUT} go build -o /dev/null <affected packages>)"
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && go_targeted_build "$FILES_IMPORTING" 2>&1)
            BUILD_EXIT=$?
            # Cache corruption retry: if build failed due to stale cache, clean and retry
            if [[ "$BUILD_EXIT" -ne 0 ]] && [[ "$(classify_go_error "$BUILD_OUTPUT")" == "cache_corruption" ]]; then
              echo "  ⚠ Go build cache corruption detected — cleaning cache and retrying..."
              (cd "$PR_WORKTREE" && go clean -cache 2>/dev/null || true)
              BUILD_OUTPUT=$(cd "$PR_WORKTREE" && go_targeted_build "$FILES_IMPORTING" 2>&1)
              BUILD_EXIT=$?
              echo "  cache-clean retry: exit=$BUILD_EXIT"
            fi
          else
            # Tidy failed — dependency resolution failed
            BUILD_OUTPUT="$_GO_TIDY_OUT"
            BUILD_EXIT=$_GO_TIDY_EXIT
          fi
          # Run go vet if build passed
          GO_VET_OUT=""
          if [[ "$BUILD_EXIT" -eq 0 ]]; then
            GO_VET_OUT=$(cd "$PR_WORKTREE" && go_targeted_vet "$FILES_IMPORTING" 2>&1) || true
            if [[ -n "$GO_VET_OUT" ]]; then
              echo "  go vet warnings found"
              BUILD_OUTPUT="$BUILD_OUTPUT
--- go vet ---
$GO_VET_OUT"
            fi
          fi
          # Security vulnerability check
          GO_VULN_OUT=$(go_check_vulnerabilities "$PR_WORKTREE" 2>&1) || true
          # Extract ###VULN_STATUS=... sentinel, strip from displayed output
          VULN_STATUS=$(echo "$GO_VULN_OUT" | grep -oE '^###VULN_STATUS=[a-z_]+' | tail -1 | cut -d= -f2)
          [[ -z "$VULN_STATUS" ]] && VULN_STATUS="unknown"
          GO_VULN_OUT_DISPLAY=$(echo "$GO_VULN_OUT" | grep -v '^###VULN_STATUS=')
          # Extract all findings in PR worktree
          _PR_VULNS=$(echo "$GO_VULN_OUT_DISPLAY" | grep -oE 'GO-[0-9]{4}-[0-9]+' | sort -u)
          # Diff against main baseline — only NEW findings count as "introduced by this PR"
          _MAIN_VULNS=""
          [[ -f "/tmp/_bc_main_vuln_findings.txt" ]] && _MAIN_VULNS=$(cat /tmp/_bc_main_vuln_findings.txt)
          _NEW_VULNS=$(comm -23 <(echo "$_PR_VULNS" | sort -u) <(echo "$_MAIN_VULNS" | sort -u) | grep -v '^$' || true)
          _NEW_VULN_COUNT=$(echo -n "$_NEW_VULNS" | grep -c . || true)
          _PRE_VULN_COUNT=$(echo -n "$_PR_VULNS" | grep -c . || true)
          _PRE_VULN_COUNT=$((_PRE_VULN_COUNT - _NEW_VULN_COUNT))
          [[ "$_PRE_VULN_COUNT" -lt 0 ]] && _PRE_VULN_COUNT=0
          # Refine status: if PR had vulns_found but ALL were pre-existing on main,
          # treat as "ok_preexisting" — the PR itself introduces no new vulns.
          if [[ "$VULN_STATUS" == "vulns_found" && "$_NEW_VULN_COUNT" -eq 0 ]]; then
            VULN_STATUS="ok_preexisting"
            echo "  [security] PR has $_PRE_VULN_COUNT pre-existing vuln(s) also present on main — no new vulns introduced"
          elif [[ "$VULN_STATUS" == "vulns_found" && "$_NEW_VULN_COUNT" -gt 0 ]]; then
            echo "  [security] PR introduces $_NEW_VULN_COUNT NEW vuln(s) (plus $_PRE_VULN_COUNT pre-existing on main)"
          fi
          echo "$VULN_STATUS" > "/tmp/_bc_vuln_status_${PR_NUM}.txt"
          # Persist new findings (one per line) and pre-existing count
          echo "$_NEW_VULNS" > "/tmp/_bc_vuln_new_findings_${PR_NUM}.txt"
          echo "$_PRE_VULN_COUNT" > "/tmp/_bc_vuln_preexisting_count_${PR_NUM}.txt"
          # Extract first NEW vuln finding for header badge (if any)
          _VULN_FINDING=$(echo "$_NEW_VULNS" | head -1)
          [[ -n "$_VULN_FINDING" ]] && echo "$_VULN_FINDING" > "/tmp/_bc_vuln_finding_${PR_NUM}.txt" || printf '' > "/tmp/_bc_vuln_finding_${PR_NUM}.txt"
          # V9.8 iter6 (C): keep govulncheck output in its OWN variable.
          # Do NOT append to BUILD_OUTPUT — that caused vuln text to be misclassified
          # as compile errors (iter5c finding F4/P0-1). Emit dedicated vuln scan file.
          VULN_OUTPUT="$GO_VULN_OUT_DISPLAY"
          if [[ -n "$VULN_OUTPUT" ]]; then
            printf '%s' "$VULN_OUTPUT" > "/tmp/_bc_vuln_output_${PR_NUM}.txt"
          else
            printf '' > "/tmp/_bc_vuln_output_${PR_NUM}.txt"
          fi
        fi
        if [[ "$ECOSYSTEM" == "gomod" ]]; then
          if [[ -n "${_GO_TIDY_OUT:-}" ]]; then
            if [[ "${_GO_MULTI_MODULE:-false}" == "true" ]]; then
              printf 'go mod tidy (affected modules) for %s %s→%s' "$PKG" "$FROM_VER" "$TO_VER" > "$BC_SCRATCH_DIR/_bc_go_resolution_command_${PR_NUM}.txt"
            else
              printf 'go mod tidy for %s %s→%s' "$PKG" "$FROM_VER" "$TO_VER" > "$BC_SCRATCH_DIR/_bc_go_resolution_command_${PR_NUM}.txt"
            fi
            printf '%s' "$_GO_TIDY_OUT" > "$BC_SCRATCH_DIR/_bc_go_resolution_output_${PR_NUM}.txt"
            printf '%s' "${_GO_TIDY_EXIT:-null}" > "$BC_SCRATCH_DIR/_bc_go_resolution_exit_${PR_NUM}.txt"
          elif [[ -n "${_GO_SYNC_OUT:-}" ]]; then
            printf 'go work sync for %s %s→%s' "$PKG" "$FROM_VER" "$TO_VER" > "$BC_SCRATCH_DIR/_bc_go_resolution_command_${PR_NUM}.txt"
            printf '%s' "$_GO_SYNC_OUT" > "$BC_SCRATCH_DIR/_bc_go_resolution_output_${PR_NUM}.txt"
            printf '%s' "${_GO_SYNC_EXIT:-null}" > "$BC_SCRATCH_DIR/_bc_go_resolution_exit_${PR_NUM}.txt"
          fi
          _GO_MODSUM_FILES=$(cd "$PR_WORKTREE" && git --no-pager diff --name-only origin/main -- 2>/dev/null | grep -E '(^|/)go\.(mod|sum)$' || true)
          if [[ -n "$_GO_MODSUM_FILES" ]]; then
            _go_modsum_args=()
            while IFS= read -r _go_modsum_file; do
              [[ -n "$_go_modsum_file" ]] && _go_modsum_args+=("$_go_modsum_file")
            done <<< "$_GO_MODSUM_FILES"
            if [[ ${#_go_modsum_args[@]} -gt 0 ]]; then
              (cd "$PR_WORKTREE" && git --no-pager diff --unified=0 origin/main -- "${_go_modsum_args[@]}" 2>/dev/null || true) > "$BC_SCRATCH_DIR/_bc_go_modsum_diff_${PR_NUM}.txt"
            fi
          fi
        fi
        # Classify Go build error for JSON output (pass exit code for timeout detection — A2-2)
        # CR5-2: Include tidy output in classification input so infra errors (GOSUMDB,
        # network, proxy) that appeared during tidy are not lost when tidy succeeds
        # but build fails. Without this, classify_go_error only sees build output and
        # may miss the infra_error pattern, defaulting to build_fail.
        if [[ "$BUILD_EXIT" -ne 0 && "$ECOSYSTEM" == "gomod" ]]; then
          _CLASSIFY_INPUT="${_GO_TIDY_OUT:-}
${BUILD_OUTPUT}"
          ERROR_CLASS=$(classify_go_error "$_CLASSIFY_INPUT" "$BUILD_EXIT")
        fi
        ;;
      pip)
        echo "  build: pip install (isolated venv) + import check..."
        _PY_VENV_PR=$(mktemp -d /tmp/bc_venv_pr_XXXXXX)
        if python3 -m venv "$_PY_VENV_PR" 2>/dev/null; then
          _PY_PIP_PR="$_PY_VENV_PR/bin/pip"
          _PY_PYTHON_PR="$_PY_VENV_PR/bin/python"
        else
          rm -rf "$_PY_VENV_PR" 2>/dev/null || true
          _PY_VENV_PR=""
          command -v pip3 &>/dev/null && _PY_PIP_PR="pip3" || _PY_PIP_PR="pip"
          _PY_PYTHON_PR="python3"
        fi
        if [[ -f "$PR_WORKTREE/requirements.txt" ]]; then
          EVIDENCE_DEP_COMMAND="pip install -r requirements.txt --quiet"
          BUILD_OUTPUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_PR" install -r requirements.txt --quiet 2>&1)
        elif [[ -f "$PR_WORKTREE/pyproject.toml" ]]; then
          EVIDENCE_DEP_COMMAND="pip install -e . --quiet"
          BUILD_OUTPUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_PR" install -e . --quiet 2>&1)
        elif [[ -f "$PR_WORKTREE/poetry.lock" ]]; then
          # Chain with && so poetry install only runs if pip install poetry succeeds (Finding-2.8)
          EVIDENCE_DEP_COMMAND="pip install poetry --quiet && python -m poetry install --quiet"
          BUILD_OUTPUT=$(cd "$PR_WORKTREE" && {
            retry_cmd 3 5 "$_PY_PIP_PR" install poetry --quiet 2>&1 && \
            retry_cmd 3 5 "$_PY_PYTHON_PR" -m poetry install --quiet 2>&1
          })
        else
          BUILD_OUTPUT="No requirements.txt, pyproject.toml, or poetry.lock found"
        fi
        BUILD_EXIT=$?
        [[ "$BUILD_EXIT" -eq 0 ]] && INSTALL_OK="true"
        if [[ "$BUILD_EXIT" -eq 0 && -n "$PKG" ]]; then
          IMPORT_NAME=$(map_import_name "$PKG")
          EVIDENCE_BUILD_COMMAND="python -c 'import ${IMPORT_NAME}'"
          IMPORT_OUT=$(timeout 30 "$_PY_PYTHON_PR" -c "import $IMPORT_NAME" 2>&1)
          IMPORT_EXIT=$?
          if [[ "$IMPORT_EXIT" -ne 0 ]]; then
            BUILD_EXIT=$IMPORT_EXIT
            BUILD_OUTPUT="$BUILD_OUTPUT
--- import check ---
$IMPORT_OUT"
          fi
        fi
        [[ -n "$_PY_VENV_PR" ]] && rm -rf "$_PY_VENV_PR" 2>/dev/null || true
        ;;
    esac

    # Determine verdict by comparing to main baseline
    # For npm: compare install-vs-install, tsc-vs-tsc separately
    # Also detect NEW errors: if PR tsc fails AND main tsc fails, check if PR
    # introduced additional error lines not present on main.
    NEW_ERRORS=""
    if [[ "$ECOSYSTEM" == "npm" ]]; then
      # For monorepos, build lazy baseline for this directory if not done yet
      rel_pkg_dir="${PKG_DIR}"
      [[ "$rel_pkg_dir" == "/" ]] && rel_pkg_dir="."
      build_npm_baseline_for_dir "$rel_pkg_dir"
      dir_key="${rel_pkg_dir//\//_}"
      main_dir_install_exit=""
      main_dir_tsc_exit=""
      # Sanitize exit codes to pure integers — trailing whitespace or corrupt
      # file content would cause bash -gt / -ne to fail under set -u (Finding-2.6)
      main_dir_install_exit=$(cat "/tmp/_bc_main_npm_install_${dir_key}.txt" 2>/dev/null | tr -dc '0-9-' || echo "-1")
      [[ -z "$main_dir_install_exit" ]] && main_dir_install_exit="-1"
      main_dir_tsc_exit=$(cat "/tmp/_bc_main_npm_tsc_${dir_key}.txt" 2>/dev/null | tr -dc '0-9-' || echo "-1")
      [[ -z "$main_dir_tsc_exit" ]] && main_dir_tsc_exit="-1"
      main_npm_output=$(cat "/tmp/_bc_main_npm_out_${dir_key}.txt" 2>/dev/null || echo "")
      # Read tsc-specific output for error comparison (Finding-2.2).
      # _bc_main_npm_out_ contains install output; _bc_main_npm_tscout_ contains tsc output.
      # Using install output for tsc error grep yields empty results — all PR errors appear "new".
      main_npm_tsc_output=$(cat "/tmp/_bc_main_npm_tscout_${dir_key}.txt" 2>/dev/null || echo "")
      main_npm_tsc_exit=$main_dir_tsc_exit
      main_npm_install_exit=$main_dir_install_exit
      main_npm_exit=$main_dir_install_exit
      [[ "$main_dir_tsc_exit" != "-1" ]] && main_npm_exit=$main_dir_tsc_exit

      if [[ "$PR_INSTALL_EXIT" -ne 0 ]]; then
        if [[ "$main_dir_install_exit" -ne 0 && "$main_dir_install_exit" -ne -1 ]]; then
          BUILD_VERDICT="pre_existing"
        else
          BUILD_VERDICT="fail"
        fi
      elif [[ "$PR_TSC_EXIT" -gt 0 ]]; then
        if [[ "$main_dir_tsc_exit" -gt 0 ]]; then
          # Both fail — but does PR have NEW errors?
          # Extract error lines (TS format: file(line,col): error TSXXXX: message)
          # Normalize: strip worktree paths from error messages so
          # '/tmp/worktree-main/node_modules/...' and '/tmp/worktree-N/node_modules/...'
          # compare as identical (avoids false pre_existing_plus_new).
          MAIN_ERRORS_FILE="/tmp/_bc_main_tsc_errors.txt"
          PR_ERRORS_FILE="/tmp/_bc_pr_tsc_errors_${PR_NUM}.txt"
          echo "$main_npm_tsc_output" | grep -oE 'error TS[0-9]+:.*' | sed "s|${WORKTREE_BASE}[^/]*/|./|g" | sort -u > "$MAIN_ERRORS_FILE" 2>/dev/null || true
          echo "$BUILD_OUTPUT" | grep -oE 'error TS[0-9]+:.*' | sed "s|${WORKTREE_BASE}[^/]*/|./|g" | sort -u > "$PR_ERRORS_FILE" 2>/dev/null || true
          NEW_ERRORS=$(comm -23 "$PR_ERRORS_FILE" "$MAIN_ERRORS_FILE" 2>/dev/null | head -10)
          rm -f "$MAIN_ERRORS_FILE" "$PR_ERRORS_FILE"
          if [[ -n "$NEW_ERRORS" ]]; then
            BUILD_VERDICT="pre_existing_plus_new"
            echo "  ⚠ NEW tsc errors on PR branch:"
            echo "$NEW_ERRORS" | head -5 | sed 's/^/    /'
          else
            BUILD_VERDICT="pre_existing"
          fi
        else
          BUILD_VERDICT="fail"
        fi
      else
        # Check npm audit severity — CRITICAL vulnerabilities should trigger security review
        if [[ "$ECOSYSTEM" == "npm" && "$AUDIT_CRITICAL" -gt 0 ]]; then
          BUILD_VERDICT="security_review"
          echo "  ⚠️  CRITICAL vulnerabilities detected — manual security review required"
        elif [[ "$ECOSYSTEM" == "npm" && "$AUDIT_HIGH" -gt 0 && "$BUMP" == "major" ]]; then
          BUILD_VERDICT="security_review"
          echo "  ⚠️  HIGH vulnerabilities with major version bump — manual security review recommended"
        else
          BUILD_VERDICT="pass"
        fi
      fi
    else
      MAIN_EXIT="-1"
      _MAIN_OUTPUT_FOR_COMPARISON=""
      case "$ECOSYSTEM" in
        gomod)
          # For multi-module repos, use per-module baseline instead of worst_exit (A2-3/CR2-2).
          # Look up the baseline for the specific module this PR touches via PKG_DIR.
          if [[ "$_GO_MULTI_MODULE" == "true" && "$PKG_DIR" != "/" ]]; then
            _pkg_mod_key=$(echo "$PKG_DIR" | tr '/' '_')
            _pkg_mod_exit_file="/tmp/_bc_main_go_mod_exit_${_pkg_mod_key}.txt"
            _pkg_mod_output_file="/tmp/_bc_main_go_mod_output_${_pkg_mod_key}.txt"
            if [[ -f "$_pkg_mod_exit_file" ]]; then
              MAIN_EXIT=$(cat "$_pkg_mod_exit_file" 2>/dev/null || echo "$main_go_exit")
              _MAIN_OUTPUT_FOR_COMPARISON=$(cat "$_pkg_mod_output_file" 2>/dev/null || echo "$main_go_output")
              echo "  multi-module: using per-module baseline for $PKG_DIR (exit=$MAIN_EXIT)"
            else
              # PKG_DIR might be a subdirectory of a module — try the root module
              _root_exit_file="/tmp/_bc_main_go_mod_exit_..txt"
              if [[ -f "$_root_exit_file" ]]; then
                MAIN_EXIT=$(cat "$_root_exit_file" 2>/dev/null || echo "$main_go_exit")
                _MAIN_OUTPUT_FOR_COMPARISON=$(cat "/tmp/_bc_main_go_mod_output_..txt" 2>/dev/null || echo "$main_go_output")
                echo "  multi-module: PKG_DIR=$PKG_DIR not found as module, using root module baseline (exit=$MAIN_EXIT)"
              else
                MAIN_EXIT=$main_go_exit
                _MAIN_OUTPUT_FOR_COMPARISON="$main_go_output"
                echo "  multi-module: no per-module baseline for $PKG_DIR, using worst_exit=$MAIN_EXIT"
              fi
            fi
          else
            MAIN_EXIT=$main_go_exit
            _MAIN_OUTPUT_FOR_COMPARISON="$main_go_output"
          fi
          ;;
        pip)   MAIN_EXIT=$main_pip_exit; _MAIN_OUTPUT_FOR_COMPARISON="$main_pip_output" ;;
      esac

      if [[ "$BUILD_EXIT" -eq 0 ]]; then
        BUILD_VERDICT="pass"
      elif [[ "$MAIN_EXIT" -eq 0 || "$MAIN_EXIT" -eq -1 ]]; then
        # P0 FIX (V8 review C3/1.1): baseline PASSES (exit=0) or wasn't run (-1),
        # but PR build FAILS. This is a genuine regression, NOT pre-existing.
        # Previous code fell through to error text comparison which could
        # false-positive match go vet warnings in baseline output against
        # go build errors in PR output, misclassifying as pre_existing.
        #
        # F003 FIX: In multi-module repos, per-module baseline may pass (exit=0)
        # while global main build fails with pre-existing errors from other modules
        # (e.g. undefined: vcmserver.* from missing OpenAPI codegen). Check if ALL
        # PR errors also exist in the global main build output before classifying.
        if [[ "$_GO_MULTI_MODULE" == "true" && "$ECOSYSTEM" == "gomod" && "$main_go_exit" -ne 0 && -n "$BUILD_OUTPUT" ]]; then
          MAIN_ERR_FILE="/tmp/_bc_main_go_errors_global.txt"
          PR_ERR_FILE="/tmp/_bc_pr_go_errors_${PR_NUM}.txt"
          echo "$main_go_output" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$MAIN_ERR_FILE" 2>/dev/null || true
          echo "$BUILD_OUTPUT" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$PR_ERR_FILE" 2>/dev/null || true
          NEW_ERRORS=$(comm -23 "$PR_ERR_FILE" "$MAIN_ERR_FILE" 2>/dev/null | head -10)
          rm -f "$MAIN_ERR_FILE" "$PR_ERR_FILE"
          if [[ -z "$NEW_ERRORS" ]]; then
            BUILD_VERDICT="pre_existing"
            echo "  build: per-module baseline passes but all PR errors exist in global main output — pre-existing"
          else
            BUILD_VERDICT="pre_existing_plus_new"
            echo "  build: per-module baseline passes, PR has NEW errors not in main:"
            echo "$NEW_ERRORS" | head -5 | sed 's/^/    /'
          fi
        elif [[ "$_GO_MULTI_MODULE" == "true" && "$ECOSYSTEM" == "gomod" && -n "$BUILD_OUTPUT" ]]; then
          # T01 FIX: Per-module baseline passes (MAIN_EXIT=0) but targeted build
          # crosses module boundaries and hits pre-existing errors in other modules
          # (e.g. vcm-proxy OpenAPI codegen). Extract error file paths, map to their
          # module roots, check if those modules have failing per-module baselines.
          _err_paths=$(echo "$BUILD_OUTPUT" | grep -oP '[^ ]*\.go:[0-9]+' | sed 's/:[0-9]*$//' | sort -u)
          if [[ -n "$_err_paths" ]]; then
            _all_preexisting="true"
            _checked_mods=""
            while IFS= read -r _epath; do
              [[ -z "$_epath" ]] && continue
              _edir=$(dirname "$_epath" 2>/dev/null || echo ".")
              # Walk up to find go.mod (module root) for this error file
              _emr="."
              _check="$_edir"
              while [[ "$_check" != "." && "$_check" != "/" ]]; do
                if [[ -f "$_check/go.mod" ]]; then
                  _emr="$_check"
                  break
                fi
                _check=$(dirname "$_check")
              done
              # Skip if already checked this module
              echo "$_checked_mods" | grep -qF "|${_emr}|" && continue
              _checked_mods="${_checked_mods}|${_emr}|"
              # Look up per-module baseline exit
              _emr_key=$(echo "$_emr" | tr '/' '_')
              _emr_exit_file="/tmp/_bc_main_go_mod_exit_${_emr_key}.txt"
              if [[ -f "$_emr_exit_file" ]]; then
                _emr_exit=$(cat "$_emr_exit_file" 2>/dev/null || echo "0")
                if [[ "$_emr_exit" -eq 0 ]]; then
                  _all_preexisting="false"
                  break
                fi
                echo "  multi-module cross-check: errors from module $_emr (baseline exit=$_emr_exit)"
              else
                _all_preexisting="false"
                break
              fi
            done <<< "$_err_paths"
            if [[ "$_all_preexisting" == "true" && -n "$_checked_mods" ]]; then
              BUILD_VERDICT="pre_existing"
              echo "  build: per-module baseline passes but targeted build errors from other failing modules — pre-existing"
            else
              # F003 FALLBACK: Per-module baselines all pass (exit=0) but targeted build
              # crosses module boundaries and fails. Run the same targeted build on main —
              # if it also fails with the same errors, these are pre-existing cross-module errors.
              echo "  build: per-module baselines pass for error modules — running targeted build on main for comparison..."
              _MAIN_TARGETED_EXIT=0
              _MAIN_TARGETED_OUTPUT=$(cd "$MAIN_DIR" && go_targeted_build "$FILES_IMPORTING" 2>&1) || _MAIN_TARGETED_EXIT=$?
              if [[ "$_MAIN_TARGETED_EXIT" -ne 0 ]]; then
                _MAIN_T_ERR="/tmp/_bc_main_targeted_errs_${PR_NUM}.txt"
                _PR_T_ERR="/tmp/_bc_pr_targeted_errs_${PR_NUM}.txt"
                echo "$_MAIN_TARGETED_OUTPUT" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$_MAIN_T_ERR" 2>/dev/null || true
                echo "$BUILD_OUTPUT" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$_PR_T_ERR" 2>/dev/null || true
                _NEW_T_ERRORS=$(comm -23 "$_PR_T_ERR" "$_MAIN_T_ERR" 2>/dev/null | head -10)
                rm -f "$_MAIN_T_ERR" "$_PR_T_ERR"
                if [[ -z "$_NEW_T_ERRORS" ]]; then
                  BUILD_VERDICT="pre_existing"
                  echo "  build: targeted build also fails on main with same errors — pre-existing"
                else
                  BUILD_VERDICT="pre_existing_plus_new"
                  echo "  build: targeted build fails on main but PR introduces new errors:"
                  echo "$_NEW_T_ERRORS" | head -5 | sed 's/^/    /'
                fi
              else
                BUILD_VERDICT="fail"
                echo "  build: targeted build passes on main (exit=0) but fails on PR (exit=$BUILD_EXIT) — genuine failure"
              fi
            fi
          else
            BUILD_VERDICT="fail"
            echo "  build: baseline exit=$MAIN_EXIT, PR exit=$BUILD_EXIT — genuine failure (not pre-existing)"
          fi
        else
          BUILD_VERDICT="fail"
          echo "  build: baseline exit=$MAIN_EXIT, PR exit=$BUILD_EXIT — genuine failure (not pre-existing)"
        fi
      elif [[ "$MAIN_EXIT" -eq 124 ]]; then
        # Baseline timed out (A4-4). The timeout means we got PARTIAL output.
        # V9.6 FIX (P0-1): Still compare errors — if PR has NEW .go:NNN errors
        # not present in main's partial output, those are genuine regressions.
        # Example: k8s version-skew errors (undefined: metav1.*) only appear
        # on PR branch but not on main because main timed out before reaching
        # the type-check of client-go. These must be caught.
        BUILD_VERDICT="pre_existing"
        echo "  ⚠ baseline build timed out (exit=124) — comparing partial errors"
        if [[ "$ECOSYSTEM" == "gomod" && -n "$BUILD_OUTPUT" ]]; then
          MAIN_ERR_FILE="/tmp/_bc_main_go_errors.txt"
          PR_ERR_FILE="/tmp/_bc_pr_go_errors_${PR_NUM}.txt"
          echo "${_MAIN_OUTPUT_FOR_COMPARISON:-$main_go_output}" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$MAIN_ERR_FILE" 2>/dev/null || true
          echo "$BUILD_OUTPUT" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$PR_ERR_FILE" 2>/dev/null || true
          NEW_ERRORS=$(comm -23 "$PR_ERR_FILE" "$MAIN_ERR_FILE" 2>/dev/null | head -10)
          rm -f "$MAIN_ERR_FILE" "$PR_ERR_FILE"
          if [[ -n "$NEW_ERRORS" ]]; then
            BUILD_VERDICT="pre_existing_plus_new"
            echo "  ⚠ NEW errors on PR branch (not in timed-out main output):"
            echo "$NEW_ERRORS" | head -5 | sed 's/^/    /'
          fi
        fi
      elif [[ "$MAIN_EXIT" -ne 0 ]]; then
        # Both fail (main_exit > 0, not 124) — check for new errors vs baseline
        if [[ "$ECOSYSTEM" == "gomod" ]]; then
          MAIN_ERR_FILE="/tmp/_bc_main_go_errors.txt"
          PR_ERR_FILE="/tmp/_bc_pr_go_errors_${PR_NUM}.txt"
          # Use per-module baseline output for comparison instead of global main_go_output (A2-3)
          echo "${_MAIN_OUTPUT_FOR_COMPARISON:-$main_go_output}" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$MAIN_ERR_FILE" 2>/dev/null || true
          echo "$BUILD_OUTPUT"   | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$PR_ERR_FILE"   2>/dev/null || true
          NEW_ERRORS=$(comm -23 "$PR_ERR_FILE" "$MAIN_ERR_FILE" 2>/dev/null | head -10)
          rm -f "$MAIN_ERR_FILE" "$PR_ERR_FILE"
        elif [[ "$ECOSYSTEM" == "pip" ]]; then
          MAIN_ERR_FILE="/tmp/_bc_main_pip_errors.txt"
          PR_ERR_FILE="/tmp/_bc_pr_pip_errors_${PR_NUM}.txt"
          echo "$main_pip_output" | grep -iE 'error:|could not find|no matching distribution|importerror|modulenotfounderror|attributeerror|typeerror|runtimeerror|syntaxerror|command errored|setup\.py error|environment error|resolve.*failed|dependency.*conflict|unspecified satisfies requirement' | sort -u > "$MAIN_ERR_FILE" 2>/dev/null || true
          echo "$BUILD_OUTPUT" | grep -iE 'error:|could not find|no matching distribution|importerror|modulenotfounderror|attributeerror|typeerror|runtimeerror|syntaxerror|command errored|setup\.py error|environment error|resolve.*failed|dependency.*conflict|unspecified satisfies requirement' | sort -u > "$PR_ERR_FILE"   2>/dev/null || true
          NEW_ERRORS=$(comm -23 "$PR_ERR_FILE" "$MAIN_ERR_FILE" 2>/dev/null | head -10)
          rm -f "$MAIN_ERR_FILE" "$PR_ERR_FILE"
        fi
        if [[ -n "$NEW_ERRORS" ]]; then
          BUILD_VERDICT="pre_existing_plus_new"
          echo "  ⚠ NEW errors on PR branch:"
          echo "$NEW_ERRORS" | head -5 | sed 's/^/    /'
        else
          BUILD_VERDICT="pre_existing"
        fi
      else
        BUILD_VERDICT="fail"
      fi
    fi

    echo "  build: exit=$BUILD_EXIT verdict=$BUILD_VERDICT"

    # Clean up worktree
    git worktree remove "$PR_WORKTREE" --force 2>/dev/null || { chmod -R u+w "$PR_WORKTREE" 2>/dev/null; rm -rf "$PR_WORKTREE" 2>/dev/null; } || true
  fi
elif [[ "$ECOSYSTEM" == "maven" ]]; then
  rm -rf "$PR_WORKTREE" 2>/dev/null || true
  git worktree add "$PR_WORKTREE" "origin/$PR_BRANCH" --quiet 2>/dev/null || { echo "  worktree: failed"; BUILD_VERDICT="error"; }
  if [[ -d "$PR_WORKTREE" ]]; then
    BUILD_DIR="$PR_WORKTREE"
    [[ "$PKG_DIR" != "/" && -d "$PR_WORKTREE/$PKG_DIR" ]] && BUILD_DIR="$PR_WORKTREE/$PKG_DIR"
    if command -v mvn &>/dev/null; then
      echo "  build: mvn compile in ${BUILD_DIR#$PR_WORKTREE/}..."
      BUILD_OUTPUT=$(cd "$BUILD_DIR" && timeout 300 mvn compile -q 2>&1)
      BUILD_EXIT=$?
      BUILD_VERDICT=$([[ "$BUILD_EXIT" -eq 0 ]] && echo "pass" || echo "fail")
      [[ "$BUILD_EXIT" -eq 0 ]] && INSTALL_OK="true"
    else
      echo "  build: maven not available"; BUILD_VERDICT="skip"
    fi
    git worktree remove "$PR_WORKTREE" --force 2>/dev/null || { chmod -R u+w "$PR_WORKTREE" 2>/dev/null; rm -rf "$PR_WORKTREE" 2>/dev/null; } || true
  fi
elif [[ "$ECOSYSTEM" == "docker" ]]; then
  echo "  build: Docker — validating base image"
  DOCKERFILE_PATH=""
  [[ "$PKG_DIR" != "/" && -f "$PKG_DIR/Dockerfile" ]] && DOCKERFILE_PATH="$PKG_DIR/Dockerfile"
  if [[ -n "$DOCKERFILE_PATH" ]]; then
    DOCKER_BASE=$(grep -m1 "^FROM" "$DOCKERFILE_PATH" 2>/dev/null | sed 's/^FROM //;s/ .*//')
    DOCKER_CMD=$(grep -E "^(CMD|ENTRYPOINT)" "$DOCKERFILE_PATH" 2>/dev/null | tail -1)
    echo "  docker: base=$DOCKER_BASE cmd=$DOCKER_CMD"
    if command -v docker &>/dev/null; then
      if docker pull "$DOCKER_BASE" > /dev/null 2>&1; then
        BUILD_OUTPUT="Dockerfile: $DOCKERFILE_PATH Base: $DOCKER_BASE CMD: $DOCKER_CMD"
        BUILD_EXIT=0
        BUILD_VERDICT="pass"
        INSTALL_OK="true"
      else
        BUILD_OUTPUT="Dockerfile: $DOCKERFILE_PATH Base: $DOCKER_BASE — image pull failed"
        BUILD_EXIT=1
        BUILD_VERDICT="fail"
      fi
    else
      BUILD_OUTPUT="Dockerfile: $DOCKERFILE_PATH Base: $DOCKER_BASE CMD: $DOCKER_CMD (docker not available)"
      BUILD_EXIT=-1
      BUILD_VERDICT="skip"
    fi
  else
    BUILD_OUTPUT="Dockerfile not found for $PKG_DIR"
    BUILD_EXIT=-1
    BUILD_VERDICT="skip"
  fi
elif [[ "$ECOSYSTEM" == "actions" ]]; then
  # GitHub Actions PRs only affect .github/workflows/ files — no application code.
  # They are inherently safe and need no build verification. Setting unconditionally SAFE
  # instead of trying to validate via GitHub API (which fails for non-actions/* orgs
  # and used a regex that never matched Dependabot PR title format).
  echo "  build: GitHub Actions — CI-only change, inherently safe"
  BUILD_OUTPUT="actions: ${PKG} ${FROM_VER:-?} → ${TO_VER:-?} — CI-only dependency, no build needed"
  BUILD_EXIT=0
  BUILD_VERDICT="pass"
  INSTALL_OK="true"
else
  echo "  build: skipped ($ECOSYSTEM — no build possible)"

fi
}
