"""
core.pr_utils — CLI utility with subcommands for inline Python blocks
extracted from build-check.sh (Phase 8 modularization).

Each subcommand preserves the exact I/O contract of the original inline
python3 -c block it replaces.
"""
import argparse
import json
import os
import re
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))  # scripts/


# ── filter_prs ───────────────────────────────────────────────────────────────
# Replaces build-check.sh lines 111-123.
# stdin: JSON array of PR objects from gh pr list
# env:   _BC_PR_FILTER — comma-separated PR numbers (optional #-prefixed)
# stdout: JSON array filtered to only those PR numbers
def cmd_filter_prs(_args):
    prs = json.load(sys.stdin)
    pr_filter = os.environ.get('_BC_PR_FILTER', '')
    allowed = set()
    for token in pr_filter.split(','):
        token = token.strip().lstrip('#')
        if not re.fullmatch(r'[0-9]+', token or ''):
            continue
        allowed.add(token)
    filtered = [p for p in prs if str(p['number']) in allowed]
    print(json.dumps(filtered))


# ── merge_alerts ─────────────────────────────────────────────────────────────
# Replaces build-check.sh lines 196-213.
# stdin:  raw output from gh api --paginate (one JSON array per line)
# argv[0]: output file path for merged alerts
# stdout: count of alerts (for informational purposes)
def cmd_merge_alerts(args):
    alerts = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, list):
                alerts.extend(obj)
            else:
                alerts.append(obj)
        except json.JSONDecodeError:
            pass
    with open(args.output_file, "w") as f:
        json.dump(alerts, f)
    print(len(alerts))


# ── cve_enrich ───────────────────────────────────────────────────────────────
# Replaces build-check.sh lines 401-443.
# argv: --pkg PKG --alerts-cache PATH
# stdout: line 1 = comma-separated CVE/GHSA IDs, line 2 = JSON details array
def cmd_cve_enrich(args):
    pkg = args.pkg
    try:
        with open(args.alerts_cache) as f:
            alerts = json.load(f)
        matches = [a for a in alerts
                   if a.get('dependency', {}).get('package', {}).get('name', '') == pkg
                   and a.get('state') == 'open']
        cves = []
        cve_details = []
        for a in matches:
            adv = a.get('security_advisory', {})
            cve_id = adv.get('cve_id') or ''
            ghsa_id = adv.get('ghsa_id') or ''
            _id = cve_id or ghsa_id
            if _id and _id not in cves:
                cves.append(_id)
                # Extract CVSS score from cvss object (if present)
                cvss = adv.get('cvss', {})
                cvss_score = cvss.get('score', None)
                severity = adv.get('severity', 'unknown')
                summary = adv.get('summary', '')
                # Build advisory URL
                adv_url = ''
                if ghsa_id:
                    adv_url = f'https://github.com/advisories/{ghsa_id}'
                cve_details.append({
                    'id': _id,
                    'severity': severity,
                    'cvss_score': cvss_score,
                    'summary': summary[:200] if summary else '',
                    'advisory_url': adv_url,
                    'ghsa_id': ghsa_id,
                    'cve_id': cve_id,
                })
        # Output: line 1 = comma-separated IDs, line 2 = JSON details
        print(','.join(cves))
        print(json.dumps(cve_details))
    except Exception:
        print('')
        print('[]')


# ── nestjs_peer_warning ──────────────────────────────────────────────────────
# Replaces build-check.sh lines 551-566.
# argv: --pkg PKG --peer-groups-file PATH --results-file PATH
# stdout: warning string (or empty)
def cmd_nestjs_peer_warning(args):
    try:
        with open(args.peer_groups_file) as f:
            pg = json.load(f)
        with open(args.results_file) as f:
            data = json.load(f)
        nestjs = pg.get('nestjs_group', [])
        pkg = args.pkg
        if pkg in nestjs:
            others = [f'#{n} ({p["package"]})' for n, p in data.get('prs', {}).items()
                      if p.get('package', '').startswith('@nestjs/') and p['package'] != pkg]
            if others:
                print('NestJS peer group: upgrade ' + pkg + ' with: ' + ', '.join(others[:5]))
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    except Exception as e:
        print(f"WARNING: NestJS peer detection error: {e}", file=sys.stderr)


# ── reconcile_cli ────────────────────────────────────────────────────────────
# Replaces build-check.sh lines 611-680.
# argv: --cli-json-file PATH --pkg PKG
# env:   BC_FILES_IMPORTING — JSON array of importing file paths
# stdout: JSON object with reconciled deterministic fields
def cmd_reconcile_cli(args):
    with open(args.cli_json_file) as f:
        data = json.load(f)
    # ── Reconcile usages with the authoritative module-scoped import scan ──
    # scan_usage_npm/go/pip runs from PKG_DIR, so files_importing is scoped to the
    # bumped module. The bundled CLI computes usages repo-wide, which over-reports
    # callsites in sibling modules that this PR does not affect. A symbol cannot be
    # used without importing the package, so when zero files import it in scope the
    # package is NOT REACHED and there can be no reachable callsites. Clearing the
    # repo-wide usages here keeps deterministic.usages consistent with
    # deterministic.files_importing so the recommendation says 'review the changelog'
    # rather than inventing callsites to verify.
    try:
        _files_importing = json.loads(os.environ.get('BC_FILES_IMPORTING') or '[]')
    except (ValueError, TypeError):
        _files_importing = []
    _usages = data.get('usages') or []
    if not isinstance(_usages, list):
        _usages = []
    # NOT REACHED gate: when the scoped import scan finds zero importing files in the
    # bumped module, the package is not reachable and there can be no reachable callsite.
    # Exception: @types/* packages can contribute ambient/global TypeScript declarations
    # without an explicit import, so zero direct imports is NOT proof of no reachability.
    if not _files_importing and not args.pkg.startswith('@types/'):
        _usages = []
    # Ambient @types packages: mark as reached with synthetic entry
    _ambient_types = {'@types/node', '@types/jest', '@types/mocha', '@types/chai'}
    if not _files_importing and args.pkg in _ambient_types:
        _files_importing = ['(ambient type declarations)']

    neg = re.compile(r'\b(no|not|without|non[-\s]?breaking|does not|did not)\b.{0,80}\b(api change|breaking|incompatible|removed|behavior change)s?\b|\b(api change|breaking change)s?\b.{0,80}\b(no|not|without|none)\b', re.I)
    sig = data.get('changelogSignal')
    if isinstance(sig, dict):
        bullets = sig.get('bullets') or []
        clean_bullets = []
        for b in bullets:
            if not isinstance(b, str):
                continue
            flat = re.sub(r'\s+', ' ', b).strip()
            if flat and not neg.search(flat):
                clean_bullets.append(b)
        sig = dict(sig)
        sig['bullets'] = clean_bullets
        if str(sig.get('status') or '').lower() == 'breaking' and not clean_bullets:
            sig['status'] = 'none'
            sig['confidence'] = 'low'
            sig['summary'] = 'No non-negated breaking-change evidence found in the analyzed changelog.'
    result = {
        'api_changes': len(data.get('apiChanges', [])),
        'api_changes_detail': data.get('apiChanges', []),
        'usages': _usages,
        'verification': {
            'tier': data.get('verification', {}).get('tier', 0),
            'verified': data.get('verification', {}).get('verified', False),
            'compatible': data.get('verification', {}).get('compatible', None),
            'symbol_results': data.get('verification', {}).get('symbolResults', {})
        },
        'score': data.get('score', {}).get('total', 0),
        'classification': data.get('classification', 'INCONCLUSIVE'),
        'merge_risk': data.get('mergeRisk', {}),
        'confidence': data.get('confidence', 'UNVERIFIED'),
        'adapter': data.get('adapterUsed', 'unknown'),
        'api_diff_tool': data.get('apiDiffTool', None),
        'security': data.get('securityUpdate', None),
        'changelogText': data.get('changelogText', ''),
        'changelogSignal': sig
    }
    print(json.dumps(result))


# ── merge_apidiff ────────────────────────────────────────────────────────────
# Replaces build-check.sh lines 708-753.
# env:   DET_IN  — JSON string of existing deterministic block
#        AD_IN   — JSON string of apidiff output
# stdout: merged JSON object
def cmd_merge_apidiff(_args):
    _din = os.environ.get('DET_IN') or '{}'
    try:
        det = json.loads(_din)
    except Exception:
        det = {}
    if not isinstance(det, dict):
        det = {}
    try:
        ad = json.loads(os.environ['AD_IN'])
    except Exception:
        ad = {}
    compatible = ad.get('compatible', None)
    removed = ad.get('removed', []) or []
    changed = ad.get('changed', []) or []
    # Structured detail so policy_lowering._has_breaking_api_change classifies removals/
    # signature changes as hard breaks (changeType in its hard set), while a clean diff
    # (compatible) carries an empty detail list.
    detail = [{'name': n, 'changeType': 'removed'} for n in removed]
    detail += [
        {'name': (c.get('name') if isinstance(c, dict) else c), 'changeType': 'signature_changed'}
        for c in changed
    ]
    det['api_changes'] = int(ad.get('apiChanges', 0) or 0)
    det['api_changes_detail'] = detail
    # Mark as a SEMANTIC, module-mode tool: ts-apidiff compares exported type
    # signatures via the TypeScript compiler (the npm analogue of Go's apidiff), so a
    # zero-change result is HIGH-confidence proof of API backward-compatibility.
    # UNAVAILABLE (compatible is None) must NOT look like a clean module diff — set
    # api_changes None and omit module mode so the api_diff signal is UNAVAILABLE
    # (never a false "compatible"), e.g. a major bump whose old version shipped no types.
    if compatible is None:
        det['api_changes'] = None
        det['api_changes_detail'] = []
        det['api_diff_tool'] = {'name': 'ts-apidiff', 'status': 'unavailable'}
    else:
        det['api_diff_tool'] = {'name': 'ts-apidiff', 'mode': 'module', 'status': 'semantic'}
    ver = det.get('verification') or {}
    if not isinstance(ver, dict):
        ver = {}
    ver['compatible'] = compatible
    ver['api_diff_unavailable_reason'] = ad.get('reason', '') if compatible is None else ''
    det['verification'] = ver
    print(json.dumps(det))


# ── tidy_modules ─────────────────────────────────────────────────────────────
# Replaces build-check.sh lines 962-998.
# env:   _BC_IMPORT_JSON — JSON array of importing file paths
#        _BC_PKG_DIR     — package directory
# stdout: one module root per line (for go mod tidy)
def cmd_tidy_modules(_args):
    try:
        files = json.loads(os.environ.get('_BC_IMPORT_JSON', '[]'))
    except Exception:
        files = []
    # Find all go.mod files
    mod_roots = set()
    for root, dirs, fnames in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in ('vendor', '.git', 'node_modules')]
        if 'go.mod' in fnames:
            mod_roots.add(os.path.normpath(root))
    # Always include PKG_DIR module
    pkg_dir = os.environ.get('_BC_PKG_DIR', '/')
    if pkg_dir != '/' and os.path.isfile(os.path.join(pkg_dir, 'go.mod')):
        mod_roots.add(os.path.normpath(pkg_dir))
    # Find which modules own importing files
    affected = set()
    for f in files:
        path = f.split(':')[0]
        d = os.path.dirname(os.path.normpath(path)) or '.'
        for mr in sorted(mod_roots, key=lambda x: -x.count('/')):
            if d == mr or d.startswith(mr + '/'):
                affected.add(mr)
                break
        else:
            if '.' in mod_roots:
                affected.add('.')
    # If no importing files, at least tidy the PKG_DIR module
    if not affected:
        if pkg_dir != '/' and os.path.isfile(os.path.join(pkg_dir, 'go.mod')):
            affected.add(os.path.normpath(pkg_dir))
        elif '.' in mod_roots:
            affected.add('.')
    for m in sorted(affected):
        print(m)


# ── gosum_bumps ──────────────────────────────────────────────────────────────
# Replaces build-check.sh lines 1759-1785.
# env:   _BC_GOSUM_NEW_LINES — newline-separated go.sum diff lines
# stdout: JSON object { module: version } of highest-version bumped modules
def cmd_gosum_bumps(_args):
    def parse(v):
        s = v.lstrip("v").split("+", 1)[0].split("-", 1)[0]
        p = s.split(".")
        try:
            return tuple(int(x) for x in p[:3]) + (0,) * (3 - min(3, len(p)))
        except ValueError:
            return None
    best = {}
    for line in os.environ.get("_BC_GOSUM_NEW_LINES", "").splitlines():
        f = line.split()
        if len(f) < 2:
            continue
        mod, ver = f[0], f[1]
        # Only the content-hash line ("mod v1.2.3 h1:...") proves the module version was
        # actually SELECTED/built. A "/go.mod"-only line is just an MVS candidate and is
        # NOT proof of a resolved bump -- skip it to avoid over-crediting CVE fixes.
        if ver.endswith("/go.mod"):
            continue
        pv = parse(ver)
        if pv is None:
            continue
        if mod not in best or pv > best[mod][0]:
            best[mod] = (pv, ver)
    print(json.dumps({m: v for m, (pv, v) in best.items()}))


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="PR utility subcommands extracted from build-check.sh"
    )
    sub = parser.add_subparsers(dest="cmd")

    # filter_prs
    sub.add_parser("filter_prs",
                    help="Filter PR JSON array by _BC_PR_FILTER env var")

    # merge_alerts
    p = sub.add_parser("merge_alerts",
                       help="Merge paginated alert JSON arrays into one file")
    p.add_argument("output_file", help="Path to write merged alerts JSON")

    # cve_enrich
    p = sub.add_parser("cve_enrich",
                       help="Enrich PR with CVE details from alerts cache")
    p.add_argument("--pkg", required=True, help="Package name")
    p.add_argument("--alerts-cache", required=True, help="Path to alerts cache JSON")

    # nestjs_peer_warning
    p = sub.add_parser("nestjs_peer_warning",
                       help="Detect NestJS peer group upgrade opportunities")
    p.add_argument("--pkg", required=True, help="Package name")
    p.add_argument("--peer-groups-file", required=True, help="Path to peer groups JSON")
    p.add_argument("--results-file", required=True, help="Path to build-results.json")

    # reconcile_cli
    p = sub.add_parser("reconcile_cli",
                       help="Reconcile CLI output with scoped import scan")
    p.add_argument("--cli-json-file", required=True, help="Path to CLI JSON output")
    p.add_argument("--pkg", required=True, help="Package name")

    # merge_apidiff
    sub.add_parser("merge_apidiff",
                   help="Merge npm api-diff results into deterministic block")

    # tidy_modules
    sub.add_parser("tidy_modules",
                   help="Find Go modules needing tidy (reads env vars)")

    # gosum_bumps
    sub.add_parser("gosum_bumps",
                   help="Parse go.sum new lines into bumped modules JSON")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "filter_prs": cmd_filter_prs,
        "merge_alerts": cmd_merge_alerts,
        "cve_enrich": cmd_cve_enrich,
        "nestjs_peer_warning": cmd_nestjs_peer_warning,
        "reconcile_cli": cmd_reconcile_cli,
        "merge_apidiff": cmd_merge_apidiff,
        "tidy_modules": cmd_tidy_modules,
        "gosum_bumps": cmd_gosum_bumps,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
