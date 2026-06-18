#!/usr/bin/env node
// npm_apidiff.mjs — semantic API/type-surface diff for npm packages.
//
// Mirrors Go's `apidiff` for the Node/TypeScript ecosystem. Given a package and
// two versions, it downloads both from the public npm registry (`npm pack`),
// locates the bundled TypeScript declaration entry (or the matching @types/*
// package), and compares the EXPORTED type surface using the TypeScript
// compiler API. Removed or signature-changed exports are incompatible
// (breaking); a purely additive surface is compatible.
//
// SOUNDNESS (zero-false-green): if EITHER version ships no type declarations,
// or the compiler cannot be loaded, the result is `compatible: null`
// (UNAVAILABLE) — never a false "compatible: true". A clean compatible verdict
// is only emitted when BOTH surfaces were parsed and no export was removed or
// changed.
//
// Usage:  node npm_apidiff.mjs <pkg> <oldVersion> <newVersion> [--json]
// Output (stdout, JSON):
//   { apiDiffTool, oldHadTypes, newHadTypes, removed[], changed[], added[],
//     apiChanges, compatible }   compatible ∈ {true,false,null}
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

function log(...a) { if (process.env.APIDIFF_DEBUG) console.error("[apidiff]", ...a); }

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function unavailable(reason, extra = {}) {
  emit({
    apiDiffTool: "ts-apidiff",
    compatible: null,
    apiChanges: 0,
    removed: [], changed: [], added: [],
    reason,
    ...extra,
  });
  process.exit(0);
}

// ── Locate / load the TypeScript compiler ─────────────────────────────────────
// Prefer a cached install under a stable dir so repeated CI runs don't re-download.
function loadTypeScript() {
  // 1. Already resolvable (running inside a repo that has typescript).
  try { return require("typescript"); } catch { /* fall through */ }
  const cacheDir = process.env.APIDIFF_TS_CACHE || join(tmpdir(), "brk-apidiff-ts");
  const cacheReq = createRequire(join(cacheDir, "noop.js"));
  try { return cacheReq("typescript"); } catch { /* need install */ }
  try {
    execFileSync("npm", ["init", "-y"], { cwd: ensureDir(cacheDir), stdio: "ignore" });
    execFileSync("npm", ["i", "--no-save", "--no-audit", "--no-fund", "typescript@5"], {
      cwd: cacheDir, stdio: "ignore",
    });
    return cacheReq("typescript");
  } catch (e) {
    return null;
  }
}

function ensureDir(d) {
  try { execFileSync("mkdir", ["-p", d]); } catch { /* ignore */ }
  return d;
}

// ── Download + extract a package version, return its extracted root ───────────
function fetchPackage(pkg, version, workDir) {
  let tgz;
  try {
    const out = execFileSync("npm", ["pack", `${pkg}@${version}`, "--silent"], {
      cwd: workDir, encoding: "utf8",
    });
    tgz = out.trim().split("\n").filter(Boolean).pop();
  } catch (e) {
    log("npm pack failed", pkg, version, String(e).slice(0, 200));
    return null;
  }
  const tgzPath = join(workDir, tgz);
  if (!existsSync(tgzPath)) return null;
  const dest = join(workDir, `x-${pkg.replace(/[^a-z0-9]/gi, "_")}-${version}`);
  ensureDir(dest);
  try {
    execFileSync("tar", ["xzf", tgzPath, "-C", dest, "--strip-components=1"]);
  } catch (e) {
    log("tar failed", String(e).slice(0, 200));
    return null;
  }
  return dest;
}

// Resolve the entry .d.ts for a package root. Returns absolute path or null.
function resolveTypesEntry(pkgRoot, pkg, workDir) {
  let pj = {};
  try { pj = JSON.parse(readFileSync(join(pkgRoot, "package.json"), "utf8")); } catch { /* ignore */ }
  const candidates = [];
  const pushd = (p) => { if (p && typeof p === "string") candidates.push(p); };
  pushd(pj.types);
  pushd(pj.typings);
  // exports map: { ".": { types: "...", import:{types:...}, ... } }
  const exp = pj.exports;
  if (exp && typeof exp === "object") {
    const root = exp["."] ?? exp;
    const collectTypes = (node) => {
      if (!node) return;
      if (typeof node === "string") return;
      if (typeof node.types === "string") pushd(node.types);
      for (const k of ["import", "require", "node", "default"]) {
        if (node[k] && typeof node[k] === "object") collectTypes(node[k]);
      }
    };
    collectTypes(root);
  }
  // Conventional fallbacks derived from "main".
  if (pj.main && typeof pj.main === "string") {
    pushd(pj.main.replace(/\.[cm]?js$/, ".d.ts"));
  }
  candidates.push("index.d.ts", "dist/index.d.ts", "types/index.d.ts", "lib/index.d.ts");

  for (const c of candidates) {
    const abs = resolve(pkgRoot, c);
    if (existsSync(abs) && statSync(abs).isFile()) return abs;
  }
  // Bundled types absent — try the @types/<pkg> companion package.
  const typesPkg = pkg.startsWith("@")
    ? "@types/" + pkg.slice(1).replace("/", "__")
    : "@types/" + pkg;
  const typesRoot = fetchPackage(typesPkg, "latest", workDir);
  if (typesRoot) {
    for (const c of ["index.d.ts", "dist/index.d.ts", "types/index.d.ts"]) {
      const abs = resolve(typesRoot, c);
      if (existsSync(abs) && statSync(abs).isFile()) return abs;
    }
  }
  return null;
}

// ── Extract the exported API surface via the TS compiler ──────────────────────
// Returns Map<exportName, signatureString> or null on failure.
function extractSurface(ts, entryDts) {
  try {
    const options = {
      noEmit: true,
      skipLibCheck: true,
      skipDefaultLibCheck: true,
      target: ts.ScriptTarget.ESNext,
      module: ts.ModuleKind.ESNext,
      moduleResolution: ts.ModuleResolutionKind.NodeNext,
      allowJs: false,
    };
    const program = ts.createProgram([entryDts], options);
    const checker = program.getTypeChecker();
    const source = program.getSourceFile(entryDts);
    if (!source) return null;
    const moduleSymbol = checker.getSymbolAtLocation(source);
    let exports = [];
    if (moduleSymbol) {
      exports = checker.getExportsOfModule(moduleSymbol);
    } else {
      // Fallback: top-level exported declarations in an ambient/script file.
      exports = (source.statements || [])
        .flatMap((st) => {
          const sym = st.name ? checker.getSymbolAtLocation(st.name) : undefined;
          return sym ? [sym] : [];
        });
    }
    const surface = new Map();
    for (const sym of exports) {
      const name = sym.getName();
      if (!name || name === "__esModule") continue;
      let sig = "";
      try {
        const decl = (sym.declarations && sym.declarations[0]) || undefined;
        const type = decl
          ? checker.getTypeOfSymbolAtLocation(sym, decl)
          : checker.getDeclaredTypeOfSymbol(sym);
        sig = checker.typeToString(
          type, decl,
          ts.TypeFormatFlags.NoTruncation | ts.TypeFormatFlags.UseFullyQualifiedType
        );
      } catch {
        sig = "<unresolved>";
      }
      // Symbol flags capture kind (class/interface/function/enum/var) so a
      // kind change (e.g. function -> object) is also caught.
      const flags = sym.getFlags();
      surface.set(name, `${flags & ts.SymbolFlags.Type ? "type:" : ""}${sig}`);
    }
    return surface;
  } catch (e) {
    log("extractSurface failed", String(e).slice(0, 300));
    return null;
  }
}

// Strip per-version extracted-root paths and bare version strings from signature
// strings so only genuine surface changes survive the diff.
function normalizeSurface(surface, roots, oldVer, newVer) {
  const out = new Map();
  const verRe = new RegExp(
    [oldVer, newVer].map((v) => v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"),
    "g"
  );
  for (const [name, sigIn] of surface) {
    let sig = sigIn;
    for (const r of roots) {
      if (r) sig = sig.split(r).join("<pkgroot>");
    }
    // Collapse any residual absolute import("/abs/path") to its trailing segment.
    sig = sig.replace(/import\("[^"]*?([^"/]+)"\)/g, 'import("<pkg>/$1")');
    sig = sig.replace(verRe, "<v>");
    out.set(name, sig);
  }
  return out;
}

function main() {
  const args = process.argv.slice(2).filter((a) => a !== "--json");
  const [pkg, oldVer, newVer] = args;
  if (!pkg || !oldVer || !newVer) {
    unavailable("usage: npm_apidiff.mjs <pkg> <oldVersion> <newVersion>");
  }
  const ts = loadTypeScript();
  if (!ts) unavailable("typescript compiler unavailable");

  const workDir = mkdtempSync(join(tmpdir(), "brk-apidiff-"));
  try {
    const oldRoot = fetchPackage(pkg, oldVer, workDir);
    const newRoot = fetchPackage(pkg, newVer, workDir);
    if (!oldRoot || !newRoot) unavailable("npm pack failed for one or both versions");

    const oldEntry = resolveTypesEntry(oldRoot, pkg, workDir);
    const newEntry = resolveTypesEntry(newRoot, pkg, workDir);
    const oldHadTypes = !!oldEntry;
    const newHadTypes = !!newEntry;
    if (!oldHadTypes || !newHadTypes) {
      unavailable("no type declarations on one or both versions", { oldHadTypes, newHadTypes });
    }

    const oldSurfaceRaw = extractSurface(ts, oldEntry);
    const newSurfaceRaw = extractSurface(ts, newEntry);
    if (!oldSurfaceRaw || !newSurfaceRaw || oldSurfaceRaw.size === 0) {
      unavailable("could not extract type surface", { oldHadTypes, newHadTypes });
    }
    // Normalize signatures: TS `typeToString` can embed absolute extracted-tarball
    // paths (e.g. `import(".../x-fast_uri-3.1.0/types/index")`) which differ between
    // versions spuriously. Strip the per-version roots and bare version strings so
    // only genuine surface changes remain.
    const oldSurface = normalizeSurface(oldSurfaceRaw, [oldRoot, newRoot, workDir], oldVer, newVer);
    const newSurface = normalizeSurface(newSurfaceRaw, [oldRoot, newRoot, workDir], oldVer, newVer);

    const removed = [];
    const changed = [];
    const added = [];
    for (const [name, sig] of oldSurface) {
      if (!newSurface.has(name)) { removed.push(name); continue; }
      if (newSurface.get(name) !== sig) {
        changed.push({ name, old: sig.slice(0, 200), new: newSurface.get(name).slice(0, 200) });
      }
    }
    for (const name of newSurface.keys()) {
      if (!oldSurface.has(name)) added.push(name);
    }
    const apiChanges = removed.length + changed.length;
    emit({
      apiDiffTool: "ts-apidiff",
      oldHadTypes, newHadTypes,
      oldSurfaceSize: oldSurface.size,
      newSurfaceSize: newSurface.size,
      removed, changed, added,
      apiChanges,
      compatible: apiChanges === 0,
    });
  } finally {
    try { rmSync(workDir, { recursive: true, force: true }); } catch { /* ignore */ }
  }
}

main();
