#!/usr/bin/env python3
"""Precise Dependabot alert attribution for merge-results security posture."""

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "moderate": 2, "low": 3, "unknown": 4}
_ECOSYSTEM_ALIASES = {
    "go": "gomod",
    "gomod": "gomod",
    "go_modules": "gomod",
    "go-mod": "gomod",
    "npm": "npm",
    "npm_and_yarn": "npm",
    "github-actions": "actions",
    "github_actions": "actions",
    "actions": "actions",
}


def _parse_semver(v):
    if not v:
        return None
    s = str(v).lstrip("v").lstrip("=").strip()
    for sep in ("-", "+"):
        if sep in s:
            s = s.split(sep, 1)[0]
    parts = s.split(".")
    try:
        return tuple(int(p) for p in parts[:3]) + (0,) * (3 - min(3, len(parts)))
    except ValueError:
        return None


def semver_gte(a, b):
    pa, pb = _parse_semver(a), _parse_semver(b)
    if pa is None or pb is None:
        return False
    return pa >= pb


def _normalize_ecosystem(value):
    eco = str(value or "").strip().lower()
    return _ECOSYSTEM_ALIASES.get(eco, eco)


def _first_patched_version(alert):
    fpv = alert.get("security_vulnerability", {}).get("first_patched_version")
    if isinstance(fpv, dict):
        fpv = fpv.get("identifier")
    return fpv or ""


def _advisory_id(alert):
    advisory = alert.get("security_advisory", {})
    return advisory.get("ghsa_id") or advisory.get("cve_id") or ""


def _display_cve_id(alert):
    advisory = alert.get("security_advisory", {})
    return advisory.get("cve_id") or advisory.get("ghsa_id") or ""


def _ecosystems_match(alert, pr):
    alert_eco = _normalize_ecosystem(alert.get("dependency", {}).get("ecosystem"))
    pr_eco = _normalize_ecosystem(pr.get("ecosystem"))
    return bool(alert_eco and pr_eco and alert_eco != "unknown" and pr_eco != "unknown" and alert_eco == pr_eco)


def build_cve_attribution(open_alerts, prs):
    """Return (cve_fixes, orphan_alerts) with fail-closed version proof.

    A fix is credited only when package identity, ecosystem, advisory id, first patched
    version, PR, and resulting package version all line up, and resulting >= fixed-in.
    Missing/invalid fixed-in or resulting versions do not produce fix rows.
    """
    cve_fixes, orphan_alerts = [], []
    seen_fixes = set()
    seen_orphans = set()

    for alert in open_alerts:
        alert_pkg = alert.get("dependency", {}).get("package", "")
        alert_eco = _normalize_ecosystem(alert.get("dependency", {}).get("ecosystem"))
        fpv = _first_patched_version(alert)
        advisory_id = _advisory_id(alert)
        sev = alert.get("security_advisory", {}).get("severity", "unknown")
        cve = _display_cve_id(alert)
        summary = alert.get("security_advisory", {}).get("summary", "")
        matched = False

        for num, pr in prs.items():
            if not _ecosystems_match(alert, pr):
                continue

            bumped = pr.get("bumped_modules") or {}
            if pr.get("package", "") == alert_pkg:
                resulting_ver = pr.get("to", "")
                via = "primary"
            elif alert_pkg in bumped:
                resulting_ver = bumped[alert_pkg]
                via = "transitive"
            else:
                continue

            if fpv and semver_gte(resulting_ver, fpv):
                fix_key = (alert_pkg, alert_eco, advisory_id, fpv, str(num), str(resulting_ver))
                if fix_key not in seen_fixes:
                    seen_fixes.add(fix_key)
                    cve_fixes.append({
                        "pr": int(num) if str(num).isdigit() else num,
                        "package": alert_pkg,
                        "cve_id": cve,
                        "severity": sev,
                        "from_version": ("" if via == "transitive" else pr.get("from", "")),
                        "to_version": resulting_ver,
                        "primary_package": pr.get("package", ""),
                        "first_patched_version": fpv,
                        "via": via,
                        "summary": summary[:200],
                    })
                matched = True

        if not matched:
            orphan_key = (alert_pkg, alert_eco, advisory_id, fpv)
            if orphan_key not in seen_orphans:
                seen_orphans.add(orphan_key)
                orphan_alerts.append({
                    "cve_id": cve,
                    "package": alert_pkg,
                    "severity": sev,
                    "first_patched_version": fpv or "unknown",
                    "summary": summary[:200],
                })

    cve_fixes.sort(key=lambda x: (_SEV_ORDER.get((x["severity"] or "").lower(), 4), x.get("pr", 9999)))
    orphan_alerts.sort(key=lambda x: _SEV_ORDER.get((x["severity"] or "").lower(), 4))
    return cve_fixes, orphan_alerts
