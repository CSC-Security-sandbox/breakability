"""Call-site evidence helpers and targeted-note builder."""
import time

from break_class_router import NOT_OBSERVABLE
from cross_pr_reconciler import filter_package_correct_evidence

from .config import MAX_USAGES
from .utils import log, safe_int, read_snippet

__all__ = ["first_prod_site", "affected_files", "our_usages", "targeted_note"]


def first_prod_site(pr):
    r = pr.get("declared_break_reachability") or {}
    pr_package = pr.get("package") or ""
    # Prefer the ACTUAL usage site (surface_evidence: where our code references the
    # changed symbol, named first), not the bare import line. The probe/oracle must
    # reason about the real call, e.g. prometheus.New at metric.go:22, not the import.
    #
    # Package-correctness guard: filter evidence to prefer entries whose import path
    # matches this PR's package before selecting a call site.  This prevents a trace PR
    # from picking up a prometheus exporter call site from a sibling otel package that
    # happens to appear first in the evidence list.
    raw_surf = [e for e in (r.get("surface_evidence") or []) if isinstance(e, dict)]
    surf = filter_package_correct_evidence(raw_surf, pr_package)
    for e in sorted(surf, key=lambda x: (not x.get("named"),)):
        if not e.get("is_test") and e.get("file"):
            site_ip = e.get("path") or e.get("import_path") or ""
            site = {
                "import_path": site_ip,
                "symbol": e.get("symbol") or "",
                "file": e.get("file"),
                "line": safe_int(e.get("line")),
                "snippet": read_snippet(e.get("file"), safe_int(e.get("line"))),
            }
            if e.get("_package_mismatch"):
                log(f"package-mismatch fallback: PR package='{pr_package}' but "
                    f"best evidence import path='{site_ip}' -- no matching evidence found; "
                    f"oracle will reason about the wrong package unless evidence is corrected")
                site["_package_mismatch"] = True
            return site
    for e in (r.get("evidence") or []):
        if isinstance(e, dict) and not e.get("is_test") and e.get("file"):
            return {
                "import_path": e.get("import_path") or e.get("path") or "",
                "file": e.get("file"),
                "line": safe_int(e.get("line")),
                "snippet": read_snippet(e.get("file"), e.get("line")),
            }
    return None


def affected_files(pr):
    """Production files that import the affected package(s) (from reachability)."""
    r = pr.get("declared_break_reachability") or {}
    files = []
    for e in (r.get("evidence") or []):
        if isinstance(e, dict) and not e.get("is_test") and e.get("file"):
            files.append(e["file"])
    return set(files)


def our_usages(pr):
    """How OUR code uses the affected package: production usage rows in the files
    that import it. This is the 'grep our call sites + eyeball them' a dev does."""
    det = pr.get("deterministic") or {}
    files = affected_files(pr)
    out = []
    for u in (det.get("usages") or []):
        if not isinstance(u, dict):
            continue
        if str(u.get("context", "")).lower() != "production":
            continue
        if files and u.get("file") not in files:
            continue
        out.append({
            "file": u.get("file"), "line": u.get("line"),
            "symbol": u.get("symbol"), "usageType": u.get("usageType"),
        })
        if len(out) >= MAX_USAGES:
            break
    return out


def targeted_note(pr, bullet, site, router):
    """Honest, committed Medium note for a break a probe can't safely clear."""
    loc = f"{site['file']}:{site['line']}" if site and site.get("line") else (
        site.get("file") if site else "")
    if router["class"] == NOT_OBSERVABLE:
        why = ("this break depends on runtime state/load/timing and is not reproducible from a "
               "minimal probe; assess against your usage")
    else:
        why = "the declared change could not be pinned to a reproducible call-site behavior"
    return {
        "grade": "medium",
        "source": "router_not_observable" if router["class"] == NOT_OBSERVABLE else "router_ambiguous",
        "behavior_changed": "unverified",
        "rationale": f"{bullet[:200]} — {why}.",
        "guidance": (f"Affected package used at {loc}. " if loc else "") +
                    "Check whether your usage relies on the changed behavior.",
        "call_site": loc,
        "router_markers": router.get("markers", []),
        "confidence": "low",
        "generated_at": int(time.time()),
    }
