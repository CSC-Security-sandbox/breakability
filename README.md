# 🛡️ Breakability Analysis

**Automated dependency upgrade risk assessment for monorepos.**

Breakability Analysis builds every Dependabot PR against your repo's main branch, detects build failures, and posts structured risk assessments — all without merging anything.

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│  1. DISCOVER          2. ANALYZE              3. REPORT         │
│                                                                 │
│  Find all open    →   Build each PR in    →   Post structured   │
│  Dependabot PRs       parallel batches        comments + merge  │
│                       against main            plan Issue         │
│                                                                 │
│  ┌──────────┐        ┌──────────────┐        ┌──────────────┐  │
│  │ gh pr    │        │ npm ci       │        │ ✅ SAFE      │  │
│  │ list     │        │ go build     │        │ ⚠️ RISK      │  │
│  │ --app    │        │ pip install  │        │ ❌ FAILS     │  │
│  │ dependa- │        │ mvn compile  │        │              │  │
│  │ bot      │        │ docker build │        │ 📋 Merge     │  │
│  └──────────┘        └──────────────┘        │    Plan      │  │
│                                               └──────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         Deterministic Pipeline                  Copilot Agent
         (GitHub Actions)                        (Zero-config AI)
```

## ⚡ Quick Start (5 minutes)

### 1. Copy the caller workflow

Create `.github/workflows/breakability.yml` in your repo:

```yaml
name: Breakability Analysis

on:
  schedule:
    - cron: '0 6 * * 1'  # Weekly Monday 6 AM UTC
  workflow_dispatch:
    inputs:
      pr_filter:
        description: 'PR numbers (comma-separated, empty = all)'
        required: false
      batch_count:
        description: 'Parallel batches'
        required: false
        default: '4'
      skip_agent:
        description: 'Skip Copilot agent'
        type: boolean
        default: false

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  breakability:
    uses: CSC-Security-sandbox/breakability/.github/workflows/breakability-reusable.yml@main
    with:
      batch_count: ${{ fromJson(github.event.inputs.batch_count || '4') }}
      skip_agent: ${{ github.event.inputs.skip_agent == 'true' }}
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

### 2. Copy Copilot instructions (recommended)

Copy [`examples/copilot-instructions.md`](examples/copilot-instructions.md) to `.github/copilot-instructions.md` in your repo. This gives Copilot repo-wide context.

### 3. Copy the agent profile (recommended)

Copy [`agents/breakability-analyst.agent.md`](agents/breakability-analyst.agent.md) to `.github/agents/breakability-analyst.agent.md` in your repo. This defines the Copilot agent persona.

### 4. Copy the prompt file

Copy [`breakability-prompt.md`](breakability-prompt.md) to `.github/breakability-prompt.md` in your repo. This contains the full analysis instructions the agent follows.

### 5. Run it

```bash
gh workflow run breakability.yml --repo YOUR-ORG/YOUR-REPO
```

**That's it.** No API keys, no secrets, no configuration needed for public dependencies.

---

## 🔧 Configuration

### Private Registries

If your repo uses private npm/Go/Python registries, create `.github/breakability-config.yml`:

```yaml
registries:
  npm:
    registry_url: "https://npm.pkg.github.com"
    scope: "@your-org"
    token_env: "NPM_TOKEN"  # Set as repo secret
```

See [`examples/breakability-config.yml`](examples/breakability-config.yml) for all options.

### Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `pr_filter` | `''` | Comma-separated PR numbers (empty = all Dependabot PRs) |
| `batch_count` | `1` | Number of parallel analysis batches |
| `skip_agent` | `false` | Run deterministic only (no Copilot agent) |
| `node_version` | `20` | Node.js version |
| `go_version` | `1.22` | Go version |
| `python_version` | `3.12` | Python version |

---

## 🏗️ Architecture

### Two-Layer Hybrid System

**Layer 1 — Deterministic Pipeline** (GitHub Actions)
- Discovers all open Dependabot PRs via `gh pr list`
- Checks out each PR branch, runs the real build commands
- Compares build outcomes against main branch baseline
- Outputs structured `build-results.json` with pass/fail verdicts
- Handles: npm, Go modules, pip, Maven, Docker, monorepo workspaces

**Layer 2 — Copilot Agent** (GitHub Copilot coding agent)
- Reads `build-results.json` from the `breakability-results` orphan branch
- Posts structured comments on every PR with risk analysis
- Creates a consolidated merge plan Issue with priority ordering
- Never overrides deterministic verdicts — only adds context

### Why Two Layers?

| Concern | Deterministic | AI Agent |
|---------|--------------|----------|
| Build pass/fail | ✅ Ground truth | ❌ Never overrides |
| Changelog context | ❌ Can't read | ✅ Summarizes |
| Merge ordering | ❌ Not its job | ✅ Priority plan |
| Comment formatting | ❌ Basic fallback | ✅ Rich structured |
| Reliability | ✅ 100% deterministic | ⚠️ Best effort |

---

## 📊 What You Get

### PR Comments

Every Dependabot PR gets a structured comment:

```
## 🔍 Breakability Analysis

**Verdict:** ✅ SAFE TO MERGE
**Package:** express 4.18.2 → 4.19.0
**Semver:** patch (non-breaking)

### Build Results
- ✅ npm ci: success (12.3s)
- ✅ npm run build: success (8.1s)
- ✅ npm test: success (45.2s)

### Risk Assessment
Low risk. Patch version bump with no API changes.
Changelog confirms bug fixes only.
```

### Merge Plan Issue

A single consolidated Issue with all PRs sorted by priority:

```
## 📋 Merge Plan — 2025-03-31

### ✅ Safe to Merge (18 PRs)
1. #42 express 4.18.2 → 4.19.0 (patch, 0 risk)
2. #43 lodash 4.17.21 → 4.17.22 (patch, 0 risk)
...

### ⚠️ Review Recommended (3 PRs)
1. #51 typescript 5.3 → 5.4 (minor, breaking changes possible)

### ❌ Build Failures (2 PRs)
1. #55 @nestjs/core 10.3 → 11.0 (major, build fails in services/api)
```

---

## 🔒 Security & Permissions

| Permission | Why |
|------------|-----|
| `contents: write` | Push build results to `breakability-results` branch |
| `pull-requests: write` | Post analysis comments on PRs |
| `issues: write` | Create merge plan Issues, assign to Copilot |

### What It NEVER Does

- ❌ Never merges, closes, or approves PRs
- ❌ Never modifies source code
- ❌ Never pushes to main or PR branches
- ❌ Never accesses secrets beyond GITHUB_TOKEN

---

## 🧪 Tested On

| Repo | Type | PRs | Result |
|------|------|-----|--------|
| NestJS monorepo | 8 services, 4 shared libs | ~115 | ✅ 100% accuracy |
| Go monorepo | 3 go.mod files | 30 | ✅ 100% accuracy |
| Test app | Node.js + Go | 23 | ✅ 100% accuracy |

---

## 📁 Repository Structure

```
breakability/
├── action.yml                          # Composite action (simple, single-job)
├── breakability-prompt.md              # Full analysis instructions for Copilot
├── agents/
│   └── breakability-analyst.agent.md   # Copilot agent profile
├── scripts/
│   ├── build-check.sh                  # Deterministic build analysis (95KB)
│   ├── merge-results.sh                # Merge parallel batch results
│   └── post-fallback-comments.sh       # Post comments when agent is skipped
├── .github/
│   └── workflows/
│       ├── breakability-reusable.yml   # Reusable workflow (recommended)
│       └── copilot-setup-steps.yml     # Copilot agent environment setup
├── examples/
│   ├── caller-workflow.yml             # Template for consuming repos
│   ├── breakability-config.yml         # Config template (private registries)
│   └── copilot-instructions.md         # Template for .github/copilot-instructions.md
└── README.md
```

---

## 🤝 Usage Modes

### Mode 1: Reusable Workflow (Recommended)
```yaml
jobs:
  breakability:
    uses: CSC-Security-sandbox/breakability/.github/workflows/breakability-reusable.yml@main
```
Full 3-job pipeline with parallel batches. Best for monorepos with many PRs.

### Mode 2: Composite Action (Simple)
```yaml
steps:
  - uses: CSC-Security-sandbox/breakability@main
    with:
      github-token: ${{ secrets.GITHUB_TOKEN }}
```
Single-job execution. Good for smaller repos with <10 PRs.

### Mode 3: Deterministic Only
```yaml
jobs:
  breakability:
    uses: CSC-Security-sandbox/breakability/.github/workflows/breakability-reusable.yml@main
    with:
      skip_agent: true
```
No Copilot agent — posts basic fallback comments with build verdicts only.

---

## 💡 FAQ

### Deployment & Setup

**Q: We have 150 open Dependabot PRs that have been sitting for 6 months. Does the tool handle that?**
A: Yes — every run is a **fresh snapshot**. We list all currently-open Dependabot PRs, build main (baseline), build each PR branch against current main, and compare. It doesn't matter if the PR was opened yesterday or 6 months ago. Stale PRs get a fresh analysis every run.

**Q: How do we deploy this to a production repo?**
A: Copy the caller workflow to `.github/workflows/breakability.yml`, copy the prompt + agent profile, run `gh workflow run breakability.yml`. Total setup: ~5 minutes. No infrastructure, no SaaS, no database. It runs in the repo's own GitHub Actions.

**Q: How much does this cost?**
A: GitHub Actions minutes only: 4 parallel runners × ~15 min = ~60 runner-minutes per full scan. The Copilot coding agent uses premium requests from your existing Copilot Business/Enterprise plan — **no separate API key or subscription needed**. No SaaS fees, no infrastructure costs.

**Q: Do I need a Copilot license?**
A: For the deterministic pipeline (build verdicts + fallback comments) — no. For the AI agent layer — yes, you need Copilot Business or Enterprise with the coding agent enabled. Use `skip_agent: true` for deterministic-only mode.

**Q: Do we need a Cursor API key?**
A: No. We migrated to **GitHub Copilot coding agent**, which authenticates natively through GitHub. The only secret needed is `GITHUB_TOKEN` (auto-provided by Actions). If you have private npm/Go registries, add those auth tokens as secrets.

---

### PR Lifecycle Scenarios

**Q: A developer already merged the package upgrade manually. What happens to the Dependabot PR?**
A: Dependabot auto-closes its PR when the package version on main matches or exceeds the PR's target. Our next run won't see it (we filter `--state open`). No stale comments, no confusion.

**Q: A developer upgraded the package inside a feature PR (not the Dependabot PR). Does the tool know?**
A: Once the feature PR merges, Dependabot auto-closes its PR if the version matches. If the developer upgraded to v1.60 but Dependabot wanted v1.62, Dependabot opens a *new* PR for the remaining gap. Either way, we only analyze open PRs — no stale data.

**Q: What if a PR has merge conflicts because main moved on?**
A: The workflow checks `mergeable_status` via GitHub API before building. If `CONFLICTING`, we skip the build entirely and post: `## ⚠️ CONFLICTED — rebase required before analysis`. No wasted CI minutes.

**Q: A new Dependabot PR opens at 3 PM. The scheduled run was at 6 AM. Does the developer wait until tomorrow?**
A: No — the caller workflow template has a `pull_request: opened` trigger. When Dependabot opens a PR, a single-PR analysis runs immediately (~5-10 min). The developer gets a comment within minutes.

**Q: What if two runs happen at the same time (scheduled + manual trigger)?**
A: The reusable workflow has `concurrency: { group: breakability, cancel-in-progress: false }`. Runs queue — no duplicate comments, no race conditions.

**Q: What happens if the workflow crashes mid-run? Do some PRs get comments and others don't?**
A: The `post-fallback-comments.sh` step runs with `if: always()` — even if the agent step fails/times out. It reads the JSON and posts minimal structured comments on any PR that didn't get a comment. **100% coverage is guaranteed.** The next scheduled run will re-analyze everything fresh.

---

### Merge Plan Freshness

**Q: How does the merge plan stay fresh? If I merge 5 PRs today, is tomorrow's plan still correct?**
A: Every run **creates a new merge plan Issue** and closes the old one. The plan is always a snapshot of "what's open right now." After you merge 5 PRs, the next run sees only the remaining open PRs and creates a new plan without the merged ones.

**Q: What if I'm mid-way through merging the plan and a new run happens?**
A: The new run will see the PRs you already merged as closed (not in `--state open`). The new plan will only contain the remaining PRs. Your workflow isn't disrupted — just reference the new Issue.

---

### Multi-Ecosystem & Monorepo

**Q: Our repo has Go, npm, Python, and Docker. Does it handle all of them?**
A: Yes — `build-check.sh` auto-detects ecosystems from the PR's Dependabot metadata (`dependabot/npm_and_yarn/*`, `dependabot/go_modules/*`, `dependabot/pip/*`, `dependabot/docker/*`). Each gets ecosystem-specific build verification.

**Q: We have a monorepo with shared libraries. If a dep upgrades in a shared lib, do consumers get flagged?**
A: Yes — `cascade_impact` detection maps which services consume which shared libraries (via `workspace_graph`). If `@nestjs/core` upgrades in `lib/common`, the PR comment lists all affected services and the merge plan recommends merge order.

**Q: We use private Go modules / private npm packages.**
A: Supported. Configure `.github/breakability-config.yml` with your registry scope + auth token env var. For Go: set `GOPRIVATE` and add a netrc token. See `examples/breakability-config.yml`.

**Q: Does it work with Go workspaces (`go.work`)?**
A: Yes — if a `go.work` file exists, builds run at the workspace level first. Per-module fallback is also supported.

---

### AI Agent Behavior

**Q: What if the AI agent gives wrong advice?**
A: The AI **cannot override build results**. If `tsc` fails, the verdict is BUILD_FAILS regardless of what the AI thinks. The AI only adds context — changelog analysis, migration notes, behavioral risk. And we have a deterministic fallback that posts structured comments even if the AI crashes entirely.

**Q: What if the AI agent hallucinates error messages?**
A: The prompt explicitly says: "Do NOT invent error messages. Only quote errors that appear in `build.errors`." The AI reads structured JSON, not raw logs. It copies `verification_label` verbatim. Anti-hallucination constraints are tested and verified.

---

### Scale & Edge Cases

**Q: What if we have 300+ Dependabot PRs?**
A: Increase `batch_count` to 6 (workflow input). 300 PRs ÷ 6 batches = 50 PRs per runner × ~15 min each = ~15 min total (parallel). The bottleneck is GitHub Actions runner availability, not the tool.

**Q: Does this work with Renovate?**
A: Not yet — we filter by the `dependencies` label (which Dependabot adds). Renovate uses different labels and PR formats. Supporting Renovate would require a title parser change.

**Q: Can this work for repos without tests?**
A: Yes — the verification levels don't require tests. L0 = install failed, L1 = install passed, L2 = type-check passed. You still get value from knowing "this upgrade installs cleanly and type-checks." Tests just push you to higher verification levels.

**Q: What about Dependabot grouped PRs (multiple packages in one PR)?**
A: Handled — the `additional_packages` field captures all packages in a grouped PR. The comment lists ALL packages in the headline. The build covers the entire directory, so all packages are verified together.

**Q: What if main doesn't build? (e.g., someone pushed a broken commit)**
A: The baseline build captures main's state. If main's `go build` or `tsc` fails, every PR gets a `pre_existing` verdict — "these errors exist on main and are NOT caused by the upgrade." The merge plan groups these under "Pre-existing — fix main first."

**Q: What if someone force-pushes to a Dependabot branch?**
A: The next run checks out the latest state of each PR branch. Force-pushes are transparent — we always build the current HEAD.

**Q: Can it break my repo?**
A: No. It only reads code and posts comments. It never merges, approves, modifies code, or pushes to main.

---

### Scope & Boundaries

**Q: What are the blind spots? What CAN'T this tool catch?**
A: We're a **breakability analysis tool**, not a QA suite. We answer: "does it build, type-check, and pass existing tests?" — which is exactly what a developer would manually check before merging. We DON'T catch runtime behavioral changes (a library changing a default timeout) or cross-service contract breaks — those require integration tests. The one real gap within our scope is combined merge testing — 5 individually-safe PRs might conflict through transitive deps when merged together. The merge plan's ordering and peer grouping partially mitigate this.

**Q: Why is the trigger `workflow_dispatch` and not `issue_comment` (e.g., `/check-breakability`)?**
A: Security. `issue_comment` triggers run in the context of the default branch but can be triggered by **any** commenter, including external contributors on their own malicious PRs. Since the workflow checks out PR branches and runs build commands (`npm ci`, `go build`), an attacker could embed malicious `postinstall` scripts. This is a classic **PWN Request** vector (GHSA class vulnerability). `workflow_dispatch` requires **write access** to the repo — safe, auditable, and the GitHub-recommended pattern. Use `pr_filter` input to target specific PRs.

---

## License

Internal use — NetApp Security Engineering.
