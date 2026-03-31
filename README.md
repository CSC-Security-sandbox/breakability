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

**Q: Do I need a Copilot license?**
A: For the deterministic pipeline (build verdicts + fallback comments) — no. For the AI agent layer — yes, you need Copilot Business or Enterprise with the coding agent enabled.

**Q: Does it work with private repos?**
A: Yes. The default `GITHUB_TOKEN` has access to the repo it runs in. For private registries (npm, Go, Python), add a `.github/breakability-config.yml`.

**Q: How long does it take?**
A: ~2-5 minutes per PR for the deterministic pipeline. With 4 parallel batches, a 30-PR repo takes ~15 minutes. The Copilot agent adds ~10-15 minutes for comment generation.

**Q: Can it break my repo?**
A: No. It only reads code and posts comments. It never merges, approves, modifies code, or pushes to main.

---

## License

Internal use — NetApp Security Engineering.
