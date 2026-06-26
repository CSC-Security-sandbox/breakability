# Dependabot Triage Agent (PoC)

A proof-of-concept adaptation of Endor Labs' agent-kit pattern that works with
GitHub Dependabot alerts instead of `endorctl`. Runs as a Claude Code agent.

## What it does

1. Pulls Dependabot alerts from GitHub API (`gh` CLI)
2. Triages each alert: CRITICAL_ACTION_REQUIRED / ACTION_RECOMMENDED / MONITOR / FALSE_POSITIVE
3. Checks reachability by analyzing import paths in code
4. Produces structured triage output with evidence
5. Can generate remediation PRs for confirmed findings

## Usage

Copy `.claude/agents/dependabot-triage.md` into your repo and invoke:

```
@dependabot-triage triage this repo
@dependabot-triage show critical alerts
@dependabot-triage remediate lodash vulnerability
```

## Requirements

- `gh` CLI authenticated
- Repository with Dependabot alerts enabled
- Claude Code (or adaptable to Cursor/Codex)
