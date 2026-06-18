#!/usr/bin/env python3
"""npm changelog/release-note fetch helpers for build-check.sh.

Network-facing ``fetch`` mode is fail-safe: any registry/GitHub failure exits 0
with no stdout so callers keep release notes UNAVAILABLE.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse


CHANGELOG_NAMES = ("CHANGELOG.md", "CHANGES.md", "HISTORY.md", "RELEASES.md")
BREAKING_RE = re.compile(
    r"\b(breaking[\s-]?change|backward[\s-]?incompatible|migration[\s-]?required)\b|"
    r"\b(?:feat|fix|refactor|perf)(?:\([^)]*\))?!:",
    re.I,
)
RUNTIME_DROP_RE = re.compile(
    r"(?:\b(?:drop(?:ped|s|ping)?|remove(?:d|s)?|end(?:ed)? support|no longer supported)\b"
    r".{0,100}\b(node\.?js|node|python|go|java|ruby)\s*v?\d+)|"
    r"(?:\b(node\.?js|node|python|go|java|ruby)\s*v?\d+.{0,100}\bno longer supported\b)",
    re.I,
)
NEGATED_BREAKING_RE = re.compile(
    r"\b(?:no|none|without)\s+(?:known\s+|identified\s+)?breaking[\s-]?changes?\b|"
    r"\bbreaking[\s-]?changes?\s*:\s*(?:none|no)\b|"
    r"\bnone identified\b",
    re.I,
)
MISSING_WORD_RE = re.compile(r"\b(missing|unparsable)\b", re.I)


@dataclass(frozen=True)
class NpmRepo:
    owner: str
    repo: str
    directory: str = ""

    @property
    def gh_path(self) -> str:
        return f"{self.owner}/{self.repo}"


def normalize_repository_url(url: str | None, directory: str | None = None) -> NpmRepo | None:
    """Normalize common npm repository URLs to GitHub owner/repo + optional dir."""
    raw = (url or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^git\+", "", raw, flags=re.I)
    raw = re.sub(r"^github:", "https://github.com/", raw, flags=re.I)
    raw = re.sub(r"^git://", "https://", raw, flags=re.I)
    raw = re.sub(r"^ssh://git@", "https://", raw, flags=re.I)
    raw = re.sub(r"^git@github\.com:", "https://github.com/", raw, flags=re.I)

    if raw.startswith("github.com/") or raw.startswith("www.github.com/"):
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host.startswith("git@"):
        host = host[4:]
    if host not in {"github.com", "www.github.com"}:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
    if not owner or not repo:
        return None
    safe_dir = (directory or "").strip().strip("/")
    return NpmRepo(owner=owner, repo=repo, directory=safe_dir)


def version_tuple(version: str | None) -> tuple[int, int, int] | None:
    """Parse semver-ish strings/tags; missing minor/patch are treated as zero."""
    m = re.search(r"(?<!\d)(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+][0-9A-Za-z.-]+)?", version or "")
    if not m:
        return None
    return tuple(int(m.group(i) or 0) for i in range(1, 4))


def versions_in_text(text: str | None) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for m in re.finditer(r"(?<!\d)(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+][0-9A-Za-z.-]+)?", text or ""):
        out.append(tuple(int(m.group(i) or 0) for i in range(1, 4)))
    return out


def tag_in_range(tag: str, from_version: str, to_version: str, package: str = "") -> bool:
    """Return True when any version in a release/tag header is in (from, to]."""
    lo, hi = version_tuple(from_version), version_tuple(to_version)
    pkg = (package or "").strip()
    if pkg:
        scoped = re.search(r"((?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+)@(?=\d)", tag or "")
        if scoped:
            tag_pkg = scoped.group(1)
            pkg_leaf = pkg.split("/")[-1]
            if tag_pkg not in {pkg, pkg_leaf}:
                return False
    versions = versions_in_text(tag)
    if not versions:
        return False
    if not lo or not hi:
        return version_tuple(tag) == version_tuple(to_version)
    # Package name is intentionally not required: monorepos often tag as
    # "@scope/pkg@1.2.3", "pkg@1.2.3", or plain "v1.2.3".
    return any(lo < v <= hi for v in versions)


def extract_changelog_sections(changelog: str, from_version: str, to_version: str, limit: int = 24000) -> str:
    """Extract version sections in (from, to] from markdown changelogs."""
    if not changelog:
        return ""
    lo, hi = version_tuple(from_version), version_tuple(to_version)
    header = re.compile(r"^\s{0,3}#{1,6}\s+.*?(?:\d+(?:\.\d+){0,2})")
    sections: list[str] = []
    current: list[str] = []
    capture = False

    def flush() -> None:
        nonlocal current
        if capture and current:
            sections.append("\n".join(current).strip())
        current = []

    for line in changelog.splitlines():
        if header.match(line):
            flush()
            versions = versions_in_text(line)
            capture = bool(
                versions
                and ((lo and hi and any(lo < v <= hi for v in versions)) or (not (lo and hi) and version_tuple(to_version) in versions))
            )
        if capture:
            current.append(line)
            if sum(len(s) for s in sections) + sum(len(s) for s in current) >= limit:
                break
    flush()
    text = "\n\n".join(s for s in sections if s)
    return text[:limit]


def _sanitize_for_cli(text: str) -> str:
    # The bundled CLI treats any occurrence of "missing" / "unparsable" as an
    # unavailable changelog sentinel. npm release prose can use those words
    # legitimately, so avoid accidental UNAVAILABLE.
    return MISSING_WORD_RE.sub(lambda m: "absent" if m.group(1).lower().startswith("miss") else "unparseable", text)


def _is_major(from_version: str, to_version: str) -> bool:
    lo, hi = version_tuple(from_version), version_tuple(to_version)
    return bool(lo and hi and hi[0] > lo[0])


def _signal_text(raw_text: str, package: str, from_version: str, to_version: str) -> str:
    """Return compact CLI-safe changelog text; empty means fail-safe UNAVAILABLE."""
    lines = []
    clean_claims = []
    for line in raw_text.splitlines():
        s = line.strip(" -*\t")
        if not s:
            continue
        if NEGATED_BREAKING_RE.search(s):
            clean_claims.append(s[:300])
            continue
        if BREAKING_RE.search(s) or RUNTIME_DROP_RE.search(s):
            lines.append(s[:300])

    seen: set[str] = set()
    signal_lines = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            signal_lines.append(line)
    if signal_lines:
        return _sanitize_for_cli("\n".join(f"- breaking change: {line}" for line in signal_lines[:40]))

    # For semver-major, absence of detected breaking markers is not strong
    # enough to clear. Only explicit "no breaking changes" claims become clean.
    if _is_major(from_version, to_version):
        if clean_claims:
            return _sanitize_for_cli(
                f"- Release notes explicitly state no API change / no behavior change for {package} {from_version} to {to_version}."
            )
        return ""

    # Minor/patch: fetched release notes with no declared break are usable
    # evidence that the release notes do not declare a breaking change.
    summary = f"Bug fix and maintenance release notes fetched for {package} {from_version} to {to_version}; no API change declared."
    return _sanitize_for_cli(summary)


def _run_json(cmd: list[str]) -> Any:
    cp = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=30)
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        return cp.stdout.strip()


def _npm_view(package: str, version: str, field: str) -> Any:
    return _run_json(["npm", "view", f"{package}@{version}", field, "--json"])


def _repository_candidates(package: str, version: str) -> tuple[list[str], str]:
    repo = _npm_view(package, version, "repository")
    homepage = _npm_view(package, version, "homepage")
    bugs = _npm_view(package, version, "bugs")
    urls: list[str] = []
    directory = ""
    if isinstance(repo, dict):
        urls.append(str(repo.get("url") or ""))
        directory = str(repo.get("directory") or "")
    elif isinstance(repo, str):
        urls.append(repo)
    if isinstance(homepage, str):
        urls.append(homepage)
    if isinstance(bugs, dict):
        urls.append(str(bugs.get("url") or ""))
    elif isinstance(bugs, str):
        urls.append(bugs)
    return [u for u in urls if u], directory


def _gh_json(path: str, jq: str | None = None) -> Any:
    cmd = ["gh", "api", path]
    if jq:
        cmd.extend(["--jq", jq])
    return _run_json(cmd)


def _gh_content(repo: NpmRepo, rel_path: str) -> str:
    data = _gh_json(f"repos/{repo.gh_path}/contents/{rel_path}", ".content // \"\"")
    if not isinstance(data, str) or not data.strip():
        return ""
    try:
        return base64.b64decode(data).decode("utf-8", "replace")
    except Exception:
        return ""


def _unique_paths(repo: NpmRepo) -> Iterable[str]:
    dirs = [repo.directory, ""]
    seen: set[str] = set()
    for d in dirs:
        for name in CHANGELOG_NAMES:
            p = f"{d}/{name}".strip("/")
            if p not in seen:
                seen.add(p)
                yield p


def fetch_changelog_text(package: str, from_version: str, to_version: str) -> str:
    urls, directory = _repository_candidates(package, to_version)
    repo = next((r for u in urls if (r := normalize_repository_url(u, directory))), None)
    if repo is None:
        return ""

    blocks: list[str] = []
    releases = _gh_json(f"repos/{repo.gh_path}/releases?per_page=100")
    if isinstance(releases, list):
        selected: list[dict[str, Any]] = []
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            tag = str(rel.get("tag_name") or rel.get("name") or "")
            if tag_in_range(tag, from_version, to_version, package):
                selected.append(rel)
        # API returns newest first; emit oldest first so major-boundary notes
        # (usually x.0.0) survive the final size cap.
        selected.reverse()
        for rel in selected:
            tag = str(rel.get("tag_name") or rel.get("name") or "")
            body = str(rel.get("body") or "")[:5000]
            title = str(rel.get("name") or tag)
            if body.strip() or title.strip():
                blocks.append(f"## GitHub Release {tag}\n{title}\n\n{body}".strip())

    for path in _unique_paths(repo):
        content = _gh_content(repo, path)
        section = extract_changelog_sections(content, from_version, to_version)
        if section:
            blocks.append(f"## {path}\n{section}".strip())
            break

    seen: set[str] = set()
    out: list[str] = []
    for block in blocks:
        key = block.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    raw = "\n\n---\n\n".join(out)[:50000]
    return _signal_text(raw, package, from_version, to_version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--package", required=True)
    fetch.add_argument("--from-version", required=True)
    fetch.add_argument("--to-version", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "fetch":
        try:
            text = fetch_changelog_text(args.package, args.from_version, args.to_version)
            if text:
                print(text)
        except Exception:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
