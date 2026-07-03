# Breakability Analysis - Product Requirements

**Goal:** Automated dependency upgrade analysis reducing developer work by 85% with zero false-greens

**Status:** Alpha - 3/7 layers working end-to-end, renderer incomplete

---

## Product Vision

Dependabot creates 100+ upgrade PRs. 80% are safe (false alarms). Developers spend 30 min/PR manually checking. **We automate this.**

**Output:** Decisive verdict (SAFE/REVIEW/BLOCKED) + evidence for each PR

---

## 7 Evidence Layers

### 1. Build Verification ✅ WORKING
- **What:** Compile main vs PR branch, compare outcomes
- **Ecosystems:** npm, Go, Python, Maven, Gradle
- **Levels:** L1 (dep resolved) → L5 (full test suite)
- **Output:** PASS/FAIL/PRE_EXISTING + error classification

### 2. Test Execution ✅ WORKING
- **What:** Run test suites on both branches
- **Ecosystems:** npm (jest, mocha), Go (go test), Python (pytest)
- **Output:** PASS/FAIL/SKIP + error diffs

### 3. API Diff ✅ WORKING (not rendered)
- **What:** Structural changes (exports added/removed/changed)
- **Tools:** npm-apidiff (TypeScript), go apidiff (Go modules)
- **Output:** Symbol-level changes + breaking classification

### 4. Changelog Analysis ✅ WORKING (not rendered)
- **What:** Parse release notes for breaking markers
- **Sources:** GitHub releases, CHANGELOG.md, commit messages
- **Markers:** BREAKING, BREAKING CHANGE, [major], M8 classification

### 5. Reachability ✅ WORKING (not rendered)
- **What:** Does your code import this package?
- **Method:** git grep for import statements
- **Output:** File:line callsites or NOT_REACHED

### 6. Behavioral Probe ⚠️ PARTIAL
- **What:** Runtime verification (does it behave differently?)
- **npm:** SHA256 of export shapes before/after
- **Go:** Compiled binary comparison
- **Status:** Code exists, workflow integration added 2026-06-23, needs E2E test

### 7. AI Arbiter ⚠️ PARTIAL
- **What:** Reason about conflicting signals
- **Input:** REVIEW verdicts with evidence
- **Output:** Downgrade to SAFE (with reasoning) OR keep REVIEW
- **Status:** reconcile_adjudication.py exists, needs verdict generation step

---

## Verdict Decision

**Authoritative verdict from:**
1. Hard BUILD_FAILS floor → BLOCKED
2. AI adjudication → SAFE/REVIEW
3. Policy engine (evidence_contract.py) → SAFE/REVIEW
4. Fail-closed → REVIEW

**Output:**
- verdict: SAFE/REVIEW/BLOCKED
- severity: none/low/medium/high
- confidence: L1-L5
- reason: Human-readable explanation
- breakability_grade: SAFE/LOW/MEDIUM/HIGH

---

## Comment Format (Gold Standard)

**Reference:** [PR #208](https://github.com/CSC-Security-sandbox/ndm-breakability-test/pull/208#issuecomment-4737308189)  
**Lines:** 408  
**Sections:** 13 mandatory

### Current Status: 5/13 sections rendered

**✅ Rendered:**
1. Verdict Header (emoji, confidence, priority)
2. Signal Summary Table (7 layers)
3. Build Analysis
4. Behavioral Probe
5. Independent Verification Resources
6. Footer

**❌ Missing:**
7. Test Analysis
8. API Diff Analysis
9. Changelog Analysis
10. Reachability Analysis
11. AI Arbiter Layer
12. Policy Decision
13. Final Recommendation (with checklist + time estimate)

**Problem:** Data exists in build-results.json, just not rendered

---

## Current Challenges

### 1. Regression: Comment Template Lost
- **Problem:** Gold standard format from PR #208 was manual showcase, never implemented
- **Impact:** Comments missing 8/13 sections
- **Fix:** Expand breakability_analyst.py to use existing data
- **Status:** In progress

### 2. AI Layer Not Running
- **Problem:** reconcile_adjudication.py doesn't generate verdicts, just reconciles
- **Impact:** ai_adjudication field always empty, AI arbiter dormant
- **Fix:** Add verdict generation step OR make reconcile call Cursor
- **Status:** Deferred to Beta (deterministic MVP first)

### 3. Contract Mismatches (FIXED 2026-06-23)
- ✅ Probe writes behavioral_grade, renderer expected deterministic.probe
- ✅ AI writes ai_adjudication, renderer expected ai_verdict
- ✅ verdict_contract.py had no CLI, workflow step was no-op

### 4. Transitive Deps Not Handled
- **Problem:** No detection of direct vs transitive dependencies
- **Impact:** transitive+CVE+build_pass should be SAFE, currently REVIEW
- **Fix:** Add dep_type classification + security advisory fetching
- **Status:** Not started

---

## MVP Definition

**Ships when:**
1. ✅ Processes 100% of Dependabot PRs
2. ✅ Posts comments to all PRs
3. ✅ Creates merge plan Issue
4. ❌ Comments match gold standard (13 sections)
5. ❌ <15min end-to-end for 40 PRs
6. ❌ Zero false-greens measured

**Current:** 3/6 MVP criteria met

---

## Ecosystem Support

| Feature | npm | Go | Python | Maven |
|---------|-----|-----|--------|-------|
| Build | ✅ | ✅ | ⚠️ | ⚠️ |
| Test | ✅ | ✅ | ⚠️ | ❌ |
| API Diff | ✅ | ✅ | ❌ | ❌ |
| Changelog | ✅ | ✅ | ✅ | ✅ |
| Probe | ✅ | ⚠️ | ❌ | ❌ |

**Target:** npm + Go full parity for MVP

---

## References

- **Gold Standard:** [ndm-breakability-test PR #208](https://github.com/CSC-Security-sandbox/ndm-breakability-test/pull/208#issuecomment-4737308189)
- **Repos:**
  - Central: https://github.com/CSC-Security-sandbox/breakability
  - NDM: https://github.com/CSC-Security-sandbox/ndm-fresh-breakability
  - VCP: https://github.com/CSC-Security-sandbox/vcp-fresh-breakability
- **Validation Reports:**
  - implementation-auditor: 0/13 sections complete
  - fix-validator: 3 contract mismatches (fixed)

---

## Next Steps (Priority)

1. **Expand renderer to 13 sections** (use existing data)
2. **E2E test** (verify all 3 P0 fixes work)
3. **AI verdict generation** (not just reconciliation)
4. **Transitive deps + CVE detection**
5. **Go probe integration** (dynamic_probe_runner.py)
