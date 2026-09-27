"""Validation tests for the first shared contract."""

import pytest
from pydantic import ValidationError

from src.contracts.models import SpatialContext


def test_spatial_context_accepts_full_scope() -> None:
    context = SpatialContext(
        location="room_101",
        frame_of_reference="robot_base",
        world_version=2,
        observer="robot_01",
        extra_context={"lighting": "yellow"},
    )

    assert context.location == "room_101"
    assert context.frame_of_reference == "robot_base"
    assert context.world_version == 2
    assert context.observer == "robot_01"
    assert context.extra_context == {"lighting": "yellow"}


def test_spatial_context_defaults_to_an_empty_scope() -> None:
    context = SpatialContext()

    assert context.location is None
    assert context.frame_of_reference is None
    assert context.world_version is None
    assert context.observer is None
    assert context.extra_context == {}


def test_spatial_context_rejects_negative_world_version() -> None:
    with pytest.raises(ValidationError):
        SpatialContext(world_version=-1)


def test_spatial_context_does_not_share_extra_context_between_instances() -> None:
    first = SpatialContext()
    second = SpatialContext()

    first.extra_context["lighting"] = "yellow"

    assert second.extra_context == {}


def test_spatial_context_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SpatialContext(unknown_context_field="not allowed")
