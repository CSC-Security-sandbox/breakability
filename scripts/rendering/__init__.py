"""
rendering — Sub-modules for breakability PR comment rendering.

Re-exports the public API so callers can use either:
    from breakability_analyst import render_pr_comment
    from rendering import render_pr_comment
"""
from rendering.normalizers import (
    _normalize_verdict,
    _normalize_changelog,
    _normalize_test,
    _normalize_probe,
    _normalize_reachability,
)
from rendering.helpers import (
    _merge_risk_tag,
    _get_recommendation,
    _count_evidence_layers,
    _per_layer_confidence,
    _build_per_layer_narrative,
    _build_expanded_layer_sections,
    _build_risk_assessment,
    _build_numbered_recommendations,
)
from rendering.renderer import (
    _synthesize_explanation,
    _render_compact,
    render_pr_comment,
)
from rendering.cli import main

__all__ = [
    "render_pr_comment",
    "main",
]
