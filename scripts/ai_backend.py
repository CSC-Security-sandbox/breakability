#!/usr/bin/env python3
"""Unified AI model backend with record/replay cassettes.

ONE interface for every AI call in the breakability pipeline (M8 changelog
comprehension, M9 reachability adjudication, M10 behavioral/differential probes,
M12 reconciliation). The same prompt can run against:

  * a live agent CLI  -- Cursor ``agent`` or any compatible command,
  * a recorded cassette -- deterministic, OFFLINE, sub-second, for unit tests
    and the fast local loop (no model calls, no network, no keychain),
  * a record pass -- capture live responses into the cassette corpus once,
    then replay them forever.

Backend selection is purely by environment, so NO caller changes when swapping
local Copilot / Cursor-in-CI / replay:

  BRK_AGENT_MODE   = live (default) | replay | record
  BRK_AGENT_CMD    = command template (default ``agent -p --force --model {model}``)
  BRK_AGENT_MODEL  = model name (default ``claude-4-sonnet``)
  BRK_CASSETTE_DIR = cassette dir (default ``.github/breakability/harness/cassettes``)

Local validation with GitHub Copilot CLI (same model class, no Cursor/key) -- the
pipeline is model-agnostic, so only env changes:

  BRK_AGENT_CMD="copilot --model {model}" BRK_AGENT_MODEL="claude-sonnet-4.5" \\
    BRK_AGENT_MODE=record   # capture cassettes once, then replay offline forever

``copilot`` is auto-completed to a non-interactive, clean-stdout invocation
(``--allow-all-tools --no-color -p <prompt>``); ``-p`` is forced last so it never
swallows another flag. Use ``record`` to bless cassettes, ``replay`` for the
sub-second deterministic loop, ``live`` only when tuning prompts.

Cassette identity: an explicit, stable ``key`` (preferred -- e.g. ``adj-10``) or,
when absent, ``sha256(namespace + "\\x00" + prompt)``. One JSON file per key.

Fail-safe invariants (the whole pipeline already treats empty output as "skip ->
stay REVIEW"):
  * replay MISS  -> returns "" (never fabricates a response, never crashes),
  * live error/timeout -> returns "" (caller stays fail-safe),
  * record runs live, persists, and returns the same text.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from typing import Optional

DEFAULT_MODEL = "claude-4-sonnet"
DEFAULT_CMD_TEMPLATE = "agent -p --force --model {model}"
DEFAULT_CASSETTE_DIR = ".github/breakability/harness/cassettes"
DEFAULT_TIMEOUT = 300

MODE_LIVE = "live"
MODE_REPLAY = "replay"
MODE_RECORD = "record"


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


def resolve_mode() -> str:
    m = _env("BRK_AGENT_MODE", MODE_LIVE).strip().lower()
    return m if m in (MODE_LIVE, MODE_REPLAY, MODE_RECORD) else MODE_LIVE


def resolve_model(model: Optional[str] = None) -> str:
    return model or _env("BRK_AGENT_MODEL", DEFAULT_MODEL)


def resolve_cmd_template(cmd: Optional[str] = None) -> str:
    return cmd or _env("BRK_AGENT_CMD", DEFAULT_CMD_TEMPLATE)


def resolve_cassette_dir(cassette_dir: Optional[str] = None) -> str:
    return cassette_dir or _env("BRK_CASSETTE_DIR", DEFAULT_CASSETTE_DIR)


def _sanitize_key(key: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in key)
    return safe[:120] or "_"


def cassette_id(namespace: str, prompt: str, key: Optional[str] = None) -> str:
    """Stable cassette identity. Prefer an explicit human-readable key; fall back
    to a content hash of (namespace, prompt) so identical prompts replay."""
    if key:
        return "{}__{}".format(_sanitize_key(namespace), _sanitize_key(key))
    h = hashlib.sha256()
    h.update(namespace.encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    return "{}__{}".format(_sanitize_key(namespace), h.hexdigest()[:32])


def cassette_path(namespace: str, prompt: str, key: Optional[str] = None,
                  cassette_dir: Optional[str] = None) -> str:
    return os.path.join(resolve_cassette_dir(cassette_dir),
                        cassette_id(namespace, prompt, key) + ".json")


def _read_cassette(path: str) -> Optional[str]:
    try:
        with open(path) as fh:
            return json.load(fh).get("response", "")
    except (OSError, ValueError):
        return None


def _write_cassette(path: str, namespace: str, prompt: str, model: str,
                    response: str, key: Optional[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    payload = {
        "namespace": namespace,
        "key": key or "",
        "model": model,
        "prompt_sha": prompt_sha,
        "response": response,
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


@dataclass
class Backend:
    """Resolved backend configuration. Construct once, ``invoke`` many times."""

    mode: str
    model: str
    cmd_template: str
    cassette_dir: str
    timeout: int = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls, *, model: Optional[str] = None, cmd: Optional[str] = None,
                 cassette_dir: Optional[str] = None,
                 timeout: Optional[int] = None) -> "Backend":
        return cls(
            mode=resolve_mode(),
            model=resolve_model(model),
            cmd_template=resolve_cmd_template(cmd),
            cassette_dir=resolve_cassette_dir(cassette_dir),
            timeout=timeout if timeout is not None else int(_env("BRK_AGENT_TIMEOUT", str(DEFAULT_TIMEOUT))),
        )

    def build_argv(self) -> list:
        argv = self.cmd_template.format(model=self.model).split()
        if not argv:
            return argv
        prog = os.path.basename(argv[0])

        # Cursor: inject API key from env so CI auth never depends on a keychain/login.
        if prog in ("agent", "cursor-agent"):
            api_key = os.environ.get("CURSOR_API_KEY", "").strip()
            if api_key and "--api-key" not in argv:
                argv = argv + ["--api-key", api_key]

        # GitHub Copilot CLI: same prompt-in / text-out contract as Cursor's agent,
        # so it is a drop-in local backend for validating the pipeline with the same
        # model class -- no Cursor/key required. Non-interactive mode needs
        # --allow-all-tools; --no-color keeps stdout clean. The prompt is appended by
        # _run_live, so -p/--prompt must be the LAST token in the template.
        if prog == "copilot":
            if "--allow-all-tools" not in argv and "--allow-all" not in argv:
                argv = argv + ["--allow-all-tools"]
            if "--no-color" not in argv:
                argv = argv + ["--no-color"]
            if "-p" not in argv and "--prompt" not in argv:
                argv = argv + ["-p"]
        return argv

    def _run_live(self, prompt: str, cwd: Optional[str], env: Optional[dict]) -> str:
        argv = self.build_argv()
        try:
            cp = subprocess.run(argv + [prompt], cwd=cwd, env=env,
                                timeout=self.timeout, capture_output=True, text=True)
        except (subprocess.TimeoutExpired, OSError):
            return ""
        return (cp.stdout or "").strip()

    def invoke(self, prompt: str, *, namespace: str, key: Optional[str] = None,
               cwd: Optional[str] = None, env: Optional[dict] = None) -> str:
        """Run one prompt through the configured backend. Always returns a string;
        "" means "no usable response" and callers must treat it as fail-safe skip."""
        path = cassette_path(namespace, prompt, key, self.cassette_dir)

        if self.mode == MODE_REPLAY:
            cached = _read_cassette(path)
            return cached if cached is not None else ""

        response = self._run_live(prompt, cwd, env)

        if self.mode == MODE_RECORD and response:
            _write_cassette(path, namespace, prompt, self.model, response, key)

        return response


def invoke(prompt: str, *, namespace: str, key: Optional[str] = None,
           model: Optional[str] = None, cmd: Optional[str] = None,
           cwd: Optional[str] = None, env: Optional[dict] = None,
           cassette_dir: Optional[str] = None) -> str:
    """Module-level convenience: resolve a Backend from env and invoke once."""
    backend = Backend.from_env(model=model, cmd=cmd, cassette_dir=cassette_dir)
    return backend.invoke(prompt, namespace=namespace, key=key, cwd=cwd, env=env)


def _cli() -> int:
    """Thin CLI shim so shell stages (independent_adjudicate.sh) route through the
    same backend and gain replay for free.

    Usage: ai_backend.py --namespace NS [--key K] [--cwd DIR] [--prompt-file F]
    Prompt is read from --prompt-file, else the first positional arg, else stdin.
    Prints the raw response to stdout (empty on miss/failure -> caller stays safe).
    """
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--key", default=None)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--prompt-file", default=None)
    ap.add_argument("prompt", nargs="?", default=None)
    args = ap.parse_args()

    if args.prompt_file:
        with open(args.prompt_file) as fh:
            prompt = fh.read()
    elif args.prompt is not None:
        prompt = args.prompt
    else:
        prompt = sys.stdin.read()

    if not prompt.strip():
        return 0
    out = invoke(prompt, namespace=args.namespace, key=args.key, cwd=args.cwd)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
