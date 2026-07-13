"""Small utility functions for the differential probe."""
import os
import re
import sys

from .config import REPO_ROOT, SNIPPET_RADIUS

__all__ = ["log", "is_residual", "safe_int", "read_snippet", "clean_bullets"]


def log(msg):
    print(f"[differential-probe] {msg}", file=sys.stderr, flush=True)


def is_residual(pr):
    r = pr.get("declared_break_reachability") or {}
    return bool(r.get("reachability_kind") == "import" and r.get("prod_reachable"))


def safe_int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def read_snippet(rel_path, line):
    line = safe_int(line)
    if not rel_path or line is None:
        return ""
    path = os.path.join(REPO_ROOT, rel_path)
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return ""
    lo = max(0, line - 1 - SNIPPET_RADIUS)
    hi = min(len(lines), line + SNIPPET_RADIUS)
    return "".join(lines[lo:hi])[:4000]


def clean_bullets(raw_bullets):
    out, seen = [], set()
    for b in raw_bullets:
        if not isinstance(b, str):
            continue
        s = re.sub(r"\s+", " ", b.replace("\r", " ").replace("\n", " ")).strip(" -*\t")
        if not s or s.startswith("#"):
            continue
        s = s[:400]
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out
