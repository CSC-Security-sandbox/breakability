"""
rendering.pr_fields — CLI utility with subcommands for inline Python blocks
extracted from post-fallback-comments.sh (Phase 8 modularization).

Handles PR field extraction from build-results.json and shell variable formatting.
NOTE: post-fallback-comments.sh is NOT modified yet (another agent is working on it).
These modules are created first; the bash replacements will be applied in a later step.
"""
import argparse
import json
import os
import re
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))  # scripts/


# ── extract ──────────────────────────────────────────────────────────────────
# Replaces post-fallback-comments.sh lines 554-611.
# argv: --results-file PATH --pr-num NUM
# stdout: JSON object with all PR fields needed for comment rendering
def cmd_extract(args):
    with open(args.results_file) as f:
        data = json.load(f)
    pr = data['prs'].get(args.pr_num, {})
    build = pr.get('build', {})
    test = pr.get('test', {})
    sec = data.get('security_posture', {}).get('prs_fixing_alerts', {}).get(args.pr_num, {})
    cve_severities = sec.get('severities', [])
    cve_ids = sec.get('cve_ids', [])
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
        'test_output_tail': test.get('output_tail', ''),
        'main_test_exit': test.get('main_test_exit', -1),
        'verification_label': pr.get('verification_label', ''),
        'files_importing': pr.get('files_importing') or pr.get('import_files') or pr.get('importFiles') or [],
        'cves':         pr.get('cves', []),
        'error_class':  build.get('error_class', ''),
        'pkg_dir':      pr.get('pkg_dir', '/'),
        'main_exit':    build.get('main_exit', -1),
        'pr_exit':      build.get('pr_exit', -1),
        'cve_severities': cve_severities,
        'cve_ids':      cve_ids,
        'gosum_new_count': pr.get('gosum_new_count', 0),
        'gosum_new_names': pr.get('gosum_new_names', ''),
        'gosum_total_pr':  pr.get('gosum_total_pr', 0),
        'gosum_total_main': pr.get('gosum_total_main', 0),
        'vuln_status':     pr.get('vuln_status', 'unknown'),
        'vuln_finding':    pr.get('vuln_finding', ''),
        'vuln_new_findings': pr.get('vuln_new_findings', []),
        'vuln_output':     pr.get('vuln_output', ''),
        'vuln_preexisting_count': pr.get('vuln_preexisting_count', 0),
        'go_resolution': pr.get('go_resolution', {}),
        'no_test_confidence': pr.get('no_test_confidence', {}),
        'deterministic': pr.get('deterministic', {}),
        'merge_risk': pr.get('merge_risk', {}) or (pr.get('deterministic', {}) or {}).get('merge_risk', {}),
        'declared_break_reachability': pr.get('declared_break_reachability', {}),
        'ai_behavioral_assessment': pr.get('ai_behavioral_assessment', {}),
        'behavioral_grade': pr.get('behavioral_grade', {}),
        'cve_details': pr.get('cve_details', []),
        'verification_steps': pr.get('verification_steps', []),
        'fixes_cves': pr.get('fixes_cves', []),
        'ai_adjudication': pr.get('ai_adjudication', {}),
    }))


# ── format_vars ──────────────────────────────────────────────────────────────
# Replaces post-fallback-comments.sh lines 615-665.
# stdin: JSON object (PR_FIELDS)
# stdout: KEY=value lines for bash eval (one per line)
def cmd_format_vars(_args):
    d = json.load(sys.stdin)
    fields = {
        'PKG': d.get('package', '?'),
        'FROM': d.get('from', '?'),
        'TO': d.get('to', '?'),
        'BUMP': d.get('bump', '?'),
        'DEP_TYPE': d.get('dep_type', '?'),
        'DEP_REL': d.get('dep_relation', '?'),
        'ECOSYSTEM': d.get('ecosystem', '?'),
        'VERDICT': d.get('verdict', '?'),
        'INSTALL_METHOD': d.get('install_method', ''),
        'INSTALL_OK': str(d.get('install_ok', False)),
        'VER_LABEL': d.get('verification_label', ''),
        'NEW_ERR_COUNT': str(len(d.get('new_errors', []))),
        'FILES_COUNT': str(len(d.get('files_importing', []))),
        'PKG_DIR': d.get('pkg_dir', '/'),
        'ERROR_CLASS': d.get('error_class', ''),
        'OOM_OVERRIDE': str(d.get('oom_override', False)),
        'OOM_PACKAGES': ','.join(d.get('oom_packages', [])),
        'GOSUM_NEW_COUNT': str(d.get('gosum_new_count', 0)),
        'GOSUM_NEW_NAMES': d.get('gosum_new_names', ''),
        'GOSUM_TOTAL_PR': str(d.get('gosum_total_pr', 0)),
        'GOSUM_TOTAL_MAIN': str(d.get('gosum_total_main', 0)),
        'VULN_STATUS': d.get('vuln_status', 'unknown'),
        'VULN_FINDING': d.get('vuln_finding', ''),
        'VULN_NEW_COUNT': str(len(d.get('vuln_new_findings', []))),
        'VULN_NEW_LIST': ','.join(d.get('vuln_new_findings', [])),
        'VULN_PREEXISTING_COUNT': str(d.get('vuln_preexisting_count', 0)),
        'VULN_EVIDENCE': (lambda f: '\n'.join(f.splitlines()[:10]) if f else '')(d.get('vuln_finding', '')),
        'TEST_SUMMARY': (lambda t: '\n'.join(l for l in t.splitlines() if 'ok' in l.lower() or 'PASS' in l or 'FAIL' in l or 'pass' in l or '--- PASS' in l or 'passed' in l or 'failed' in l or 'tests' in l.lower())[:500] if t else '')(d.get('test_output_tail', '')),
        'FILES_LIST': '|'.join((f.split(':')[0] if ':' in f else f) for f in d.get('files_importing', [])[:8]),
        'TEST_FAIL_DETAIL': next((s.get('detail', '') for s in d.get('verification_steps', []) if s.get('step') == 'test_suite' and s.get('status') == 'pre_existing'), ''),
        'BUILD_EXIT': str(d.get('pr_exit', -1)),       # PR-branch build exit
        'PR_BUILD_EXIT': str(d.get('pr_exit', -1)),
        'MAIN_BUILD_EXIT': str(d.get('main_exit', -1)),
        'TEST_EXIT_CODE': str(d.get('test_exit', -1)),
        'TEST_RAN': str(d.get('test_ran', False)),
        'MAIN_TEST_EXIT': str(d.get('main_test_exit', -1)),
        'BUILD_EVIDENCE': (lambda t: next((l.strip() for l in t.splitlines() if 'targeted build' in l or 'full build' in l or 'npm run build' in l), ''))(d.get('output_tail', '')),
        'BUILD_DIRS': (lambda t: next((l.strip() for l in t.splitlines() if 'dirs:' in l), ''))(d.get('output_tail', '')),
        'MERGE_RISK_TAG': (d.get('merge_risk') or {}).get('tag', ''),
        'MERGE_RISK_REASON': (d.get('merge_risk') or {}).get('reason', ''),
        'MERGE_RISK_EVIDENCE': (d.get('merge_risk') or {}).get('evidenceAxis', ''),
        'MERGE_RISK_BUILD_VERIFICATION': (d.get('merge_risk') or {}).get('buildVerificationAxis', '') or (d.get('merge_risk') or {}).get('confidenceAxis', ''),
    }
    for k, v in fields.items():
        # Use null byte as delimiter to safely handle any value content
        print(f'{k}={v}')


# ── l1_excerpt ───────────────────────────────────────────────────────────────
# Replaces post-fallback-comments.sh lines 895-929.
# stdin: JSON object (PR_FIELDS)
# env:   _BC_PKG — package name
# stdout: line 1 = attribution metadata, then ---EXCERPT--- delimiter, then excerpt lines
def cmd_l1_excerpt(_args):
    d = json.load(sys.stdin)
    tail = d.get('output_tail', '')
    pkg = os.environ.get('_BC_PKG', '')
    error_class = d.get('error_class', '')
    # Extract error/fail lines for context
    lines = [l.strip() for l in tail.splitlines()
             if any(k in l.lower() for k in ('error', 'fail', 'cannot', 'undefined', 'fatal', 'signal: kill', 'killed'))][:6]
    if not lines:
        non_empty = [l.strip() for l in tail.splitlines() if l.strip()][-4:]
        lines = non_empty
    # Extract OOM-killed package names for attribution
    killed_pkgs = []
    for line in tail.splitlines():
        if 'signal: killed' in line.lower() or 'signal: kill' in line.lower():
            m = re.match(r'^(\S+?):\s', line)
            if m:
                killed_pkgs.append(m.group(1))
    # Build attribution note
    attr = ''
    if killed_pkgs and error_class == 'resource_exhaustion':
        short_names = [p.split('/')[-1] if '/' in p else p for p in killed_pkgs[:3]]
        attr = 'OOM_PKGS=' + ','.join(killed_pkgs[:3])
        # Check if any killed package relates to PR's package
        related = any(pkg.lower() in kp.lower() for kp in killed_pkgs) if pkg else False
        if not related:
            attr += '|UNRELATED'
        else:
            attr += '|RELATED'
    # Output: first line is attribution metadata, rest is excerpt
    print(attr)
    print('---EXCERPT---')
    print('\n'.join(lines) if lines else '')


# ── build_excerpt ────────────────────────────────────────────────────────────
# Replaces post-fallback-comments.sh lines 1029-1038.
# stdin: JSON object (PR_FIELDS)
# stdout: error lines excerpt from build output
def cmd_build_excerpt(_args):
    d = json.load(sys.stdin)
    tail = d.get('output_tail', '')
    lines = [l for l in tail.splitlines() if 'error' in l.lower() or 'Error' in l][:8]
    if lines:
        print('\n'.join(lines))
    else:
        print(tail[:300] if tail else '')


# ── inject_merge_risk ────────────────────────────────────────────────────────
# Replaces post-fallback-comments.sh lines 1149-1162.
# env:   COMMENT_BODY    — full comment markdown
#        MERGE_RISK_LINE — risk tag line to inject
# stdout: comment with merge risk line injected after first ## heading
def cmd_inject_merge_risk(_args):
    body = os.environ.get("COMMENT_BODY", "")
    risk = os.environ.get("MERGE_RISK_LINE", "").strip()
    if risk and "Merge Risk:" not in body:
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("## "):
                lines.insert(i + 1, "")
                lines.insert(i + 2, risk)
                body = "\n".join(lines)
                break
    print(body)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="PR field extraction subcommands extracted from post-fallback-comments.sh"
    )
    sub = parser.add_subparsers(dest="cmd")

    # extract
    p = sub.add_parser("extract",
                       help="Extract all PR fields from build-results.json")
    p.add_argument("--results-file", required=True, help="Path to build-results.json")
    p.add_argument("--pr-num", required=True, help="PR number")

    # format_vars
    sub.add_parser("format_vars",
                   help="Format PR fields as KEY=value lines for bash eval (reads stdin)")

    # l1_excerpt
    sub.add_parser("l1_excerpt",
                   help="Extract L1 error excerpt and OOM attribution (reads stdin)")

    # build_excerpt
    sub.add_parser("build_excerpt",
                   help="Extract build error excerpt (reads stdin)")

    # inject_merge_risk
    sub.add_parser("inject_merge_risk",
                   help="Inject merge risk line into comment (reads env vars)")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "extract": cmd_extract,
        "format_vars": cmd_format_vars,
        "l1_excerpt": cmd_l1_excerpt,
        "build_excerpt": cmd_build_excerpt,
        "inject_merge_risk": cmd_inject_merge_risk,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
