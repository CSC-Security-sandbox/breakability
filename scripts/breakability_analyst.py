#!/usr/bin/env python3
"""
breakability_analyst.py - Compact PR comment renderer for breakability analysis.

Reads build-results.json and produces ~40-line comments per PR with collapsible
evidence details. Called by breakability-agent.yml workflow (line 346).

This file is a backward-compatible shim.  The implementation has been split into
sub-modules under ``rendering/``:

    rendering/normalizers.py  — signal normalizers
    rendering/helpers.py      — risk, recommendation, confidence helpers
    rendering/renderer.py     — Markdown comment renderer
    rendering/cli.py          — CLI entry point
"""

# Re-export every name that the monolith previously exposed so that
# ``from breakability_analyst import *`` and direct attribute access
# continue to work unchanged.

from rendering.normalizers import (      # noqa: F401
    _normalize_verdict,
    _normalize_changelog,
    _normalize_test,
    _normalize_probe,
    _normalize_reachability,
)
from rendering.helpers import (          # noqa: F401
    _merge_risk_tag,
    _get_recommendation,
    _count_evidence_layers,
    _per_layer_confidence,
    _build_per_layer_narrative,
    _build_expanded_layer_sections,
    _build_risk_assessment,
    _build_numbered_recommendations,
)
from rendering.renderer import (         # noqa: F401
    _synthesize_explanation,
    _render_compact,
    render_pr_comment,
)
from rendering.cli import main           # noqa: F401

if __name__ == "__main__":
    main()
