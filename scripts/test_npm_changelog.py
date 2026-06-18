#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from npm_changelog import (  # noqa: E402
    extract_changelog_sections,
    normalize_repository_url,
    _signal_text,
    tag_in_range,
    version_tuple,
)


def test_normalize_git_https_suffix():
    repo = normalize_repository_url("git+https://github.com/axios/axios.git")
    assert repo is not None
    assert repo.gh_path == "axios/axios"
    assert repo.directory == ""


def test_normalize_scoped_monorepo_directory():
    repo = normalize_repository_url(
        "git+https://github.com/nestjs/nest.git",
        "packages/common",
    )
    assert repo is not None
    assert repo.gh_path == "nestjs/nest"
    assert repo.directory == "packages/common"


def test_normalize_ssh_and_github_short_forms():
    assert normalize_repository_url("git@github.com:uuidjs/uuid.git").gh_path == "uuidjs/uuid"
    assert normalize_repository_url("github:remix-run/react-router").gh_path == "remix-run/react-router"


def test_tag_in_range_supports_plain_v_and_scoped_monorepo_tags():
    assert tag_in_range("v1.17.0", "1.13.5", "1.17.0", "axios")
    assert tag_in_range("@nestjs/common@11.0.1", "10.4.22", "11.1.26", "@nestjs/common")
    assert tag_in_range("common@11.1.26", "10.4.22", "11.1.26", "@nestjs/common")
    assert not tag_in_range("@nestjs/core@10.4.23", "10.4.22", "11.1.26", "@nestjs/common")
    assert not tag_in_range("v1.13.5", "1.13.5", "1.17.0", "axios")


def test_major_only_versions_compare_as_semver_floor():
    assert version_tuple("10") == (10, 0, 0)
    assert tag_in_range("v14.0.0", "10", "14", "uuid")
    assert not tag_in_range("v14.0.1", "10", "14", "uuid")


def test_extract_changelog_sections_in_range_only():
    changelog = """# Changelog

## 1.18.0
future

## 1.17.0
minor release

## 1.14.0
bug fixes

## 1.13.5
old
"""
    section = extract_changelog_sections(changelog, "1.13.5", "1.17.0")
    assert "minor release" in section
    assert "bug fixes" in section
    assert "future" not in section
    assert "old" not in section


def test_signal_text_fails_safe_for_major_without_explicit_clean_or_break():
    assert _signal_text("## 2.0.0\nBug fixes and documentation.", "pkg", "1.9.0", "2.0.0") == ""


def test_signal_text_keeps_declared_major_break_and_sanitizes_missing():
    text = _signal_text(
        "## 11.0.0\nNode v16 and v18 are no longer supported.\nAdded missing docs.",
        "@nestjs/common",
        "10.4.22",
        "11.1.26",
    )
    assert "no longer supported" in text
    assert "missing" not in text.lower()


def test_signal_text_minor_negated_breaking_is_clean():
    text = _signal_text(
        "## 1.17.0\nBreaking Changes: None identified in this release.\nBug fixes.",
        "axios",
        "1.13.5",
        "1.17.0",
    )
    assert "no API change declared" in text
    assert "Breaking Changes" not in text
