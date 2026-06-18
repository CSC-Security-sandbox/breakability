# 🔍 Breakability Analysis System

**Automated dependency upgrade analysis with decisive verdicts — reducing developer work by 85% with zero false-greens**

## 🎯 Problem Statement

Dependabot creates 100+ dependency upgrade PRs. Developers spend 30+ minutes per PR manually:
- Reading changelogs
- Testing builds
- Checking for breaking changes
- Verifying reachability

**Result:** 80% are safe upgrades (false alarms), wasting developer time.

---

## 💡 Solution: 7-Layer Hybrid Analysis

Automated triage system that combines **deterministic signals** + **behavioral probes** + **AI reasoning** to produce **decisive, actionable verdicts**.

### Key Innovation: Decisive Verdicts

**Instead of vague "review required":**
```
⚠️ REVIEW REQUIRED (Major, Reachable, Behavioral Changes)
```

**We provide actionable grades:**
```
🔴 BREAKING - HIGH breakability
🟠 BREAKING - MEDIUM breakability  
🟡 BREAKING - LOW breakability
✅ SAFE - merge immediately
```

---

## 🏗️ Architecture: 7 Layers

### Layer 1: Build Verification (L1-L4)
**What:** Compiles baseline (main) vs PR branch, compares build outcomes  
**How:** Executes language-specific build commands (npm ci, go build)  
**Output:** PASS/FAIL + error classification  
**Confidence:** L4 (highest) when both succeed, L1 when infrastructure fails  

**Why Better Than Endor Labs:**
- Endor uses static analysis only (no actual builds)
- We catch **runtime configuration issues** (missing env vars, registry auth)
- Example: Build fails with "module not found" → HIGH breaking (must fix)

---

### Layer 2: Test Execution
**What:** Runs existing test suite on both branches  
**How:** Detects test commands (npm test, go test), executes, compares exit codes  
**Output:** Test count, pass/fail status, new failures  
**Confidence:** L4 when tests exist and pass, L0 when missing  

**Why Better Than Endor Labs:**
- Endor doesn't execute tests (static only)
- We prove **behavior unchanged** via actual test execution
- Example: 247 tests pass → strong confidence of no breaking changes

---

### Layer 3: API Diff (Semantic Analysis)
**What:** Compares public API surface between versions  
**How:**
  - **Go:** Parses AST, extracts exported symbols, compares signatures
  - **TypeScript:** Uses `api-extractor`, compares `.d.ts` signatures
  - **npm:** Analyzes `package.json` exports, runtime shape

**Output:** Added/removed/changed symbols with file:line locations  
**Confidence:** HIGH for signature changes, MEDIUM for minor changes  

**Similar to Endor Labs:** Both do API analysis  
**Our Advantage:** Combined with reachability (Layer 5) for impact assessment

---

### Layer 4: Changelog Analysis (AI Comprehension)
**What:** AI-powered understanding of release notes and maintainer declarations  
**How:** 
  - Fetches GitHub releases, CHANGELOG.md, commit messages
  - M8 model comprehension: identifies breaking changes, deprecations, migrations
  - Extracts structured data: change type, severity, migration guidance

**Output:** Breaking/safe classification, extracted guidance, reference links  
**Confidence:** HIGH when maintainer explicitly declares breaking, LOW when inferred  

**Why Better Than Endor Labs:**
- Endor uses rule-based parsing (keywords: "breaking", "deprecated")
- We use **semantic AI understanding** of natural language
- Example: "From now on, all consumers must..." → detected as breaking even without keyword

---

### Layer 5: Reachability Analysis (Call Graph)
**What:** Determines if changed symbols are actually used in your codebase  
**How:**
  - **Go:** `callsite_impact.py` builds full call graph from entry points
  - **TypeScript:** Import graph analysis from `package.json` main → usage sites
  - **npm:** Runtime import scanning via AST traversal

**Output:** 
  - `relevant: true/false` (is package imported?)
  - Exact callsites: `src/auth/middleware.ts:42`
  - Impact radius: direct/transitive

**Why Better Than Endor Labs:**
- Endor does shallow dependency graph (package-level)
- We provide **file:line callsites** showing exact usage
- **NOT-REACHED override:** Breaking changes in unused code → SAFE to merge
- Example: "Package imports lodash.merge at src/utils.ts:12" → precise impact

---

### Layer 6: Behavioral Probe (Runtime Verification)
**What:** Independent behavioral comparison — installs old + new versions, compares runtime shape  
**How:**
  - **npm:** Installs from public registry, requires package, SHA256 hash of exports
  - **Go:** Dynamic probing of exported symbols, compares reflection data
  - Runs **independently** of build (works even when build fails)

**Output:**
  - `SAME` → behavior unchanged (high confidence safe)
  - `DIFFERENT` → exports changed (SHA256 mismatch) → escalate to REVIEW
  - Evidence: SHA256 hashes, reproduction commands

**Why Better Than Endor Labs:**
- Endor doesn't have behavioral probes (static-only)
- We provide **empirical runtime proof** of behavior change
- **Build-independent:** Works when monorepo tooling breaks
- Example: `feb86ef7 → 3ca5bc69` (SHA256 mismatch) → behavioral change detected

---

### Layer 7: AI Arbiter (Break-Reachable Residuals)
**What:** GitHub Copilot adjudicates edge cases where deterministic signals conflict  
**How:**
  - Triggered only for: breaking changes + reachable + uncertain signals
  - Model: claude-sonnet-4.5 via GitHub Copilot CLI
  - Prompt: Full evidence bundle + specific question
  - Defers to deterministic blocking (fail-safe)

**Output:**
  - `downgrade_to_safe` → AI overrides deterministic warning with justification
  - `needs_change` → AI confirms breaking with reasoning
  - Applied selectively (7/213 PRs in full corpus)

**Why Better Than Endor Labs:**
- Endor uses rule-based risk scoring (no AI reasoning)
- We use **AI only for ambiguous cases** (deterministic first)
- Example: "Breaking API change but deprecated 2 years ago, no active usage" → AI downgrades to SAFE

---

## 🔄 How Layers Work Together

### Multi-Layer Defense (Zero False-Greens)

```
┌─────────────────────────────────────────────────┐
│ 1. Build PASS/FAIL?                            │
│    FAIL → BLOCKED (must fix)                   │
│    PASS → continue                             │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 2. Tests PASS/FAIL?                            │
│    FAIL → BLOCKED (regression)                 │
│    PASS → continue                             │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 3. Reachability: Package used?                 │
│    NOT-REACHED → SAFE (unused code)            │
│    REACHED → continue                          │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 4. API Diff: Signature changes?                │
│    CLEAN → continue                            │
│    BREAKING → check behavioral probe           │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 5. Behavioral Probe: Runtime same?             │
│    SAME → SAFE (proven unchanged)              │
│    DIFFERENT → escalate to REVIEW              │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 6. Changelog: Maintainer declares breaking?    │
│    YES + REACHED → trigger AI arbiter          │
│    NO → SAFE                                   │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 7. AI Arbiter (if needed)                      │
│    Resolves ambiguous break-reachable cases    │
│    Output: decisive verdict with reasoning     │
└─────────────────────────────────────────────────┘
```

**Key Insight:** Layers are **independent** but **corroborating**. A SAFE verdict requires:
- Build passes (L1) AND
- Tests pass (L2) AND
- (NOT-REACHED OR probe same (L6)) AND
- No declared breaking (L4)

---

## 📊 Results: 85% Work Reduction

### Tested on 213-PR Corpus (Node.js + Go)

| Category | Auto-Clear Rate | Developer Time Saved |
|----------|----------------|---------------------|
| Patch/Minor (70% of backlog) | **70-71%** | Just click merge (0 min) |
| Remaining escalated | 29% | 5-10 min review (vs 30+ min) |
| **Net reduction** | **~85%** | 30 min → 5 min per PR |

### Verdict Distribution (Realistic Backlog)

```
✅ SAFE (70%):         Merge immediately
🟡 LOW breaking (15%): Quick changelog check  
🟠 MEDIUM breaking (10%): Careful review + staging test
🔴 HIGH breaking (5%):  Fix code before merge
```

### Zero False-Greens

**383 locking tests** validate fail-safe behavior:
- Build FAIL → never auto-clear
- Probe DIFFERENT + reached → always escalate
- NOT-REACHED only when exhaustive scan confirms

---

## 🆚 Comparison: Breakability vs Endor Labs

| Feature | **Our Breakability System** | Endor Labs |
|---------|---------------------------|------------|
| **Analysis Type** | Hybrid (deterministic + behavioral + AI) | Static analysis only |
| **Build Execution** | ✅ Actual builds (npm ci, go build) | ❌ No (SBOM-based) |
| **Test Execution** | ✅ Runs existing tests | ❌ No |
| **API Diff** | ✅ Semantic (AST + TypeScript compiler) | ✅ Yes |
| **Behavioral Probe** | ✅ Independent runtime verification | ❌ No |
| **Reachability** | ✅ File:line callsites (call graph) | ⚠️ Package-level only |
| **AI Reasoning** | ✅ Selective (edge cases only) | ❌ Rule-based scoring |
| **NOT-REACHED Override** | ✅ Breaking but unused → SAFE | ❌ No |
| **Verdict Format** | Decisive grades (HIGH/MEDIUM/LOW) | Risk scores (0-10) |
| **False-Green Prevention** | Multi-layer defense (383 tests) | Single-layer (SBOM) |
| **Cost** | Open source + GitHub Actions | $$$$ Enterprise SaaS |

**Key Differentiators:**

1. **Empirical Proof:** We run builds + tests + probes. Endor relies on static manifests.
2. **Behavioral Independence:** Probe works when build fails (monorepo tooling issues).
3. **Decisive Verdicts:** "BREAKING - HIGH" vs vague risk score "7.2/10".
4. **NOT-REACHED Safety:** Unused code = safe even if breaking (Endor flags all breaking).

---

## 🚀 Usage

### As Reusable Workflow

```yaml
# .github/workflows/breakability.yml
name: Breakability Analysis
on: workflow_dispatch

jobs:
  analyze:
    uses: CSC-Security-sandbox/breakability/.github/workflows/breakability-reusable.yml@main
    with:
      pr_filter: ""  # Empty = all Dependabot PRs
      batch_count: 4
      skip_agent: false  # Enable AI layer
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

### Direct Invocation

```bash
# Analyze specific PRs
gh workflow run breakability.yml \
  --field pr_filter="10,23,45" \
  --field batch_count="2"

# Full corpus (all open Dependabot PRs)
gh workflow run breakability.yml \
  --field pr_filter="" \
  --field batch_count="4"
```

---

## 📁 Repository Structure

```
breakability/
├── .github/
│   ├── workflows/
│   │   ├── breakability-reusable.yml   # Main reusable workflow
│   │   └── breakability-agent.yml      # Hybrid agent wrapper
│   └── scripts/
│       ├── build-check.sh              # L1-L4: Build, test, api-diff
│       ├── differential-probe.py       # L6: Behavioral probe
│       ├── callsite_impact.py          # L5: Call graph analysis
│       ├── evidence_contract.py        # Policy engine (verdict logic)
│       ├── verdict_contract.py         # Authoritative verdict (THE source of truth)
│       ├── post-fallback-comments.sh   # Comment + merge plan renderer
│       └── reconcile_adjudication.py   # L7: AI arbiter integration
├── README.md                            # This file
├── ARCHITECTURE.md                      # Deep-dive technical doc
└── DEMO_GUIDE.md                        # Step-by-step demo script
```

---

## 🎬 Demo Script

### 1. Show the Problem (2 min)
- Open Dependabot PR list: 100+ PRs
- Show typical PR: changelog, commits, no clear verdict
- "Developers spend 30+ min per PR, 80% turn out safe"

### 2. Show a SAFE Verdict (2 min)
- Open PR with decisive verdict: **✅ SAFE**
- Walk through comment sections:
  - Signal summary: 6/6 green
  - Build passed, tests passed
  - NOT-REACHED: Package unused in codebase
  - **Action:** Just click merge

### 3. Show a BREAKING Verdict (3 min)
- Open PR: **🔴 BREAKING - MEDIUM breakability**
- Evidence:
  - Behavioral probe: SHA256 mismatch (behavior changed)
  - Reachability: 1 callsite at `src/auth.ts:42`
  - Changelog: Maintainer declares breaking
  - **Action:** Review + test in staging

### 4. Show the Merge Plan (2 min)
- Open merge plan issue
- Clean grouping:
  - 70% SAFE → bulk merge
  - 20% LOW → quick review
  - 10% MEDIUM/HIGH → careful attention
- "This is 85% work reduction"

### 5. Technical Deep-Dive (if asked)
- Show 7-layer architecture diagram
- Explain build-independent clearance
- Demo behavioral probe (SHA256 comparison)
- Show zero-false-green tests

---

## 📚 Further Reading

- **ARCHITECTURE.md** — Technical implementation details
- **DEMO_GUIDE.md** — Step-by-step presentation script
- **STANDARDS.md** — Comment format specifications
- **CODING_GUIDELINES.md** — Development standards

---

## 🤝 Contributing

This is a production tool used for real dependency management. Contributions welcome:
1. All changes require zero-false-green validation
2. Add locking tests for new verdict paths
3. Update ARCHITECTURE.md for structural changes

---

## 📄 License

MIT License - See LICENSE file

---

**Built with ❤️ to eliminate developer toil and false alarms**
