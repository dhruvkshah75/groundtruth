"""Global pytest fixtures for GroundTruth.

Ensures that the test suite runs completely offline with the deterministic
rule-based intent provider by default, matching the requirement that offline
mode is explicitly selected and that automated tests never require internet
access or live LLM credentials.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _set_test_offline_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default all tests to explicit offline mode unless overridden."""
    monkeypatch.setenv("GROUNDTRUTH_PROVIDER_MODE", "offline")
