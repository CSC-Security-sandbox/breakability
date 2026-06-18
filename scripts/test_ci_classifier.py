#!/usr/bin/env python3
"""Tests for the canonical CI-dependency sensitivity classifier."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ci_classifier import ci_review_tier, ci_security_sensitive


class CiClassifierTests(unittest.TestCase):
    def test_secsens_token_action(self):
        self.assertTrue(ci_security_sensitive("actions/create-github-app-token"))
        self.assertEqual(ci_review_tier("actions/create-github-app-token", "major"), "secsens")

    def test_secsens_cloud_auth_actions(self):
        for pkg in ("aws-actions/configure-aws-credentials",
                    "azure/login",
                    "google-github-actions/auth",
                    "docker/login-action"):
            self.assertTrue(ci_security_sensitive(pkg), pkg)
            self.assertEqual(ci_review_tier(pkg, "minor"), "secsens", pkg)

    def test_secsens_deploy_publish_signing(self):
        for pkg in ("actions/deploy-pages", "some/release-action",
                    "sigstore/cosign-installer", "crazy-max/ghaction-import-gpg"):
            self.assertTrue(ci_security_sensitive(pkg), pkg)

    def test_benign_setup_actions_are_not_sensitive(self):
        for pkg in ("actions/setup-python", "actions/setup-node",
                    "actions/checkout", "actions/cache",
                    "azure/setup-kubectl"):
            self.assertFalse(ci_security_sensitive(pkg), pkg)
            # Majorness alone must NOT trigger review for a benign CI dep.
            self.assertEqual(ci_review_tier(pkg, "major"), "", pkg)
            self.assertEqual(ci_review_tier(pkg, "minor"), "", pkg)

    def test_empty_package_is_benign(self):
        self.assertFalse(ci_security_sensitive(""))
        self.assertEqual(ci_review_tier("", "major"), "")


if __name__ == "__main__":
    unittest.main()
