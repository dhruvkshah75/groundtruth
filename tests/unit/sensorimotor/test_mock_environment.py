"""Unit tests for MockEnvironment."""

import uuid
from datetime import UTC, datetime

import pytest

from contracts.models import ObservationRequest, ObservationUnavailable
from sensorimotor.mock_environment import MockEnvironment
from sensorimotor.world_models import (
    AmbientLight,
    Obstacle,
    RobotState,
    SimulatedObject,
    WorldState,
)


@pytest.fixture
def fixed_clock():
    def _clock():
        return datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

    return _clock


@pytest.fixture
def deterministic_uuid_factory():
    def _factory():
        return uuid.UUID("12345678-1234-5678-1234-567812345678")

    return _factory


def build_scenario_a_world() -> WorldState:
    """
    Scenario A:
    - robot_01 in room_101;
    - robot facing toward front (north);
    - obstacle_01 exactly 12 cm ahead;
    """
    robot = RobotState(
        robot_id="robot_01",
        x_cm=0.0,
        y_cm=0.0,
        direction="north",
        location="room_101",
        frame_of_reference="robot_base",
    )
    obstacle = Obstacle(
        obstacle_id="obstacle_01",
        x_cm=0.0,
        y_cm=12.0,
        location="room_101",
        active=True,
    )
    return WorldState(robot=robot, obstacles={"obstacle_01": obstacle})


def build_scenario_b_world() -> WorldState:
    """
    Scenario B:
    - box_01 visible in room_101;
    - yellow ambient colour cast;
    """
    robot = RobotState(
        robot_id="robot_01",
        x_cm=0.0,
        y_cm=0.0,
        direction="north",
        location="room_101",
        frame_of_reference="robot_base",
    )
    box = SimulatedObject(
        object_id="box_01",
        x_cm=10.0,
        y_cm=10.0,
        location="room_101",
        intrinsic_color="red",
        visible=True,
    )
    light = AmbientLight(intensity=1.0, color_cast="yellow")
    return WorldState(robot=robot, objects={"box_01": box}, light=light)


def test_registry_contains_four_capabilities():
    world = build_scenario_a_world()
    env = MockEnvironment(world=world)
    caps = env.get_capabilities()

    assert len(caps) == 4
    names = {cap.name for cap in caps}
    assert names == {"lidar_scan", "camera_detect", "ambient_light", "robot_pose"}


def test_lidar_scenario_a(fixed_clock, deterministic_uuid_factory):
    world = build_scenario_a_world()
    env = MockEnvironment(world=world, clock=fixed_clock, id_factory=deterministic_uuid_factory)

    req = ObservationRequest(capability="lidar_scan", parameters={"direction": "front"})
    obs = env.observe(req)

    assert obs.capability == "lidar_scan"
    assert obs.measurements["detected"] is True
    assert obs.measurements["nearest_distance_cm"] == 12.0
    assert obs.measurements["obstacle_id"] == "obstacle_01"
    assert obs.measurements["status"] == "blocked"
    assert obs.context.world_version == 0
    assert obs.context.location == "room_101"


def test_lidar_multiple_obstacles(fixed_clock, deterministic_uuid_factory):
    world = build_scenario_a_world()
    # Add an obstacle further away
    world.obstacles["obstacle_02"] = Obstacle(
        obstacle_id="obstacle_02", x_cm=0.0, y_cm=20.0, location="room_101"
    )
    # Add an obstacle behind the robot
    world.obstacles["obstacle_03"] = Obstacle(
        obstacle_id="obstacle_03", x_cm=0.0, y_cm=-5.0, location="room_101"
    )
    # Add an obstacle in a different room
    world.obstacles["obstacle_04"] = Obstacle(
        obstacle_id="obstacle_04", x_cm=0.0, y_cm=5.0, location="room_102"
    )

    env = MockEnvironment(world=world, clock=fixed_clock, id_factory=deterministic_uuid_factory)
    req = ObservationRequest(capability="lidar_scan", parameters={"direction": "front"})
    obs = env.observe(req)

    # Should still detect obstacle_01 at 12cm
    assert obs.measurements["obstacle_id"] == "obstacle_01"
    assert obs.measurements["nearest_distance_cm"] == 12.0


def test_lidar_mutation_changes_reading(fixed_clock, deterministic_uuid_factory):
    world = build_scenario_a_world()
    env = MockEnvironment(world=world, clock=fixed_clock, id_factory=deterministic_uuid_factory)

    # Disable the obstacle
    world.obstacles["obstacle_01"].active = False
    world.increment_version()

    req = ObservationRequest(capability="lidar_scan", parameters={"direction": "front"})
    obs = env.observe(req)

    assert obs.measurements["detected"] is False
    assert obs.measurements["nearest_distance_cm"] is None
    assert obs.context.world_version == 1


def test_camera_scenario_b(fixed_clock, deterministic_uuid_factory):
    world = build_scenario_b_world()
    env = MockEnvironment(world=world, clock=fixed_clock, id_factory=deterministic_uuid_factory)

    req = ObservationRequest(capability="camera_detect", target="box_01")
    obs = env.observe(req)

    assert obs.capability == "camera_detect"
    assert obs.measurements["object_id"] == "box_01"
    assert obs.measurements["apparent_color"] == "brown"
    assert obs.context.extra_context["lighting"] == "yellow"


def test_camera_neutral_light(fixed_clock, deterministic_uuid_factory):
    world = build_scenario_b_world()
    world.light.color_cast = "neutral"

    env = MockEnvironment(world=world, clock=fixed_clock, id_factory=deterministic_uuid_factory)

    req = ObservationRequest(capability="camera_detect", target="box_01")
    obs = env.observe(req)

    assert obs.measurements["apparent_color"] == "red"


def test_camera_target_not_visible(fixed_clock, deterministic_uuid_factory):
    world = build_scenario_b_world()
    world.objects["box_01"].visible = False

    env = MockEnvironment(world=world, clock=fixed_clock, id_factory=deterministic_uuid_factory)
    req = ObservationRequest(capability="camera_detect", target="box_01")
    obs = env.observe(req)

    assert isinstance(obs, ObservationUnavailable)
    assert obs.reason == "target_not_visible"


def test_unsupported_capability(fixed_clock, deterministic_uuid_factory):
    world = build_scenario_a_world()
    env = MockEnvironment(world=world, clock=fixed_clock, id_factory=deterministic_uuid_factory)

    req = ObservationRequest(capability="fake_scan", parameters={})
    obs = env.observe(req)

    assert isinstance(obs, ObservationUnavailable)
    assert obs.reason == "unsupported_capability"


def test_robot_pose(fixed_clock, deterministic_uuid_factory):
    world = build_scenario_a_world()
    env = MockEnvironment(world=world, clock=fixed_clock, id_factory=deterministic_uuid_factory)

    req = ObservationRequest(capability="robot_pose", parameters={})
    obs = env.observe(req)

    assert obs.measurements["robot_id"] == "robot_01"
    assert obs.measurements["x_cm"] == 0.0
    assert obs.measurements["y_cm"] == 0.0
    assert obs.measurements["direction"] == "north"
