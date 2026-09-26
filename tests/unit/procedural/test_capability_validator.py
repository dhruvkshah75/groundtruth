"""Tests for the fixed Tier 3 capability registry."""

import pytest

from src.contracts import CapabilityDescriptor
from src.procedural import (
    CapabilityConfigurationError,
    CapabilityKindMismatchError,
    CapabilityValidator,
    UnknownCapabilityError,
)


def lidar_descriptor() -> CapabilityDescriptor:
    """Return a normal LiDAR sensor descriptor for a test."""
    return CapabilityDescriptor(
        name="lidar_scan",
        kind="sensor",
        description="Measures obstacles in front of the robot.",
        parameters=["target"],
    )


def test_known_sensor_capability_is_returned() -> None:
    validator = CapabilityValidator([lidar_descriptor()])

    descriptor = validator.require_capability("lidar_scan", "sensor")

    assert descriptor.name == "lidar_scan"
    assert descriptor.kind == "sensor"


def test_unknown_capability_is_rejected() -> None:
    validator = CapabilityValidator([lidar_descriptor()])

    with pytest.raises(UnknownCapabilityError, match="read_weather"):
        validator.require_capability("read_weather", "sensor")


def test_wrong_capability_kind_is_rejected() -> None:
    validator = CapabilityValidator([lidar_descriptor()])

    with pytest.raises(CapabilityKindMismatchError, match="expected action"):
        validator.require_capability("lidar_scan", "action")


def test_duplicate_capability_names_are_invalid_configuration() -> None:
    with pytest.raises(CapabilityConfigurationError, match="duplicate capability name"):
        CapabilityValidator([lidar_descriptor(), lidar_descriptor()])


def test_validator_does_not_mutate_or_share_its_input_registry() -> None:
    capabilities = [lidar_descriptor()]
    validator = CapabilityValidator(capabilities)

    capabilities[0].parameters.append("direction")
    returned = validator.require_capability("lidar_scan", "sensor")
    returned.parameters.append("range_cm")

    assert capabilities[0].parameters == ["target", "direction"]
    assert validator.require_capability("lidar_scan", "sensor").parameters == ["target"]


def test_has_capability_requires_an_exact_name_and_kind() -> None:
    validator = CapabilityValidator([lidar_descriptor()])

    assert validator.has_capability("lidar_scan", "sensor") is True
    assert validator.has_capability("LIDAR_SCAN", "sensor") is False
    assert validator.has_capability("lidar_scan", "action") is False
