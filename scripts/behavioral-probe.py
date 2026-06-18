#!/usr/bin/env python3
"""Targeted behavioral probe (feed-deterministic-to-ai).

Fires ONE focused AI call per PR that hits the declared-behavioral residual:
build/tests/api-diff are clean, but the changelog declares a *behavioral* break
and the project's production code imports the affected package. The probe judges
whether the specific usage relies on the changed behavior and writes an advisory
`ai_behavioral_assessment` back into build-results.json.

Design invariants (rubber-ducked):
- Cost-bounded: only residual PRs are probed, capped at BP_MAX_PRS; zero residual
  PRs => zero AI calls.
- Advisory only: the result NEVER flips the deterministic verdict; the renderer
  keeps Medium/Review regardless.
- Fail-open: any error/timeout/invalid output for a PR leaves it UNannotated, so
  the deterministic comment is byte-identical to today. Never writes a partial or
  malformed annotation.
- No side effects from the agent: invoked with a minimal env (no GH tokens) and a
  prompt that forbids commands/comments/writes other than the one JSON file.
"""
import json
import os
import subprocess
import sys
import time

RESULTS = os.environ.get("BP_RESULTS", "/tmp/build-results.json")
PROMPT_FILE = os.environ.get("BP_PROMPT", ".github/behavioral-probe-prompt.md")
REPO_ROOT = os.environ.get("BP_REPO_ROOT", ".")
# Default agent command; override with BP_AGENT_CMD (e.g. a stub) for local testing.
AGENT_CMD = os.environ.get(
    "BP_AGENT_CMD", "agent -p --force --model claude-4-sonnet"
)
MAX_PRS = int(os.environ.get("BP_MAX_PRS", "5"))
MAX_SITES = int(os.environ.get("BP_MAX_SITES", "3"))
MAX_BULLETS = int(os.environ.get("BP_MAX_BULLETS", "3"))
SNIPPET_RADIUS = int(os.environ.get("BP_SNIPPET_RADIUS", "20"))
PROBE_TIMEOUT = int(os.environ.get("BP_TIMEOUT", "300"))
TMP = os.environ.get("BP_TMPDIR", "/tmp")

ALLOWED = {"affected", "not_affected", "uncertain"}


def log(msg):
    print(f"[behavioral-probe] {msg}", file=sys.stderr, flush=True)


def is_residual(pr):
    """The declared-behavioral residual gate (same predicate the renderer uses)."""
    r = pr.get("declared_break_reachability") or {}
    return bool(r.get("reachability_kind") == "import" and r.get("prod_reachable"))


def safe_int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def read_snippet(rel_path, line):
    """Read a small window around `line` from the importing file. Fail-soft to ''."""
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
    window = lines[lo:hi]
    snippet = "".join(window)
    return snippet[:4000]  # hard cap


def clean_bullets(raw_bullets):
    """Drop markdown headers / empty markers, collapse whitespace, dedupe (mirrors g6)."""
    import re
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


def build_context(num, pr):
    det = pr.get("deterministic") or {}
    sig = det.get("changelogSignal") or {}
    bullets = clean_bullets(sig.get("bullets") or [])[:MAX_BULLETS]
    r = pr.get("declared_break_reachability") or {}
    ev = [e for e in (r.get("evidence") or []) if isinstance(e, dict) and not e.get("is_test")]
    seen, sites = set(), []
    for e in ev:
        fp = e.get("file")
        key = (e.get("import_path") or e.get("path"), fp, e.get("line"))
        if not fp or key in seen:
            continue
        seen.add(key)
        sites.append({
            "import_path": e.get("import_path") or e.get("path") or "",
            "file": fp,
            "line": safe_int(e.get("line")),
            "snippet": read_snippet(fp, e.get("line")),
        })
        if len(sites) >= MAX_SITES:
            break
    if not bullets or not sites:
        return None
    return {
        "pr": str(num),
        "package": pr.get("package", ""),
        "from": pr.get("from", ""),
        "to": pr.get("to", ""),
        "ecosystem": pr.get("ecosystem", ""),
        "bullets": bullets,
        "call_sites": sites,
    }


def run_agent(in_path, out_path):
    """Invoke the agent with a minimal env. Returns True if it exited cleanly."""
    try:
        prompt = open(PROMPT_FILE).read()
    except Exception as e:
        log(f"cannot read prompt: {e}")
        return False
    full = (
        prompt
        + f"\n\n---\nBP_INPUT={in_path}\nBP_OUTPUT={out_path}\n"
        + "Read BP_INPUT, write the JSON array to BP_OUTPUT, then stop. "
        + "Do not run any other command and do not post anything."
    )
    # Minimal env: deny GH credentials so the agent cannot mutate PRs even if asked.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "CURSOR_API_KEY": os.environ.get("CURSOR_API_KEY", ""),
    }
    cmd = AGENT_CMD.split() + [full]
    try:
        cp = subprocess.run(
            cmd, env=env, timeout=PROBE_TIMEOUT,
            capture_output=True, text=True,
        )
        if cp.returncode != 0:
            log(f"agent exit {cp.returncode}: {cp.stderr[-400:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        log(f"agent timed out after {PROBE_TIMEOUT}s")
        return False
    except Exception as e:
        log(f"agent invocation failed: {e}")
        return False


def parse_output(out_path):
    """Validate the agent's JSON array. Returns a cleaned list or None (fail-open)."""
    try:
        raw = open(out_path).read().strip()
    except Exception:
        return None
    # Tolerate accidental markdown fences.
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("["):] if "[" in raw else raw
    try:
        arr = json.loads(raw)
    except Exception as e:
        log(f"invalid output json: {e}")
        return None
    if not isinstance(arr, list) or not arr:
        return None
    cleaned = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        v = str(item.get("verdict", "")).strip().lower()
        if v not in ALLOWED:
            continue
        conf = str(item.get("confidence", "")).strip().lower()
        cleaned.append({
            "bullet": str(item.get("bullet", ""))[:200],
            "verdict": v,
            "confidence": conf if conf in ("low", "medium", "high") else "low",
            "behavior_match": item.get("behavior_match"),
            "call_site": str(item.get("call_site", ""))[:200],
            "rationale": str(item.get("rationale", "")).strip()[:600],
            "limitations": str(item.get("limitations", "")).strip()[:400],
        })
    return cleaned or None


def derive_assessment(per_bullet):
    """Conservative PR-level rollup. Returns the ai_behavioral_assessment dict."""
    verdicts = {b["verdict"] for b in per_bullet}
    if "affected" in verdicts:
        pr_verdict = "affected"
        rep = next(b for b in per_bullet if b["verdict"] == "affected")
    elif "uncertain" in verdicts:
        pr_verdict = "uncertain"
        rep = next(b for b in per_bullet if b["verdict"] == "uncertain")
    else:
        pr_verdict = "not_affected"
        rep = per_bullet[0]
    return {
        "verdict": pr_verdict,
        "confidence": rep.get("confidence", "low"),
        "rationale": rep.get("rationale", ""),
        "call_site": rep.get("call_site", ""),
        "checked_behavior": rep.get("bullet", ""),
        "per_bullet": per_bullet,
        "model": AGENT_CMD,
        "generated_at": int(time.time()),
        "advisory": True,
    }


def main():
    if not os.path.isfile(RESULTS):
        log(f"no results file at {RESULTS}; nothing to do")
        return 0
    try:
        data = json.load(open(RESULTS))
    except Exception as e:
        log(f"cannot parse {RESULTS}: {e}")
        return 0
    prs = data.get("prs") or {}
    residual = [(n, pr) for n, pr in prs.items() if isinstance(pr, dict) and is_residual(pr)]
    if not residual:
        log("no declared-behavioral residual PRs; zero AI calls")
        return 0
    if len(residual) > MAX_PRS:
        log(f"{len(residual)} residual PRs exceeds cap {MAX_PRS}; probing first {MAX_PRS}")
        residual = residual[:MAX_PRS]

    annotated = 0
    for num, pr in residual:
        try:
            ctx = build_context(num, pr)
            if ctx is None:
                log(f"PR {num}: insufficient context (no bullets/sites); skipping")
                continue
            in_path = os.path.join(TMP, f"bp-in-{num}.json")
            out_path = os.path.join(TMP, f"bp-out-{num}.json")
            with open(in_path, "w") as f:
                json.dump(ctx, f)
            try:
                os.remove(out_path)
            except OSError:
                pass
            log(f"PR {num}: probing {ctx['package']} {ctx['from']}->{ctx['to']} "
                f"({len(ctx['bullets'])} bullets, {len(ctx['call_sites'])} sites)")
            if not run_agent(in_path, out_path):
                continue  # fail-open
            per_bullet = parse_output(out_path)
            if not per_bullet:
                log(f"PR {num}: no usable assessment; leaving unannotated")
                continue
            pr["ai_behavioral_assessment"] = derive_assessment(per_bullet)
            annotated += 1
            log(f"PR {num}: verdict={pr['ai_behavioral_assessment']['verdict']}")
        except Exception as e:
            log(f"PR {num}: probe failed ({e}); leaving unannotated")
            continue

    if annotated:
        try:
            with open(RESULTS, "w") as f:
                json.dump(data, f)
            log(f"annotated {annotated} PR(s); wrote {RESULTS}")
        except Exception as e:
            log(f"failed to write {RESULTS}: {e}")
    else:
        log("no PRs annotated; results unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
