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


def test_punctuation_does_not_hide_a_route_word() -> None:
    reviewer = IntentCoverageReviewer(CapabilityValidator([descriptor("lidar_scan")]))

    assert reviewer.candidate_intents("Is it blocked?") == ("current_route_status",)


def test_pose_phrase_does_not_match_a_longer_word() -> None:
    reviewer = IntentCoverageReviewer(CapabilityValidator([descriptor("robot_pose")]))

    assert reviewer.candidate_intents("Where are your logs?") == ()


def test_audit_word_does_not_match_a_longer_word() -> None:
    reviewer = IntentCoverageReviewer(CapabilityValidator([]), audit_available=True)

    assert reviewer.candidate_intents("Visit the auditorium.") == ()


def test_object_pose_history_and_audit_rules_have_stable_ids() -> None:
    reviewer = IntentCoverageReviewer(
        CapabilityValidator([descriptor("camera_detect"), descriptor("robot_pose")]),
        history_available=True,
        audit_available=True,
    )

    review = reviewer.review("Where are you? What color appears before an audit?")

    assert review.candidate_intents == (
        "current_object_perception",
        "current_robot_pose",
        "historical_fact_lookup",
        "audit_explanation",
    )
    assert review.matched_rule_ids == (
        "object_perception_v1",
        "robot_pose_v1",
        "historical_lookup_v1",
        "audit_explanation_v1",
    )


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
