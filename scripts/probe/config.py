"""Configuration constants for the differential probe."""
import os
import re

# ── directory helpers ─────────────────────────────────────────────────────────
# _SCRIPTS_DIR = scripts/  (parent of probe/)
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── results and prompt paths ──────────────────────────────────────────────────
RESULTS = os.environ.get("DP_RESULTS", "/tmp/build-results.json")
_PROMPTS_DIR = os.environ.get("BREAKABILITY_PROMPTS_DIR",
    os.path.join(os.path.dirname(_SCRIPTS_DIR), "prompts"))
PROMPT_FILE = os.environ.get("DP_PROMPT", os.path.join(_PROMPTS_DIR, "differential-probe-prompt.md"))
REASON_PROMPT_FILE = os.environ.get("DP_REASON_PROMPT", os.path.join(_PROMPTS_DIR, "differential-reasoning-prompt.md"))
REPO_ROOT = os.environ.get("DP_REPO_ROOT", ".")

# ── agent command ─────────────────────────────────────────────────────────────
AGENT_CMD = os.environ.get("DP_AGENT_CMD", "agent -p --force --model claude-4-sonnet")
# Substitute a {model} placeholder (consistent with ai_backend's template) so a
# shared BRK/DP command template can be reused without fragile shell brace-escaping.
_DP_MODEL = os.environ.get("DP_AGENT_MODEL", "").strip()
if "{model}" in AGENT_CMD:
    AGENT_CMD = AGENT_CMD.replace("{model}", _DP_MODEL or "claude-sonnet-4.5")

# ── budgets and limits ────────────────────────────────────────────────────────
MAX_PRS = int(os.environ.get("DP_MAX_PRS", "5"))
MAX_REASON = int(os.environ.get("DP_MAX_REASON", "15"))
MAX_BULLETS = int(os.environ.get("DP_MAX_BULLETS", "5"))
MAX_USAGES = int(os.environ.get("DP_MAX_USAGES", "20"))
SNIPPET_RADIUS = int(os.environ.get("DP_SNIPPET_RADIUS", "20"))
PROBE_TIMEOUT = int(os.environ.get("DP_TIMEOUT", "360"))
REASON_TIMEOUT = int(os.environ.get("DP_REASON_TIMEOUT", "180"))
CACHE_DIR = os.environ.get("DP_CACHE_DIR", "/tmp/dp-cache")

# ── npm probe config ─────────────────────────────────────────────────────────
NPM_PROBE_TIMEOUT = int(os.environ.get("DP_NPM_TIMEOUT", "120"))
NPM_PROBE_ROOT = os.environ.get("DP_NPM_PROBE_ROOT", os.path.join(REPO_ROOT, ".github", ".npm-probe-work"))

# ── Go module probe config ───────────────────────────────────────────────────
GOMOD_PROBE_TIMEOUT = int(os.environ.get("DP_GOMOD_TIMEOUT", "120"))
GOMOD_PROBE_ROOT = os.environ.get("DP_GOMOD_PROBE_ROOT", os.path.join(REPO_ROOT, ".github", ".gomod-probe-work"))

# ── deterministic-only mode ──────────────────────────────────────────────────
# When set, grade ONLY deterministic npm runtime-shape candidates and skip any
# residual that would require the AI agent. Lets the deterministic npm probe run
# under --skip-ai (no agent backend) without spending/needing AI budget.
DETERMINISTIC_ONLY = str(os.environ.get("DP_DETERMINISTIC_ONLY", "")).strip().lower() in ("1", "true", "yes")

# ── prompt versions ──────────────────────────────────────────────────────────
PROMPT_VERSION = "dp-v1"
REASON_PROMPT_VERSION = "dr-v1"

# ── grade levels ─────────────────────────────────────────────────────────────
GRADES = ("none", "low", "medium", "high")

# ── npm validation patterns ──────────────────────────────────────────────────
_NPM_NAME_RE = re.compile(r"^(?:@[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+|[A-Za-z0-9._~-]+)$")
_NPM_VERSION_RE = re.compile(r"^[A-Za-z0-9._~:+\-]+$")

# ── Go module validation patterns ────────────────────────────────────────────
_GOMOD_RE = re.compile(r"^[a-zA-Z0-9_.~\-]+(/[a-zA-Z0-9_.~\-]+)*$")
_GOMOD_VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+([.\-][\w.+\-]+)?$")

__all__ = [
    "RESULTS", "PROMPT_FILE", "REASON_PROMPT_FILE", "REPO_ROOT",
    "AGENT_CMD", "MAX_PRS", "MAX_REASON", "MAX_BULLETS", "MAX_USAGES",
    "SNIPPET_RADIUS", "PROBE_TIMEOUT", "REASON_TIMEOUT", "CACHE_DIR",
    "NPM_PROBE_TIMEOUT", "NPM_PROBE_ROOT",
    "GOMOD_PROBE_TIMEOUT", "GOMOD_PROBE_ROOT",
    "DETERMINISTIC_ONLY", "PROMPT_VERSION", "REASON_PROMPT_VERSION",
    "GRADES",
    # Private names re-exported for backward compatibility
    "_PROMPTS_DIR", "_DP_MODEL", "_SCRIPTS_DIR",
    "_NPM_NAME_RE", "_NPM_VERSION_RE",
    "_GOMOD_RE", "_GOMOD_VERSION_RE",
]
