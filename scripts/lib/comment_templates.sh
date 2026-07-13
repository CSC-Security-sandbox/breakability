#!/usr/bin/env bash
# comment_templates.sh — dispatch_comment_template function
# Extracted from post-fallback-comments.sh to reduce file size.
# Reads globals: VERDICT, ECOSYSTEM, _CI_TIER, BUMP, FILES_COUNT, OOM_OVERRIDE,
#   DEP_REL, VER_LABEL, NEW_ERR_COUNT, HOW_CHECKED, COMMENT, plus all block variables
#   from build_cve_blocks/build_evidence_blocks/build_checklist_blocks.
# Writes: COMMENT (and may echo + continue for skipped/cancelled verdicts).

dispatch_comment_template() {
  # ── Classify and generate comment ─────────────────────────────────────────
  COMMENT=""

  # PRs with breakability:skip label were intentionally excluded from analysis.
  # Don't post any comment — the developer already opted out of analysis.
  if [[ "$VERDICT" == "skipped" ]]; then
    echo "  PR #$PR_NUM has breakability:skip label — skipping fallback comment"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # V8 FIX (C2): Cancelled PRs already have a comment posted above
  if [[ "$VERDICT" == "cancelled" ]]; then
    echo "  PR #$PR_NUM was cancelled — comment already posted"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # V9.8 iter6 A (security verdict gate): a PR that INTRODUCES new vulnerabilities
  # (vuln_status=vulns_found with non-empty vuln_new_findings) must NEVER be SAFE,
  # regardless of build status. Demote pass → vulns_introduced so the dispatch
  # chain renders the security-risk comment instead of SAFE.
  # Pre-existing-only vulns (ok_preexisting) do NOT demote — PR is not at fault.
  if [[ "$VULN_STATUS" == "vulns_found" && "$VULN_NEW_COUNT" -gt 0 ]]; then
    if [[ "$VERDICT" != "vulns_introduced" ]]; then
      echo "  PR #$PR_NUM: demoting VERDICT=$VERDICT → vulns_introduced ($VULN_NEW_COUNT new CVE(s))"
      VERDICT="vulns_introduced"
    fi
  fi

  if [[ "$VERDICT" == "pre_existing" ]]; then
    _GUARD_BUILD_BADGE="⚠️ pre-existing failures (not introduced by this PR)"
  else
    _GUARD_BUILD_BADGE="✅ passes"
  fi
  if [[ ( "$MERGE_RISK_TAG" == "High" || ( "$_DECL_BEHAVIORAL_REVIEW" == "1" && ( "$VERDICT" == "pass" || "$VERDICT" == "pre_existing" ) ) ) && "$VERDICT" != "fail" && "$VERDICT" != "vulns_introduced" && "$VERDICT" != "security_review" && "$VERDICT" != "pre_existing_plus_new" ]]; then
    # FALSE-SAFE GUARD (global): a High merge-risk signal (e.g. a maintainer-declared breaking
    # change) is structurally unverifiable by build/test/apidiff. Pre-empt EVERY green
    # headline below (actions/docker SAFE, pass, pre_existing) — a green build must NOT clear
    # this PR. Hard-fail/security verdicts keep their own stronger BLOCKED messaging.
    # Two grades share this headline: High → "⛔ REVIEW REQUIRED" (confirmed/unverifiable break);
    # Medium import-reachable behavioral declaration → "⚠️ REVIEW SUGGESTED" (not a confirmed break,
    # but a green build must not silently say "merge when ready").
    if [[ "$MERGE_RISK_TAG" == "High" ]]; then
      _REVIEW_TITLE="⛔ REVIEW REQUIRED"
      _REVIEW_WHY="Build and tests pass on the PR branch, but that does **not** clear this upgrade: ${MERGE_RISK_REASON}. Behavioral changes (changed defaults, error/ordering semantics) and breaks in sibling or transitive modules are invisible to compilation and to existing tests.${_REVIEW_WHY_TAIL}"
    else
      _REVIEW_TITLE="⚠️ REVIEW SUGGESTED"
      _REVIEW_WHY="Build and tests pass on the PR branch — but the maintainer **declares a behavioral breaking change** and your code imports the affected package. Behavioral changes (changed defaults, error/ordering semantics) are invisible to compilation, tests, and API-diff, so this is a **review signal, not a confirmed break**. The behavioral verdict below grades your actual exposure, and the exact import site is in the reachability block."
    fi
    COMMENT="<!-- breakability-check -->
## ${_REVIEW_TITLE} — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ${_GUARD_BUILD_BADGE} · Verification: **${VER_LABEL:-L2}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

${MERGE_RISK_LINE}

### Why a passing build is not enough here
${_REVIEW_WHY}${CHANGELOG_INLINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ ( "$ECOSYSTEM" == "actions" || "$ECOSYSTEM" == "docker" ) && -n "$_CI_TIER" ]]; then
    # CI dependency that is NOT auto-safe: security-sensitive (secsens) or a non-sensitive major.
    if [[ "$_CI_TIER" == "secsens" ]]; then
      _CI_REVIEW_TITLE="🔐 REVIEW — supply-chain sensitive"
      _CI_REVIEW_WHY="This CI dependency handles **tokens, credentials, registry/cloud auth, code signing, or deployment/publishing**. A breaking change or a compromised release here is a **supply-chain risk** — the class of dependency an attacker most wants merged unread. \"CI-only\" does **not** mean \"safe\" here."
      _CI_REVIEW_CHECK="- **Pin to a full commit SHA** (not a moving tag) so a re-tagged release can't silently change what runs.
- Review the **release notes / changelog** for changed **permissions**, token scopes, or inputs.
- Confirm the publisher and that the new version is the official release."
      _CI_FOOT="CI dependency flagged supply-chain sensitive; not auto-cleared"
    else
      _CI_REVIEW_TITLE="🟡 REVIEW — major CI action bump"
      _CI_REVIEW_WHY="This is a **major** version bump of a CI action. Major bumps routinely change inputs, runtime defaults, or output names and can **break your workflow** — even though no application code is affected. This is a **breakability glance, not a security flag**."
      _CI_REVIEW_CHECK="- Skim the **release notes / changelog** for breaking input/output or runtime changes.
- Optionally pin to a full commit SHA."
      _CI_FOOT="major CI action bump; quick changelog review suggested"
    fi
    COMMENT="<!-- breakability-check -->
## ${_CI_REVIEW_TITLE} — \`$PKG\` $FROM → $TO · dev (CI) · $BUMP_DISPLAY

${_CI_REVIEW_WHY}

### What to check before merging
${_CI_REVIEW_CHECK}${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — ${_CI_FOOT}*"

  elif [[ "$ECOSYSTEM" == "actions" ]]; then
    # GitHub Actions — always safe, no app code affected.
    # No L0/fallback labels — CI-only changes need no build verification (end-user feedback 2.4).
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · dev (CI) · $BUMP_DISPLAY

GitHub Actions workflow dependency. No application code affected. No build verification needed.${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — CI-only change, no build impact*"

  elif [[ "$ECOSYSTEM" == "docker" && "$BUMP" != "major" ]]; then
    # Docker non-major — typically safe
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · production · $BUMP_DISPLAY

Docker base image $BUMP_DISPLAY bump. No application source changes.${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pass" && "$OOM_OVERRIDE" == "True" ]]; then
    # V9.3: OOM override — build was killed by OOM on unrelated sub-packages.
    # The PR's own targeted packages were not affected.
    _OOM_PKG_NOTE=""
    if [[ -n "$OOM_PACKAGES" ]]; then
      _OOM_PKG_LIST=$(echo "$OOM_PACKAGES" | tr ',' '\n' | sed 's/^/  - /' | head -5)
      _OOM_PKG_NOTE="

OOM-killed sub-packages (unrelated to this upgrade):
\`\`\`
${_OOM_PKG_LIST}
\`\`\`"
    fi
    _DEV_DEP_NOTE=""
    if [[ "$FILES_COUNT" -eq 0 ]]; then
      _DEV_DEP_NOTE=" · ⚙️ 0 direct imports (dev/indirect dependency)"
    fi
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ infra OOM on unrelated sub-packages — not caused by this upgrade · Verification: **${VER_LABEL:-L2}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}${_DEV_DEP_NOTE}${CVE_LINE}${FIXES_CVE_LINE}

### What this means
The CI runner ran out of memory (\`signal: killed\`) building sub-packages unrelated to \`$PKG\`. This PR's targeted packages are not affected. The same OOM occurs on \`main\` — it is an infrastructure limitation, not a code regression.${_OOM_PKG_NOTE}

**Recommendation:** Safe to merge. The OOM is a CI runner memory issue, not caused by this $BUMP_DISPLAY bump.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pass" && "$BUMP" == "patch" && "$FILES_COUNT" -lt 5 ]]; then
    # Patch bump, build passes, low usage surface — simple safe
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · patch

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}

$BUMP_DISPLAY bump with passing build. No new type errors introduced.${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pass" && "$DEP_REL" == "transitive" ]]; then
    # Transitive dep, build passes
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · transitive · $BUMP_DISPLAY

Build: ✅ passes · Verification: **${VER_LABEL:-L1}**

Transitive dependency — your code does not import it directly. Build passes.${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pass" ]]; then
    # Build passes — general case
    NEW_ERR_NOTE=""
    if [[ "$NEW_ERR_COUNT" -gt 0 ]]; then
      NEW_ERR_NOTE=" · ⚠️ $NEW_ERR_COUNT new error(s) found"
    fi
    COMMENT="<!-- breakability-check -->
## 🔍 BUILD ANALYSIS — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}$NEW_ERR_NOTE${CVE_LINE}${FIXES_CVE_LINE}

### Summary (deterministic analysis)
- Package: \`$PKG\` $FROM → $TO ($BUMP_DISPLAY bump)
- Type: $DEP_TYPE / $DEP_REL
- Build passes on PR branch
- New type errors: $NEW_ERR_COUNT

**Recommendation:** Review changelog for $BUMP_DISPLAY bump breaking changes. Build passes — merge when ready.${CHANGELOG_INLINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "fail" ]]; then
    # Build fails — most important fallback to get right
    EXCERPT_BLOCK=""
    if [[ -n "$BUILD_EXCERPT" ]]; then
      EXCERPT_BLOCK="
\`\`\`
${BUILD_EXCERPT}
\`\`\`"
    fi
    # P0 (reviewer): a BUILD_FAILS verdict on a PR where NO source file imports the
    # upgraded dependency (Usage: 0 file(s)) is suspicious. Generic toolchain errors
    # like "no import data available" are NOT attributable to this upgrade. Flag the
    # likely false-positive instead of confidently telling the dev "do not merge".
    _FALSE_POSITIVE_NOTE=""
    _GENERIC_TOOLCHAIN_ERR=0
    if echo "$BUILD_EXCERPT" | grep -qiE "no import data available|no required module provides|missing go\.sum entry|cannot find module"; then
      _GENERIC_TOOLCHAIN_ERR=1
    fi
    if [[ "$FILES_COUNT" == "0" ]]; then
      if [[ "$_GENERIC_TOOLCHAIN_ERR" == "1" ]]; then
        _FALSE_POSITIVE_NOTE="

> ⚠️ **Likely false positive.** No source file in this repo imports \`$PKG\`, and the failure above is a generic Go toolchain message (not a type/compile error attributable to this upgrade). This is most likely a build-cache/module-resolution artifact, **not** a real break caused by the bump. Verify locally with \`go build ./...\` on a clean checkout before treating this as blocking."
      else
        _FALSE_POSITIVE_NOTE="

> ⚠️ **Note:** No source file directly imports \`$PKG\` (Usage: 0 file(s)). If the error below is in an unrelated package, this failure may not be caused by this upgrade — confirm the failing package is actually affected before blocking the merge."
      fi
    fi
    COMMENT="<!-- breakability-check -->
## ❌ BUILD_FAILS — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ❌ fails on PR branch, ✅ passes on main · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${CVE_LINE}${FIXES_CVE_LINE}${_FALSE_POSITIVE_NOTE}

### Build errors (excerpt)$EXCERPT_BLOCK

### What to do
1. Check the full build output in the Actions run for this PR
2. Review the \`$PKG\` $FROM → $TO changelog for breaking changes${CHANGELOG_INLINE}
3. Fix type errors or update your code to match the new API
4. Re-run the breakability analysis after your fix

**Do not merge — build is broken.** ($BUMP_DISPLAY bump)${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pre_existing" ]]; then
    # Pre-existing failures — split on verification level (Finding-3.3, A2-8).
    # L2+ means tsc/go-build actually passed (identical errors = no new problems) → SAFE.
    # L1 means deps resolved but build inconclusive → LIKELY SAFE.
    # L0 means deps didn't even resolve → UNVERIFIED (do NOT say "LIKELY SAFE").
    if [[ "$VER_LABEL" == L2* || "$VER_LABEL" == L3* || "$VER_LABEL" == L4* || "$VER_LABEL" == L5* ]]; then
      COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ verified — same result as main baseline, not caused by this change · Verification: **${VER_LABEL}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

### What this means
The build produces the same errors on both \`main\` and this PR branch. This upgrade does **not** introduce new failures. Verified at **${VER_LABEL}**.

**Recommendation:** Safe to merge. Pre-existing build issues are unrelated to this upgrade.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
    elif [[ "$VER_LABEL" == L1* ]]; then
      # L1: dependency resolution passed but build/type-check inconclusive
      COMMENT="<!-- breakability-check -->
## ⚙️ LIKELY SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ⚙️ same errors on main and PR branch — pre-existing failure, **not caused by this upgrade** · Verification: **${VER_LABEL}**${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

### What this means
Dependencies resolved successfully. The build fails on both \`main\` and this PR with the same errors. This upgrade does **not** introduce new failures. Full build verification was limited by pre-existing issues on \`main\`.

**Recommendation:** Likely safe to merge — no new errors detected. Fix pre-existing build failures on \`main\` for full verification coverage.${_TIMEOUT_CAVEAT}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
    else
      # L0: dependency resolution failed or build inconclusive.
      # End-user feedback (P1): "UNVERIFIED" is misleading when the tool DID compare
      # both branches and found zero new errors. That IS a safety signal.
      # Use "LIKELY SAFE" when zero new errors detected, "UNVERIFIED" only when
      # we truly have no signal (e.g., install_ok=false with no comparison done).
      if [[ "$NEW_ERR_COUNT" -eq 0 ]]; then
        COMMENT="<!-- breakability-check -->
## ⚙️ LIKELY SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ⚙️ same errors on \`main\` and PR branch — **not caused by this upgrade** · Verification: **${VER_LABEL:-L0}**${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

### What this means
Both \`main\` and this PR branch produce the same build errors. This upgrade does **not** introduce new failures. Build verification was limited by pre-existing infrastructure issues.

**Recommendation:** Likely safe to merge — zero new errors detected. Fix baseline build on \`main\` for full verification.${_TIMEOUT_CAVEAT}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
      else
        COMMENT="<!-- breakability-check -->
## ⚠️ UNVERIFIED — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ⚠️ build verification could not complete — infrastructure/configuration errors · Verification: **${VER_LABEL:-L0}**${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

### What to do
1. Fix the baseline build on \`main\` (see merge plan for error details)
2. Re-run analysis: \`gh workflow run breakability-agent.yml\`

**Recommendation:** Cannot confirm safety. Fix build environment first, then re-analyze.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
      fi
    fi

  elif [[ "$VERDICT" == "pre_existing_plus_new" ]]; then
    # Pre-existing + new errors
    COMMENT="<!-- breakability-check -->
## ❌ BUILD_FAILS — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ❌ new errors introduced by this PR (on top of pre-existing failures)${CVE_LINE}${FIXES_CVE_LINE}

This upgrade introduces **$NEW_ERR_COUNT new error(s)** not present on \`main\`. Fix required before merging.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "security_review" ]]; then
    # Build passes but npm audit found CRITICAL/HIGH vulnerabilities
    COMMENT="<!-- breakability-check -->
## ⚠️ SECURITY REVIEW — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${CVE_LINE}${FIXES_CVE_LINE}

### Security concern
Build passes, but \`npm audit\` found **critical or high** vulnerabilities in this upgrade. Manual security review recommended before merging.

**Recommendation:** Review the npm audit output and CVE details. If vulnerabilities are in transitive deps not used by your code, merge may still be safe.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "vulns_introduced" ]]; then
    # V9.8 iter6 (A): PR build passes but govulncheck found NEW CVE(s) not on main.
    # This overrides SAFE because a PR that introduces vulnerabilities is never SAFE.
    _VULN_IDS_LIST="${VULN_NEW_LIST:-unknown}"
    # G4: Extract per-finding reachability info from govulncheck output.
    _VULN_REACHABILITY=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || echo "❓ CVE reachability unknown (hint only) — run \`govulncheck ./...\` locally to check"
import json, os, re
d = json.loads(os.environ["_BC_PRF"])
output = d.get('vuln_output', '') or ''
new_ids = d.get('vuln_new_findings', []) or []
details = d.get('cve_details', []) or []
sev_by_id = {}
for item in details:
    if not isinstance(item, dict):
        continue
    ids = [item.get('cve_id'), item.get('ghsa_id'), item.get('id')]
    sev = (item.get('severity') or item.get('cvss_severity') or '').upper()
    for k in ids:
        if k and sev:
            sev_by_id[k] = sev

def section_of(pos):
    sym = output.rfind('=== Symbol Results ===', 0, pos)
    pkg = output.rfind('=== Package Results ===', 0, pos)
    mod = output.rfind('=== Module Results ===', 0, pos)
    best = max(sym, pkg, mod)
    if best < 0 or best == sym:
        return 'symbol'
    return 'imported'

def block_for(vid):
    idx = output.find(vid)
    if idx < 0:
        return '', 'unknown'
    nexts = [p for p in [output.find('Vulnerability #', idx + len(vid)), output.find('GO-', idx + len(vid))] if p > idx]
    end = min(nexts) if nexts else len(output)
    return output[idx:end], section_of(idx)

def chain(block):
    vals = []
    for pat in [r'#\d+:\s+[^:]+:\d+:\d+:\s*(.+)', r'\b([\w./-]+\.[\w*]+)\s+calls\s+([\w./-]+\.[\w*]+)']:
        for m in re.findall(pat, block):
            if isinstance(m, tuple):
                vals.append(' → '.join(x for x in m if x))
            else:
                vals.append(m.strip())
    return vals[:4]

lines = []
for vid in new_ids:
    block, sect = block_for(vid)
    aliases = sorted(set(re.findall(r'CVE-\d{4}-\d+|GHSA-[0-9a-z-]+', block, re.I)))
    sev = next((sev_by_id.get(x) for x in [vid]+aliases if sev_by_id.get(x)), 'UNKNOWN')
    cve_label = ', '.join(aliases) if aliases else vid
    lines.append(f'- **{cve_label}** (`{vid}`) · Severity: **{sev}**')
    if sect == 'symbol' and block:
        ch = chain(block)
        if ch:
            lines.append('  - CVE exploitability reachability (hint only): reachable from your code — ' + ' → '.join(f'`{x}`' for x in ch))
        else:
            lines.append('  - CVE exploitability reachability (hint only): reachable from your code (govulncheck Symbol Results; call-chain text not emitted)')
    elif sect == 'imported':
        lines.append('  - CVE exploitability reachability (hint only): no reachable path found in govulncheck Symbol Results; this is not safe-to-ignore evidence')
    else:
        lines.append('  - CVE exploitability reachability (hint only): not determined in govulncheck output; run `govulncheck ./...` locally to confirm')
    lines.append('  - Does merging this PR fix it? **No** — absent on `main`, present on this PR branch (introduced by the upgrade).')
if not lines:
    lines.append('❓ CVE reachability unknown (hint only) — govulncheck produced no per-finding call-graph data; run `govulncheck ./...` locally to check')
print('\n'.join(lines))
PYEOF
)
    # EU-6: Get max severity for the new vulns
    _VULN_SEVERITY_NOTE=""
    if [[ -n "$CVE_MAX_SEVERITY" ]]; then
      _VULN_SEVERITY_NOTE=" · Severity: **${CVE_MAX_SEVERITY}**"
    fi
    COMMENT="<!-- breakability-check -->
## 🚨 SECURITY RISK — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${_VULN_SEVERITY_NOTE}${CVE_LINE}${FIXES_CVE_LINE}

### 🚨 This PR introduces **$VULN_NEW_COUNT NEW vulnerability(ies)** not present on \`main\`

**New CVEs:** $_VULN_IDS_LIST
### Heads-up: CVE reachability (hint only)
> Snyk-style caveat: **no reachable path found** means “not observed in this scan”, not “safe to ignore”.
${_VULN_REACHABILITY}

Pre-existing on main: $VULN_PREEXISTING_COUNT (unaffected by this PR).

**Recommendation:** Do **NOT** merge until these vulnerabilities are addressed. Options:
1. Bump to a later fixed version that patches these CVEs, or
2. Close this PR and wait for an upstream fix, or
3. Treat any "no reachable path found" result as a prioritization hint only, not as permission to ignore the introduced CVE.
${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — govulncheck diffed against \`main\` baseline*"

  elif [[ "$INSTALL_METHOD" == "infra_error" ]]; then
    # Infrastructure blocked analysis
    COMMENT="<!-- breakability-check -->
## 🔍 REVIEW — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ⚠️ blocked by infrastructure error — build verification could not run${CVE_LINE}${FIXES_CVE_LINE}

### What happened
The build check was blocked by an infrastructure issue (private registry, network timeout, or missing dependency not caused by this upgrade). **This is not a build failure from the upgrade.**

**Recommendation:** Verify infrastructure health, then re-run. If infrastructure is healthy, review manually.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "conflict" ]]; then
    # Conflicted PR — cannot merge or analyze until rebased (Finding-3.6)
    COMMENT="<!-- breakability-check -->
## ⚠️ CONFLICTED — \`$PKG\` $FROM → $TO — rebase required

This PR has merge conflicts and cannot be merged or analyzed until rebased.
Run \`@dependabot recreate\` or rebase manually.${PLAN_LINE}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  else
    # Catch-all: skip/unknown verdict
    COMMENT="<!-- breakability-check -->
## 🔍 REVIEW — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build analysis status: \`$VERDICT\` (verification: ${VER_LABEL:-unknown})${CVE_LINE}${FIXES_CVE_LINE}

Automated build analysis was not conclusive for this PR. Manual review recommended.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
  fi
}
