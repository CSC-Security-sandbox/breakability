"""Sandbox safety helpers and agent runner."""
import json
import os
import subprocess

from .config import AGENT_CMD, PROMPT_FILE, PROBE_TIMEOUT, REPO_ROOT
from .grading import parse_contract
from .utils import log

__all__ = [
    "repo_porcelain", "scrub_env_for_agent", "create_ephemeral_home",
    "validate_workdir", "run_agent",
]


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
