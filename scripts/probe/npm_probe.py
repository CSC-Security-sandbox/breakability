"""Deterministic npm runtime-shape probe."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from .config import (
    NPM_PROBE_TIMEOUT, NPM_PROBE_ROOT, REPO_ROOT,
    _NPM_NAME_RE, _NPM_VERSION_RE,
)
from .sandbox import scrub_env_for_agent
from .utils import log

__all__ = [
    "is_npm_probe_candidate", "npm_unavailable_grade", "npm_grade_from_snapshots",
    "run_npm_differential_probe",
    # Private names exported for backward compatibility / tests
    "_PRIVATE_SCOPES_CACHE", "_is_private_npm_package", "_get_private_scopes",
    "_valid_npm_ref", "_npm_snapshot_digest", "_npm_loaded",
    "_prop_is_breaking", "_npm_breaking_diff", "_npm_snapshot_summary",
    "_npm_diff_hint", "_dig", "_npm_install_and_snapshot", "_npm_probe_env",
    "_NPM_RUNTIME_SHAPE_SCRIPT",
]


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
    for scope in _get_private_scopes():
        if pkg.startswith(scope.rstrip("/") + "/"):
            return True
    return False


_PRIVATE_SCOPES_CACHE = None


def _get_private_scopes():
    global _PRIVATE_SCOPES_CACHE
    if _PRIVATE_SCOPES_CACHE is not None:
        return _PRIVATE_SCOPES_CACHE
    scopes = []
    config_path = os.environ.get("BREAKABILITY_CONFIG", "")
    if config_path and os.path.isfile(config_path):
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            for reg in cfg.get("private_registries", []):
                s = reg.get("scope", "").strip()
                if s:
                    scopes.append(s)
        except Exception:
            pass
    if not scopes:
        raw = os.environ.get("BREAKABILITY_PRIVATE_SCOPES", "")
        if raw:
            scopes = [s.strip() for s in raw.split(",") if s.strip()]
    _PRIVATE_SCOPES_CACHE = scopes
    return scopes


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
    object) are additive and NOT breaking -- axios/react-router minors commonly add
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
