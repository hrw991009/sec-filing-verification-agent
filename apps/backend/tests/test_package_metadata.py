"""Tests for package metadata consistency."""

from importlib.metadata import version

import industry_platform


def test_runtime_version_matches_distribution_metadata() -> None:
    assert industry_platform.__version__ == version("industry-platform-backend")
