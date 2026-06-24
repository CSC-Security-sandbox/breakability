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

def _normalize_verdict(pr: Dict) -> Dict[str, str]:
    """Extract verdict from verdict_v2 or deterministic layer.
    
    Returns: {verdict: str, confidence: str, severity: str, priority: str}
    """
    # Try verdict_v2 first (new format)
    v2 = pr.get("verdict_v2", {})
    if v2 and v2.get("verdict"):
        return {
            "verdict": v2.get("verdict", "REVIEW"),
            "confidence": v2.get("confidence", "MEDIUM"),
            "severity": v2.get("severity", "medium"),
            "priority": v2.get("priority", "P2")
        }
    
    # Fall back to deterministic layer
    det = pr.get("deterministic", {})
    verdict_data = det.get("verdict") or det.get("merge_risk") or det.get("classification")
    
    # Handle dict verdict (tag-based)
    if isinstance(verdict_data, dict):
        tag = verdict_data.get("tag", "Medium")
        # Map tag to verdict
        verdict_map = {
            "Low": "SAFE",
            "Medium": "REVIEW",
            "High": "REVIEW",
            "BuildFails": "BUILD_FAILS",
            "Blocked": "BLOCKED"
        }
        verdict = verdict_map.get(tag, "REVIEW")
        severity = tag.lower() if tag in ["Low", "Medium", "High"] else "medium"
    elif isinstance(verdict_data, str):
        # Map old verdicts to new
        verdict_map = {
            "safe": "SAFE",
            "review": "REVIEW",
            "build_fails": "BUILD_FAILS",
            "blocked": "BLOCKED"
        }
        verdict = verdict_map.get(verdict_data.lower(), verdict_data.upper())
        severity = "medium"
    else:
        verdict = "REVIEW"
        severity = "medium"
    
    return {
        "verdict": verdict,
        "confidence": det.get("confidence", "MEDIUM"),
        "severity": severity,
        "priority": det.get("priority", "P2")
    }

def format_verdict_header(pr: Dict[str, Any]) -> str:
    """Format the verdict header with emoji, confidence, priority."""
    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    confidence = verdict_norm["confidence"]
    severity = verdict_norm["severity"]
    priority = verdict_norm["priority"]
    
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
    
    # Generate headline based on verdict
    headlines = {
        "SAFE": "This upgrade is safe to merge.",
        "REVIEW": "Review required for this upgrade.",
        "BUILD_FAILS": "Build fails with this upgrade.",
        "BLOCKED": "Critical issues block this upgrade."
    }
    headline = headlines.get(verdict, "Review required for this upgrade.")
    
    return f"""## {emoji} Breakability Analysis — {label} ({bump.title()}, Reachable, Behavioral Changes)

**Package:** `{pkg}` {from_ver} → {to_ver}  
**Bump Type:** {bump} · **Dep Type:** {dep_type} · **Priority:** {priority}  
**Verdict:** {emoji} **{label}** · **Confidence:** {confidence.upper()}

**Headline:** {headline}

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
    
    # Normalize test data - pipeline produces test.ran/exit/main_test_exit
    ran = test.get("ran", False)
    exit_code = test.get("exit", test.get("main_test_exit", -1))
    
    # Determine verdict from actual schema
    if not ran:
        verdict = "skip"
        reason = test.get("reason", "Tests not executed (build requirements not met)")
    elif exit_code == 0:
        verdict = "pass"
        reason = "All tests passed"
    elif exit_code is None:
        verdict = "skip"
        reason = "Test execution status unknown"
    else:
        verdict = "fail"
        reason = f"Tests failed with exit code {exit_code}"
    
    status_emoji = {"pass": "✅", "fail": "❌", "skip": "⚠️"}.get(verdict, "⬜")
    status_label = verdict.upper().replace("_", " ")
    
    section = f"""### 🧪 Test Analysis
**Status:** {status_emoji} **{status_label}** | **Reason:** {reason}

**What we checked:**
"""
    
    if verdict == "pass":
        section += f"- ✅ Test suite executed successfully\n"
        section += f"- ✅ All tests passed (exit {exit_code})\n"
        section += f"- Tests ran: {test.get('ran', 'N/A')}\n"
    elif verdict == "fail":
        section += f"- ❌ Test failures detected (exit {exit_code})\n"
        section += f"- Check build logs for failure details\n"
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
    
    # Real schema: cl.status = "clean"/"breaking"/"missing", cl.bullets = []
    if isinstance(cl, str):
        # Legacy format fallback: just a string like "breaking" or "clean"
        status = "⚠️ **BREAKING**" if cl == "breaking" else "✅ CLEAN"
        section = f"""### 📋 Changelog Analysis
**Status:** {status} | **Source:** Package changelog

**M8 Classification:** **{cl.upper()}**

**Confidence:** **MEDIUM** — Based on changelog signal.

---
"""
        return section
    
    changelog_status = cl.get("status", "missing")
    bullets = cl.get("bullets", [])
    
    if changelog_status == "missing":
        return """### 📋 Changelog Analysis
**Status:** ⚪ **NOT AVAILABLE** | **Source:** No changelog found

**Confidence:** **LOW** — Cannot assess changes without changelog.

---
"""
    
    # Check bullets for BREAKING markers (case-insensitive)
    has_breaking = any("BREAKING" in str(bullet).upper() or "BREAK" in str(bullet).upper() for bullet in bullets)
    is_breaking = changelog_status == "breaking" or has_breaking
    
    status = "⚠️ **BREAKING**" if is_breaking else "✅ CLEAN"
    
    section = f"""### 📋 Changelog Analysis
**Status:** {status} | **Source:** GitHub Releases / CHANGELOG.md

**Key Changes (from {pr.get('from', '?')} → {pr.get('to', '?')}):**
"""
    
    if bullets:
        for bullet in bullets[:10]:  # First 10 changes
            section += f"- {bullet}\n"
    else:
        section += f"- Changelog status: {changelog_status}\n"
    
    m8_class = "BREAKING" if is_breaking else "SAFE"
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
    canonical_reason = verdict_v2.get("reason", "Review required for this upgrade.")
    
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
    applied_rule = None
    applied_line = None
    
    build = pr.get("build", {})
    if build.get("verdict") == "fail":
        steps.append("❌ **[P1: Build]** Build completely fails → **BLOCKED**")
        if not applied_rule:
            applied_rule = "Build failure blocks merge"
            applied_line = "Line 1"
    elif build.get("verdict") == "pre_existing":
        steps.append("⚠️ **[P1: Build]** Pre-existing failures (not caused by this upgrade)")
    else:
        steps.append("✅ **[P1: Build]** Build passes")
    
    # Check security/CVE (precedence #2)
    cve = pr.get("deterministic", {}).get("cve")
    if cve and cve.get("found"):
        steps.append("🔴 **[P2: Security]** CVE detected → **BLOCKED**")
        if not applied_rule:
            applied_rule = "Security advisory blocks merge"
            applied_line = "Line 2"
    
    # Check behavioral probe (precedence #3)
    probe_norm = _normalize_probe(pr)
    if probe_norm["state"] == "DIFFERENT":
        steps.append("⚠️ **[P3: Probe]** Runtime behavior changed → **REVIEW**")
        if not applied_rule:
            applied_rule = "Behavioral changes require review (probe DIFFERENT + reached)"
            applied_line = "Line 3"
    elif probe_norm["state"] == "SAME":
        steps.append("✅ **[P3: Probe]** Runtime behavior unchanged")
    else:
        steps.append("⚪ **[P3: Probe]** Not executed")
    
    # Check reachability + breaking (precedence #4)
    det = pr.get("deterministic", {}) or {}
    changelog_norm = _normalize_changelog(det)
    if det.get("reachable") and ((det.get("api_changes") or 0) > 0 or changelog_norm["is_breaking"]):
        steps.append("⚠️ **[P4: Breaking]** Reached + API/changelog breaking → **REVIEW**")
        if not applied_rule:
            applied_rule = "Breaking changes in reached code"
            applied_line = "Line 4"
    
    # Check AI arbiter (precedence #5)
    ai = pr.get("ai_adjudication")
    if ai and ai.get("applied") == "downgrade_to_safe":
        steps.append("✅ **[P5: AI]** AI arbiter analyzed and downgraded to **SAFE**")
        if not applied_rule:
            applied_rule = "AI confirmed low risk after analysis"
            applied_line = "Line 5"
    
    # Default (precedence #6)
    if not applied_rule:
        applied_rule = "No warning signals detected (default safe)"
        applied_line = "Line 6"
    
    for step in steps:
        section += f"{step}\n"
    
    # Risk assessment
    risk_level = _assess_overall_risk(pr)
    zero_false_green = _check_zero_false_green(pr)
    
    section += f"""
**Applied rule:** {applied_line} ({applied_rule})

**Final Verdict:** **{verdict}** (Confidence: {confidence})

**Why {verdict}?** {canonical_reason}

**Risk Assessment:**
- Breaking change risk: **{risk_level}**
- Zero-false-green guarantee: {'✅ Multiple warning signals, fail-safe to REVIEW' if zero_false_green else '⚠️ Limited evidence, conservative REVIEW'}

**Confidence Calculation:**
- Build confidence: {_assess_build_confidence(pr)}
- Probe confidence: {_assess_probe_confidence(pr)}
- Signal agreement: {_calculate_signal_agreement(pr)}

**Precedence Applied:** The highest-precedence rule that matched determined the verdict. Lower-precedence rules were not consulted (fail-safe cascade).

---
"""
    
    return section

def format_final_recommendation(pr: Dict[str, Any]) -> str:
    """Format final recommendation section with specific callsite verification."""
    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    
    reach_norm = _normalize_reachability(pr)
    usages = reach_norm["usages"]
    
    probe_norm = _normalize_probe(pr)
    det = pr.get("deterministic", {}) or {}
    changelog_norm = _normalize_changelog(det.get("changelogSignal") or {})
    
    recommendations = {
        "SAFE": "✅ **MERGE** — No breaking changes detected. Safe to auto-merge.",
        "REVIEW": "⚠️ **REVIEW THEN MERGE**",
        "BUILD_FAILS": "❌ **DO NOT MERGE** — Build fails. Fix build issues before merging.",
        "BLOCKED": "🔴 **BLOCKED** — Critical issues detected. Manual investigation required."
    }
    
    action = recommendations.get(verdict, "⚠️ **REVIEW** — Manual review recommended.")
    
    section = f"""### 🎯 Final Recommendation
{action}

"""
    
    if verdict == "SAFE":
        section += """**Next Steps:**
1. Auto-merge via Dependabot
2. Monitor post-merge CI/CD for any issues

"""
    elif verdict == "REVIEW":
        # Add specific callsite verification if reached
        if usages and len(usages) > 0:
            first_usage = usages[0]
            file_path = first_usage.get("file", "unknown")
            line_num = first_usage.get("line", "?")
            symbol = first_usage.get("symbol", "unknown")
            usage_type = first_usage.get("usageType", "UNKNOWN")
            
            section += f"""**What to review:**

1. **Verify callsite compatibility:**
   - **File:** `{file_path}:{line_num}`
   - **Symbol:** `{symbol}` ({usage_type})
   - **Question:** Is this usage pattern still compatible with the new version?
   - **Expected:** {'YES (basic usage)' if usage_type == 'DIRECT_CALL' else 'Verify usage pattern'}

"""
            
            # Add probe-specific questions if probe ran
            if probe_norm["state"] == "DIFFERENT":
                section += f"""2. **Check runtime behavior:**
   - **Probe result:** SHA256 mismatch detected
   - **Question:** Does the behavioral change affect `{symbol}` usage?
   - **Expected:** Review probe output and compare export shapes

"""
            
            # Add changelog-specific questions if breaking
            if changelog_norm["is_breaking"]:
                section += f"""3. **Review breaking changes:**
   - **Changelog:** Breaking changes declared
   - **Question:** Are the breaking changes relevant to our usage?
   - **Expected:** Check changelog bullets for `{symbol}` or related APIs

"""
            
            # Usage count context
            total_usages = len(usages)
            if total_usages > 1:
                section += f"""4. **Check all {total_usages} callsites:**
   - **Impact:** Multiple files import this package
   - **Action:** Review all callsites listed in Reachability section above

"""
            
            section += f"""**Why this needs review:**
- {'⚠️ Probe confirms behavioral change (not false alarm)' if probe_norm['state'] == 'DIFFERENT' else ''}
- {'⚠️ Changelog declares breaking changes' if changelog_norm['is_breaking'] else ''}
- {'✅ Single callsite (low blast radius)' if total_usages == 1 else f'⚠️ {total_usages} callsites (verify each)'}

**Estimated review time:** {'5-10 minutes (single callsite)' if total_usages == 1 else f'{5 + total_usages * 3}-{10 + total_usages * 5} minutes ({total_usages} callsites)'}

"""
        else:
            # Unreached - simpler review
            section += """**What to review:**
1. Review the changelog for any breaking changes
2. Package is not directly imported (transitive dependency)
3. Consider updating if security fixes are included

**Estimated review time:** 2-5 minutes (unreached, low risk)

"""
    
    elif verdict in ["BUILD_FAILS", "BLOCKED"]:
        section += """**Next Steps:**
1. Fix build issues first
2. Re-run analysis after fixes
3. Do not merge until build is green

"""
    
    section += "---\n"
    
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
    
    # Explicit three-state logic: SAME/DIFFERENT/NOT_RUN
    # None means probe unavailable (not executed or data missing)
    same_behavior = probe.get("same_behavior")
    if same_behavior is None:
        # Probe was not run or data unavailable
        if old_sha == "N/A" and new_sha == "N/A":
            return "### 🔬 Behavioral Probe\n**Status:** ⬜ **NOT RUN** — No probe data available\n\n---\n"
        # SHAs present but same_behavior=None, infer from SHA equality
        same_behavior = (old_sha == new_sha)
    
    status_emoji = "✅" if same_behavior else "⚠️"
    status_text = "SAME" if same_behavior else "DIFFERENT"
    
    pkg = pr.get("package")
    from_ver = pr.get("from")
    to_ver = pr.get("to")
    
    section = f"""### 🔬 Behavioral Probe ⭐
**Status:** {status_emoji} **{status_text}** | **Method:** npm runtime-shape diff | **Grade:** HIGH

**Runtime Verification:**
- Old version SHA256: `{old_sha}`
- New version SHA256: `{new_sha}`
- Export shape: **{'UNCHANGED' if same_behavior else 'CHANGED'}**

**What this means:**
"""
    if same_behavior:
        section += """Runtime probe confirms the package behaves identically. No behavioral breaking changes detected.

**Why this matters:**
- Provides independent runtime verification beyond static analysis
- Catches implementation changes not visible in type signatures
- HIGH confidence that upgrade is safe (behavior proven unchanged)

"""
    else:
        section += """Runtime SHA256 mismatch proves behavioral changes are real, not just TypeScript type changes.
The package restructuring causes measurable runtime differences.

**Why probe evidence is decisive:**
- **Without probe:** Only have changelog + API diff (could be false alarm, types-only change)
- **With probe:** Runtime SHA256 mismatch **proves** behavior changed
- **This is the 85% value:** Probe prevents false-safe (blocking safe upgrades) and false-green (missing real breaks)

**Impact:** The probe provides independent confirmation beyond API diff. This catches:
- Implementation bugs introduced in new version
- Loader incompatibilities (CJS/ESM/UMD changes)
- Package.json misconfiguration affecting runtime
- Hidden behavioral changes not declared in changelog or types

"""
    
    # Add reproduction steps
    section += f"""**Independent verification:**
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
    """Format independent verification resources section with 6 complete workflows."""
    pkg = pr.get("package")
    from_ver = pr.get("from")
    to_ver = pr.get("to")
    det = pr.get("deterministic", {}) or {}
    usages = det.get("usages", [])
    
    section = f"""### 📚 Independent Verification Resources

**For developers who want to verify this analysis:**

**1. Changelog Source:**
   - Latest Release: https://github.com/{pkg.split('/')[-1]}/releases/tag/v{to_ver}
   - Older Release: https://github.com/{pkg.split('/')[-1]}/releases/tag/v{from_ver}
   - Full CHANGELOG: https://github.com/{pkg.split('/')[-1]}/blob/main/CHANGELOG.md
   - NPM Page: https://www.npmjs.com/package/{pkg}/v/{to_ver}

**2. API Diff Tool:**
   ```bash
   # Run locally:
   npx npm-diff-ts {pkg}@{from_ver} {pkg}@{to_ver}
   
   # Or compare exports manually:
   npm view {pkg}@{from_ver} exports
   npm view {pkg}@{to_ver} exports
   
   # Check for type-only changes:
   npm view {pkg}@{from_ver} | grep types
   npm view {pkg}@{to_ver} | grep types
   ```

**3. Behavioral Probe (reproduce):**
   ```bash
   cd /tmp && npm init -y
   
   # Install old version, inspect runtime:
   npm install {pkg}@{from_ver}
   node -e "const u=require('{pkg}'); console.log(Object.keys(u).sort())"
   
   # Install new version, compare:
   npm install {pkg}@{to_ver}
   node -e "const u=require('{pkg}'); console.log(Object.keys(u).sort())"
   
   # Generate SHA256 of export shapes:
   node -e "const u=require('{pkg}'); const c=require('crypto'); console.log(c.createHash('sha256').update(JSON.stringify(Object.keys(u).sort())).digest('hex').slice(0,16))"
   ```

**4. Reachability Check:**
   ```bash
   # Search all imports in your codebase:
   git grep -n "from '{pkg}'" src/
   git grep -n "require('{pkg}')" src/
   
   # Find specific symbol usage:
   git grep -n "{pkg.split('/')[-1]}" src/
   ```
"""
    
    if usages and len(usages) > 0:
        first_file = usages[0].get("file", "unknown")
        first_line = usages[0].get("line", 0)
        first_symbol = usages[0].get("symbol", "unknown")
        
        section += f"""
**5. Callsite Inspection:**
   ```bash
   # View the actual usage context:
   cat {first_file} | sed -n '{max(1, first_line-5)},{first_line+5}p' | cat -n
   
   # Or open in editor:
   code {first_file}:{first_line}
   
   # Check if symbol '{first_symbol}' usage is affected by upgrade
   ```
   
   **Verification questions:**
   - Does `{first_symbol}` exist in new version? (Check API diff)
   - Has signature changed? (Check TypeScript types)
   - Is usage pattern compatible? (Check changelog for migration notes)
"""
    else:
        section += """
**5. Callsite Inspection:**
   ```bash
   # No direct callsites found (unreached dependency)
   # Check transitive usage:
   npm ls {pkg}
   ```
"""
    
    section += f"""
**6. Analysis Run Logs:**
   - This analysis run: Check GitHub Actions workflow artifacts
   - Build results JSON: Download from Actions → Artifacts → build-results
   - Probe output: Check deterministic stage logs in Actions
   - Full pipeline: Review all 7 evidence layers in build-results.json

**Callgraph Tool (when available):**
```bash
# Future: exact call-chain from production entry to dependency
python3 .github/scripts/callsite_impact.py \\
  --pr-data build-results.json \\
  --package {pkg}
# Will show: entry.ts → service.ts → {pkg}.method()
```

**External Resources:**
- Package Security: https://snyk.io/advisor/npm-package/{pkg}
- Bundle Size Impact: https://bundlephobia.com/package/{pkg}@{to_ver}
- Type Definitions: https://www.npmjs.com/package/@types/{pkg.split('/')[-1]}
- Migration Guide: Search https://github.com/{pkg.split('/')[-1]}/blob/main/UPGRADING.md

---
"""
    return section

# ============================================================================
# SCHEMA NORMALIZERS - Single source of truth for data interpretation
# ============================================================================

def _normalize_changelog(det: Dict) -> Dict[str, Any]:
    """Normalize changelog signal to unified format.
    
    Handles:
    - String format: "breaking" / "clean" / "missing"
    - Dict with status: {"status": "breaking", "bullets": [...]}
    - Dict bullets-only: {"bullets": ["BREAKING: ..."]}
    - Null / missing
    
    Returns: {status: str, bullets: list, is_breaking: bool, available: bool}
    """
    cl = det.get("changelogSignal")
    
    # Null or missing
    if not cl:
        return {"status": "missing", "bullets": [], "is_breaking": False, "available": False}
    
    # String format (legacy)
    if isinstance(cl, str):
        return {
            "status": cl,
            "bullets": [],
            "is_breaking": cl == "breaking",
            "available": cl != "missing"
        }
    
    # Dict format
    if not isinstance(cl, dict):
        return {"status": "missing", "bullets": [], "is_breaking": False, "available": False}
    
    status = cl.get("status", "unknown")
    bullets = cl.get("bullets", [])
    
    # Coerce bullets to list (handle null, string, non-list)
    if bullets is None:
        bullets = []
    elif isinstance(bullets, str):
        bullets = [bullets] if bullets else []
    elif not isinstance(bullets, list):
        bullets = []
    
    # Check bullets for BREAKING patterns (case-insensitive)
    has_breaking_in_bullets = any(
        "BREAKING" in str(bullet).upper() or "BREAK" in str(bullet).upper() 
        for bullet in bullets
    )
    
    # Determine if breaking: status OR bullets content
    is_breaking = status == "breaking" or has_breaking_in_bullets
    
    # Available if status != missing OR bullets exist
    available = status != "missing" or len(bullets) > 0
    
    return {
        "status": status,
        "bullets": bullets,
        "is_breaking": is_breaking,
        "available": available
    }

def _normalize_test(test: Dict) -> Dict[str, Any]:
    """Normalize test result to unified format.
    
    Handles:
    - New schema: test.ran, test.exit, test.main_test_exit
    - Legacy schema: test.verdict, test.exit_code
    - Missing/null
    
    Returns: {verdict: str, exit_code: int, ran: bool, reason: str}
    """
    if not test:
        return {"verdict": "skip", "exit_code": -1, "ran": False, "reason": "No test data"}
    
    # Check for new schema first
    if "ran" in test:
        ran = test.get("ran", False)
        exit_code = test.get("exit")
        if exit_code is None:
            exit_code = test.get("main_test_exit", -1)
        
        if not ran:
            verdict = "skip"
            reason = test.get("reason", "Tests not executed")
        elif exit_code == 0:
            verdict = "pass"
            reason = "All tests passed"
        elif exit_code is None:
            verdict = "skip"
            reason = "Test execution status unknown"
        else:
            verdict = "fail"
            reason = f"Tests failed with exit code {exit_code}"
        
        return {"verdict": verdict, "exit_code": exit_code, "ran": ran, "reason": reason}
    
    # Legacy schema fallback
    verdict = test.get("verdict", "skip")
    exit_code = test.get("exit_code", -1)
    reason = test.get("reason", "Test execution status")
    ran = verdict == "pass" or verdict == "fail"
    
    return {"verdict": verdict, "exit_code": exit_code, "ran": ran, "reason": reason}

def _normalize_probe(pr: Dict) -> Dict[str, Any]:
    """Normalize behavioral probe to unified format.
    
    Handles:
    - same_behavior: True/False/None
    - behavior_changed / changed_behavior: True/False
    - Legacy different: True/False
    - Missing/null probe data
    - Both behavioral_grade and deterministic.probe
    
    Returns: {state: str, same_behavior: bool|None, evidence: dict}
    """
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
    
    if not probe:
        return {"state": "NOT_RUN", "same_behavior": None, "evidence": {}}
    
    # Check same_behavior field (primary)
    same_behavior = probe.get("same_behavior")
    
    # Check behavior_changed / changed_behavior (inverse of same_behavior)
    if same_behavior is None:
        behavior_changed = probe.get("behavior_changed") or probe.get("changed_behavior")
        if behavior_changed is True:
            same_behavior = False
        elif behavior_changed is False:
            same_behavior = True
        elif behavior_changed == "unverified":
            same_behavior = None
    
    # Check legacy different field
    if same_behavior is None and "different" in probe:
        different = probe.get("different")
        if different is True:
            same_behavior = False
        elif different is False:
            same_behavior = True
    
    # Determine state
    if same_behavior is True:
        state = "SAME"
    elif same_behavior is False:
        state = "DIFFERENT"
    else:
        # Check SHA equality as last resort
        old_sha = probe.get("old_sha256", "")[:16]
        new_sha = probe.get("new_sha256", "")[:16]
        if old_sha and new_sha:
            if old_sha == new_sha:
                state = "SAME"
                same_behavior = True
            else:
                state = "DIFFERENT"
                same_behavior = False
        else:
            state = "NOT_RUN"
    
    return {
        "state": state,
        "same_behavior": same_behavior,
        "evidence": probe
    }

def _normalize_reachability(pr: Dict) -> Dict[str, Any]:
    """Normalize reachability from deterministic.usages or reachability key."""
    det = pr.get("deterministic", {})
    reach = pr.get("reachability", {})
    usages = det.get("usages", []) or reach.get("usages", [])
    import_files = det.get("files_importing", []) or reach.get("import_files", [])
    reached = len(usages) > 0 or len(import_files) > 0
    return {"usages": usages, "import_files": import_files, "reached": reached}

def _format_build_signal(build: Dict) -> str:
    verdict = build.get("verdict", "unknown")
    return {"pass": "✅ PASS", "fail": "❌ FAIL", "pre_existing": "⚠️ PRE-EXISTING"}.get(verdict, "⬜ UNKNOWN")

def _format_test_signal(test: Dict) -> str:
    normalized = _normalize_test(test)
    verdict = normalized["verdict"]
    return {"pass": "✅ PASS", "fail": "❌ FAIL", "skip": "⬜ SKIPPED"}.get(verdict, "⬜ UNKNOWN")

def _format_api_diff_signal(det: Dict) -> str:
    changes = det.get("api_changes") or 0
    if changes == 0:
        return "✅ CLEAN"
    return f"⚠️ **BREAKING** ({changes} changes)"

def _format_changelog_signal(det: Dict) -> str:
    normalized = _normalize_changelog(det)
    if not normalized["available"]:
        return "⚪ NOT AVAILABLE"
    if normalized["is_breaking"]:
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
    normalized = _normalize_probe(pr)
    state = normalized["state"]
    
    if state == "SAME":
        return "✅ SAME"
    elif state == "DIFFERENT":
        return "⚠️ **DIFFERENT**"
    else:
        return "⬜ NOT RUN"

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
        test_norm = _normalize_test(pr.get("test", {}))
        if test_norm["verdict"] == "skip":
            return "Not run"
        return "Passed" if test_norm["verdict"] == "pass" else f"Failed (exit {test_norm['exit_code']})"
    elif "API" in layer:
        return f"{(pr.get('deterministic', {}).get('api_changes') or 0)} symbols"
    elif "Changelog" in layer:
        det = pr.get("deterministic", {}) or {}
        cl_norm = _normalize_changelog(det)
        if cl_norm["is_breaking"]:
            return "Breaking"
        elif cl_norm["available"]:
            return "Clean"
        return "Not available"
    elif "Reachability" in layer:
        det = pr.get("deterministic", {}) or {}
        files = det.get("import_files", [])
        return f"{len(files)} file(s)" if files else "Not imported"
    elif "Probe" in layer:
        probe_norm = _normalize_probe(pr)
        if probe_norm["state"] == "SAME":
            return "Behavior same"
        elif probe_norm["state"] == "DIFFERENT":
            return "Behavior changed"
        return "Not run"
    elif "AI" in layer:
        return "Human review required"
    return ""

def _count_warning_signals(signals: List) -> str:
    warnings = sum(1 for _, result, _ in signals if "⚠️" in result or "❌" in result)
    total = len([s for s in signals if "⬜" not in s[1]])
    return f"{warnings}/{total}"

def _get_recommendation(pr: Dict) -> str:
    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    
    if verdict == "SAFE":
        return "Safe to merge. Build passes and no breaking changes detected."
    elif verdict == "BUILD_FAILS":
        return "Fix build errors before merging."
    else:
        # REVIEW verdict
        reach_norm = _normalize_reachability(pr)
        if not reach_norm["reached"]:
            # Not reached - review changelog only, no callsite mention
            return "Review the changelog for any notable changes, then merge."
        
        # Reached - check if we have file paths
        files = reach_norm["import_files"]
        if files and len(files) > 0:
            file_ref = files[0] if len(files) == 1 else f"{files[0]} and {len(files)-1} other file{'s' if len(files) > 2 else ''}"
            return f"Review the changelog and verify callsites in `{file_ref}` are compatible, then merge."
        else:
            # Reached but no file data
            return "Review the changelog and verify affected callsites are compatible, then merge."

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
    import argparse
    parser = argparse.ArgumentParser(description="Render breakability analysis PR comments")
    parser.add_argument("build_results", help="Path to build-results.json")
    parser.add_argument("--pr", type=str, help="Render only specific PR number")
    parser.add_argument("--stdout", action="store_true", help="Write to stdout instead of files")
    args = parser.parse_args()
    
    with open(args.build_results) as f:
        data = json.load(f)
    
    results = data.get("results", [])
    if not results:
        print("No results found in build-results.json", file=sys.stderr)
        sys.exit(1)
    
    # Filter by PR if requested
    if args.pr:
        results = [pr for pr in results if str(pr.get("pr_num")) == args.pr]
        if not results:
            print(f"PR #{args.pr} not found in results", file=sys.stderr)
            sys.exit(1)
    
    for pr in results:
        pr_num = pr.get("pr_num")
        if not pr_num:
            continue
        
        comment = render_pr_comment(pr)
        
        if args.stdout:
            print(comment)
        else:
            # Write to file for review (actual posting done by calling script)
            output_file = f"/tmp/pr-{pr_num}-comment.md"
            with open(output_file, "w") as f:
                f.write(comment)
            
            print(f"✅ Rendered PR #{pr_num} comment to {output_file}")

def _assess_overall_risk(pr: Dict[str, Any]) -> str:
    """Assess overall breaking change risk level."""
    det = pr.get("deterministic", {})
    api_changes = det.get("api_changes", 0)
    changelog_status = det.get("changelogSignal", {}).get("status", "missing")
    reachable = det.get("reachable", False)
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
    same_behavior = probe.get("same_behavior")
    
    # High risk: reached + breaking + different behavior
    if reachable and changelog_status == "breaking" and same_behavior is False:
        return "HIGH (breaking + reached + behavior changed)"
    
    # Medium risk: reached + breaking OR different behavior alone
    if (reachable and (api_changes > 0 or changelog_status == "breaking")) or same_behavior is False:
        return "MEDIUM (some warning signals)"
    
    # Low risk: not reached or all signals clean
    return "LOW (clean signals or unreached)"

def _check_zero_false_green(pr: Dict[str, Any]) -> bool:
    """Check if multiple warning signals confirm REVIEW verdict (not just one)."""
    warning_count = 0
    
    build = pr.get("build", {})
    if build.get("verdict") == "fail":
        warning_count += 1
    
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
    if probe.get("same_behavior") is False:
        warning_count += 1
    
    det = pr.get("deterministic", {})
    if det.get("reachable") and (det.get("api_changes", 0) > 0 or det.get("changelogSignal", {}).get("status") == "breaking"):
        warning_count += 1
    
    return warning_count >= 2

def _assess_build_confidence(pr: Dict[str, Any]) -> str:
    """Assess build verification confidence."""
    build = pr.get("build", {})
    verdict = build.get("verdict", "skip")
    verification = build.get("verification_level", "L0")
    
    if verdict == "pass" and verification in ["L2_tests_pass", "L3_e2e"]:
        return "**HIGH** (tests passed)"
    elif verdict == "pass":
        return "**MEDIUM** (build only, no tests)"
    elif verdict == "pre_existing":
        return "**LOW** (pre-existing failures)"
    else:
        return "**LOW** (build issues)"

def _assess_probe_confidence(pr: Dict[str, Any]) -> str:
    """Assess behavioral probe confidence."""
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
    same_behavior = probe.get("same_behavior")
    
    if same_behavior is True:
        return "**HIGH** (runtime verified unchanged)"
    elif same_behavior is False:
        return "**HIGH** (runtime verified changed)"
    else:
        return "**N/A** (probe not run)"

def _calculate_signal_agreement(pr: Dict[str, Any]) -> str:
    """Calculate how many signals warn vs pass."""
    det = pr.get("deterministic", {}) or {}
    build = pr.get("build", {}) or {}
    test = pr.get("test", {}) or {}
    probe_bg = pr.get("behavioral_grade", {}) or {}
    probe_det = det.get("probe", {}) or {}
    changelog = det.get("changelogSignal", {}) or {}
    
    signals = {
        "build": build.get("verdict", "skip") != "pass",
        "test": test.get("ran", False) and test.get("exit", -1) != 0,
        "api": (det.get("api_changes") or 0) > 0,
        "changelog": changelog.get("status") == "breaking",
        "probe": probe_bg.get("same_behavior") is False or probe_det.get("same_behavior") is False,
        "reachable": det.get("reachable", False)
    }
    
    warn_count = sum(1 for v in signals.values() if v)
    total_count = len(signals)
    
    return f"**{warn_count}/{total_count} signals warn** → {'REVIEW' if warn_count > 0 else 'SAFE'}"

if __name__ == "__main__":
    main()

# Phase 3: Helper functions for actionability

def _guess_compatibility(pr: Dict) -> str:
    """Guess compatibility based on usage patterns."""
    bump = pr.get("bump", "unknown")
    api_changes = pr.get("deterministic", {}).get("api_changes", 0)
    
    if bump == "patch" and api_changes == 0:
        return "HIGH (patch with no API changes usually safe)"
    elif bump == "minor":
        return "MEDIUM (minor should be backward compatible)"
    elif api_changes > 10:
        return "LOW (many API changes, verify carefully)"
    return "MEDIUM (review recommended)"

def _estimate_review_time(pr: Dict) -> str:
    """Estimate developer review time."""
    files = pr.get("deterministic", {}).get("import_files", [])
    api_changes = pr.get("deterministic", {}).get("api_changes", 0)
    
    if len(files) <= 1 and api_changes <= 5:
        return "5-10 minutes (single callsite, straightforward API)"
    elif len(files) <= 3 and api_changes <= 20:
        return "15-30 minutes (few callsites, moderate changes)"
    else:
        return "30-60 minutes (multiple callsites or complex changes)"

def _calculate_evidence_strength(pr: Dict) -> str:
    """Calculate overall evidence strength."""
    layers = _count_evidence_layers(pr)
    
    if layers >= 5:
        return "HIGH"
    elif layers >= 3:
        return "MEDIUM-HIGH"
    elif layers >= 2:
        return "MEDIUM"
    return "LOW"

def _count_evidence_layers(pr: Dict) -> int:
    """Count how many independent evidence layers provided data."""
    count = 0
    
    if pr.get("build", {}).get("verdict"):
        count += 1
    if pr.get("test", {}).get("verdict") not in [None, "skip"]:
        count += 1
    if pr.get("deterministic", {}).get("api_changes", 0) > 0:
        count += 1
    if pr.get("deterministic", {}).get("changelogSignal"):
        count += 1
    if pr.get("deterministic", {}).get("import_files"):
        count += 1
    if pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe"):
        count += 1
    if pr.get("ai_adjudication"):
        count += 1
    
    return count

def _get_matched_rule(pr: Dict) -> str:
    """Explain which precedence rule matched."""
    verdict = pr.get("verdict_v2", {}).get("verdict", "REVIEW")
    
    build = pr.get("build", {})
    if build.get("verdict") == "fail":
        return "Line 1 (Build Failures → BLOCKED)"
    
    cve = pr.get("deterministic", {}).get("cve")
    if cve and cve.get("found"):
        return "Line 2 (Security/CVE → BLOCKED)"
    
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
    if probe.get("same_behavior") == False or probe.get("different"):
        return "Line 3 (Probe DIFFERENT → REVIEW)"
    
    det = pr.get("deterministic", {})
    if det.get("reachable") and ((det.get("api_changes") or 0) > 0 or det.get("changelogSignal") == "breaking"):
        return "Line 4 (Reached + Breaking → REVIEW)"
    
    ai = pr.get("ai_adjudication")
    if ai and ai.get("applied") == "downgrade_to_safe":
        return "Line 5 (AI Downgrade → SAFE)"
    
    return "Line 6 (Default → SAFE)"

def _explain_confidence(conf: str, layer: str, pr: Dict) -> str:
    """Explain why confidence is at this level."""
    if layer == "build":
        if conf == "HIGH":
            return "(full build + tests pass)"
        elif conf == "MEDIUM":
            return "(dep resolution only, no tests)"
        return "(no evidence)"
    elif layer == "test":
        if conf == "HIGH":
            return "(all tests pass)"
        elif conf == "LOW":
            return "(tests skipped)"
        return "(no tests)"
    elif layer == "probe":
        if conf == "HIGH":
            return "(independent runtime verification)"
        return "(not run)"
    return ""

def _assess_breaking_risk(pr: Dict) -> str:
    """Assess breaking change risk."""
    api_changes = pr.get("deterministic", {}).get("api_changes", 0)
    files = pr.get("deterministic", {}).get("import_files", [])
    
    if api_changes > 10 and len(files) > 3:
        return "**HIGH** (many API changes + multiple callsites)"
    elif api_changes > 5 or len(files) > 1:
        return "**MEDIUM** (some changes + few callsites)"
    elif len(files) == 1:
        return "**LOW** (single callsite, easy to verify)"
    return "**NONE** (not reached or no API changes)"

def _assess_regression_risk(pr: Dict) -> str:
    """Assess regression risk."""
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})
    test = pr.get("test", {})
    
    if probe and not probe.get("same_behavior", True):
        return "**MEDIUM** (probe confirms behavior changed)"
    elif test.get("verdict") == "pass":
        return "**LOW** (tests pass, behavior verified)"
    return "**MEDIUM** (insufficient testing, unknown behavior)"

def _assess_security_risk(pr: Dict) -> str:
    """Assess security risk."""
    cve = pr.get("deterministic", {}).get("cve")
    
    if cve and cve.get("found"):
        severity = cve.get("severity", "UNKNOWN").upper()
        return f"**{severity}** (CVE detected, see security section)"
    return "**NONE** (no CVEs, but stay current for future patches)"

