#!/usr/bin/env python3
"""Differential probe (agent-driven behavioral verification).

For each declared-behavioral residual PR (build/tests/api-diff clean, but the
changelog declares a behavioral break that prod code imports), this driver commits
a behavioral grade in {none, low, medium, high} -- never "review it yourself".

Pipeline (closed loop), per residual PR:
  depth-1 usage scan (already done -> declared_break_reachability) supplies the call site
  -> break-class router decides if the break is observable from a minimal call-site probe
     - NOT observable (cardinality/memory/latency/concurrency/load/temporal/state) -> a probe
       would FALSE-GREEN it; commit honest Medium + targeted note. Zero AI calls.
     - AMBIGUOUS                                                                   -> Medium + note.
     - call-observable (default/return/error/format/signature)                     -> run the probe.
  -> probe: agent reads the dep source at from & to, builds+runs a self-contained probe that
     exercises ONLY the named dimension, diffs it -> emits a proof contract.
  -> DRIVER owns conservatism: floors the grade. Incomplete/timeout/can't-build/trigger-not-
     exercised -> Medium. None/Low ONLY with source-cited proof the break doesn't apply to us.

Invariants (two independent rubber-duck reviews):
- Never false-green: only call-observable breaks are probed; absence of an observed diff never
  lowers risk unless the trigger was actually exercised AND our exposure is disproven with a reason.
- Cost-bounded: not-observable/ambiguous residuals cost ZERO AI calls; probe-able ones cost one,
  capped at DP_MAX_PRS, and a per-(pkg,from,to,call-site) cache amortizes repeats.
- Sandboxed: the driver creates an ephemeral workdir, runs the agent there with cwd enforced, a
  scrubbed env (no GH/secrets, GOWORK=off, HOME=scratch), and verifies the repo stayed clean.
- Fail-open: any failure leaves a committed Medium (never a crash, never a false low/none).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from break_class_router import classify_break, NOT_OBSERVABLE, AMBIGUOUS, CALL_OBSERVABLE
from cross_pr_reconciler import filter_package_correct_evidence, reconcile_release_train_grades

RESULTS = os.environ.get("DP_RESULTS", "/tmp/build-results.json")
PROMPT_FILE = os.environ.get("DP_PROMPT", ".github/differential-probe-prompt.md")
REASON_PROMPT_FILE = os.environ.get("DP_REASON_PROMPT", ".github/differential-reasoning-prompt.md")
REPO_ROOT = os.environ.get("DP_REPO_ROOT", ".")
AGENT_CMD = os.environ.get("DP_AGENT_CMD", "agent -p --force --model claude-4-sonnet")
# Substitute a {model} placeholder (consistent with ai_backend's template) so a
# shared BRK/DP command template can be reused without fragile shell brace-escaping.
_DP_MODEL = os.environ.get("DP_AGENT_MODEL", "").strip()
if "{model}" in AGENT_CMD:
    AGENT_CMD = AGENT_CMD.replace("{model}", _DP_MODEL or "claude-sonnet-4.5")
MAX_PRS = int(os.environ.get("DP_MAX_PRS", "5"))
MAX_REASON = int(os.environ.get("DP_MAX_REASON", "15"))
MAX_BULLETS = int(os.environ.get("DP_MAX_BULLETS", "5"))
MAX_USAGES = int(os.environ.get("DP_MAX_USAGES", "20"))
SNIPPET_RADIUS = int(os.environ.get("DP_SNIPPET_RADIUS", "20"))
PROBE_TIMEOUT = int(os.environ.get("DP_TIMEOUT", "360"))
REASON_TIMEOUT = int(os.environ.get("DP_REASON_TIMEOUT", "180"))
CACHE_DIR = os.environ.get("DP_CACHE_DIR", "/tmp/dp-cache")
NPM_PROBE_TIMEOUT = int(os.environ.get("DP_NPM_TIMEOUT", "120"))
NPM_PROBE_ROOT = os.environ.get("DP_NPM_PROBE_ROOT", os.path.join(REPO_ROOT, ".github", ".npm-probe-work"))
# When set, grade ONLY deterministic npm runtime-shape candidates and skip any
# residual that would require the AI agent. Lets the deterministic npm probe run
# under --skip-ai (no agent backend) without spending/needing AI budget.
DETERMINISTIC_ONLY = str(os.environ.get("DP_DETERMINISTIC_ONLY", "")).strip().lower() in ("1", "true", "yes")
PROMPT_VERSION = "dp-v1"
REASON_PROMPT_VERSION = "dr-v1"

GRADES = ("none", "low", "medium", "high")

_NPM_NAME_RE = re.compile(r"^(?:@[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+|[A-Za-z0-9._~-]+)$")
_NPM_VERSION_RE = re.compile(r"^[A-Za-z0-9._~:+\-]+$")


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
                    f"best evidence import path='{site_ip}' — no matching evidence found; "
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


# ── proof-contract parsing + conservative grade derivation ──────────────────
def parse_contract(out_path):
    try:
        raw = open(out_path).read().strip()
    except Exception:
        return None
    if raw.startswith("```"):
        raw = raw.strip("`")
        i = raw.find("{")
        raw = raw[i:] if i >= 0 else raw
        j = raw.rfind("}")
        raw = raw[: j + 1] if j >= 0 else raw
    try:
        obj = json.loads(raw)
    except Exception as e:
        log(f"invalid contract json: {e}")
        return None
    return obj if isinstance(obj, dict) else None


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes"):
            return True
        if s in ("false", "no"):
            return False
    return None  # unknown / "unclear"


# ── provenance helpers ──────────────────────────────────────────────────────
# Strings the agent might write as placeholder "observed" values that do NOT
# constitute real probe output. Checked case-insensitively.
_TRIVIAL_OUTPUT = frozenset({
    "", "none", "n/a", "null", "undefined", "unknown", "false", "true",
    "0", "1", "[]", "{}", '""', "''", "pass", "ok", "error", "(none)", "n/a.",
    "no output", "no change", "-", "--",
})
# Minimum bytes for a non-trivial observed value.
_MIN_OBSERVED_LEN = 4


def _observed_output_is_real(ofrom: str, oto: str) -> bool:
    """Both observed_from/to are non-trivial, indicating the probe actually ran
    and captured concrete output rather than filling in placeholder text."""
    f = ofrom.strip()
    t = oto.strip()
    return (
        len(f) >= _MIN_OBSERVED_LEN
        and len(t) >= _MIN_OBSERVED_LEN
        and f.lower() not in _TRIVIAL_OUTPUT
        and t.lower() not in _TRIVIAL_OUTPUT
    )


def _evidence_grounded_in_sources(evidence_text: str, source_context) -> bool:
    """Return True iff evidence_text contains at least one verifiable anchor
    (a token of ≥10 characters) drawn from the supplied changelog/bullet/callsite
    inputs that were fed to the agent.

    Conservative by design: if all source texts are too generic (no 10-char
    tokens), or if source_context is absent, this returns False (→ Medium floor).
    AI-authored prose that does not quote from the actual supplied inputs fails
    the check regardless of length.
    """
    if not source_context or not evidence_text:
        return False
    ev = evidence_text.lower()
    # Changelog text and bullet: shared technical tokens are reliable anchors.
    for key in ("bullet", "changelog_text"):
        src = (source_context.get(key) or "").strip().lower()
        for token in re.findall(r"[a-z0-9_/.\-]{10,}", src):
            if token in ev:
                return True
    # Call-site: the file path or symbol name are stable identifiers.
    cs = source_context.get("call_site") or {}
    for anchor_key in ("file", "symbol"):
        anchor = (cs.get(anchor_key) or "").strip()
        if len(anchor) >= 6 and anchor.lower() in ev:
            return True
    return False


def derive_grade(c, source_context=None):
    grade, reason = _derive_grade_raw(c)
    # PROOF FLOOR: the AI may only LOWER risk with grounded proof.
    # A probe-derived low/none must be backed by:
    #   (a) real observed from->to values (both non-trivial — the probe ran), OR
    #   (b) evidence text containing a verifiable anchor from the supplied
    #       changelog/bullet/callsite inputs (not arbitrary prose).
    # Length of invented prose is NOT sufficient — floored back to Medium.
    if grade in ("low", "none"):
        ofrom = str(c.get("observed_from", "")).strip()
        oto = str(c.get("observed_to", "")).strip()
        evidence = str(c.get("evidence", "")).strip()
        if not (_observed_output_is_real(ofrom, oto)
                or _evidence_grounded_in_sources(evidence, source_context)):
            return "medium", (
                "probe lowered risk but evidence lacked provenance: "
                "observed from/to were absent or trivial, and evidence contained "
                "no verifiable anchor from the supplied changelog/bullet/callsite; "
                "floored to Medium (no false-green)"
            )
    return grade, reason


def _derive_grade_raw(c):
    """Driver-owned conservative floors. Returns (grade, reason).

    Base for any declared-behavioral residual is MEDIUM. We only move OFF Medium with
    real evidence:
      - HIGH: trigger exercised, behavior changed, and our usage is exposed.
      - LOW : either (a) trigger exercised and behavior did NOT change for the named
              dimension, or (b) our usage is provably NOT exposed (reasoned mapping).
      - NONE: reserved -- requires (b) AND an explicit not-used mapping; otherwise floored to LOW.
    Anything incomplete (no probe / trigger not exercised / unknown) stays MEDIUM.
    """
    built = _as_bool(c.get("probe_built"))
    exercised = _as_bool(c.get("trigger_condition_exercised"))
    changed = _as_bool(c.get("behavior_changed"))
    exposed = _as_bool(c.get("our_usage_exposed"))
    mapping = (c.get("our_usage_mapping") or "").strip()

    # Incomplete proof -> Medium floor.
    if not built or exercised is not True:
        return "medium", "probe did not exercise the trigger; committed at Medium (no false-green)"

    if changed is True:
        if exposed is True:
            return "high", "probe exercised the trigger; behavior changed and our usage is exposed"
        if exposed is False and len(mapping) >= 12:
            return "low", "behavior changed but our usage is provably not exposed: " + mapping[:160]
        return "medium", "behavior changed; our exposure is unclear -> Medium"

    if changed is False:
        # Trigger was actually exercised and the named dimension did not change for us.
        if exposed is False and len(mapping) >= 12:
            return "none", "trigger exercised, no change, and our usage does not rely on it: " + mapping[:140]
        return "low", "trigger exercised; the named behavior did not change in a way that affects this call"

    return "medium", "inconclusive probe result; committed at Medium"


def build_grade_from_contract(c, source_context=None):
    grade, reason = derive_grade(c, source_context)
    return {
        "grade": grade,
        "source": "probe",
        "rationale": reason,
        "changed_behavior": str(c.get("changed_behavior_summary", ""))[:200],
        "observed_from": str(c.get("observed_from", ""))[:200],
        "observed_to": str(c.get("observed_to", ""))[:200],
        "trigger_condition": str(c.get("trigger_condition", ""))[:200],
        "trigger_exercised": _as_bool(c.get("trigger_condition_exercised")),
        "behavior_changed": _as_bool(c.get("behavior_changed")),
        "our_usage_exposed": _as_bool(c.get("our_usage_exposed")),
        "our_usage_mapping": str(c.get("our_usage_mapping", ""))[:300],
        "evidence": str(c.get("evidence", ""))[:600],
        "limitations": str(c.get("limitations", ""))[:300],
        "confidence": str(c.get("confidence", "low")).strip().lower()
        if str(c.get("confidence", "")).strip().lower() in ("low", "medium", "high") else "low",
        "probe_commands": [str(x)[:200] for x in (c.get("probe_commands") or [])][:8],
        "model": AGENT_CMD,
        "generated_at": int(time.time()),
        "honest_cap": "reproduced the documented change under a synthetic call configuration; "
                      "not a production guarantee",
    }


# ── not-observable reasoning oracle (release-notes + usage, no execution) ────
def derive_reasoning_grade(c, source_context=None):
    grade, reason = _derive_reasoning_grade_raw(c)
    # PROOF FLOOR: lowering to LOW requires evidence grounded in the supplied
    # changelog/bullet/callsite inputs, not arbitrary "structurally avoids" prose.
    # Length alone is not sufficient — the reasoning path has no probe output to
    # rely on, so only source-text grounding counts. No anchor → honest Medium.
    if grade == "low":
        evidence = str(c.get("evidence", "")).strip()
        if not _evidence_grounded_in_sources(evidence, source_context):
            return "medium", (
                "release-notes reasoning suggested low exposure but evidence "
                "lacked verifiable provenance (no anchor from supplied "
                "changelog/bullet/callsite); committed at Medium (no false-green)"
            )
    return grade, reason


def _derive_reasoning_grade_raw(c):
    """Conservative floors for the release-notes reasoning oracle.

    This oracle reasons about a break a probe CANNOT reproduce (cardinality, memory,
    latency, retry, concurrency, stateful). It cannot PROVE absence of a runtime break,
    so it NEVER returns None. It mirrors how a senior dev reads the release notes and
    maps them to our call sites:
      - HIGH  : our usage plausibly HITS the trigger condition, with cited reasoning.
      - LOW   : our usage STRUCTURALLY avoids the trigger, with cited reasoning.
      - MEDIUM: uncertain / under-justified (the honest default, never a shrug).
    """
    assess = str(c.get("exposure_assessment", "")).strip().lower()
    reasoning = str(c.get("exposure_reasoning", "")).strip()
    if assess == "hits" and len(reasoning) >= 24:
        return "high", "release-notes reasoning: our usage hits the trigger condition — " + reasoning[:200]
    if assess in ("avoids", "structurally_avoids", "not_exposed") and len(reasoning) >= 24:
        return "low", "release-notes reasoning: our usage structurally avoids the trigger — " + reasoning[:200]
    return "medium", "release-notes reasoning: exposure uncertain; committed at Medium"


def build_reasoning_grade(c, site, router, source_context=None):
    grade, reason = derive_reasoning_grade(c, source_context)
    loc = f"{site['file']}:{site['line']}" if site and site.get("line") else (site.get("file") if site else "")
    return {
        "grade": grade,
        "source": "reasoning",
        "rationale": reason,
        "trigger_condition": str(c.get("trigger_condition", ""))[:240],
        "our_relevant_usage": str(c.get("our_relevant_usage", ""))[:300],
        "exposure_assessment": str(c.get("exposure_assessment", ""))[:40],
        "guidance": str(c.get("guidance", ""))[:300] or (f"Affected package used at {loc}." if loc else ""),
        "evidence": str(c.get("evidence", ""))[:600],
        "behavior_changed": "declared",
        "call_site": loc,
        "router_class": router["class"],
        "router_markers": router.get("markers", []),
        "confidence": str(c.get("confidence", "low")).strip().lower()
        if str(c.get("confidence", "")).strip().lower() in ("low", "medium", "high") else "low",
        "limitations": str(c.get("limitations", ""))[:300]
        or "reasoned from release notes + static usage; not a runtime guarantee",
        "model": AGENT_CMD,
        "generated_at": int(time.time()),
    }



def repo_porcelain():
    try:
        cp = subprocess.run(["git", "-C", REPO_ROOT, "status", "--porcelain"],
                            capture_output=True, text=True, timeout=30)
        return cp.stdout
    except Exception:
        return None


# ── sandbox safety helpers ──────────────────────────────────────────────────
def scrub_env_for_agent(env, keep_api_key=True, work_gocache=None):
    """Return a scrubbed environment for sandboxed agent execution.
    
    Removes all credential-like variables and dangerous paths, keeping only
    essentials (USER, TERM, etc). Intentionally aggressive to prevent credential
    leakage through obscure paths.
    """
    out = {}
    # Allowlist: minimum vars needed for a normal shell/Go execution
    safe_prefixes = ("TERM", "LANG", "LC_", "USER", "LOGNAME", "SHELL", "TMPDIR")
    safe_exact = ("PATH", "HOME", "PWD")  # These will be set/verified separately
    # Go module-resolution config (no secrets) needed for the probe to build/run.
    go_passthrough = (
        "GOPROXY", "GOFLAGS", "GOSUMDB", "GONOSUMCHECK", "GONOSUMDB",
        "GOPRIVATE", "GONOSUM", "GOPATH", "GOMODCACHE", "GOTOOLCHAIN",
        "GOOS", "GOARCH", "GOROOT", "GOINSECURE",
    )
    
    # Credential/secret patterns to remove (case-insensitive key checks)
    dangerous_patterns = (
        "TOKEN", "SECRET", "PASSWORD", "PASSWD", "KEY", "CREDENTIAL", "PRIVATE",
        "AUTH", "CERTIFICATE", "CERT", "KEYFILE", "PEM", "RSA", "SSH",
        "API_KEY", "APIKEY", "BEARER", "SESSION", "VAULT", "KUBE",
        "SLACK_", "STRIPE_", "DATABASE_", "DB_", "GOOGLE_", "AWS_",
        "AZURE_", "DOCKER_", "ENCRYPTION", "CERTIFICATE", "ENCRYPTION_",
    )
    
    for k, v in env.items():
        ku = k.upper()
        
        # Skip special handling for model-access keys if requested.
        # CURSOR_API_KEY (cursor-agent) and COPILOT_GITHUB_TOKEN (copilot
        # backend) are model-access credentials -- the agent needs one to reach
        # its model. They are kept symmetrically; no broad GH_TOKEN/GITHUB_TOKEN
        # is ever passed through (those stay scrubbed to prevent repo-cred leak).
        if ku in ("CURSOR_API_KEY", "COPILOT_GITHUB_TOKEN"):
            if keep_api_key and v:
                out[k] = v
            continue

        # Explicit safe allowlist, checked BEFORE the dangerous-pattern strip so
        # legitimately-named vars survive (e.g. GOPRIVATE contains "PRIVATE").
        # - go_passthrough: Go module-resolution config, carries no secrets.
        # - BREAKDEP_/BRK_PROBE_: execution-proof sentinel dir paths (test + prod).
        if k in go_passthrough or ku.startswith("BREAKDEP_") or ku.startswith("BRK_PROBE_"):
            out[k] = v
            continue
        
        # Remove any var with dangerous patterns
        if any(pat in ku for pat in dangerous_patterns):
            continue
        
        # Remove dangerous path/library vars
        if ku in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH", "PERL5LIB", 
                  "RUBYLIB", "CLASSPATH", "DYLD_LIBRARY_PATH"):
            continue
        
        # Remove package manager paths that might be hijacked
        if ku in ("NPM_CONFIG_PREFIX", "PIP_INDEX_URL", "GEM_HOME"):
            continue
        
        # Keep safe allowlisted vars
        if any(k.startswith(p) for p in safe_prefixes):
            out[k] = v
        elif k in safe_exact:
            out[k] = v
    
    # Force safe Go config
    if work_gocache:
        out["GOCACHE"] = work_gocache
    out["GOWORK"] = "off"
    
    return out


def create_ephemeral_home(workdir):
    """Create an ephemeral HOME directory within workdir for agent isolation.
    
    Returns the path to the ephemeral HOME. Ensures no inherited credentials
    are accessible to the agent.
    """
    home = os.path.join(workdir, ".agent-home")
    os.makedirs(home, exist_ok=True)
    
    # Create empty SSH and git config dirs to prevent agent from
    # discovering or using inherited credentials
    for subdir in (".ssh", ".gnupg"):
        d = os.path.join(home, subdir)
        os.makedirs(d, exist_ok=True)
    
    # Minimal gitconfig to prevent git credential lookups
    gitconfig = os.path.join(home, ".gitconfig")
    try:
        with open(gitconfig, "w") as f:
            f.write("[user]\n")
            f.write("    name = Differential Probe\n")
            f.write("    email = dp@local\n")
    except Exception:
        pass
    
    return home


def validate_workdir(workdir, parent_temp_dir):
    """Validate that workdir is within expected temporary boundaries.
    
    Returns True if workdir is safely contained within parent_temp_dir
    (preventing escape attempts via symlinks or path traversal).
    """
    try:
        # Resolve symlinks and relative paths
        workdir_real = os.path.realpath(workdir)
        parent_real = os.path.realpath(parent_temp_dir)
        
        # workdir must be exactly parent or a direct child
        if workdir_real == parent_real:
            return True
        if workdir_real.startswith(parent_real + os.sep):
            return True
        
        return False
    except Exception:
        return False


def run_agent(ctx, workdir, prompt_file=PROMPT_FILE, timeout=PROBE_TIMEOUT):
    try:
        prompt = open(prompt_file).read()
    except Exception as e:
        log(f"cannot read prompt: {e}")
        return None
    
    # Sandbox setup: ephemeral HOME and workdir validation
    ephemeral_home = create_ephemeral_home(workdir)
    if not validate_workdir(ephemeral_home, workdir):
        log(f"PR {ctx['pr']}: SAFETY -- ephemeral home validation failed")
        return None
    
    in_path = os.path.join(workdir, "dp-in.json")
    out_path = os.path.join(workdir, "dp-out.json")
    with open(in_path, "w") as f:
        json.dump(ctx, f)
    full = (prompt + f"\n\n---\nDP_INPUT={in_path}\nDP_OUTPUT={out_path}\nDP_WORKDIR={workdir}\n"
            + "cd into DP_WORKDIR first. Read DP_INPUT, do the analysis there only, write the "
              "proof-contract JSON to DP_OUTPUT, then stop.")
    
    # Sandbox environment: ephemeral HOME + comprehensive credential scrubbing
    # The agent runs with cwd=workdir (preventing repo writes) and HOME=ephemeral
    # (isolating any dotfiles/caches). All credential-like vars are scrubbed.
    env = scrub_env_for_agent(os.environ, keep_api_key=True,
                              work_gocache=os.path.join(workdir, "gocache"))
    env["HOME"] = ephemeral_home
    
    # Pass the secret on the command line so the real Cursor CLI authenticates from it
    # directly (not from a stored login / keychain). Skip for stub agents used in tests.
    cmd = AGENT_CMD.split()
    prog = os.path.basename(cmd[0]) if cmd else ""
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if api_key and cmd and prog in ("agent", "cursor-agent") and "--api-key" not in cmd:
        cmd = cmd + ["--api-key", api_key]
    # Copilot CLI arg-completion (mirrors ai_backend.build_argv): needs agentic
    # tool access, clean stdout, and the prompt passed via -p (which must come
    # LAST so the prompt becomes its value rather than swallowing a later flag).
    if prog == "copilot":
        if "--allow-all-tools" not in cmd and "--allow-all" not in cmd:
            cmd = cmd + ["--allow-all-tools"]
        if "--no-color" not in cmd:
            cmd = cmd + ["--no-color"]
        if "-p" in cmd:
            cmd = [c for c in cmd if c != "-p"]
        if "--prompt" not in cmd:
            cmd = cmd + ["-p"]
    
    before = repo_porcelain()
    try:
        cp = subprocess.run(cmd + [full], env=env, cwd=workdir,
                            timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        log(f"PR {ctx['pr']}: agent timed out after {timeout}s (sandbox cleaned on exit)")
        return None
    except Exception as e:
        log(f"PR {ctx['pr']}: agent invocation failed: {e}")
        return None
    
    after = repo_porcelain()
    # Fail CLOSED: if we cannot prove the repo tree is unchanged, discard the result.
    if before is None or after is None or before != after:
        log(f"PR {ctx['pr']}: SAFETY -- repo cleanliness unverified/changed; discarding probe result")
        return None
    if cp.returncode != 0:
        log(f"PR {ctx['pr']}: agent exit {cp.returncode}: {cp.stderr[-300:]}")
        # still try to read output -- the agent may have written before a nonzero exit
    return parse_contract(out_path)


# ── deterministic npm runtime-shape probe ────────────────────────────────────
def is_npm_probe_candidate(pr):
    """npm probe is deterministic and does not consume AI budget.

    It is useful even when the release-note residual router has no call site: npm
    api-diff can be unavailable (no shipped types) or shallow (barrel packages).
    """
    if str(pr.get("ecosystem") or "").strip().lower() != "npm":
        return False
    return bool(str(pr.get("package") or "").strip()
                and str(pr.get("from") or "").strip()
                and str(pr.get("to") or "").strip())


def _is_private_npm_package(pkg):
    return pkg.startswith("@netapp-cloud-datamigrate/")


def _valid_npm_ref(pkg, version):
    if not _NPM_NAME_RE.match(pkg or ""):
        return False
    if "/" in pkg and not pkg.startswith("@"):
        return False
    if ".." in pkg or pkg.startswith("-"):
        return False
    return bool(_NPM_VERSION_RE.match(version or "")) and not version.startswith("-")


def npm_unavailable_grade(reason, commands=None):
    return {
        "grade": "medium",
        "source": "probe",
        "probe_kind": "npm_runtime_shape",
        "behavior_changed": "unverified",
        "same_behavior": None,
        "rationale": f"npm runtime-shape probe unavailable: {str(reason)[:220]}; committed at Medium (no false-green).",
        "confidence": "low",
        "probe_commands": commands or [],
        "generated_at": int(time.time()),
    }


def _npm_snapshot_digest(snapshot):
    raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _npm_loaded(snapshot):
    load = snapshot.get("load") if isinstance(snapshot, dict) else {}
    req = load.get("require") if isinstance(load, dict) else {}
    imp = load.get("import") if isinstance(load, dict) else {}
    return bool((isinstance(req, dict) and req.get("ok")) or (isinstance(imp, dict) and imp.get("ok")))


def npm_grade_from_snapshots(pkg, from_version, to_version, old_snapshot=None, new_snapshot=None,
                             error="", commands=None):
    """Build behavioral_grade from two npm runtime snapshots.

    SAME is emitted only after both versions installed, the snapshot script ran,
    at least one entrypoint loader succeeded for each version, and canonical
    existing public runtime exports and compatibility-sensitive package
    metadata show no removed/changed surface. Additive exports do not block a
    same-behavior result.
    """
    commands = commands or []
    if error:
        return npm_unavailable_grade(error, commands)
    if not isinstance(old_snapshot, dict) or not isinstance(new_snapshot, dict):
        return npm_unavailable_grade("missing old/new runtime snapshot", commands)
    if not old_snapshot.get("ok") or not new_snapshot.get("ok"):
        return npm_unavailable_grade("snapshot script did not complete for both versions", commands)
    if not _npm_loaded(old_snapshot) or not _npm_loaded(new_snapshot):
        return npm_unavailable_grade("entrypoint did not require()/import() successfully for both versions", commands)

    old_hash = _npm_snapshot_digest(old_snapshot)
    new_hash = _npm_snapshot_digest(new_snapshot)
    old_summary = _npm_snapshot_summary(old_snapshot, old_hash)
    new_summary = _npm_snapshot_summary(new_snapshot, new_hash)
    breaking_diff = _npm_breaking_diff(old_snapshot, new_snapshot)
    if not breaking_diff:
        return {
            "grade": "low",
            "source": "probe",
            "probe_kind": "npm_runtime_shape",
            "behavior_changed": False,
            "same_behavior": True,
            "rationale": (
                f"npm runtime-shape probe installed {pkg}@{from_version} and {pkg}@{to_version}; "
                "existing runtime exports, loader status, and compatibility-sensitive package metadata matched."
            ),
            "observed_from": old_summary,
            "observed_to": new_summary,
            "evidence": f"{pkg} runtime export shape matched under Node; no removed exports or incompatible package map changes.",
            "confidence": "high",
            "probe_commands": commands,
            "generated_at": int(time.time()),
        }
    return {
        "grade": "medium",
        "source": "probe",
        "probe_kind": "npm_runtime_shape",
        "behavior_changed": True,
        "same_behavior": False,
        "changed_behavior": "npm package metadata, loader status, or runtime export shape differs",
        "rationale": (
            f"npm runtime-shape probe installed {pkg}@{from_version} and {pkg}@{to_version}; "
            "observable runtime surface differed, so this cannot be auto-cleared."
        ),
        "observed_from": old_summary,
        "observed_to": new_summary,
        "evidence": "; ".join(breaking_diff)[:600] or _npm_diff_hint(old_snapshot, new_snapshot),
        "confidence": "high",
        "probe_commands": commands,
        "generated_at": int(time.time()),
    }


def _prop_is_breaking(old_p, new_p):
    """True only if an EXISTING export's runtime shape changed in a compatibility-
    sensitive way. Pure additions (a method/static gained on an existing export
    object) are additive and NOT breaking — axios/react-router minors commonly add
    members to the default export object while preserving every existing one.

    Breaking = kind changed (e.g. function->object), an accessor turned into/out of
    a getter/setter, a function's arity DECREASED (lost a required parameter), or a
    nested own-property was REMOVED. Added nested keys and arity increases (usually
    new optional params) do not count.
    """
    if not isinstance(old_p, dict) or not isinstance(new_p, dict):
        return old_p != new_p
    if old_p.get("type") != new_p.get("type"):
        return True
    if bool(old_p.get("accessor")) != bool(new_p.get("accessor")):
        return True
    if old_p.get("accessor") or new_p.get("accessor"):
        if (old_p.get("get"), old_p.get("set")) != (new_p.get("get"), new_p.get("set")):
            return True
    if old_p.get("type") == "function" and new_p.get("type") == "function":
        oa, na = old_p.get("arity"), new_p.get("arity")
        if isinstance(oa, int) and isinstance(na, int) and na < oa:
            return True
    old_keys = set(old_p.get("keys") or [])
    new_keys = set(new_p.get("keys") or [])
    if old_keys - new_keys:
        return True
    return False


def _npm_breaking_diff(old_snapshot, new_snapshot):
    """Compatibility-sensitive npm snapshot differences.

    Additive exports are not breakage: axios minors commonly add explicit export
    aliases while preserving the old entrypoints. Removed exports, changed old
    export targets, engines/main/module/type changes, loader changes, or changed
    existing runtime export shapes remain review-worthy. package.browser is NOT
    compared: it only steers bundlers (webpack/browserify) and never affects the
    Node require()/import() runtime this probe actually exercises.
    """
    diffs = []
    for path in (("package", "main"), ("package", "module"), ("package", "type"),
                 ("package", "engines"), ("load", "require"),
                 ("load", "import"), ("surface", "root")):
        old_v = _dig(old_snapshot, path)
        new_v = _dig(new_snapshot, path)
        if old_v != new_v:
            diffs.append(".".join(path))

    old_exports = _dig(old_snapshot, ("package", "exports"))
    new_exports = _dig(new_snapshot, ("package", "exports"))
    if isinstance(old_exports, dict) and isinstance(new_exports, dict):
        old_keys = set(old_exports)
        new_keys = set(new_exports)
        removed = sorted(old_keys - new_keys)
        if removed:
            diffs.append("removed_package_exports=" + ",".join(removed[:20]))
        changed = [k for k in sorted(old_keys & new_keys) if old_exports.get(k) != new_exports.get(k)]
        if changed:
            diffs.append("changed_package_exports=" + ",".join(changed[:20]))
    elif old_exports != new_exports:
        diffs.append("package.exports")

    old_props = _dig(old_snapshot, ("surface", "props")) or {}
    new_props = _dig(new_snapshot, ("surface", "props")) or {}
    if isinstance(old_props, dict) and isinstance(new_props, dict):
        removed = sorted(set(old_props) - set(new_props))
        if removed:
            diffs.append("removed_exports=" + ",".join(removed[:20]))
        changed = [k for k in sorted(set(old_props) & set(new_props))
                   if _prop_is_breaking(old_props.get(k), new_props.get(k))]
        if changed:
            diffs.append("changed_exports=" + ",".join(changed[:20]))
    elif old_props != new_props:
        diffs.append("surface.props")
    return diffs


def _npm_snapshot_summary(snapshot, digest):
    surface = snapshot.get("surface") if isinstance(snapshot, dict) else {}
    keys = surface.get("keys") if isinstance(surface, dict) else []
    pkg = snapshot.get("package") if isinstance(snapshot, dict) else {}
    load = snapshot.get("load") if isinstance(snapshot, dict) else {}
    req_ok = bool(((load.get("require") if isinstance(load, dict) else {}) or {}).get("ok"))
    imp_ok = bool(((load.get("import") if isinstance(load, dict) else {}) or {}).get("ok"))
    return (
        f"shape_sha256={digest[:16]} keys={len(keys) if isinstance(keys, list) else 0} "
        f"require_ok={req_ok} import_ok={imp_ok} "
        f"main={str((pkg or {}).get('main',''))[:40]} exports={bool((pkg or {}).get('exports'))}"
    )


def _npm_diff_hint(old_snapshot, new_snapshot):
    hints = []
    for path in (("package", "main"), ("package", "module"), ("package", "type"),
                 ("package", "exports"), ("package", "engines"), ("load", "require"),
                 ("load", "import"), ("surface", "root")):
        old_v = _dig(old_snapshot, path)
        new_v = _dig(new_snapshot, path)
        if old_v != new_v:
            hints.append(".".join(path))
    old_keys = set(_dig(old_snapshot, ("surface", "keys")) or [])
    new_keys = set(_dig(new_snapshot, ("surface", "keys")) or [])
    removed = sorted(old_keys - new_keys)[:20]
    added = sorted(new_keys - old_keys)[:20]
    if removed:
        hints.append("removed_exports=" + ",".join(removed))
    if added:
        hints.append("added_exports=" + ",".join(added))
    return "; ".join(hints)[:600] or "canonical runtime snapshots differed"


def _dig(obj, path):
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def run_npm_differential_probe(num, pr):
    pkg = str(pr.get("package") or "").strip()
    from_version = str(pr.get("from") or "").strip()
    to_version = str(pr.get("to") or "").strip()
    commands = [
        f"npm i --no-save --ignore-scripts {pkg}@{from_version}",
        f"npm i --no-save --ignore-scripts {pkg}@{to_version}",
        "node npm-runtime-shape-probe.mjs",
    ]
    if _is_private_npm_package(pkg):
        return npm_unavailable_grade("workspace/private package is not probeable from public npm registry", commands)
    if not _valid_npm_ref(pkg, from_version) or not _valid_npm_ref(pkg, to_version):
        return npm_unavailable_grade("invalid npm package/version reference", commands)
    if shutil.which("npm") is None or shutil.which("node") is None:
        return npm_unavailable_grade("node/npm executable not found", commands)

    os.makedirs(NPM_PROBE_ROOT, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix=f"npm-dp-{num}-", dir=NPM_PROBE_ROOT)
    try:
        old_snapshot = _npm_install_and_snapshot(pkg, from_version, os.path.join(workdir, "old"))
        new_snapshot = _npm_install_and_snapshot(pkg, to_version, os.path.join(workdir, "new"))
        return npm_grade_from_snapshots(pkg, from_version, to_version, old_snapshot, new_snapshot, commands=commands)
    except Exception as e:
        return npm_unavailable_grade(str(e), commands)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _npm_install_and_snapshot(pkg, version, project_dir):
    project_dir = os.path.abspath(project_dir)
    os.makedirs(project_dir, exist_ok=False)
    with open(os.path.join(project_dir, "package.json"), "w") as f:
        json.dump({"name": "breakability-npm-probe", "private": True, "type": "commonjs"}, f)
    env = _npm_probe_env(project_dir)
    install = subprocess.run(
        [
            "npm", "i", "--no-save", "--ignore-scripts", "--no-audit", "--no-fund",
            "--registry", "https://registry.npmjs.org/", f"{pkg}@{version}",
        ],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=NPM_PROBE_TIMEOUT,
        check=False,
    )
    if install.returncode != 0:
        raise RuntimeError(f"npm install {pkg}@{version} failed: {(install.stderr or install.stdout)[-500:]}")
    script_path = os.path.join(project_dir, "npm-runtime-shape-probe.mjs")
    with open(script_path, "w") as f:
        f.write(_NPM_RUNTIME_SHAPE_SCRIPT)
    snap = subprocess.run(
        ["node", script_path, pkg],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=min(NPM_PROBE_TIMEOUT, 60),
        check=False,
    )
    if snap.returncode != 0:
        raise RuntimeError(f"node runtime snapshot for {pkg}@{version} failed: {(snap.stderr or snap.stdout)[-500:]}")
    try:
        obj = json.loads(snap.stdout)
    except Exception as e:
        raise RuntimeError(f"invalid npm runtime snapshot JSON for {pkg}@{version}: {e}")
    return obj


def _npm_probe_env(project_dir):
    env = scrub_env_for_agent(os.environ, keep_api_key=False)
    env["PATH"] = os.environ.get("PATH", "")
    env["HOME"] = os.path.join(project_dir, "home")
    env["NPM_CONFIG_CACHE"] = os.path.join(project_dir, ".npm-cache")
    env["NPM_CONFIG_REGISTRY"] = "https://registry.npmjs.org/"
    env["NPM_CONFIG_IGNORE_SCRIPTS"] = "true"
    for d in (env["HOME"], env["NPM_CONFIG_CACHE"]):
        os.makedirs(d, exist_ok=True)
    return env


_NPM_RUNTIME_SHAPE_SCRIPT = r'''
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const pkgName = process.argv[2];
const req = createRequire(import.meta.url);

function stable(value) {
  if (value === undefined) return "__undefined__";
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(stable);
  const out = {};
  for (const key of Object.keys(value).sort()) out[key] = stable(value[key]);
  return out;
}

function err(e) {
  return { name: String(e && e.name || "Error"), code: String(e && e.code || ""), message: String(e && e.message || "").slice(0, 160) };
}

function status(r) {
  return r.ok ? { ok: true } : { ok: false, error: r.error };
}

function pkgDir() {
  return path.join(process.cwd(), "node_modules", ...pkgName.split("/"));
}

function readPackageJson() {
  const raw = JSON.parse(fs.readFileSync(path.join(pkgDir(), "package.json"), "utf8"));
  return stable({
    name: raw.name || "",
    type: raw.type || "",
    main: raw.main || "",
    module: raw.module || "",
    browser: raw.browser || "",
    types: raw.types || raw.typings || "",
    exports: raw.exports || null,
    engines: raw.engines || null,
  });
}

function describeValue(value, depth = 0, seen = new Set()) {
  const t = typeof value;
  const out = { type: t };
  if (t === "function") out.arity = value.length;
  if ((t !== "object" && t !== "function") || value === null) return out;
  if (seen.has(value)) {
    out.circular = true;
    return out;
  }
  seen.add(value);
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const keys = Object.keys(descriptors).sort().slice(0, 200);
  out.keys = keys;
  out.props = {};
  for (const key of keys) {
    const d = descriptors[key];
    if (!d) continue;
    if ("get" in d || "set" in d) {
      out.props[key] = { accessor: true, get: typeof d.get === "function", set: typeof d.set === "function" };
      continue;
    }
    const v = d.value;
    const vt = typeof v;
    const p = { type: vt };
    if (vt === "function") p.arity = v.length;
    if (depth < 1 && v && (vt === "object" || vt === "function")) {
      const child = Object.getOwnPropertyDescriptors(v);
      p.keys = Object.keys(child).sort().slice(0, 80);
    }
    out.props[key] = p;
  }
  return out;
}

function tryRequire() {
  try { return { ok: true, value: req(pkgName) }; }
  catch (e) { return { ok: false, error: err(e) }; }
}

async function tryImport() {
  try { return { ok: true, value: await import(pkgName) }; }
  catch (e) { return { ok: false, error: err(e) }; }
}

const required = tryRequire();
const imported = await tryImport();
const chosen = required.ok ? required.value : (imported.ok ? imported.value : null);
const surface = chosen ? describeValue(chosen) : null;
console.log(JSON.stringify({
  ok: true,
  package: readPackageJson(),
  load: { require: status(required), import: status(imported) },
  surface,
}, null, 0));
'''




# ── cache ───────────────────────────────────────────────────────────────────
def cache_key(ctx):
    sig = "|".join([
        ctx.get("kind", "probe"),
        ctx.get("package", ""), ctx.get("from", ""), ctx.get("to", ""),
        (ctx.get("call_site") or {}).get("file", ""),
        str((ctx.get("call_site") or {}).get("line", "")),
        ctx.get("bullet", ""),
        ctx.get("prompt_version", PROMPT_VERSION),
    ])
    return hashlib.sha256(sig.encode()).hexdigest()[:24]


def cache_get(key):
    try:
        return json.load(open(os.path.join(CACHE_DIR, key + ".json")))
    except Exception:
        return None


def cache_put(key, contract):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, key + ".json"), "w") as f:
            json.dump(contract, f)
    except Exception:
        pass


# ── main ────────────────────────────────────────────────────────────────────
def grade_residual(num, pr, budgets):
    """Return a committed behavioral_grade dict for one residual PR (never None).

    budgets = {"probe": int, "reason": int} -- remaining AI-call allowances. The
    returned dict may carry a private "_ai_kind" ("probe"/"reason") + "_ai_attempted"
    so main() can decrement the right budget.

    The returned dict always includes "call_site_import_path" (the import path of the
    package at the selected call site) so the cross-PR reconciler can detect
    package-mismatch grades (e.g. a trace PR whose site resolved to a prometheus
    exporter because that entry appeared first in the evidence list).
    """
    g = _grade_residual_inner(num, pr, budgets)
    site = first_prod_site(pr)
    if isinstance(g, dict) and site:
        g.setdefault("call_site_import_path", site.get("import_path") or "")
        if site.get("_package_mismatch"):
            g.setdefault("_call_site_package_mismatch", True)
    return g


def _grade_residual_inner(num, pr, budgets):
    if is_npm_probe_candidate(pr):
        log(f"PR {num}: deterministic npm runtime-shape probe for {pr.get('package')} "
            f"{pr.get('from')}->{pr.get('to')}")
        return run_npm_differential_probe(num, pr)

    det = pr.get("deterministic") or {}
    sig = det.get("changelogSignal") or {}
    # Classify over ALL bullets before truncating prompt payload. A not-observable
    # runtime/default/config bullet hidden after MAX_BULLETS must still veto probing;
    # otherwise earlier call-observable bullets can create a false-green probe.
    all_bullets = clean_bullets(sig.get("bullets") or [])
    bullets = all_bullets[:MAX_BULLETS]
    site = first_prod_site(pr)
    router = classify_break(all_bullets)

    if not all_bullets or not site:
        # No usable scope -> honest Medium (still committed, no punt).
        return {
            "grade": "medium", "source": "insufficient_context",
            "behavior_changed": "unverified",
            "rationale": "declared behavioral break is import-reachable but lacks a precise "
                         "call site/changelog bullet to verify.",
            "confidence": "low", "generated_at": int(time.time()),
            "router_class": router["class"],
        }

    if not router["probe_recommended"]:
        # NOT_OBSERVABLE / AMBIGUOUS: a probe would be structurally blind (false-green
        # risk). Instead, reason over the release notes + our usage like a senior dev
        # would -- a graded, cited verdict, NOT a shrug-Medium. Falls back to the
        # honest targeted note if the reasoning budget is spent (no AI attempt).
        bullet = router["observable_bullet"] or bullets[0]
        if budgets.get("reason", 0) > 0:
            return run_reasoning(num, pr, site, router, bullets)
        note = targeted_note(pr, bullet, site, router)
        note["router_class"] = router["class"]
        log(f"PR {num}: router={router['class']} -> Medium note (reason budget spent; {router['reason']})")
        return note

    if budgets.get("probe", 0) <= 0:
        loc = f"{site['file']}:{site['line']}" if site.get("line") else site.get("file", "")
        return {
            "grade": "medium", "source": "budget_exhausted",
            "behavior_changed": "unverified",
            "rationale": "probe budget exhausted for this run; committed at Medium.",
            "guidance": f"Affected package used at {loc}." if loc else "",
            "call_site": loc, "confidence": "low", "generated_at": int(time.time()),
            "router_class": router["class"],
        }

    # call-observable -> probe (with cache)
    ctx = {
        "pr": str(num), "kind": "probe", "prompt_version": PROMPT_VERSION,
        "package": pr.get("package", ""), "ecosystem": pr.get("ecosystem", ""),
        "from": pr.get("from", ""), "to": pr.get("to", ""),
        "bullet": router["observable_bullet"], "dimension_hint": router.get("markers", []),
        "call_site": site,
    }
    key = cache_key(ctx)
    contract = cache_get(key)
    cached = contract is not None
    ai_attempted = False
    if not cached:
        ai_attempted = True  # a cache miss reaches run_agent -> counts against the budget
        workdir = tempfile.mkdtemp(prefix=f"dp-{num}-")
        try:
            log(f"PR {num}: router=call_observable -> probing {ctx['package']} {ctx['from']}->{ctx['to']}")
            contract = run_agent(ctx, workdir, PROMPT_FILE, PROBE_TIMEOUT)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if contract is not None:
            cache_put(key, contract)

    if contract is None:
        # Probe failed -> committed Medium floor (no false-green).
        loc = f"{site['file']}:{site['line']}" if site.get("line") else site.get("file", "")
        return {
            "grade": "medium", "source": "probe_failed",
            "behavior_changed": "unverified",
            "rationale": f"{ctx['bullet'][:200]} — probe could not be built/run; committed at Medium.",
            "guidance": f"Affected package used at {loc}." if loc else "",
            "call_site": loc, "confidence": "low", "generated_at": int(time.time()),
            "router_class": router["class"], "_ai_kind": "probe", "_ai_attempted": ai_attempted,
        }

    try:
        source_ctx = {
            "bullet": ctx.get("bullet", ""),
            "changelog_text": str((pr.get("deterministic") or {}).get("changelogText", "")),
            "call_site": site,
        }
        g = build_grade_from_contract(contract, source_ctx)
    except Exception as e:
        # Agent already ran (budget consumed); a malformed contract must NOT escape
        # un-counted or crash the loop. Commit Medium, preserve the budget flag.
        loc = f"{site['file']}:{site['line']}" if site.get("line") else site.get("file", "")
        log(f"PR {num}: contract parse failed ({e}); committing Medium")
        return {
            "grade": "medium", "source": "probe_contract_invalid",
            "behavior_changed": "unverified",
            "rationale": f"{ctx['bullet'][:200]} — probe ran but returned an unusable proof contract; committed at Medium.",
            "guidance": f"Affected package used at {loc}." if loc else "",
            "call_site": loc, "confidence": "low", "generated_at": int(time.time()),
            "router_class": router["class"], "_ai_kind": "probe", "_ai_attempted": ai_attempted,
        }
    g["router_class"] = router["class"]
    g["cached"] = cached
    g["_ai_kind"] = "probe"
    g["_ai_attempted"] = ai_attempted
    g["call_site"] = f"{site['file']}:{site['line']}" if site.get("line") else site.get("file", "")
    log(f"PR {num}: probe grade={g['grade']} ({g['rationale'][:80]})")
    return g


def run_reasoning(num, pr, site, router, bullets):
    """Release-notes + usage reasoning oracle for not-observable breaks. ALWAYS returns
    a committed grade dict (never None). On a cache MISS it consumes one reason-budget
    AI call (marked via _ai_kind/_ai_attempted); on failure it falls back to the honest
    Medium targeted note BUT keeps the budget marker so a flapping oracle can't burn
    unbounded calls."""
    det = pr.get("deterministic") or {}
    bullet = router["observable_bullet"] or bullets[0]
    ctx = {
        "pr": str(num), "kind": "reason", "prompt_version": REASON_PROMPT_VERSION,
        "package": pr.get("package", ""), "ecosystem": pr.get("ecosystem", ""),
        "from": pr.get("from", ""), "to": pr.get("to", ""),
        "bullet": bullet, "all_bullets": bullets,
        "changelog_text": str(det.get("changelogText", ""))[:4000],
        "dimension_hint": router.get("markers", []),
        "call_site": site, "our_usages": our_usages(pr),
    }
    key = cache_key(ctx)
    contract = cache_get(key)
    cached = contract is not None
    ai_attempted = not cached
    if not cached:
        workdir = tempfile.mkdtemp(prefix=f"dr-{num}-")
        try:
            log(f"PR {num}: router={router['class']} -> reasoning over release notes for "
                f"{ctx['package']} {ctx['from']}->{ctx['to']}")
            contract = run_agent(ctx, workdir, REASON_PROMPT_FILE, REASON_TIMEOUT)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if contract is not None:
            cache_put(key, contract)

    if contract is None:
        # Oracle failed -> honest Medium note, but the AI attempt still counts.
        note = targeted_note(pr, bullet, site, router)
        note["router_class"] = router["class"]
        note["source"] = "reasoning_failed"
        note["_ai_kind"] = "reason"
        note["_ai_attempted"] = ai_attempted
        log(f"PR {num}: reasoning oracle produced no contract; committed Medium note")
        return note
    try:
        source_ctx = {
            "bullet": ctx.get("bullet", ""),
            "changelog_text": ctx.get("changelog_text", ""),
            "call_site": ctx.get("call_site"),
        }
        g = build_reasoning_grade(contract, site, router, source_ctx)
    except Exception as e:
        log(f"PR {num}: reasoning contract invalid ({e}); falling back to Medium note")
        g = targeted_note(pr, bullet, site, router)
        g["router_class"] = router["class"]
        g["source"] = "reasoning_invalid"
    g["cached"] = cached
    g["_ai_kind"] = "reason"
    g["_ai_attempted"] = ai_attempted
    log(f"PR {num}: reasoning grade={g['grade']} ({g.get('rationale','')[:80]})")
    return g


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
    if DETERMINISTIC_ONLY:
        candidates = [
            (n, pr) for n, pr in prs.items()
            if isinstance(pr, dict) and is_npm_probe_candidate(pr)
        ]
        if not candidates:
            log("deterministic-only mode: no npm runtime-shape probe candidates; nothing to grade")
            return 0
    else:
        candidates = [
            (n, pr) for n, pr in prs.items()
            if isinstance(pr, dict) and (is_residual(pr) or is_npm_probe_candidate(pr))
        ]
        if not candidates:
            log("no declared-behavioral residual or npm probe candidate PRs; nothing to grade")
            return 0
    probe_budget = MAX_PRS
    reason_budget = MAX_REASON
    annotated = 0

    def _persist():
        # Atomic: write to a temp file in the same dir, then os.replace() over RESULTS so a
        # mid-loop kill can never leave a truncated/corrupt artifact for downstream steps.
        try:
            d = os.path.dirname(os.path.abspath(RESULTS)) or "."
            fd, tmp = tempfile.mkstemp(prefix=".dp-results-", dir=d)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f)
                os.replace(tmp, RESULTS)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return True
        except Exception as e:
            log(f"failed to write {RESULTS}: {e}")
            return False

    for num, pr in candidates:
        try:
            g = grade_residual(num, pr, {"probe": probe_budget, "reason": reason_budget})
            kind = g.pop("_ai_kind", None)
            if g.pop("_ai_attempted", False):
                if kind == "probe":
                    probe_budget -= 1
                elif kind == "reason":
                    reason_budget -= 1
            pr["behavioral_grade"] = g
            annotated += 1
        except Exception as e:
            log(f"PR {num}: grading failed ({e}); committing Medium")
            pr["behavioral_grade"] = {
                "grade": "medium", "source": "error", "behavior_changed": "unverified",
                "rationale": "behavioral grading failed; committed at Medium.",
                "confidence": "low", "generated_at": int(time.time()),
            }
            annotated += 1
        # Persist after EACH PR so a mid-loop timeout/kill (budgets can exceed the step
        # timeout) does not discard grades already committed.
        _persist()

    # ── Cross-PR reconciliation: detect package-mismatch and grade inconsistency ──
    # Run AFTER all PRs are graded so every behavioral_grade is populated.
    # This catches the "trace PR reasoning about a prometheus exporter callsite" class
    # of bugs and flags same-evidence -> different-grade inconsistencies within
    # release-train groups (otel #23/#27/#36, k8s modules, etc.).
    try:
        reconcile_notes = reconcile_release_train_grades(prs, data.get("cross_pr_deps") or [])
        if reconcile_notes:
            log(f"cross-PR reconciliation flagged {len(reconcile_notes)} PR(s)")
            for num, note in reconcile_notes.items():
                if num in prs and isinstance(prs[num].get("behavioral_grade"), dict):
                    prs[num]["behavioral_grade"]["reconciliation_note"] = note
                    log(f"  PR {num}: {note[:120]}")
            _persist()
    except Exception as e:
        log(f"cross-PR reconciliation failed (non-fatal): {e}")

    if annotated:
        if _persist():
            log(f"committed behavioral grades for {annotated} residual PR(s); wrote {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
