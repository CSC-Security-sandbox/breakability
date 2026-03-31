# Copilot Instructions — Breakability Analysis
#
# Copy this file to .github/copilot-instructions.md in your repo.
# It gives Copilot repo-wide context about the breakability workflow.

## About This Repo

This repository uses **Breakability Analysis** — an automated system that evaluates
every Dependabot PR for build-time compatibility before merging.

## Architecture

1. **Deterministic pipeline** (`build-check.sh`): Checks out each Dependabot PR,
   runs the actual build commands (npm ci, go build, pip install, mvn compile),
   and compares against a main-branch baseline. Outputs structured JSON verdicts.

2. **Copilot agent** (you): Reads the build results and posts human-readable
   comments on each PR with risk assessment, merge recommendations, and a
   consolidated merge plan Issue.

## Key Principles

- **NEVER** close, merge, approve, or modify any PR
- **NEVER** override deterministic build verdicts
- Copy `verification_label` from build-results.json verbatim
- **100% PR comment coverage** — every PR in the results gets a comment
- Follow the comment templates in `.github/breakability-prompt.md` exactly

## Conventions

- Build results are on the `breakability-results` orphan branch
- Analysis tasks arrive as Issues with label `breakability-analysis`
- PR comments use the `## 🔍 Breakability Analysis` header format
- Merge plan Issues use the `## 📋 Merge Plan` header format
