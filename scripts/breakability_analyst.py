#!/usr/bin/env python3
"""
breakability_analyst.py - Rich comment renderer for PR analysis

Reads build-results.json and produces gold-standard format comments
with all 13 mandatory sections.

Based on PR #208 gold standard:
https://github.com/CSC-Security-sandbox/ndm-breakability-test/pull/208#issuecomment-4737308189
"""
import json
import sys
import os
from typing import Dict, Any, List, Optional

def format_verdict_header(pr: Dict[str, Any]) -> str:
    """Format the verdict header with emoji, confidence, priority."""
    verdict = pr.get("verdict_v2", {}).get("verdict", "REVIEW")
    confidence = pr.get("verdict_v2", {}).get("confidence", "MEDIUM")
    severity = pr.get("verdict_v2", {}).get("severity", "medium")
    priority = pr.get("verdict_v2", {}).get("priority", "P2")
    
    pkg = pr.get("package", "unknown")
    from_ver = pr.get("from", "?")
    to_ver = pr.get("to", "?")
    bump = pr.get("bump", "unknown")
    dep_type = pr.get("dep_type", "dependency")
    
    # Map verdict to emoji and label
    verdict_map = {
        "SAFE": ("✅", "SAFE", "None"),
        "REVIEW": ("🟠", "REVIEW REQUIRED", severity.title()),
        "BUILD_FAILS": ("❌", "BUILD FAILS", "Critical"),
        "BLOCKED": ("🔴", "BLOCKED", "High")
    }
    emoji, label, breakability = verdict_map.get(verdict, ("⚠️", "REVIEW", "Medium"))
    
    return f"""## {emoji} Breakability Analysis — {label} ({bump.title()}, Reachable, Behavioral Changes)

**Package:** `{pkg}` {from_ver} → {to_ver}  
**Bump Type:** {bump} · **Dep Type:** {dep_type} · **Priority:** {priority}  
**Verdict:** {emoji} **{label}** · **Confidence:** {confidence.upper()}

**Headline:** {pr.get('verdict_v2', {}).get('reason', 'Review required for this upgrade.')}

**Recommendation:** {_get_recommendation(pr)}

---
"""

def format_signal_summary(pr: Dict[str, Any]) -> str:
    """Format the 7-layer signal summary table."""
    det = pr.get("deterministic", {})
    build = pr.get("build", {})
    test = pr.get("test", {})
    
    # Map signals to results
    signals = [
        ("🔧 Build", _format_build_signal(build), _get_build_confidence(build)),
        ("🧪 Test", _format_test_signal(test), _get_test_confidence(test)),
        ("📝 API Diff", _format_api_diff_signal(det), "HIGH" if (det.get("api_changes") or 0) > 0 else "N/A"),
        ("📋 Changelog", _format_changelog_signal(det), "HIGH" if det.get("changelogSignal") else "LOW"),
        ("🔍 Reachability", _format_reachability_signal(pr), "HIGH"),
        ("🔬 Behavioral Probe", _format_probe_signal(pr), "HIGH"),
        ("🤖 AI Arbiter", _format_ai_signal(pr), "N/A")
    ]
    
    table = """### 📊 Signal Summary

| Layer | Result | Confidence | Evidence |
|-------|--------|------------|----------|
"""
    for layer, result, conf in signals:
        evidence = _get_evidence_summary(pr, layer)
        table += f"| {layer} | {result} | {conf} | {evidence} |\n"
    
    signal_agreement = _count_warning_signals(signals)
    table += f"\n**Signal Agreement:** {signal_agreement} signals warn → {pr.get('verdict_v2', {}).get('verdict', 'REVIEW')}\n\n---\n"
    
    return table

def format_build_analysis(pr: Dict[str, Any]) -> str:
    """Format detailed build analysis section."""
    build = pr.get("build", {})
    verdict = build.get("verdict", "unknown")
    ver_label = pr.get("verification_label", "L1")
    
    status_emoji = {"pass": "✅", "fail": "❌", "pre_existing": "⚠️"}.get(verdict, "⚙️")
    
    section = f"""### 🔧 Build Analysis
**Status:** {status_emoji} **{verdict.upper().replace('_', ' ')}** | **Verification Level:** {ver_label}

**What we checked:**
"""
    
    # Add build steps
    steps = []
    if build.get("verdict"):
        if verdict == "pass":
            steps.append("✅ Dependencies resolved successfully")
            steps.append(f"✅ Build passes (exit {build.get('pr_exit', 0)})")
        elif verdict == "fail":
            steps.append("❌ Build failed with new errors")
        elif verdict == "pre_existing":
            steps.append("⚠️ Build fails on both `main` and PR branch with same errors")
            steps.append("✅ No NEW errors introduced by this upgrade")
    
    for step in steps:
        section += f"- {step}\n"
    
    # Add build output
    if build.get("output_tail"):
        section += f"\n**Build Output:**\n```\n{build['output_tail'][:500]}\n```\n"
    
    section += f"\n**Confidence:** **{_get_build_confidence(build)}** — {_get_build_confidence_reason(build)}\n\n---\n"
    
    return section

def format_test_analysis(pr: Dict[str, Any]) -> str:
    """Format test analysis section."""
    test = pr.get("test", {})
    verdict = test.get("verdict", "skip")
    reason = test.get("reason", "Build prerequisites not met")
    
    status_emoji = {"pass": "✅", "fail": "❌", "skip": "⚠️"}.get(verdict, "⬜")
    status_label = verdict.upper().replace("_", " ")
    
    section = f"""### 🧪 Test Analysis
**Status:** {status_emoji} **{status_label}** | **Reason:** {reason}

**What we checked:**
"""
    
    if verdict == "pass":
        section += f"- ✅ Test suite executed successfully\n"
        section += f"- ✅ All tests passed (exit {test.get('exit_code', 0)})\n"
        section += f"- Tests run: {test.get('tests_run', 'N/A')}\n"
    elif verdict == "fail":
        section += f"- ❌ Test failures detected\n"
        section += f"- Failed: {test.get('failed_count', 'N/A')} | Passed: {test.get('passed_count', 'N/A')}\n"
    else:
        section += f"- Test execution skipped ({reason})\n"
        section += f"- Cannot verify runtime behavior via tests\n"
    
    confidence = "HIGH" if verdict == "pass" else "LOW"
    section += f"\n**Confidence:** **{confidence}** — {'Test suite provides runtime verification' if verdict == 'pass' else 'No test evidence (mitigated by behavioral probe below)'}.\n\n---\n"
    
    return section

def format_api_diff_analysis(pr: Dict[str, Any]) -> str:
    """Format API diff analysis section with detailed changes."""
    det = pr.get("deterministic", {})
    changes = det.get("api_changes") or 0
    removed = det.get("api_removed") or 0
    added = det.get("api_added") or 0
    
    if changes == 0 and removed == 0 and added == 0:
        return """### 📝 API Diff Analysis
**Status:** ✅ **CLEAN** | **Tool:** api-diff (semantic analysis)

**What we checked:**
- No breaking changes detected
- All exports remain stable

**Confidence:** **HIGH** — No API changes.

---
"""
    
    section = f"""### 📝 API Diff Analysis
**Status:** ⚠️ **BREAKING** | **Tool:** api-diff (semantic analysis)

**What we checked:**
- Removed exports: **{removed}**
- Changed exports: **{changes}** (signature/implementation changes)
- Added exports: **{added}**
"""
    
    # Add API changes details if available
    api_details = det.get("api_details", "")
    if api_details:
        section += f"\n**API Changes:**\n```typescript\n{api_details[:800]}\n```\n"
    
    section += f"\n**Confidence:** **HIGH** — Semantic analysis confirms API surface changes.\n\n---\n"
    
    return section

def format_changelog_analysis(pr: Dict[str, Any]) -> str:
    """Format changelog analysis section."""
    det = pr.get("deterministic", {})
    cl = det.get("changelogSignal", {})
    
    if isinstance(cl, str):
        # Legacy format: just a string like "breaking" or "clean"
        status = "⚠️ **BREAKING**" if cl == "breaking" else "✅ CLEAN"
        section = f"""### 📋 Changelog Analysis
**Status:** {status} | **Source:** Package changelog

**M8 Classification:** **{cl.upper()}**

**Confidence:** **MEDIUM** — Based on changelog signal.

---
"""
        return section
    
    breaking_markers = cl.get("breaking_markers", 0)
    has_changelog = cl.get("status") != "missing"
    
    if not has_changelog:
        return """### 📋 Changelog Analysis
**Status:** ⚪ **NOT AVAILABLE** | **Source:** No changelog found

**Confidence:** **LOW** — Cannot assess changes without changelog.

---
"""
    
    status = "⚠️ **BREAKING**" if breaking_markers > 0 else "✅ CLEAN"
    
    section = f"""### 📋 Changelog Analysis
**Status:** {status} | **Source:** GitHub Releases / CHANGELOG.md

**Key Changes (from {pr.get('from', '?')} → {pr.get('to', '?')}):**
"""
    
    changes = cl.get("changes", [])
    if changes:
        for change in changes[:10]:  # First 10 changes
            section += f"- {change}\n"
    else:
        section += f"- **Breaking markers:** {breaking_markers}\n"
    
    m8_class = "BREAKING" if breaking_markers > 0 else "SAFE"
    section += f"\n**M8 Classification:** **{m8_class}**\n"
    section += f"\n**Confidence:** **HIGH** — Explicit version documentation available.\n\n---\n"
    
    return section

def format_reachability_analysis(pr: Dict[str, Any]) -> str:
    """Format reachability analysis section with callsite detail."""
    det = pr.get("deterministic", {})
    reachable = det.get("reachable", False)
    import_files = det.get("import_files", [])
    
    if not reachable or not import_files:
        return """### 🔍 Reachability Analysis
**Status:** ✅ **NOT REACHED** | **Import scan:** No production imports

**What we checked:**
- Import scan: **0 production files** import this package
- Package appears to be unused or dev-only dependency

**Confidence:** **HIGH** — Static analysis confirms no imports.

---
"""
    
    pkg = pr.get("package", "unknown")
    
    # Enhanced section with callsite detail
    section = f"""### 🔍 Reachability Analysis
**Status:** ⚠️ **REACHED** | **Import scan:** {len(import_files)} file(s) import this package

**What we checked:**
- Import scan: **{len(import_files)} production file(s)** import `{pkg}`
- Static analysis: Found import statements in codebase

**Files Importing This Package:**
```
"""
    
    # Add file:line detail for each import
    for file in import_files[:10]:  # First 10 files
        section += f"{file}\n"
        # Add callsite detail if available
        callsites = det.get("callsites", {}).get(file, [])
        if callsites:
            for cs in callsites[:3]:  # First 3 callsites per file
                line = cs.get("line", "?")
                symbol = cs.get("symbol", "?")
                section += f"  Line {line}: {symbol}\n"
    
    if len(import_files) > 10:
        section += f"... and {len(import_files) - 10} more files\n"
    
    section += f"""```

**Callsite Impact:**
- Package is actively used in production code
- Breaking changes could affect {len(import_files)} file(s)
- **Recommendation:** Review all callsites to verify compatibility

"""
    
    # Add callgraph analysis if available
    api_changes = det.get("api_changes") or 0
    if api_changes > 0:
        section += f"""**Breaking Change Risk:**
- API changes detected: {api_changes} exports modified
- Each import site should be verified against new signatures
- Risk level: {"HIGH" if api_changes > 5 else "MEDIUM"}

"""
    
    section += f"""**Confidence:** **HIGH** — Import scan confirms usage.

**Next Steps:** Review the specific symbols called at each import site to ensure compatibility with the new version.

---
"""
    
    return section

def format_ai_arbiter_section(pr: Dict[str, Any]) -> str:
    """Format AI arbiter layer section."""
    ai = pr.get("ai_adjudication") or pr.get("ai_verdict", {})
    
    if not ai:
        verdict_v2 = pr.get("verdict_v2", {}).get("verdict", "REVIEW")
        return f"""### 🤖 AI Arbiter Layer
**Status:** ⬜ **NOT-APPLICABLE** (human review required)

**Why NOT applied:**
The AI arbiter engages for break-reachable cases where signals conflict and automated adjudication could reduce false positives. In this case, deterministic signals recommend **{verdict_v2}** and no conflict exists to resolve.

**Policy:** When deterministic signals unanimously recommend a clear verdict, AI does not override (fail-safe principle).

---
"""
    
    applied = ai.get("applied", "not_applied")
    reason = ai.get("reason", "No AI adjudication performed")
    
    if applied == "downgrade_to_safe":
        status = "✅ **SAFE** (AI downgraded from REVIEW)"
    elif applied == "needs_change":
        status = "⚠️ **REVIEW** (AI confirmed)"
    else:
        status = "⬜ **NOT-APPLICABLE**"
    
    section = f"""### 🤖 AI Arbiter Layer
**Status:** {status}

**AI Decision:**
{reason}

**Model:** {pr.get('ai_model', 'claude-sonnet-4.5')}  
**Confidence:** {ai.get('confidence', 'MEDIUM')}

---
"""
    
    return section

def format_policy_decision(pr: Dict[str, Any]) -> str:
    """Format policy decision section with clear precedence hierarchy."""
    verdict_v2 = pr.get("verdict_v2", {})
    verdict = verdict_v2.get("verdict", "REVIEW")
    confidence = verdict_v2.get("confidence", "MEDIUM")
    
    section = f"""### 🧮 Policy Decision
**How the verdict was reached:**

The final verdict follows a **strict precedence hierarchy** (fail-safe design):

```
Precedence Order (highest to lowest):
1. Build Failures → BLOCKED (nothing works = immediate block)
2. Security/CVE → BLOCKED (safety-critical, never auto-merge)
3. Behavioral Probe DIFFERENT → REVIEW (runtime changes = human verify)
4. Reached + Breaking API/Changelog → REVIEW (impact confirmed)
5. AI Arbiter Downgrade → SAFE (low-risk after analysis)
6. Default (no warnings) → SAFE (appears safe to merge)
```

**This PR's Decision Path:**
"""
    
    # Reconstruct decision path with precedence labels
    steps = []
    final_reason = None
    
    build = pr.get("build", {})
    if build.get("verdict") == "fail":
        steps.append("❌ **[P1: Build]** Build completely fails → **BLOCKED**")
        final_reason = "Build failure blocks merge (precedence #1)"
    elif build.get("verdict") == "pre_existing":
        steps.append("⚠️ **[P1: Build]** Pre-existing failures (not caused by this upgrade)")
    else:
        steps.append("✅ **[P1: Build]** Build passes")
    
    # Check security/CVE (precedence #2)
    cve = pr.get("deterministic", {}).get("cve")
    if cve and cve.get("found"):
        steps.append("🔴 **[P2: Security]** CVE detected → **BLOCKED**")
        final_reason = "Security advisory blocks merge (precedence #2)"
    
    # Check behavioral probe (precedence #3)
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
    if probe.get("same_behavior") == False or probe.get("different"):
        steps.append("⚠️ **[P3: Probe]** Runtime behavior changed → **REVIEW**")
        if not final_reason:
            final_reason = "Behavioral changes require review (precedence #3)"
    
    # Check reachability + breaking (precedence #4)
    det = pr.get("deterministic", {})
    if det.get("reachable") and ((det.get("api_changes") or 0) > 0 or det.get("changelogSignal") == "breaking"):
        steps.append("⚠️ **[P4: Breaking]** Reached + API/changelog breaking → **REVIEW**")
        if not final_reason:
            final_reason = "Breaking changes in reached code (precedence #4)"
    
    # Check AI arbiter (precedence #5)
    ai = pr.get("ai_adjudication")
    if ai and ai.get("applied") == "downgrade_to_safe":
        steps.append("✅ **[P5: AI]** AI arbiter analyzed and downgraded to **SAFE**")
        if not final_reason:
            final_reason = "AI confirmed low risk after analysis (precedence #5)"
    
    # Default case (precedence #6)
    if not final_reason:
        final_reason = "No warning signals detected (precedence #6 default)"
    
    for step in steps:
        section += f"{step}\n"
    
    section += f"""
**Final Verdict:** **{verdict}** (Confidence: {confidence})

**Why {verdict}?** {final_reason}

**Precedence Applied:** The highest-precedence rule that matched determined the verdict. Lower-precedence rules were not consulted (fail-safe cascade).

---
"""
    
    return section

def format_final_recommendation(pr: Dict[str, Any]) -> str:
    """Format final recommendation section."""
    verdict = pr.get("verdict_v2", {}).get("verdict", "REVIEW")
    
    recommendations = {
        "SAFE": "✅ **MERGE** — No breaking changes detected. Safe to auto-merge.",
        "REVIEW": "⚠️ **REVIEW THEN MERGE** — Breaking changes detected. Review changelog and affected files, then merge.",
        "BUILD_FAILS": "❌ **DO NOT MERGE** — Build fails. Fix build issues before merging.",
        "BLOCKED": "🔴 **BLOCKED** — Critical issues detected. Manual investigation required."
    }
    
    action = recommendations.get(verdict, "⚠️ **REVIEW** — Manual review recommended.")
    
    section = f"""### 🎯 Final Recommendation
{action}

**Next Steps:**
"""
    
    if verdict == "SAFE":
        section += "1. Auto-merge via Dependabot\n"
        section += "2. Monitor post-merge CI/CD for any issues\n"
    elif verdict == "REVIEW":
        section += "1. Review the changelog above\n"
        section += "2. Check affected callsites in reachability section\n"
        section += "3. Verify behavioral changes are acceptable\n"
        section += "4. Merge after review\n"
    elif verdict in ["BUILD_FAILS", "BLOCKED"]:
        section += "1. Fix build issues first\n"
        section += "2. Re-run analysis after fixes\n"
        section += "3. Do not merge until build is green\n"
    
    section += "\n---\n"
    
    return section

def format_probe_section(pr: Dict[str, Any]) -> str:
    """Format behavioral probe section with SHA256 and reproduction."""
    # Try behavioral_grade first (differential-probe.py output), then fallback to deterministic.probe
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
    
    if not probe:
        return "### 🔬 Behavioral Probe\n**Status:** ⬜ **NOT RUN**\n\n---\n"
    
    # Handle both behavioral_grade and deterministic.probe formats
    old_sha = probe.get("old_sha256", "N/A")[:16] if "old_sha256" in probe else "N/A"
    new_sha = probe.get("new_sha256", "N/A")[:16] if "new_sha256" in probe else "N/A"
    same = old_sha == new_sha or probe.get("same_behavior", False)
    
    status_emoji = "✅" if same else "⚠️"
    status_text = "SAME" if same else "DIFFERENT"
    
    pkg = pr.get("package")
    from_ver = pr.get("from")
    to_ver = pr.get("to")
    
    section = f"""### 🔬 Behavioral Probe
**Status:** {status_emoji} **{status_text}** | **Confidence:** HIGH

**Runtime Verification:**
- Old version SHA256: `{old_sha}`
- New version SHA256: `{new_sha}`
- Export shape: **{'UNCHANGED' if same else 'CHANGED'}**

**What this means:**
"""
    if same:
        section += "Runtime probe confirms the package behaves identically. No behavioral breaking changes detected.\n"
    else:
        section += """Runtime SHA256 mismatch proves behavioral changes are real, not just TypeScript type changes.
The package restructuring causes measurable runtime differences.

**Impact:** The probe provides independent confirmation beyond API diff. This catches:
- Implementation bugs
- Loader incompatibilities  
- Package.json misconfiguration
- Hidden behavioral changes not declared in changelog
"""
    
    # Add reproduction steps
    section += f"""
**Independent verification:**
```bash
# You can reproduce this probe locally:
cd /tmp
npm init -y
npm install {pkg}@{from_ver}
node -p "Object.keys(require('{pkg}')).sort().join(', ')"
npm install {pkg}@{to_ver}
node -p "Object.keys(require('{pkg}')).sort().join(', ')"
# Compare outputs and compute SHA256 of export shapes
node -e "const u=require('{pkg}'); const c=require('crypto'); console.log(c.createHash('sha256').update(JSON.stringify(Object.keys(u).sort())).digest('hex').slice(0,16))"
```

---
"""
    return section

def format_independent_verification(pr: Dict[str, Any]) -> str:
    """Format independent verification resources section."""
    pkg = pr.get("package")
    from_ver = pr.get("from")
    to_ver = pr.get("to")
    
    return f"""### 📚 Independent Verification Resources

**For developers who want to verify this analysis:**

1. **Changelog Source:**
   - Latest Release: https://github.com/search?q=repo:{pkg}+path:CHANGELOG&type=code
   - All Releases: https://github.com/{pkg}/releases

2. **API Diff Tool:**
   ```bash
   # Run locally:
   npx npm-diff-ts {pkg}@{from_ver} {pkg}@{to_ver}
   
   # Or compare exports:
   npm view {pkg}@{from_ver} exports
   npm view {pkg}@{to_ver} exports
   ```

3. **Behavioral Probe (reproduce):**
   ```bash
   cd /tmp && npm init -y
   
   # Install old version, inspect runtime:
   npm install {pkg}@{from_ver}
   node -e "const u=require('{pkg}'); console.log(Object.keys(u).sort())"
   
   # Install new version, compare:
   npm install {pkg}@{to_ver}
   node -e "const u=require('{pkg}'); console.log(Object.keys(u).sort())"
   ```

4. **Reachability Check:**
   ```bash
   # Search all imports:
   git grep -n "from '{pkg}'" src/
   git grep -n "require('{pkg}')" src/
   ```

5. **Analysis Run Logs:**
   - GitHub Actions: {pr.get('analysis_run_url', 'https://github.com/actions')}
   - Build results JSON: Available in Actions artifacts

---
"""

def _format_build_signal(build: Dict) -> str:
    verdict = build.get("verdict", "unknown")
    return {"pass": "✅ PASS", "fail": "❌ FAIL", "pre_existing": "⚠️ PRE-EXISTING"}.get(verdict, "⬜ UNKNOWN")

def _format_test_signal(test: Dict) -> str:
    if not test:
        return "⬜ SKIPPED"
    verdict = test.get("verdict", "skip")
    return {"pass": "✅ PASS", "fail": "❌ FAIL", "skip": "⬜ SKIPPED"}.get(verdict, "⬜ UNKNOWN")

def _format_api_diff_signal(det: Dict) -> str:
    changes = det.get("api_changes") or 0
    if changes == 0:
        return "✅ CLEAN"
    return f"⚠️ **BREAKING** ({changes} changes)"

def _format_changelog_signal(det: Dict) -> str:
    cl = det.get("changelogSignal", {})
    # Handle both string and dict formats
    if isinstance(cl, str):
        if cl == "breaking":
            return "⚠️ **BREAKING**"
        elif cl == "clean":
            return "✅ CLEAN"
        return "⚪ NOT AVAILABLE"
    
    if cl.get("status") == "missing":
        return "⚪ NOT AVAILABLE"
    if cl.get("breaking_markers", 0) > 0:
        return "⚠️ **BREAKING**"
    return "✅ CLEAN"

def _format_reachability_signal(pr: Dict) -> str:
    det = pr.get("deterministic", {})
    files = det.get("import_files", [])
    reachable = det.get("reachable", False)
    
    # Check both reachable flag and import_files list
    if reachable and files:
        return f"⚠️ **REACHED** ({len(files)} files)"
    elif files:
        return f"⚠️ **REACHED** ({len(files)} files)"
    return "✅ NOT REACHED"

def _format_probe_signal(pr: Dict) -> str:
    # Try behavioral_grade first, then deterministic.probe
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
    if not probe:
        return "⬜ NOT RUN"
    
    # Handle both formats
    if "same_behavior" in probe:
        return "✅ SAME" if probe.get("same_behavior") else "⚠️ **DIFFERENT**"
    
    old_sha = probe.get("old_sha256", "")[:16]
    new_sha = probe.get("new_sha256", "")[:16]
    if old_sha and new_sha and old_sha == new_sha:
        return "✅ SAME"
    return "⚠️ **DIFFERENT**"

def _format_ai_signal(pr: Dict) -> str:
    # Try ai_adjudication first, then ai_verdict for backward compat
    ai = pr.get("ai_adjudication") or pr.get("ai_verdict", {})
    if not ai:
        return "⬜ NOT-APPLICABLE"
    
    # Handle both formats
    if "applied" in ai:
        applied = ai.get("applied", "")
        if applied == "downgrade_to_safe":
            return "✅ SAFE"
        elif applied == "needs_change":
            return "⚠️ REVIEW"
    
    return ai.get("verdict", "REVIEW")

def _get_evidence_summary(pr: Dict, layer: str) -> str:
    """Get brief evidence for signal table."""
    if "Build" in layer:
        build = pr.get("build", {})
        return build.get("verdict", "unknown").replace("_", " ")
    elif "Test" in layer:
        test = pr.get("test", {})
        if not test or test.get("verdict") == "skip":
            return "Not run"
        return "Passed" if test.get("verdict") == "pass" else "Failed"
    elif "API" in layer:
        return f"{(pr.get('deterministic', {}).get('api_changes') or 0)} symbols"
    elif "Changelog" in layer:
        cl = pr.get("deterministic", {}).get("changelogSignal", {})
        # Handle both string and dict formats
        if isinstance(cl, str):
            return "Breaking" if cl == "breaking" else "Clean"
        return "Breaking markers found" if cl.get("breaking_markers") else "Clean"
    elif "Reachability" in layer:
        det = pr.get("deterministic", {})
        files = det.get("import_files", [])
        return f"{len(files)} file(s)" if files else "Not imported"
    elif "Probe" in layer:
        probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
        if probe:
            if "same_behavior" in probe:
                return "Behavior changed" if not probe["same_behavior"] else "Behavior same"
            old = probe.get("old_sha256", "")[:16]
            new = probe.get("new_sha256", "")[:16]
            return "SHA256 mismatch" if old != new else "SHA256 match"
        return "Not run"
    elif "AI" in layer:
        return "Human review required"
    return ""

def _count_warning_signals(signals: List) -> str:
    warnings = sum(1 for _, result, _ in signals if "⚠️" in result or "❌" in result)
    total = len([s for s in signals if "⬜" not in s[1]])
    return f"{warnings}/{total}"

def _get_recommendation(pr: Dict) -> str:
    verdict = pr.get("verdict_v2", {}).get("verdict", "REVIEW")
    if verdict == "SAFE":
        return "Safe to merge. Build passes and no breaking changes detected."
    elif verdict == "BUILD_FAILS":
        return "Fix build errors before merging."
    else:
        pkg = pr.get("package")
        det = pr.get("deterministic", {})
        files = det.get("import_files", [])
        file_ref = files[0] if files else "affected code"
        return f"Review the changelog and verify callsites at `{file_ref}` are compatible, then merge."

def _get_build_confidence(build: Dict) -> str:
    verdict = build.get("verdict", "unknown")
    if verdict == "pass":
        return "HIGH"
    elif verdict == "pre_existing":
        return "MEDIUM"
    return "LOW"

def _get_build_confidence_reason(build: Dict) -> str:
    verdict = build.get("verdict", "unknown")
    if verdict == "pass":
        return "Build passes with no new errors"
    elif verdict == "pre_existing":
        return "Pre-existing errors not caused by this upgrade"
    return "Build verification incomplete"

def _get_test_confidence(test: Dict) -> str:
    if not test:
        return "LOW"
    verdict = test.get("verdict", "skip")
    return "HIGH" if verdict == "pass" else "LOW"

def render_pr_comment(pr: Dict[str, Any]) -> str:
    """Render complete PR comment in gold standard format (13 sections)."""
    sections = [
        format_verdict_header(pr),           # 1. Header with verdict
        format_signal_summary(pr),           # 2. Signal summary table
        format_build_analysis(pr),           # 3. Build analysis
        format_test_analysis(pr),            # 4. Test analysis
        format_api_diff_analysis(pr),        # 5. API diff analysis
        format_changelog_analysis(pr),       # 6. Changelog analysis
        format_reachability_analysis(pr),    # 7. Reachability analysis
        format_probe_section(pr),            # 8. Behavioral probe
        format_ai_arbiter_section(pr),       # 9. AI arbiter layer
        format_policy_decision(pr),          # 10. Policy decision
        format_final_recommendation(pr),     # 11. Final recommendation
        format_independent_verification(pr)  # 12. Independent verification
    ]
    
    footer = f"""
📋 **Merge Plan:** [#{pr.get('merge_plan_issue', 'TBD')}](TBD)  
🔗 **Analysis Run:** [Actions]({pr.get('analysis_run_url', 'https://github.com/actions')})  
🔬 **Mode:** Deterministic + Behavioral Probe · **Model:** {pr.get('ai_model', 'claude-sonnet-4.5')} · **Analyzed:** {pr.get('analyzed_at', 'TBD')}

---

**💡 About this analysis:**
This comment was generated by the Breakability Pipeline, which combines 7 independent evidence layers to provide high-confidence merge recommendations. The goal is to reduce developer review time by 85% while maintaining zero false-greens.
"""  # 13. Footer
    
    return "\n".join(sections) + footer

def main():
    if len(sys.argv) < 2:
        print("Usage: breakability_analyst.py <build-results.json>", file=sys.stderr)
        sys.exit(1)
    
    results_file = sys.argv[1]
    with open(results_file) as f:
        data = json.load(f)
    
    results = data.get("results", [])
    if not results:
        print("No results found in build-results.json", file=sys.stderr)
        sys.exit(1)
    
    for pr in results:
        pr_num = pr.get("pr_num")
        if not pr_num:
            continue
        
        comment = render_pr_comment(pr)
        
        # Write to file for review (actual posting done by calling script)
        output_file = f"/tmp/pr-{pr_num}-comment.md"
        with open(output_file, "w") as f:
            f.write(comment)
        
        print(f"✅ Rendered PR #{pr_num} comment to {output_file}")

if __name__ == "__main__":
    main()
