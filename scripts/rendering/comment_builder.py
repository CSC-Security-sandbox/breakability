#!/usr/bin/env python3
"""Build the structured PR comment body from PR fields and results data.

Reads environment variables:
    COMMENT_BODY   - existing comment body
    PR_FIELDS      - JSON blob of per-PR fields
    PR_NUM         - pull request number
    RESULTS_FILE   - path to build-results.json
    MERGE_RISK_LINE - merge-risk one-liner
    PLAN_LINE      - optional merge-plan link line
    RUN_LINK       - optional actions run link
    ADVISORY_FOOTER - optional advisory-mode footer

Prints the assembled comment to stdout.
"""

import json
import os
import re


def clip(text, n=360):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if len(text) <= n:
        return text
    return text[:n].rsplit(' ', 1)[0].rstrip(' ,.;:-') + '…'

def first_lines(text, n=8):
    return '\n'.join([l for l in str(text or '').splitlines() if l.strip()][-n:])

def grade_label(bg):
    if not isinstance(bg, dict):
        return 'not run'
    src = str(bg.get('source') or '').lower()
    g = str(bg.get('grade') or '').lower()
    text = ' '.join(str(bg.get(k) or '') for k in ('rationale', 'guidance', 'evidence', 'old_sha256', 'new_sha256', 'sha256_old', 'sha256_new'))
    sha_vals = [str(bg.get(k) or '').strip().lower() for k in ('old_sha256', 'new_sha256', 'sha256_old', 'sha256_new')]
    missing_sha = any(v in ('', 'n/a', 'na', 'none', 'null', 'missing', 'unavailable') for v in sha_vals) or re.search(r'sha\s*256[^\n]*(n/a|missing|unavailable)', text, re.I)
    if missing_sha and g in ('medium', 'high'):
        return 'insufficient hash evidence'
    cited = src in ('reasoning', 'probe') and any(str(bg.get(k) or '').strip() for k in ('rationale', 'guidance', 'evidence'))
    if g in ('none', 'low', 'medium', 'high') and cited:
        conf = str(bg.get('confidence') or '').lower()
        extra = f', {conf} confidence' if conf in ('low', 'medium', 'high') else ''
        return f'{g}{extra}'
    if bg.get('skip_reason') or bg.get('not_run_reason'):
        return 'not run — ' + clip(bg.get('skip_reason') or bg.get('not_run_reason'), 120)
    return 'not run'

def probe_label_for(pr, package, eco):
    note = grade_label(pr.get('behavioral_grade') or {})
    if eco in ('gomod', 'go') and note == 'not run':
        return 'not run — Go probe unavailable; build/test/govulncheck evidence used'
    if str(package or '').startswith('@types/') and note == 'not run':
        return 'not run — ambient type package; compile evidence used'
    return note

def _negated_breaking_only(status, bullets):
    if str(status or '').lower() != 'breaking':
        return False
    neg = re.compile(r"\b(no|not|without|non[-\s]?breaking|does not|did not)\b.{0,80}\b(api change|breaking|incompatible|removed|behavior change)s?\b|\b(api change|breaking change)s?\b.{0,80}\b(no|not|without|none)\b", re.I)
    clean = []
    for b in bullets or []:
        flat = re.sub(r'\s+', ' ', str(b or '')).strip()
        if flat and not neg.search(flat):
            clean.append(flat)
    return not clean

def _probe_changed(bg):
    if not isinstance(bg, dict):
        return False
    text = ' '.join(str(bg.get(k) or '') for k in ('grade','source','rationale','guidance','evidence','behavior_changed')).lower()
    if re.search(r'\b(different|changed|regression|breaking|sha-only|sha only)\b', text):
        return True
    return str(bg.get('behavior_changed') or '').lower() in ('true', '1', 'yes')

def verdict_from(pr, fields):
    build = pr.get('build') or {}
    test = pr.get('test') or {}
    v2 = pr.get('verdict_v2') if isinstance(pr.get('verdict_v2'), dict) else {}
    raw = build.get('verdict') or fields.get('verdict') or ''
    mr = pr.get('merge_risk') or (pr.get('deterministic') or {}).get('merge_risk') or {}
    tag = str(mr.get('tag') or '').lower()
    bg = pr.get('behavioral_grade') or {}
    bg_grade = str(bg.get('grade') or '').lower()
    det = pr.get('deterministic') or {}
    ch = det.get('changelogSignal') or {}
    changelog_breaking = str(ch.get('status') or '').lower() == 'breaking' and not _negated_breaking_only(ch.get('status'), ch.get('bullets') or [])
    test_failed = bool(test.get('ran')) and test.get('exit') not in (0, None, -1)
    build_failed = raw in ('fail', 'pre_existing_plus_new', 'vulns_introduced', 'conflict') or (build.get('pr_exit') not in (0, None, -1) and raw not in ('pre_existing', 'pass', 'security_review'))
    blocked = build_failed or test_failed or v2.get('verdict') == 'BLOCKED'
    review = tag == 'high' or _probe_changed(bg) or bg_grade == 'high' or changelog_breaking or v2.get('verdict') == 'REVIEW'
    if blocked:
        if test_failed and not build_failed:
            return '⛔ BLOCKED', f'Do not merge until the failing test signal (exit {test.get("exit")}) is resolved or proven unrelated on main.'
        return '⛔ BLOCKED', 'Do not merge until the failing build/security signal is resolved.'
    if review:
        reasons = []
        if tag == 'high': reasons.append('high merge risk')
        if _probe_changed(bg) or bg_grade == 'high': reasons.append('behavioral probe changed')
        if changelog_breaking: reasons.append('non-negated breaking changelog evidence')
        if v2.get('verdict') == 'REVIEW': reasons.append(v2.get('reason') or 'policy review')
        why = '; '.join(r for r in reasons if r) or 'manual-review signal'
        return '⚠️ REVIEW', f'Review required because {why}. Merge only if the evidence does not apply to your usage.'
    return '✅ SAFE', 'Safe to merge based on the checked evidence.'

def signal_summary(pr, fields):
    build = pr.get('build') or {}
    test = pr.get('test') or {}
    files = fields.get('files_importing') or pr.get('files_importing') or pr.get('import_files') or []
    package = fields.get('package') or pr.get('package') or ''
    eco = fields.get('ecosystem') or pr.get('ecosystem') or ''
    parts = []
    bv = build.get('verdict') or fields.get('verdict') or '?'
    if bv in ('pass', 'security_review'):
        parts.append(f'Build: pass (exit {build.get("pr_exit", 0)})')
    elif bv == 'pre_existing':
        parts.append('Build: same as main (pre-existing failures)')
    else:
        parts.append(f'Build: {bv} (exit {build.get("pr_exit", "?")})')
    if test.get('ran'):
        parts.append('Tests: pass' if test.get('exit') == 0 else f'Tests: fail (exit {test.get("exit")})')
    else:
        parts.append('Tests: not run')
    if (pr.get('package') or fields.get('package') or '').startswith('@types/') and not files:
        parts.append('Usage: ambient @types package (0 direct imports ≠ unreachable)')
    else:
        parts.append(f'Usage: {len(files)} importing file(s)')
    parts.append(f'Probe: {probe_label_for(pr, package, eco)}')
    cves = pr.get('fixes_cves') or pr.get('cves') or []
    if cves:
        parts.append(f'CVE: {len(cves)} item(s)')
    return ' · '.join(parts)

def cve_lines(pr):
    out = []
    fixes = pr.get('fixes_cves') or []
    if fixes:
        out.append('**CVE context:**')
        for f in fixes[:4]:
            if isinstance(f, dict):
                cid = f.get('cve_id') or f.get('id') or 'advisory'
                sev = (f.get('severity') or 'unknown').upper()
                cvss = f.get('cvss_score') or f.get('cvss') or ''
                url = f.get('advisory_url') or f.get('url') or ''
                patched = f.get('first_patched_version') or 'patched version'
                score = f' CVSS {cvss}' if cvss else ''
                link = f' — {url}' if url else ''
                out.append(f'- `{cid}` ({sev}{score}) — version-gated fix reaches `{patched}`.{link}')
    elif pr.get('cves'):
        out.append('**CVE context:** ' + ', '.join(f'`{c}`' for c in pr.get('cves')[:6]) + ' (from PR/advisory metadata; reachability is not proven).')
    else:
        out.append('**CVE context:** no CVE/advisory fix matched this PR in the artifact.')
    return '\n'.join(out)

def ai_line(pr):
    adj = pr.get('ai_adjudication')
    if isinstance(adj, dict) and (adj.get('applied') or adj.get('evidence') or adj.get('reason_code')):
        applied = adj.get('applied') or 'reviewed'
        ev = clip(adj.get('evidence') or adj.get('reason_code') or '', 220)
        return f'**AI arbiter:** {applied} — {ev}' if ev else f'**AI arbiter:** {applied}'
    bg = pr.get('behavioral_grade') or {}
    if any(str(bg.get(k) or '').strip() for k in ('rationale','guidance','evidence')):
        return '**AI arbiter:** synthesized behavioral risk — ' + clip(bg.get('rationale') or bg.get('guidance') or bg.get('evidence'), 240)
    return '**AI arbiter:** deterministic synthesis only — no LLM adjudication artifact was available; not shown as NOT-APPLICABLE.'


def main():
    try:
        fields = json.loads(os.environ.get('PR_FIELDS') or '{}')
    except Exception:
        fields = {}
    try:
        with open(os.environ.get('RESULTS_FILE', '/tmp/build-results.json')) as fh:
            data = json.load(fh)
    except Exception:
        data = {'prs': {}}
    pr = (data.get('prs') or {}).get(str(os.environ.get('PR_NUM')), {})
    if not isinstance(pr, dict):
        pr = {}
    package = fields.get('package') or pr.get('package') or '?'
    fr = fields.get('from') or pr.get('from') or '?'
    to = fields.get('to') or pr.get('to') or '?'
    dep_type = fields.get('dep_type') or pr.get('dep_type') or '?'
    bump = fields.get('bump') or pr.get('bump') or '?'
    eco = fields.get('ecosystem') or pr.get('ecosystem') or '?'
    verdict, recommendation = verdict_from(pr, fields)
    risk = os.environ.get('MERGE_RISK_LINE') or ''
    if not risk.strip():
        mr = pr.get('merge_risk') or (pr.get('deterministic') or {}).get('merge_risk') or {}
        risk = f"**Merge Risk: {mr.get('tag','Medium')}** (Evidence: {mr.get('evidenceAxis','limited evidence')} × Build verification: {mr.get('buildVerificationAxis') or pr.get('verification_label','unverified')}) — {mr.get('reason','change evidence is limited')}"
    what = []
    if verdict.startswith('✅'):
        what.append(f'This {bump} upgrade has no blocking signal in the artifact. The build evidence and import scan do not show a regression introduced by `{package}`.')
    elif verdict.startswith('⛔'):
        what.append('This upgrade has a blocking signal in the artifact. Do not merge until the failing build/security evidence is resolved.')
    else:
        what.append('This upgrade has a non-green signal that a build alone cannot clear. Use the evidence below to decide whether the changed behavior reaches your code.')
    what.append(recommendation)
    files = fields.get('files_importing') or pr.get('files_importing') or pr.get('import_files') or []
    if package.startswith('@types/') and not files:
        file_lines = '- No direct import files recorded. **Caveat:** ambient `@types/*` declarations can still affect every TypeScript compile.'
    else:
        file_lines = '\n'.join(f'- `{str(f).split(":")[0]}`' for f in files[:8]) or '- No import files recorded in the artifact.'
    if len(files) > 8:
        file_lines += f'\n- …and {len(files)-8} more.'
    build = pr.get('build') or {}
    test = pr.get('test') or {}
    probe_note = probe_label_for(pr, package, eco)
    how = [
        f'- Build verdict: `{build.get("verdict", fields.get("verdict", "?"))}`; main exit `{build.get("main_exit", "?")}`, PR exit `{build.get("pr_exit", "?")}`.',
        '- Tests: ' + (f'ran, exit `{test.get("exit")}`.' if test.get('ran') else 'not run; no behavioral-probe mitigation assumed.'),
        f'- Behavioral probe: {probe_note}.',
        f'- Verification label: `{pr.get("verification_label") or fields.get("verification_label") or "unknown"}` (L0 unresolved, L2 build/type checked, L4 tests passed).'
    ]
    if package.startswith('@types/') and not files:
        how.append('- Type package caveat: `@types/*` can affect global TypeScript compilation even with zero direct imports; do not treat zero imports as proof of no reachability.')
    if pr.get('declared_break_reachability'):
        r = pr.get('declared_break_reachability') or {}
        how.append(f'- Declared-break reachability: checked={bool(r.get("checked"))}, prod_reachable={bool(r.get("prod_reachable"))}.')
    output = first_lines(build.get('output_tail') or fields.get('output_tail') or '', 10) or 'No build stdout captured.'
    if build.get('verdict') in ('pass', 'pre_existing', 'security_review') and re.search(r'\bnpm\s+ERR!|ERESOLVE|E[A-Z0-9]+\b', output):
        how.append('- Build-output integrity note: captured stdout still contains npm error text despite a non-failing verdict; treat this as fallback-install evidence, not a clean `npm ci` transcript.')
    run_link = os.environ.get('RUN_LINK','').strip()
    plan = os.environ.get('PLAN_LINE','').strip()
    footer = os.environ.get('ADVISORY_FOOTER','').strip()
    specific = ''
    if package.startswith('@types/'):
        specific = ' — ambient TypeScript types can affect compilation without imports'
    elif dep_type.lower() in ('production', 'prod', 'runtime', 'direct') and bump == 'major':
        specific = ' — production major upgrade'
    elif build.get('verdict') not in ('pass', 'pre_existing', 'security_review'):
        specific = ' — build/test evidence needs attention'
    lines = ['<!-- breakability-check -->', f'## {verdict}{specific} — `{package}` {fr} → {to} · {dep_type} · {bump}', '', risk, '', signal_summary(pr, fields), '', '### What this means', ' '.join(what), '', cve_lines(pr), '', ai_line(pr)]
    if plan:
        lines += ['', plan]
    lines += ['', '<details><summary>How we checked</summary>', '', *how, '</details>', '', '<details><summary>Files importing</summary>', '', file_lines, '</details>', '', '<details><summary>Build output</summary>', '', '```', output, '```', '</details>']
    if run_link:
        lines += ['', run_link]
    if footer:
        lines += ['', footer]
    print('\n'.join(lines))


if __name__ == "__main__":
    main()
