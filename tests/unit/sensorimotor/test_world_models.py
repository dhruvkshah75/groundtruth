"""Unit tests for world models."""

import pytest
from pydantic import ValidationError

from sensorimotor.world_models import (
    RobotState,
    WorldState,
)


def test_robot_state_initialization():
    robot = RobotState(
        robot_id="robot_01",
        x_cm=10.0,
        y_cm=20.0,
        direction="north",
        location="room_101",
        frame_of_reference="robot_base",
    )
    assert robot.robot_id == "robot_01"
    assert robot.direction == "north"


def test_robot_state_invalid_direction():
    with pytest.raises(ValidationError):
        RobotState(
            robot_id="robot_01",
            x_cm=10.0,
            y_cm=20.0,
            direction="up",  # type: ignore
            location="room_101",
            frame_of_reference="robot_base",
        )


def test_world_state_increment_version():
    robot = RobotState(
        robot_id="robot_01",
        x_cm=0,
        y_cm=0,
        direction="north",
        location="room_1",
        frame_of_reference="base",
    )
    world = WorldState(robot=robot)
    assert world.world_version == 0
    world.increment_version()
    assert world.world_version == 1
