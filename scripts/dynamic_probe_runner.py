#!/usr/bin/env python3
"""Bounded dynamic old-vs-new probe runner.

Runs controlled Go snippets twice against from/to dependency versions, compares
observable process results, and emits typed evidence_contract probe records.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evidence_contract import (  # noqa: E402
    CommandEvidence,
    Confidence,
    EvidenceRecord,
    SafetySeverity,
    SignalName,
    SignalStatus,
)

DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_OUTPUT_LIMIT = 4096
DEFAULT_SCRATCH_ROOT = Path(__file__).resolve().parent / ".probe-work"
_SKIP_GO_GET_PACKAGES = {"", "std", "stdlib", "standard-library"}
_ALLOWED_GO_COMMANDS = {
    ("go", "run"),
    ("go", "test"),
}


class ProbeClassification(str, Enum):
    SAME_BEHAVIOR = "SAME_BEHAVIOR"
    CHANGED_BEHAVIOR = "CHANGED_BEHAVIOR"
    PROBE_FAILED = "PROBE_FAILED"
    NONDETERMINISTIC = "NONDETERMINISTIC"


@dataclass(frozen=True)
class ProbeSpec:
    ecosystem: str
    package: str
    from_version: str
    to_version: str
    source: str
    command: str = "go run ."
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProbeSpec":
        source = data.get("source", data.get("snippet", ""))
        return cls(
            ecosystem=str(data.get("ecosystem", "")),
            package=str(data.get("package", "")),
            from_version=str(data.get("from_version", data.get("from", ""))),
            to_version=str(data.get("to_version", data.get("to", ""))),
            source=str(source),
            command=str(data.get("command", "go run .")),
            timeout=float(data.get("timeout", DEFAULT_TIMEOUT_SECONDS)),
        )

    def validate(self) -> None:
        if self.ecosystem != "go":
            raise ValueError("only go ecosystem is supported")
        if not self.from_version or not self.to_version:
            raise ValueError("from_version and to_version are required")
        if not self.source.strip():
            raise ValueError("source is required")
        if self.timeout <= 0 or self.timeout > 60:
            raise ValueError("timeout must be >0 and <=60 seconds")
        _parse_go_command(self.command)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def comparable(self) -> Tuple[int, str, str, bool]:
        return (self.exit_code, _stable_output(self.stdout), _stable_output(self.stderr), self.timed_out)

    def output_text(self) -> str:
        return f"exit={self.exit_code} timed_out={self.timed_out}\nSTDOUT:\n{self.stdout}\nSTDERR:\n{self.stderr}"

    def output_hash(self) -> str:
        return hashlib.sha256(repr(self.comparable()).encode("utf-8", "replace")).hexdigest()


@dataclass(frozen=True)
class VersionRun:
    version: str
    results: Tuple[CommandResult, CommandResult]

    @property
    def first(self) -> CommandResult:
        return self.results[0]

    def deterministic(self) -> bool:
        return self.results[0].comparable() == self.results[1].comparable()


@dataclass(frozen=True)
class ProbeResult:
    classification: ProbeClassification
    spec: ProbeSpec
    old: Optional[VersionRun]
    new: Optional[VersionRun]
    error: str = ""

    def to_evidence_record(self) -> EvidenceRecord:
        old_result = self.old.first if self.old else None
        new_result = self.new.first if self.new else None
        old_hash = old_result.output_hash() if old_result else ""
        new_hash = new_result.output_hash() if new_result else ""
        old_text = old_result.output_text() if old_result else self.error
        new_text = new_result.output_text() if new_result else self.error
        command = CommandEvidence(
            command=self.spec.command,
            exit_code=new_result.exit_code if new_result else None,
            old_output_hash=old_hash,
            new_output_hash=new_hash,
            old_output_text=old_text,
            new_output_text=new_text,
        )
        if self.classification == ProbeClassification.SAME_BEHAVIOR:
            return EvidenceRecord(
                name=SignalName.PROBE,
                status=SignalStatus.PASS,
                relevant=False,
                same_behavior=True,
                confidence=Confidence.HIGH,
                commands=(command,),
                old_output_hash=old_hash,
                new_output_hash=new_hash,
                old_output_text=old_text,
                new_output_text=new_text,
            )
        if self.classification == ProbeClassification.CHANGED_BEHAVIOR:
            return EvidenceRecord(
                name=SignalName.PROBE,
                status=SignalStatus.FAIL,
                severity=SafetySeverity.MEDIUM,
                relevant=True,
                same_behavior=False,
                residual_risk=SafetySeverity.MEDIUM,
                confidence=Confidence.HIGH,
                commands=(command,),
                old_output_hash=old_hash,
                new_output_hash=new_hash,
                old_output_text=old_text,
                new_output_text=new_text,
            )
        status = SignalStatus.UNAVAILABLE if self.classification == ProbeClassification.PROBE_FAILED else SignalStatus.UNKNOWN
        return EvidenceRecord(
            name=SignalName.PROBE,
            status=status,
            tool_failure=True,
            confidence=Confidence.LOW,
            commands=(command,),
            old_output_hash=old_hash,
            new_output_hash=new_hash,
            old_output_text=old_text,
            new_output_text=new_text,
            rationale=self.error or self.classification.value,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "classification": self.classification.value,
            "error": self.error,
            "old": _version_run_to_dict(self.old),
            "new": _version_run_to_dict(self.new),
            "evidence": self.to_evidence_record().to_dict(),
        }


def run_probe(spec: ProbeSpec, *, scratch_root: Optional[Path] = None, output_limit: int = DEFAULT_OUTPUT_LIMIT) -> ProbeResult:
    try:
        spec.validate()
    except Exception as exc:
        return ProbeResult(ProbeClassification.PROBE_FAILED, spec, None, None, str(exc))
    if shutil.which("go") is None:
        return ProbeResult(ProbeClassification.PROBE_FAILED, spec, None, None, "go executable not found")

    scratch = Path(scratch_root or DEFAULT_SCRATCH_ROOT)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="dynprobe-", dir=str(scratch)) as work:
            root = Path(work)
            old = _run_version_twice(spec, spec.from_version, root / "old", output_limit)
            new = _run_version_twice(spec, spec.to_version, root / "new", output_limit)
    except Exception as exc:
        return ProbeResult(ProbeClassification.PROBE_FAILED, spec, None, None, str(exc))

    if not old.deterministic() or not new.deterministic():
        return ProbeResult(ProbeClassification.NONDETERMINISTIC, spec, old, new, "repeated probe outputs differed")
    if _failed(old.first) or _failed(new.first):
        return ProbeResult(ProbeClassification.PROBE_FAILED, spec, old, new, "probe command failed or timed out")
    if old.first.comparable() == new.first.comparable():
        return ProbeResult(ProbeClassification.SAME_BEHAVIOR, spec, old, new)
    return ProbeResult(ProbeClassification.CHANGED_BEHAVIOR, spec, old, new)


def _run_version_twice(spec: ProbeSpec, version: str, project_dir: Path, output_limit: int) -> VersionRun:
    results = []
    for idx in (1, 2):
        run_dir = project_dir / f"run-{idx}"
        _prepare_go_project(spec, version, run_dir)
        results.append(_run_go_command(spec, version, run_dir, output_limit))
    return VersionRun(version=version, results=(results[0], results[1]))


def _prepare_go_project(spec: ProbeSpec, version: str, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)
    env = _scrubbed_go_env(run_dir, version)
    _checked_go(["go", "mod", "init", "dynamicprobe.local/probe"], run_dir, env, spec.timeout)
    if _needs_go_get(spec.package):
        _checked_go(["go", "get", f"{spec.package}@{version}"], run_dir, env, spec.timeout)
    filename = "probe_test.go" if _is_go_test(spec.command) else "main.go"
    (run_dir / filename).write_text(_render_source(spec.source, version), encoding="utf-8")


def _run_go_command(spec: ProbeSpec, version: str, run_dir: Path, output_limit: int) -> CommandResult:
    argv = _parse_go_command(spec.command)
    env = _scrubbed_go_env(run_dir, version)
    try:
        completed = subprocess.run(
            argv,
            cwd=str(run_dir),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=spec.timeout,
            check=False,
        )
        return CommandResult(
            exit_code=completed.returncode,
            stdout=_truncate(completed.stdout, output_limit),
            stderr=_truncate(completed.stderr, output_limit),
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            exit_code=124,
            stdout=_truncate(exc.stdout or "", output_limit),
            stderr=_truncate((exc.stderr or "") + "\nprobe timed out", output_limit),
            timed_out=True,
        )


def _checked_go(argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout: float) -> None:
    completed = subprocess.run(
        list(argv),
        cwd=str(cwd),
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {_truncate(completed.stderr or completed.stdout, 1000)}")


def _scrubbed_go_env(run_dir: Path, version: str) -> Dict[str, str]:
    path = os.environ.get("PATH", "")
    env = {
        "PATH": path,
        "HOME": str(run_dir / "home"),
        "GOCACHE": str(run_dir / "gocache"),
        "GOMODCACHE": str(run_dir / "gomodcache"),
        "GOPATH": str(run_dir / "gopath"),
        "GOWORK": "off",
        "CGO_ENABLED": "0",
        "DYNAMIC_PROBE_VERSION": version,
    }
    for key in ("GOROOT", "GOOS", "GOARCH", "GOPROXY", "GOSUMDB", "GONOSUMDB", "GOPRIVATE"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    for subdir in ("home", "gocache", "gomodcache", "gopath"):
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)
    return env


def _parse_go_command(command: str) -> List[str]:
    argv = shlex.split(command)
    if len(argv) < 2 or tuple(argv[:2]) not in _ALLOWED_GO_COMMANDS:
        raise ValueError("command must start with 'go run' or 'go test'")
    if any(part in {";", "&&", "||", "|", ">", "<"} for part in argv):
        raise ValueError("shell operators are not allowed in command")
    return argv


def _is_go_test(command: str) -> bool:
    return _parse_go_command(command)[:2] == ["go", "test"]


def _needs_go_get(package: str) -> bool:
    return package.strip().lower() not in _SKIP_GO_GET_PACKAGES


def _render_source(source: str, version: str) -> str:
    return source.replace("{{DYNAMIC_PROBE_VERSION}}", version)


def _failed(result: CommandResult) -> bool:
    return result.timed_out or result.exit_code != 0


def _truncate(text: Any, limit: int) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = str(text)
    if len(text) <= limit:
        return text
    marker = f"\n...[truncated {len(text) - limit} chars]"
    keep = max(0, limit - len(marker))
    return text[:keep] + marker


def _stable_output(text: str) -> str:
    # `go test` appends nondeterministic package durations to otherwise stable
    # observable output. Normalize that harness noise; keep program output intact.
    return re.sub(r"(?m)^(ok|FAIL)(\s+\S+)\s+\d+(?:\.\d+)?s$", r"\1\2 <duration>", text)


def _version_run_to_dict(run: Optional[VersionRun]) -> Optional[Dict[str, Any]]:
    if run is None:
        return None
    return {"version": run.version, "deterministic": run.deterministic(), "results": [dataclasses.asdict(r) for r in run.results]}


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded old-vs-new Go dynamic probe")
    parser.add_argument("spec", nargs="?", help="JSON spec file; defaults to stdin")
    parser.add_argument("--scratch-root", default=str(DEFAULT_SCRATCH_ROOT), help="directory for isolated probe projects")
    parser.add_argument("--output-limit", type=int, default=DEFAULT_OUTPUT_LIMIT)
    args = parser.parse_args(list(argv) if argv is not None else None)
    raw = Path(args.spec).read_text(encoding="utf-8") if args.spec else sys.stdin.read()
    result = run_probe(ProbeSpec.from_dict(json.loads(raw)), scratch_root=Path(args.scratch_root), output_limit=args.output_limit)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.classification in (ProbeClassification.SAME_BEHAVIOR, ProbeClassification.CHANGED_BEHAVIOR) else 2


if __name__ == "__main__":
    raise SystemExit(main())
