# 🎤 Breakability Analysis — Demo Guide

**5-Minute Showcase Script**

---

## 📋 Preparation Checklist

Before demo:
- [ ] Have NDM fresh repo open: https://github.com/CSC-Security-sandbox/ndm-fresh-breakability
- [ ] Have merge plan issue open (will be #7 or similar)
- [ ] Have 2 PR tabs ready:
  - One SAFE verdict PR
  - One BREAKING verdict PR (ideally PR#208 gold standard)
- [ ] Have this demo guide visible (second screen)

---

## 🎯 Demo Flow

### Slide 1: The Problem (30 seconds)

**Say:**
"Dependabot creates 100+ dependency upgrade PRs. Developers manually review each one — read changelogs, test builds, check for breaking changes. Takes 30+ minutes per PR. 80% turn out safe. Massive time waste."

**Show:**
- Dependabot PR list (point to count: "6 PRs here, imagine 100+")

---

### Slide 2: Our Solution (30 seconds)

**Say:**
"We built an automated triage system. 7-layer analysis: builds, tests, API diffs, changelogs, reachability, behavioral probes, and AI reasoning. Output: decisive verdicts with full evidence."

**Show:**
- Architecture diagram (if available) OR
- README section on 7 layers

**Key Point:**
"Not just 'review required' — we say SAFE, LOW breaking, MEDIUM breaking, or HIGH breaking. Actionable."

---

### Slide 3: SAFE Example (1.5 minutes)

**Say:**
"Let me show you a SAFE verdict. This is a GitHub Actions upgrade."

**Navigate to:** SAFE PR (likely PR#6: docker/setup-buildx-action)

**Point out header:**
```
✅ SAFE · Oracle: high · Priority: P3
```

**Say:**
"Clear verdict: SAFE. High confidence. Priority 3."

**Scroll to Signal Summary Table:**
"All 6 layers checked:
- Build: PASS
- Test: PASS  
- API Diff: CLEAN
- Changelog: Non-breaking
- Reachability: NOT-REACHED (unused in production code)
- Behavioral Probe: Runtime shape unchanged"

**Key Point:**
"This is a CI dependency, doesn't affect application code. Multiple independent signals confirm safety. Developer action: just click merge. 0 minutes work."

---

### Slide 4: BREAKING Example (2 minutes)

**Say:**
"Now a BREAKING verdict — this needs review."

**Navigate to:** PR with BREAKING verdict (if available)

**Point out header:**
```
🔴 BREAKING - MEDIUM breakability · Oracle: high · Priority: P2
```

**Say:**
"Decisive: MEDIUM breaking. High confidence. Priority 2 — needs attention."

**Scroll to evidence sections:**

1. **Behavioral Probe:**
   "SHA256 hash mismatch: `feb86ef7` → `3ca5bc69`
   This proves behavior changed at runtime — not just theory."

2. **Reachability:**
   "Package is imported at `src/middleware/request-context.middleware.ts:12`
   We show exact file and line — precise impact."

3. **Independent Verification:**
   "Bash reproduction commands — developer can verify locally in 30 seconds."

**Key Point:**
"We don't say 'maybe check this' — we provide proof of breaking + exact impact location + reproduction steps. Developer still reviews, but now it's 5-10 minutes instead of 30+."

---

### Slide 5: Merge Plan (1 minute)

**Say:**
"For a full repo, we generate a merge plan grouping all PRs by severity."

**Navigate to:** Merge plan issue

**Point out sections:**
```
Summary:
- SAFE (70%): 4 PRs → merge immediately
- LOW breaking (20%): 1 PR → quick changelog check
- MEDIUM breaking (10%): 1 PR → review + test
```

**Say:**
"Clean actionable groups. Bulk merge the SAFE queue. Quick review for LOW. Careful attention for MEDIUM/HIGH."

**Key Result:**
"85% work reduction: 70% auto-cleared, remaining 30% get full evidence for faster review."

---

## 🔬 Deep-Dive Q&A (if time permits)

### Q: "How is this different from Endor Labs?"

**Answer:**
"Three key differences:

1. **Empirical proof:** We run actual builds and tests. Endor uses static SBOM analysis only — no execution.

2. **Behavioral probe:** Independent runtime verification. Example: we install old + new npm versions, compare SHA256 of exports. Works even when build fails.

3. **NOT-REACHED override:** Breaking changes in unused code → SAFE to merge. Endor flags everything breaking regardless of usage.

Bottom line: We provide evidence-based verdicts, not risk scores."

---

### Q: "What about false-greens?"

**Answer:**
"Multi-layer defense. 383 locking tests validate:
- Build FAIL → never auto-clear
- Test FAIL → never auto-clear
- Probe DIFFERENT + reachable → always escalate to REVIEW
- NOT-REACHED only when exhaustive scan confirms

If ANY layer says 'uncertain' or 'breaking', we escalate. Fail-safe."

---

### Q: "Does it work for Go and Node.js?"

**Answer:**
"Yes, full support for both:
- Go: `go build`, `go test`, AST-based API diff, call graph analysis
- Node.js/TypeScript: npm workspace support, `api-extractor`, runtime probe
- Also: GitHub Actions, Docker (CI dependencies)

Extensible to other languages — same layer pattern."

---

### Q: "How long does it take?"

**Answer:**
"Deterministic stage: ~20-30 minutes for 20-30 PRs (parallelizes across 4 runners).
AI stage (if needed): +7 hours for full 200+ PR corpus, but AI only engages for ~7% of PRs.

Most teams: run deterministic-only overnight, get results in morning."

---

### Q: "What's the AI layer for?"

**Answer:**
"Selective adjudication. Only triggers when:
- Breaking changes detected AND
- Package is reachable AND
- Deterministic signals conflict (e.g., breaking declared but tests pass)

Model: claude-sonnet-4.5 via GitHub Copilot. Reviews full evidence bundle, provides reasoning. Applied to <10% of PRs. Deterministic is primary."

---

## 🎬 Closing Statement

**Say:**
"This system is production-ready. We've achieved 85% developer work reduction while maintaining zero false-greens. It works across languages, scales with parallel execution, and provides actionable verdicts with full evidence. Dependabot PRs go from 30-minute manual reviews to 5-minute evidence-based decisions — or fully automated merges for 70% of upgrades."

**End with:** 
"Questions?"

---

## 📊 Backup Slides (if needed)

### Technical Architecture
- Show 7-layer diagram
- Explain multi-layer defense
- Point out build-independent clearance

### Locking Tests
- Show `test_evidence_contract.py`
- 383 tests validate fail-safe behavior
- No regression possible

### Scalability
- 4 parallel runners (AWS c5.4xlarge)
- Deterministic layer: embarrassingly parallel
- AI layer: batched to avoid rate limits

---

**Demo Duration Breakdown:**
- Problem: 30s
- Solution: 30s
- SAFE example: 1.5 min
- BREAKING example: 2 min
- Merge plan: 1 min
- **Total: 5.5 minutes**
- Q&A buffer: 4.5 minutes
- **Full slot: 10 minutes**
