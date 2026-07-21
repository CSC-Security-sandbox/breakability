"""Deterministic Go module API-surface probe."""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time

from pathlib import Path

from .config import REPO_ROOT, GOMOD_PROBE_TIMEOUT, GOMOD_PROBE_ROOT, _GOMOD_RE, _GOMOD_VERSION_RE

__all__ = [
    "is_gomod_probe_candidate", "gomod_unavailable_grade",
    "gomod_grade_from_snapshots", "run_gomod_differential_probe",
    # Private names exported for backward compatibility
    "_valid_gomod_ref", "_go_doc_snapshot", "_find_go_binary",
]


def _find_go_binary():
    """Locate the Go binary, searching PATH and common CI installation directories."""
    found = shutil.which("go")
    if found:
        return found
    search_dirs = ["/usr/local/go/bin", os.path.expanduser("~/go/bin")]
    goroot = os.environ.get("GOROOT")
    if goroot:
        search_dirs.insert(0, os.path.join(goroot, "bin"))
    tool_cache_roots = [Path("/opt/hostedtoolcache/go")]
    runner_tool_cache = os.environ.get("RUNNER_TOOL_CACHE")
    if runner_tool_cache:
        tool_cache_roots.insert(0, Path(runner_tool_cache) / "go")
    for hc in tool_cache_roots:
        if hc.exists():
            for hd in hc.glob("*/x64/bin"):
                search_dirs.append(str(hd))
    for d in search_dirs:
        candidate = os.path.join(d, "go")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return candidate
    return None


def is_gomod_probe_candidate(pr):
    if str(pr.get("ecosystem") or "").strip().lower() != "gomod":
        return False
    return bool(str(pr.get("package") or "").strip()
                and str(pr.get("from") or "").strip()
                and str(pr.get("to") or "").strip())


def _valid_gomod_ref(pkg, version):
    if not pkg or ".." in pkg or pkg.startswith("-"):
        return False
    if not _GOMOD_VERSION_RE.match(version or ""):
        return False
    return True


def gomod_unavailable_grade(reason, commands=None, *, source="fallback"):
    return {
        "grade": "medium",
        "source": source,
        "probe_kind": "gomod_api_surface",
        "behavior_changed": "unverified",
        "same_behavior": None,
        "rationale": f"Go module API-surface probe unavailable: {str(reason)[:220]}; committed at Medium (no false-green).",
        "confidence": "low",
        "probe_commands": commands or [],
        "generated_at": int(time.time()),
    }


def _go_doc_snapshot(pkg, version, workdir):
    ver = version if version.startswith("v") else f"v{version}"
    project_dir = os.path.join(workdir, ver.replace("/", "_"))
    os.makedirs(project_dir, exist_ok=True)

    init = subprocess.run(
        ["go", "mod", "init", "breakability-gomod-probe"],
        cwd=project_dir, capture_output=True, text=True,
        timeout=GOMOD_PROBE_TIMEOUT, check=False,
    )
    if init.returncode != 0:
        raise RuntimeError(f"go mod init failed: {init.stderr[-300:]}")

    get = subprocess.run(
        ["go", "get", f"{pkg}@{ver}"],
        cwd=project_dir, capture_output=True, text=True,
        timeout=GOMOD_PROBE_TIMEOUT, check=False,
    )
    if get.returncode != 0:
        raise RuntimeError(f"go get {pkg}@{ver} failed: {get.stderr[-300:]}")

    doc = subprocess.run(
        ["go", "doc", "-all", pkg],
        cwd=project_dir, capture_output=True, text=True,
        timeout=GOMOD_PROBE_TIMEOUT, check=False,
    )
    doc_output = doc.stdout.strip() if doc.returncode == 0 else ""

    lst = subprocess.run(
        ["go", "list", "-json", pkg],
        cwd=project_dir, capture_output=True, text=True,
        timeout=GOMOD_PROBE_TIMEOUT, check=False,
    )
    list_output = lst.stdout.strip() if lst.returncode == 0 else ""

    return {"doc": doc_output, "list": list_output, "ok": bool(doc_output or list_output)}


def gomod_grade_from_snapshots(pkg, from_version, to_version, old_snap, new_snap, commands=None):
    commands = commands or []
    if not old_snap.get("ok") or not new_snap.get("ok"):
        return gomod_unavailable_grade("go doc did not produce output for both versions", commands)

    old_doc = old_snap.get("doc", "")
    new_doc = new_snap.get("doc", "")
    old_hash = hashlib.sha256(old_doc.encode("utf-8", "replace")).hexdigest()
    new_hash = hashlib.sha256(new_doc.encode("utf-8", "replace")).hexdigest()

    if old_hash == new_hash:
        return {
            "grade": "low",
            "source": "probe",
            "probe_kind": "gomod_api_surface",
            "behavior_changed": False,
            "same_behavior": True,
            "rationale": (
                f"Go API-surface probe compared `go doc -all` for {pkg}@{from_version} and "
                f"{pkg}@{to_version}; exported API documentation is identical."
            ),
            "observed_from": f"doc_sha256={old_hash[:16]}",
            "observed_to": f"doc_sha256={new_hash[:16]}",
            "evidence": f"{pkg} public API surface unchanged between versions.",
            "confidence": "high",
            "probe_commands": commands,
            "generated_at": int(time.time()),
        }

    old_lines = set(old_doc.splitlines())
    new_lines = set(new_doc.splitlines())
    removed = old_lines - new_lines
    func_removed = [l.strip() for l in removed if l.strip().startswith("func ")]

    if func_removed:
        diff_detail = f"removed symbols: {'; '.join(func_removed[:5])}"
    elif removed:
        diff_detail = f"{len(removed)} API doc line(s) changed"
    else:
        diff_detail = "API documentation differs (additions only)"

    return {
        "grade": "medium",
        "source": "probe",
        "probe_kind": "gomod_api_surface",
        "behavior_changed": True,
        "same_behavior": False,
        "changed_behavior": diff_detail,
        "rationale": (
            f"Go API-surface probe compared `go doc -all` for {pkg}@{from_version} and "
            f"{pkg}@{to_version}; public API surface differs."
        ),
        "observed_from": f"doc_sha256={old_hash[:16]}",
        "observed_to": f"doc_sha256={new_hash[:16]}",
        "evidence": diff_detail,
        "confidence": "high",
        "probe_commands": commands,
        "generated_at": int(time.time()),
    }


def run_gomod_differential_probe(num, pr):
    pkg = str(pr.get("package") or "").strip()
    from_version = str(pr.get("from") or "").strip()
    to_version = str(pr.get("to") or "").strip()
    commands = [
        f"go get {pkg}@v{from_version}",
        f"go get {pkg}@v{to_version}",
        f"go doc -all {pkg}",
    ]
    if not _valid_gomod_ref(pkg, from_version) or not _valid_gomod_ref(pkg, to_version):
        return gomod_unavailable_grade("invalid Go module/version reference", commands, source="fallback")
    if _find_go_binary() is None:
        return gomod_unavailable_grade("go executable not found", commands, source="fallback")

    os.makedirs(GOMOD_PROBE_ROOT, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix=f"gomod-dp-{num}-", dir=GOMOD_PROBE_ROOT)
    env_backup = os.environ.copy()
    try:
        os.environ["GOWORK"] = "off"
        old_snap = _go_doc_snapshot(pkg, from_version, workdir)
        new_snap = _go_doc_snapshot(pkg, to_version, workdir)
        return gomod_grade_from_snapshots(pkg, from_version, to_version, old_snap, new_snap, commands=commands)
    except Exception as e:
        return gomod_unavailable_grade(str(e), commands)
    finally:
        os.environ.clear()
        os.environ.update(env_backup)
        shutil.rmtree(workdir, ignore_errors=True)
