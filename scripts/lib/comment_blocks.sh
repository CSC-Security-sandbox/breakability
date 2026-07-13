#!/usr/bin/env bash
# Evidence block builder functions for post-fallback-comments.sh.
# Sourced by post-fallback-comments.sh — do not run directly.
#
# All functions read/write caller-scope globals (bash default).
# Required globals are documented in each function header.

# ── build_cve_blocks ─────────────────────────────────────────────────────────
# Reads:  PR_FIELDS, PR_NUM, ECOSYSTEM, PKG_DIR
# Sets:   CVE_LIST, CVE_COUNT, CVE_MAX_SEVERITY, CVE_LINE, CVE_DETAIL_BLOCK,
#         MODULE_LINE, FIXES_CVE_LINE, _FIXES_CVE_DATA
build_cve_blocks() {
  # CVE extraction — core security data
  CVE_LIST=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
cves = d.get('cves', [])
if cves:
    print(','.join(cves))
else:
    print('')
" 2>/dev/null || echo "")
  CVE_COUNT=$(echo "$PR_FIELDS" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('cves',[])))")

  # Extract CVE severity info (end-user P1: make CVEs impossible to miss)
  CVE_MAX_SEVERITY=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
sevs = d.get('cve_severities', [])
if not sevs:
    print('')
else:
    order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    worst = min(sevs, key=lambda s: order.get(s.lower(), 99))
    print(worst.upper())
" 2>/dev/null || echo "")

  # V8 FIX: Build enriched security line with severity, CVSS, and advisory links
  CVE_LINE=""
  CVE_DETAIL_BLOCK=""
  if [[ "$CVE_COUNT" -gt 0 && "$CVE_COUNT" != "0" ]]; then
    # Build per-CVE detail lines with severity and advisory link
    CVE_DETAIL_BLOCK=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
# Look up cve_details from build-results.json (richer than PR_FIELDS inline)
pr_num = '$PR_NUM'
try:
    with open('/tmp/build-results.json') as f:
        results = json.load(f)
    details = results.get('prs', {}).get(pr_num, {}).get('cve_details', [])
except:
    details = []
if not details:
    sys.exit(0)
for det in details:
    _id = det.get('id', '?')
    sev = det.get('severity', 'unknown').upper()
    cvss = det.get('cvss_score')
    summary = det.get('summary', '')
    url = det.get('advisory_url', '')
    line = f'- **{_id}** ({sev}'
    if cvss:
        line += f', CVSS {cvss}'
    line += ')'
    if summary:
        line += f': {summary}'
    if url:
        line += f' — [advisory]({url})'
    print(line)
" 2>/dev/null || echo "")

    if [[ -n "$CVE_MAX_SEVERITY" ]]; then
      CVE_LINE="
🔴 **Security ($CVE_MAX_SEVERITY): $CVE_COUNT CVE(s) fixed by this upgrade:**"
    else
      CVE_LINE="
🔴 **Security: $CVE_COUNT CVE(s) fixed by this upgrade:** $CVE_LIST"
    fi
    if [[ -n "$CVE_DETAIL_BLOCK" ]]; then
      CVE_LINE="$CVE_LINE
$CVE_DETAIL_BLOCK"
    else
      CVE_LINE="$CVE_LINE $CVE_LIST"
    fi
  fi

  # Module line for monorepo context (end-user feedback: which module does this affect?)
  MODULE_LINE=""
  if [[ "$ECOSYSTEM" == "gomod" && "$PKG_DIR" != "/" && -n "$PKG_DIR" ]]; then
    MODULE_LINE=" · Module: \`$PKG_DIR\`"
  fi

  # V9.9 iter8: Show CVEs this PR fixes (from Dependabot alert matching) even if
  # the PR body doesn't mention them. This ensures PR #19 etc. show the fix.
  FIXES_CVE_LINE=""
  _FIXES_CVE_DATA=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
fixes = d.get('fixes_cves', [])
if not fixes:
    sys.exit(0)
sev_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
fixes.sort(key=lambda x: sev_order.get((x.get('severity') or '').lower(), 9))
parts = []
for f in fixes:
    cve = f.get('cve_id', '?')
    sev = (f.get('severity') or 'unknown').upper()
    parts.append(f'{cve} ({sev})')
print(' · '.join(parts))
" 2>/dev/null || echo "")
  if [[ -n "$_FIXES_CVE_DATA" && -z "$CVE_LINE" ]]; then
    # Only add if CVE_LINE is empty (PR body didn't have CVEs)
    FIXES_CVE_LINE="
🛡️ **This PR fixes known vulnerabilities:** $_FIXES_CVE_DATA — **merge with priority** (CVE reachability is a hint only)"
  elif [[ -n "$_FIXES_CVE_DATA" && -n "$CVE_LINE" ]]; then
    # PR body already has CVEs, append Dependabot-matched ones if different
    FIXES_CVE_LINE=""
  fi
}

# ── build_evidence_blocks ────────────────────────────────────────────────────
# Reads:  PR_FIELDS, ECOSYSTEM, PKG, FROM, TO, MERGE_RISK_TAG, MERGE_RISK_REASON,
#         MERGE_RISK_EVIDENCE, MERGE_RISK_BUILD_VERIFICATION, VER_LABEL,
#         MERGE_RISK_ORACLE_CONFIDENCE, FILES_LIST, FILES_COUNT
# Sets:   CHANGELOG_LINK, CHANGELOG_BLOCK, MERGE_RISK_LINE, _FILES_DETAIL_BLOCK,
#         _USAGE_CONTEXT_BLOCK, _USAGE_CONTEXT_INLINE, _DECLARED_BREAK_REACH_BLOCK,
#         _DECL_BEHAVIORAL_REVIEW
build_evidence_blocks() {
  # V9.9 iter9/G2: Changelog evidence for Go/npm packages
  # G6: prefer the persisted deterministic changelog analysis (one source of truth with the
  # verdict); fall back to the live GitHub re-fetch only for legacy records lacking it.
  CHANGELOG_LINK=""
  CHANGELOG_BLOCK=""
  CHANGELOG_BLOCK=$(build_changelog_block_persisted "$PR_FIELDS")
  if [[ -z "$CHANGELOG_BLOCK" && "$ECOSYSTEM" == "gomod" && -n "$PKG" && -n "$FROM" && -n "$TO" ]]; then
    CHANGELOG_BLOCK=$(build_go_changelog_block "$PKG" "$FROM" "$TO")
  fi
  if [[ -n "$CHANGELOG_BLOCK" ]]; then
    CHANGELOG_LINK="${CHANGELOG_BLOCK}"
  elif [[ "$ECOSYSTEM" == "npm" && -n "$PKG" && -n "$FROM" && -n "$TO" ]]; then
    # npm: try npmjs changelog
    CHANGELOG_LINK="
📝 [Changelog](https://github.com/search?q=repo%3A${PKG}+path%3ACHANGELOG&type=code)"
  fi

  MERGE_RISK_TAG="${MERGE_RISK_TAG:-Medium}"
  MERGE_RISK_REASON="${MERGE_RISK_REASON:-change evidence is limited; default caution}"
  MERGE_RISK_EVIDENCE="${MERGE_RISK_EVIDENCE:-limited evidence}"
  MERGE_RISK_BUILD_VERIFICATION="${MERGE_RISK_BUILD_VERIFICATION:-${VER_LABEL:-unverified}}"
  MERGE_RISK_LINE="**Merge Risk: ${MERGE_RISK_TAG}** (Evidence: ${MERGE_RISK_EVIDENCE} × Build verification: ${MERGE_RISK_BUILD_VERIFICATION} × Oracle confidence: ${MERGE_RISK_ORACLE_CONFIDENCE:-not available}) — ${MERGE_RISK_REASON}"

    # Build "How we checked" checklist from verification_label
  # Build file-list detail block for evidence
  _FILES_DETAIL_BLOCK=""
  if [[ -n "$FILES_LIST" && "$FILES_LIST" != "" ]]; then
    _SHOWN_FILES=$(echo "$FILES_LIST" | tr '|' '\n' | head -8 | sed 's/^/- `/' | sed 's/$/`/')
    _TOTAL_FILES="${FILES_COUNT:-0}"
    _SHOWN_COUNT=$(echo "$FILES_LIST" | tr '|' '\n' | head -8 | wc -l | tr -d ' ')
    _MORE_NOTE=""
    if [[ "$_TOTAL_FILES" -gt "$_SHOWN_COUNT" ]]; then
      _MORE_NOTE="
- *...and $((_TOTAL_FILES - _SHOWN_COUNT)) more file(s) — see full import graph in Actions run*"
    fi
    _FILES_DETAIL_BLOCK="
<details><summary>📂 Files importing this package ($FILES_COUNT file(s))</summary>

${_SHOWN_FILES}${_MORE_NOTE}
</details>"
  fi
  _USAGE_CONTEXT_INLINE=""
  _USAGE_CONTEXT_BLOCK=""
  if [[ "$ECOSYSTEM" == "gomod" ]]; then
    _USAGE_CONTEXT_BLOCK=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || true
import json, os, sys
d = json.loads(os.environ["_BC_PRF"])
det = (d.get('deterministic') or {})
usages = (det.get('usages') or [])
active = [u for u in usages if u.get('usageType') in ('DIRECT_CALL', 'PROPERTY_ACCESS')]
if not active:
    sys.exit(0)
# The authoritative set of symbols that ACTUALLY changed in this dependency comes from apidiff
# (api_changes_detail), NOT from the broad usage scan (which also picks up stdlib/other-package
# symbols in the same files). Reachability must be the intersection of the two.
changed_detail = (det.get('api_changes_detail') or [])
changed_syms = []
sym_change_type = {}
for c in changed_detail:
    if isinstance(c, dict):
        s = (c.get('symbol') or c.get('name') or '').strip()
        if s and s not in changed_syms:
            changed_syms.append(s)
        if s:
            sym_change_type[s] = (c.get('changeType') or '').lower()
# Build matchable name components for each changed symbol (e.g. 'Float64Counter.Enabled'
# matches a usage of 'Enabled' or 'Float64Counter' or the full dotted name).
def components(sym):
    parts = [p for p in sym.split('.') if p]
    return set([sym] + parts)
changed_lookup = {sym: components(sym) for sym in changed_syms}
order = ['production', 'test', 'cicd', 'generated', 'iac']
labels = {'production': 'prod', 'test': 'test', 'cicd': 'CI/CD', 'generated': 'generated', 'iac': 'IaC'}
def files_by_context(items):
    out = {ctx: set() for ctx in order}
    for u in items:
        ctx = u.get('context') or 'production'
        if ctx not in out:
            ctx = 'production'
        f = (u.get('file') or '').split(':')[0]
        if f:
            out[ctx].add(f)
    return out
def fmt(counts):
    parts = []
    for ctx in order:
        n = len(counts.get(ctx, set()))
        if n:
            noun = 'file' if n == 1 else 'files'
            parts.append(f'{n} {labels[ctx]} {noun}')
    return ', '.join(parts)
usage_names = set((u.get('symbol') or '').strip() for u in active if (u.get('symbol') or '').strip())
# Which changed symbols are actually reached by our code?
reached = {}
for sym, comps in changed_lookup.items():
    hit = [u for u in active if (u.get('symbol') or '').strip() in comps]
    if hit:
        reached[sym] = hit
overall = files_by_context(active)
inline = fmt(overall)
print('INLINE= · Context: ' + inline if inline else 'INLINE=')
print('---BLOCK---')
print('### BREAK-reachability context')
if changed_syms:
    if not reached:
        print(f'- ✅ None of the {len(changed_syms)} changed API symbol(s) from this upgrade are reached by your code — the changed surface is unused here (raises confidence).')
        print(f'  - Changed symbols (apidiff): ' + ', '.join(f'`{s}`' for s in changed_syms[:8]) + (f' …(+{len(changed_syms)-8})' if len(changed_syms) > 8 else ''))
        print('  - Note: import-level reachability still applies (see imported files below); behavioral/transitive breaks are not visible to apidiff.')
    else:
        breaking_reached = [s for s in reached if sym_change_type.get(s) in ('removed', 'changed')]
        additive_reached = [s for s in reached if sym_change_type.get(s) not in ('removed', 'changed')]
        for sym in breaking_reached[:8]:
            loc = fmt(files_by_context(reached[sym]))
            if loc:
                print(f'- ⚠️ removed/changed API symbol `{sym}` reached in {loc} — a caller of this symbol can break')
        for sym in additive_reached[:8]:
            loc = fmt(files_by_context(reached[sym]))
            if loc:
                print(f'- ℹ️ changed API symbol `{sym}` reached in {loc} — additive (new method/symbol); only breaks code that *implements* the changed interface, not callers')
        extra = len(reached) - len(breaking_reached[:8]) - len(additive_reached[:8])
        if extra > 0:
            print(f'- …and {extra} more reached changed symbol(s)')
else:
    print('- No exported API symbols changed per apidiff; reachability is import-level only (see imported files below).')
if not overall['production'] and any(overall[ctx] for ctx in ('test', 'cicd', 'generated')):
    print('- Non-production-only reachability (test/CI/generated) is down-weighted in the merge-risk score.')
PYEOF
)
  fi
  if [[ -n "$_USAGE_CONTEXT_BLOCK" ]]; then
    _USAGE_CONTEXT_INLINE=$(echo "$_USAGE_CONTEXT_BLOCK" | grep '^INLINE=' | head -1 | cut -d= -f2-)
    _USAGE_CONTEXT_BLOCK=$(echo "$_USAGE_CONTEXT_BLOCK" | sed -n '/^---BLOCK---$/,$p' | tail -n +2)
    if [[ -n "$_USAGE_CONTEXT_BLOCK" ]]; then
      _USAGE_CONTEXT_BLOCK=$(printf '\n\n%s' "$_USAGE_CONTEXT_BLOCK")
    fi
  fi
  # Declared-break reachability proof block: turns a declared-breaking verdict from a punt
  # ("verify yourself") into evidence by naming the file that imports the affected package.
  _DECLARED_BREAK_REACH_BLOCK=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || true
import json, os
d = json.loads(os.environ["_BC_PRF"])
r = d.get('declared_break_reachability') or {}
if not r.get('checked'):
    raise SystemExit(0)
paths = r.get('affected_paths') or []
ev = r.get('evidence') or []
# Optional AI behavioral probe result (advisory only — never flips the deterministic verdict).
aba = d.get('ai_behavioral_assessment') or {}
aba_verdict = (aba.get('verdict') or '').strip().lower() if isinstance(aba, dict) else ''
# The two-oracle behavioral grade (differential probe / release-notes reasoning oracle).
# When it committed a CITED, graded verdict we must not also tell the dev to "check it
# yourself" — that is the exact unhelpful punt the grade replaces.
_bg = d.get('behavioral_grade') or {}
_bg_source = (_bg.get('source') or '').strip().lower() if isinstance(_bg, dict) else ''
_bg_cited = _bg_source in ('reasoning', 'probe') and bool(
    (_bg.get('rationale') or '').strip() or (_bg.get('guidance') or '').strip()
    or (_bg.get('evidence') or '').strip())
def _aba_bullet():
    # Render the AI probe outcome as an advisory bullet, clearly labelled as a non-proof judgment.
    rationale = (aba.get('rationale') or '').strip()
    site = (aba.get('call_site') or '').strip()
    conf = (aba.get('confidence') or '').strip().lower()
    behavior = (aba.get('checked_behavior') or '').strip()
    conf_txt = f", {conf} confidence" if conf in ('low', 'medium', 'high') else ''
    site_txt = f" (checked `{site}`)" if site else ''
    head = '🤖 **AI behavioral probe** — reasoned judgment over the release note + your call site; **not executed, not type-checked, not proof**.'
    if aba_verdict == 'affected':
        body = f"**Likely affected{conf_txt}.** {rationale}{site_txt} This strengthens the review signal but is still **not** a confirmed break — please confirm against the release notes before relying on it."
    elif aba_verdict == 'not_affected':
        body = f"**Probe found no reliance{conf_txt}.** {rationale}{site_txt} This is advisory only — the deterministic grade stays **Medium / Review**; please confirm before merging."
    else:
        return None  # uncertain / unknown → fall through to the deterministic punt
    if behavior:
        body += f" Behavior checked: {behavior}."
    return f"- {head} {body}"
lines = ['### Reachability of the declared break']
if r.get('prod_reachable'):
    sk = r.get('surface_kind') or 'unknown'
    surf = r.get('surface_evidence') or []
    named_syms = r.get('named_symbols') or []
    sbp = r.get('surface_by_path') or {}
    prod = [e for e in ev if not e.get('is_test')]
    reason = (d.get('merge_risk') or {}).get('reason') or ''
    by_path = {}
    for e in prod:
        by_path.setdefault(e['path'], e)
    ordered = sorted(by_path.values(), key=lambda e: (e['path'] not in reason, e['path']))
    def _local_for(path):
        return (sbp.get(path) or {}).get('local') or path.split('/')[-1]
    if sk == 'named':
        lines.append('- ⚠️ **Directly on the changed surface.** Your production code calls a symbol the changelog flags as changed:')
        for e in ([x for x in surf if x.get('named')] or surf)[:3]:
            lines.append(f"  - `{_local_for(e['path'])}.{e['symbol']}` at `{e['file']}:{e['line']}`  ·  package `{e['path']}`")
        lines.append('- This is the **strongest exposure signal** — your code touches the exact surface the maintainer changed. The change is *behavioral* (same type signature), so build, tests, and API-diff still cannot confirm it affects you. Graded **Medium / Review**, not a confirmed break.')
    elif sk == 'package':
        lines.append('- ⚠️ **Uses the affected package, but not the named symbol directly.** Your production code calls into the package surface:')
        for e in surf[:3]:
            lines.append(f"  - `{_local_for(e['path'])}.{e['symbol']}` at `{e['file']}:{e['line']}`  ·  package `{e['path']}`")
        nm = (', '.join(f'`{s}`' for s in named_syms[:3])) if named_syms else 'the changed behavior'
        lines.append(f"- The declared change centers on {nm}, which the library runs **internally** (e.g. during scrape / collect / serialize), not via a call you make directly. So whether it affects you depends on your **runtime data and configuration** — build, tests, and API-diff cannot see this.")
    elif sk == 'import_only':
        lines.append('- ℹ️ **Imported, but no exported surface referenced.** Your production code imports the affected package, but we found no call into its exported API (possibly a blank or transitive import):')
        for e in ordered[:3]:
            lines.append(f"  - `{e['path']}` at `{e['file']}:{e['line']}`")
        if _bg_cited:
            lines.append('- **Lower exposure** — the behavioral oracle graded this against the release notes (see the verdict above); behavior can still change behind a blank or transitive import.')
        else:
            lines.append('- **Lower exposure** — but still verify against the release notes, since behavior can change behind a blank or transitive import.')
    else:
        lines.append('- ⚠️ **Import-reachable behavioral change — unconfirmed.** Your production code imports the affected package:')
        for e in ordered[:3]:
            lines.append(f"  - `{e['path']}` at `{e['file']}:{e['line']}`")
        lines.append('- The maintainer declared a **behavioral** break (changed defaults / error or ordering semantics). Build, tests, and API-diff **cannot see** behavioral changes, so we cannot confirm — or rule out — that your usage triggers it. Importing the package is necessary but **not sufficient** to break.')
    _ai_line = _aba_bullet() if aba_verdict in ('affected', 'not_affected') else None
    if _ai_line:
        lines.append(_ai_line)
    elif _bg_cited:
        # The behavioral oracle already committed a graded, cited verdict (rendered in
        # the headline above). Point to it instead of the generic "check it yourself".
        _g = (_bg.get('grade') or 'medium').strip().lower()
        _label = {'none': 'None', 'low': 'Low', 'medium': 'Medium', 'high': 'High'}.get(_g, 'Medium')
        _guid = (_bg.get('guidance') or '').strip()
        _tail = f' {_guid}' if _guid else ''
        lines.append(f"- The behavioral oracle assessed this against the release notes and your call site and committed **Breakability: {_label}** with cited reasoning (see the verdict above) — this is a graded answer, not a \"verify it yourself\".{_tail}")
    else:
        lines.append('- This is a **manual-review signal, not a confirmed break** — graded **Medium / Review**, not High. To settle it: check whether your usage relies on the changed behavior described in the release notes. If it does not, this signal does not block the merge.')
elif r.get('test_only'):
    lines.append('- ℹ️ The declared break is only reachable from **test/CI code**, not production — verdict down-weighted to Medium.')
    for e in ev[:3]:
        lines.append(f"  - `{e['path']}` at `{e['file']}:{e['line']}` (test)")
else:
    lines.append('- ✅ The declared break is in ' + ', '.join(f'`{p}`' for p in paths[:4]) + ' — **your code does not import** ' + ('it' if len(paths) == 1 else 'them') + '. Down-weighted to Medium (not reachable).')
print('\n\n' + '\n'.join(lines))
PYEOF
)
  # Flag: declared BEHAVIORAL break that is import-reachable in production. merge-risk has graded
  # this Medium (review, not High), but a plain "build passes" headline would bury it — so we route
  # it through the REVIEW headline below with softer wording.
  _DECL_BEHAVIORAL_REVIEW=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || echo 0
import json, os
r = (json.loads(os.environ["_BC_PRF"]).get('declared_break_reachability') or {})
print('1' if (r.get('reachability_kind') == 'import' and r.get('prod_reachable')) else '0')
PYEOF
)
  _DECL_BEHAVIORAL_REVIEW=${_DECL_BEHAVIORAL_REVIEW:-0}
}

# ── build_checklist_blocks ───────────────────────────────────────────────────
# Reads:  PR_FIELDS, ECOSYSTEM, GOSUM_NEW_COUNT, GOSUM_NEW_NAMES, VULN_STATUS,
#         VULN_PREEXISTING_COUNT, VULN_NEW_COUNT, VULN_NEW_LIST, CVE_COUNT,
#         _FIXES_CVE_DATA, BUILD_EVIDENCE, BUILD_DIRS, ADVISORY_FOOTER
# Sets:   _TRANSITIVE_NOTE, _VULN_NOTE, _VULN_HEADER_BADGE, _PRE_NOTE,
#         ADVISORY_FOOTER (may clear), _DEP_RESOLUTION_LINE, _GO_RESOLUTION_BLOCK,
#         _NO_TEST_CONFIDENCE_BLOCK, _API_DIFF_TOOL_BLOCK, _BUILD_STDOUT_BLOCK,
#         _TEST_STDOUT_BLOCK, _EV_TEST, _EV_BUILD
build_checklist_blocks() {
  # Build transitive dep note — with threshold warning for high counts
  _TRANSITIVE_NOTE=""
  if [[ -n "$GOSUM_NEW_COUNT" && "$GOSUM_NEW_COUNT" -gt 0 ]]; then
    _GOSUM_CONTEXT=""
    _GOSUM_NAMES_NOTE=""
    if [[ -n "$GOSUM_NEW_NAMES" ]]; then
      _GOSUM_NAMES_NOTE=": ${GOSUM_NEW_NAMES}"
    fi
    if [[ "$GOSUM_NEW_COUNT" -gt 20 ]]; then
      _TRANSITIVE_NOTE="
- ⚠️ go.sum: **$GOSUM_NEW_COUNT new transitive deps**${_GOSUM_NAMES_NOTE}${_GOSUM_CONTEXT} — high count, review for supply-chain risk"
    else
      _TRANSITIVE_NOTE="
- ℹ️ go.sum: $GOSUM_NEW_COUNT new transitive deps${_GOSUM_NAMES_NOTE}${_GOSUM_CONTEXT}"
    fi
  fi
  # Build govulncheck note (inline checklist item) + top-of-comment header badge.
  # CVE reachability is advisory only; break-reachability (API calls) drives merge risk.
  # V9.7b: distinguish NEW findings (this PR introduces) from pre-existing on main
  _VULN_NOTE=""
  _VULN_HEADER_BADGE=""
  _PRE_NOTE=""
  [[ "${VULN_PREEXISTING_COUNT:-0}" -gt 0 ]] && _PRE_NOTE=" (+ ${VULN_PREEXISTING_COUNT} pre-existing on main)"
  case "$VULN_STATUS" in
    ok)
      _VULN_NOTE="
- ✅ govulncheck: no known vulnerabilities (all modules scanned)"
      ;;
    ok_preexisting)
      # PR scan found vulns, but ALL were already on main — PR introduces none.
      _VULN_NOTE="
- ✅ govulncheck: PR introduces **no new vulnerabilities** (${VULN_PREEXISTING_COUNT} pre-existing on main — unaffected by this PR; CVE reachability is hint-only)"
      ;;
    vulns_found)
      _VULN_NOTE="
- 🚨 Heads-up: CVE reachability (hint only): govulncheck found **${VULN_NEW_COUNT} NEW vulnerability(ies) introduced by this PR** — ${VULN_NEW_LIST}${_PRE_NOTE}"
      _VULN_HEADER_BADGE="> 🚨 **Security:** This PR introduces **${VULN_NEW_COUNT} new vulnerability(ies)** not present on main: ${VULN_NEW_LIST}${_PRE_NOTE}. **Review before merge.**
"
      ;;
    failed_oom)
      _VULN_NOTE="
- ⚠️ govulncheck crashed (out-of-memory) — **vuln scan incomplete for this PR**"
      _VULN_HEADER_BADGE="> ⚠️ **govulncheck crashed (OOM)** — vulnerability scan did NOT complete for this PR. Do not treat absence of findings as safe.
"
      ;;
    failed_timeout)
      _VULN_NOTE="
- ⚠️ govulncheck timed out (>180s per module) — **vuln scan incomplete**"
      _VULN_HEADER_BADGE="> ⚠️ **govulncheck timed out** — vulnerability scan did NOT complete. Do not treat absence of findings as safe.
"
      ;;
    failed_error)
      _VULN_NOTE="
- ⚠️ govulncheck failed (unexpected error) — **vuln scan incomplete**"
      _VULN_HEADER_BADGE="> ⚠️ **govulncheck failed** (unexpected error) — vulnerability scan did NOT complete.
"
      ;;
    not_installed)
      _VULN_NOTE="
- ℹ️ govulncheck not installed — vuln scan skipped"
      ;;
    skipped_disabled)
      if [[ ( "${CVE_COUNT:-0}" =~ ^[0-9]+$ && "${CVE_COUNT:-0}" -gt 0 ) || -n "${_FIXES_CVE_DATA:-}" ]]; then
        # This PR touches CVEs but govulncheck (call-graph reachability) did NOT run, so
        # the reachability section below is import-level only — NOT a per-CVE call-chain.
        # Be explicit so a dev doesn't read absence-of-call-chain as "not reachable".
        _VULN_NOTE="
- ⚠️ **CVE reachability NOT computed for this PR.** govulncheck (call-graph reachability on _our_ source) is disabled by config; the CVE list comes from **Dependabot**, which matches advisory version-ranges only — it does NOT prove the vulnerable symbol is reachable from our code, nor detect NEW CVEs the target version may regress in.
  - To get a per-CVE call-chain proof, re-run with \`BREAKABILITY_GOVULNCHECK=1\`.
  - Until then, treat the CVE list as advisory (version-match), not reachability-confirmed."
      else
        _VULN_NOTE="
- ℹ️ govulncheck: disabled by config — CVE list sourced from Dependabot alerts (govulncheck is hint-only; not a merge gate)"
      fi
      ;;
    *)
      _VULN_NOTE="
- ℹ️ govulncheck: status unknown"
      ;;
  esac
  # Suppress advisory disclaimer when security risk is flagged
  [[ -n "$_VULN_HEADER_BADGE" ]] && ADVISORY_FOOTER=""
  # Build Go dependency-resolution evidence blocks
  _DEP_RESOLUTION_LINE="- ✅ Dependency resolved — \`go get\`/\`npm install\` exit 0"
  _GO_RESOLUTION_BLOCK=""
  if [[ "$ECOSYSTEM" == "gomod" ]]; then
    _GO_RESOLUTION_PARSE=$(echo "$PR_FIELDS" | python3 -c "
import json, sys, re
d=json.load(sys.stdin)
gr=d.get('go_resolution') or {}
cmd=gr.get('command','')
out=gr.get('output_tail','') or ''
diff=gr.get('modsum_diff','') or ''
if cmd:
    print('CMD=' + cmd)
adds=[]; rems=[]
for line in diff.splitlines():
    if not line or line[0] not in '+-':
        continue
    m=re.match(r'^[+-]\s*(?:require\s+)?([A-Za-z0-9_.:/@-]+)\s+(v?\d[^\s]*)', line)
    if not m:
        continue
    (adds if line[0]=='+' else rems).append(m.group(1)+' '+m.group(2))
changed=[]
rem_by={x.split()[0]:x.split()[1] for x in rems}
add_by={x.split()[0]:x.split()[1] for x in adds}
for name in sorted(set(rem_by)&set(add_by)):
    if rem_by[name] != add_by[name]:
        changed.append(f'{name} {rem_by[name]}→{add_by[name]}')
new=[x for x in adds if x.split()[0] not in rem_by]
removed=[x for x in rems if x.split()[0] not in add_by]
if changed or new or removed:
    print('SUMMARY=' + f'{len(changed)} changed, {len(new)} new, {len(removed)} removed' + (': ' + '; '.join((changed+new+removed)[:6]) if (changed+new+removed) else ''))
print('---OUT---')
print('\n'.join([l for l in out.splitlines() if l.strip()][-20:]))
print('---DIFF---')
# Drop file-sections for internal breakability tooling (e.g. .github/tools/reachability)
# that build-check mutates during analysis — they are not part of the analyzed module
# and only add confusing noise to the go.mod/go.sum diff shown to developers.
def _filter_internal(diff_text):
    out, skip = [], False
    for ln in diff_text.splitlines():
        if ln.startswith('diff --git '):
            skip = '.github/tools/' in ln
        if skip:
            continue
        out.append(ln)
    return '\n'.join(out)
print('\n'.join(_filter_internal(diff).splitlines()[:160]))
" 2>/dev/null || true)
    _GO_RES_CMD=$(echo "$_GO_RESOLUTION_PARSE" | grep '^CMD=' | head -1 | cut -d= -f2-)
    _GO_RES_SUMMARY=$(echo "$_GO_RESOLUTION_PARSE" | grep '^SUMMARY=' | head -1 | cut -d= -f2-)
    _GO_RES_OUT=$(echo "$_GO_RESOLUTION_PARSE" | sed -n '/^---OUT---$/,/^---DIFF---$/p' | sed '1d;$d')
    _GO_RES_DIFF=$(echo "$_GO_RESOLUTION_PARSE" | sed -n '/^---DIFF---$/,$p' | tail -n +2)
    if [[ -n "$_GO_RES_CMD" ]]; then
      _DEP_RESOLUTION_LINE="- ✅ Dependency resolved — \`$_GO_RES_CMD\`"
      [[ -n "$_GO_RES_SUMMARY" ]] && _DEP_RESOLUTION_LINE="${_DEP_RESOLUTION_LINE} — go.mod/go.sum: ${_GO_RES_SUMMARY}"
    fi
    if [[ -n "$_GO_RES_OUT" ]]; then
      _GO_RESOLUTION_BLOCK="
<details><summary>📦 Go dependency-resolution output</summary>

\`\`\`
${_GO_RES_OUT}
\`\`\`
</details>"
    fi
    if [[ -n "$_GO_RES_DIFF" ]]; then
      _GO_RESOLUTION_BLOCK="${_GO_RESOLUTION_BLOCK}
<details><summary>🧾 go.mod / go.sum diff</summary>

\`\`\`diff
${_GO_RES_DIFF}
\`\`\`
</details>"
    fi
  fi

  _NO_TEST_CONFIDENCE_BLOCK=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || true
import json, os, sys
d=json.loads(os.environ["_BC_PRF"])
nt=d.get('no_test_confidence') or {}
if not nt.get('applies'):
    sys.exit(0)
b=nt.get('basis') or {}
print('### Confidence without tests')
print(f'Derived confidence: **{nt.get("confidence","unknown")}** (no Go test files were present).')
print(f'- API diff changes: `{b.get("api_changes", 0)}`')
print(f'- BREAK-reachability signals (changed API symbols your code calls/accesses): `{b.get("usage_signals", 0)}`')
print(f'- Semver bump: `{b.get("semver_bump", "?")}` · dep type: `{b.get("dep_type", "?")}`')
print(f'- **Residual risk:** {nt.get("residual_risk","Runtime behavior is not covered by tests.")}')
PYEOF
)

  _API_DIFF_TOOL_BLOCK=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || true
import json, os, sys
d=json.loads(os.environ["_BC_PRF"])
tool=((d.get('deterministic') or {}).get('api_diff_tool') or {})
if not tool:
    sys.exit(0)
status=tool.get('status')
print('\n\n### API diff signal')
if status == 'semantic':
    print(f'- ✅ Go apidiff ran in **{tool.get("mode","module")} mode** using `{tool.get("package","golang.org/x/exp/cmd/apidiff")}@{tool.get("version","unknown")}`')
    if tool.get('command'):
        print(f'- Command: `{tool.get("command")}`')
elif status == 'structural_fallback':
    print(f'- ⚠️ Go apidiff was unavailable; structural fallback ran instead.')
    if tool.get('warning'):
        print(f'- Reason: {tool.get("warning")}')
    print('- Coverage note: fallback is evidence, but may miss subpackage/type-compatibility breaks that module-mode apidiff would catch.')
PYEOF
)

  # Build build-stdout evidence block
  _BUILD_STDOUT_BLOCK=""
  _BUILD_STDOUT_SNIPPET=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
tail = d.get('output_tail', '')
# Show last 8 non-empty lines of build output as evidence
lines = [l for l in tail.splitlines() if l.strip()][-8:]
if lines:
    print('\n'.join(lines))
" 2>/dev/null || true)
  if [[ -n "$_BUILD_STDOUT_SNIPPET" ]]; then
    _BUILD_STDOUT_BLOCK="
<details><summary>🖥️ Build output (last lines)</summary>

\`\`\`
${_BUILD_STDOUT_SNIPPET}
\`\`\`
</details>"
  fi
  # Build TEST-stdout evidence block + parse a trustworthy test summary.
  # Reviewer P1: "Tests pass (exit=0)" with no test names/count is NOT evidence.
  # We surface the actual `go test` / pytest stdout AND derive a count line.
  _TEST_STDOUT_BLOCK=""
  _EV_TEST=""        # inline summary appended to the "Tests pass" checklist line
  _TEST_PARSE=$(echo "$PR_FIELDS" | python3 -c "
import json, sys, re
d = json.load(sys.stdin)
tail = d.get('test_output_tail', '') or ''
lines = [l for l in tail.splitlines() if l.strip()]
# Go:  'ok   pkg/path   0.123s'  /  '--- PASS: TestFoo'  /  'FAIL'
# Py:  '5 passed, 1 warning in 0.42s'  / '=== 3 passed ==='
ok_pkgs   = [l for l in lines if re.match(r'^ok\s+\S', l)]
no_test   = [l for l in lines if 'no test files' in l.lower()]
pass_cnt  = sum(1 for l in lines if l.startswith('--- PASS'))
fail_cnt  = sum(1 for l in lines if l.startswith('--- FAIL') or l.strip()=='FAIL' or l.startswith('FAIL'))
# pytest-style summary line
py_sum    = next((l.strip() for l in reversed(lines) if re.search(r'\d+\s+(passed|failed|error)', l)), '')
parts = []
if ok_pkgs:  parts.append(f'{len(ok_pkgs)} package(s) ok')
if pass_cnt: parts.append(f'{pass_cnt} test(s) PASS')
if fail_cnt: parts.append(f'{fail_cnt} FAIL')
if py_sum:   parts.append(py_sum)
summary = '; '.join(parts)
# If every package reports 'no test files', say so honestly.
only_no_tests = no_test and not ok_pkgs and not pass_cnt
print('SUMMARY=' + summary)
print('ONLY_NO_TESTS=' + ('1' if only_no_tests else '0'))
# emit last 12 non-empty lines for the details block
print('---TAIL---')
print('\n'.join(lines[-12:]))
" 2>/dev/null || true)
  _TEST_SUMMARY_LINE=$(echo "$_TEST_PARSE" | grep '^SUMMARY=' | head -1 | cut -d= -f2-)
  _TEST_ONLY_NO_TESTS=$(echo "$_TEST_PARSE" | grep '^ONLY_NO_TESTS=' | head -1 | cut -d= -f2-)
  _TEST_TAIL=$(echo "$_TEST_PARSE" | sed -n '/^---TAIL---$/,$p' | tail -n +2)
  if [[ -n "$_TEST_SUMMARY_LINE" ]]; then
    _EV_TEST=" — $_TEST_SUMMARY_LINE"
  elif [[ "$_TEST_ONLY_NO_TESTS" == "1" ]]; then
    _EV_TEST=" — ⚠️ no test files in affected packages (exit 0 ≠ tests ran)"
  fi
  if [[ -n "$_TEST_TAIL" ]]; then
    _TEST_STDOUT_BLOCK="
<details><summary>🧪 Test output (last lines)</summary>

\`\`\`
${_TEST_TAIL}
\`\`\`
</details>"
  fi
  # Build inline evidence strings for checklist items
  _EV_BUILD=""
  [[ -n "$BUILD_EVIDENCE" ]] && _EV_BUILD=" — \`$BUILD_EVIDENCE\`"
  [[ -n "$BUILD_DIRS" && -z "$_EV_BUILD" ]] && _EV_BUILD=" — \`$BUILD_DIRS\`"
}
