"""Tests to ensure Tier 3 maintains architectural isolation."""

import sys


def test_tier_3_does_not_import_tier_1_or_2():
    """Ensure sensorimotor does not import higher tiers or database logic."""
    import importlib

    importlib.import_module("sensorimotor.mock_environment")

    for module_name in sys.modules:
        assert "tier1" not in module_name, f"Forbidden import: {module_name}"
        assert "tier2" not in module_name, f"Forbidden import: {module_name}"
