#!/usr/bin/env python3
"""Canonical CI-dependency (GitHub Actions / Docker) sensitivity classifier.

Single source of truth for deciding whether a CI dependency upgrade is
security-sensitive (token / registry / cloud-cred / code-signing / deploy) and
therefore needs human review, versus benign (e.g. ``actions/setup-python``)
which is a changelog glance.

This MUST stay in sync with the bash ``_CI_TIER`` / ``ci_review_tier`` classifier
in post-fallback-comments.sh. It is imported by the policy layer
(policy_lowering.py) so the verdict contract can flag secsens CI deps at decision
time — the renderer's ci_tier is computed too late for the gate to see it.
"""

from __future__ import annotations

import re

# Auth/token/registry/cloud-cred/signing/deploy CI deps -> security review.
# Keep the alternation identical to _ci_secsens_re in post-fallback-comments.sh.
_CI_SECSENS_RE = re.compile(
    r"token|credential|secret|password|login|oauth|oidc|/auth|-auth|ssh-agent|"
    r"import-gpg|gpg|cosign|sigstore|vault|kms|aws-actions|azure/login|"
    r"google-github-actions/auth|configure-aws-credentials|registry|ghcr|ecr|"
    r"gcr|deploy|release|publish|pages",
    re.IGNORECASE,
)


def ci_security_sensitive(package: str) -> bool:
    """True if the CI dependency handles credentials/registry/deploy/signing."""
    return bool(_CI_SECSENS_RE.search(str(package or "")))


def ci_review_tier(package: str, bump: str) -> str:
    """Classify a CI (actions/docker) dependency upgrade.

    Returns:
      ``"secsens"`` -> security-sensitive; always human review.
      ``""``         -> benign; changelog glance (auto-safe).

    Note: majorness alone is NOT a review trigger for benign CI deps — a major
    ``setup-*`` bump is still a glance per the breakability oracle. Only
    security-sensitivity forces review.
    """
    if ci_security_sensitive(package):
        return "secsens"
    return ""
