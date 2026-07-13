#!/usr/bin/env python3
"""Truncate a merge-plan body to fit GitHub's 65536-char issue limit.

Reads the full plan from the MP_FULL environment variable and prints a
head-truncated copy that fits within GitHub's limit, preserving the most
actionable sections (summary, security fixes, per-PR review) at the top.

Usage:
    MP_FULL="$MERGE_PLAN_BODY" python3 rendering/truncate_plan.py
"""

import os


def main():
    body = os.environ.get("MP_FULL", "")
    LIMIT = 65536
    NOTICE = ("\n\n---\n> ⚠️ **Plan truncated** — the full plan exceeded GitHub's "
              "65,536-character issue limit. The most actionable sections (summary, "
              "security fixes, per-PR review) are shown above; the complete plan is "
              "available as the `merge-plan.md` CI artifact / dry-run output.\n")
    budget = LIMIT - len(NOTICE) - 16
    if len(body) <= budget:
        print(body, end="")
    else:
        head = body[:budget]
        nl = head.rfind("\n")
        if nl > 0:
            head = head[:nl]
        print(head + NOTICE, end="")


if __name__ == "__main__":
    main()
