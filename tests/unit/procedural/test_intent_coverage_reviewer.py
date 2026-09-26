"""Tests for bounded unsupported-intent coverage review."""

from src.contracts import CapabilityDescriptor
from src.procedural import CapabilityValidator, IntentCoverageReviewer


def descriptor(name: str, kind: str = "sensor") -> CapabilityDescriptor:
    """Create one advertised capability for a test.

    Args:
        name: The capability name.
        kind: The advertised capability kind.

    Returns:
        A capability descriptor.
    """
    return CapabilityDescriptor(name=name, kind=kind, description=f"Test {name}.")


def test_route_wording_with_lidar_has_one_candidate() -> None:
    reviewer = IntentCoverageReviewer(CapabilityValidator([descriptor("lidar_scan")]))

    candidates = reviewer.candidate_intents("  Is anything BLOCKING me?  ")

    assert candidates == ("current_route_status",)


def test_unknown_wording_has_no_candidate() -> None:
    reviewer = IntentCoverageReviewer(CapabilityValidator([descriptor("lidar_scan")]))

    assert reviewer.candidate_intents("What is the room temperature?") == ()


def test_route_wording_without_sensor_has_no_candidate() -> None:
    reviewer = IntentCoverageReviewer(CapabilityValidator([]))

    assert reviewer.candidate_intents("Is anything blocking me?") == ()


def test_route_wording_with_wrong_capability_kind_has_no_candidate() -> None:
    reviewer = IntentCoverageReviewer(
        CapabilityValidator([descriptor("lidar_scan", kind="action")])
    )

    assert reviewer.candidate_intents("Is anything blocking me?") == ()


def test_wording_that_matches_two_supported_families_returns_two_candidates() -> None:
    reviewer = IntentCoverageReviewer(
        CapabilityValidator([descriptor("lidar_scan"), descriptor("camera_detect")])
    )

    candidates = reviewer.candidate_intents("Is an obstacle ahead and what color is it?")

    assert candidates == ("current_route_status", "current_object_perception")
