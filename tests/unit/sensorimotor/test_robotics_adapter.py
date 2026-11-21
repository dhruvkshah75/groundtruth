"""Unit tests for RecordedRoboticsSensorAdapter and telemetry models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from src.contracts.models import (
    ObservationRequest,
    ObservationUnavailable,
    SensorObservation,
)
from src.contracts.simulation import (
    CameraScan,
    LidarMeasurement,
    LightMeasurement,
    ObservationSample,
    Point,
    Pose,
    PoseMeasurement,
    SensorSource,
)
from src.sensorimotor.robotics_adapter import RecordedRoboticsSensorAdapter
from src.sensorimotor.robotics_adapter_models import (
    ClockCalibration,
    ExternalDetection,
    ExternalLidarMeasurement,
    ExternalLightMeasurement,
    ExternalRoboticsRecord,
    FrameTransform,
    TransformCalibration,
    UnknownFrameError,
    UnmappableTimeError,
    derive_deterministic_ids,
)

FIXTURE_PATH = Path("tests/fixtures/phase_two/robotics_recording.json")


@pytest.fixture
def sample_epoch() -> datetime:
    return datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock_cal(sample_epoch: datetime) -> ClockCalibration:
    return ClockCalibration(epoch_utc=sample_epoch)


@pytest.fixture
def default_robot_pose() -> Pose:
    return Pose(
        x_cm=100.0,
        y_cm=200.0,
        robot_id="robot_01",
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )


# =============================================================================
# Clock Calibration Tests
# =============================================================================


def test_clock_calibration_relative_ms(clock_cal: ClockCalibration, sample_epoch: datetime) -> None:
    sim_ms, captured = clock_cal.calculate_sim_time(None, rel_ms=1500)
    assert sim_ms == 1500
    assert captured == sample_epoch + timedelta(milliseconds=1500)


def test_clock_calibration_utc_timestamp(
    clock_cal: ClockCalibration, sample_epoch: datetime
) -> None:
    dt = sample_epoch + timedelta(seconds=12, milliseconds=340)
    sim_ms, captured = clock_cal.calculate_sim_time(dt, None)
    assert sim_ms == 12340
    assert captured == dt


def test_clock_calibration_rejections(clock_cal: ClockCalibration, sample_epoch: datetime) -> None:
    with pytest.raises(UnmappableTimeError, match="cannot be negative"):
        clock_cal.calculate_sim_time(None, rel_ms=-10)

    prior_dt = sample_epoch - timedelta(seconds=5)
    with pytest.raises(UnmappableTimeError, match="before epoch"):
        clock_cal.calculate_sim_time(prior_dt, None)

    naive_dt = datetime(2026, 1, 1, 0, 0, 5)
    with pytest.raises(UnmappableTimeError, match="timezone-aware"):
        clock_cal.calculate_sim_time(naive_dt, None)

    with pytest.raises(UnmappableTimeError, match="at least one"):
        clock_cal.calculate_sim_time(None, None)


# =============================================================================
# Coordinate Transform Calibration Tests
# =============================================================================


def test_transform_canonical_frame() -> None:
    cal = TransformCalibration()
    pt = cal.transform_point(Point(x_cm=45.0, y_cm=90.0), source_frame="warehouse_map")
    assert pt.x_cm == 45.0
    assert pt.y_cm == 90.0


def test_transform_robot_frame_all_directions() -> None:
    cal = TransformCalibration()
    # Object is 30 cm to the right (+x) and 50 cm forward (+y) in robot frame
    obj_in_robot = Point(x_cm=30.0, y_cm=50.0)

    # North: +y forward (+y in map), +x right (+x in map)
    pose_north = Pose(
        x_cm=100.0,
        y_cm=200.0,
        robot_id="r1",
        direction="north",
        location="f",
        frame_of_reference="warehouse_map",
    )
    pt_north = cal.transform_point(obj_in_robot, "robot_frame", robot_pose=pose_north)
    assert pt_north.x_cm == 130.0
    assert pt_north.y_cm == 250.0

    # East: +y forward (+x in map), +x right (-y in map)
    pose_east = Pose(
        x_cm=100.0,
        y_cm=200.0,
        robot_id="r1",
        direction="east",
        location="f",
        frame_of_reference="warehouse_map",
    )
    pt_east = cal.transform_point(obj_in_robot, "robot_frame", robot_pose=pose_east)
    assert pt_east.x_cm == 150.0
    assert pt_east.y_cm == 170.0

    # South: +y forward (-y in map), +x right (-x in map)
    pose_south = Pose(
        x_cm=100.0,
        y_cm=200.0,
        robot_id="r1",
        direction="south",
        location="f",
        frame_of_reference="warehouse_map",
    )
    pt_south = cal.transform_point(obj_in_robot, "robot_frame", robot_pose=pose_south)
    assert pt_south.x_cm == 70.0
    assert pt_south.y_cm == 150.0

    # West: +y forward (-x in map), +x right (+y in map)
    pose_west = Pose(
        x_cm=100.0,
        y_cm=200.0,
        robot_id="r1",
        direction="west",
        location="f",
        frame_of_reference="warehouse_map",
    )
    pt_west = cal.transform_point(obj_in_robot, "robot_frame", robot_pose=pose_west)
    assert pt_west.x_cm == 50.0
    assert pt_west.y_cm == 230.0


def test_transform_base_link_ros_rep103() -> None:
    cal = TransformCalibration()
    # In ROS REP 103 base_link: +x is forward, +y is left
    # Object 40cm forward (+x), 10cm left (+y)
    obj_ros = Point(x_cm=40.0, y_cm=10.0)
    pose_north = Pose(
        x_cm=100.0,
        y_cm=200.0,
        robot_id="r1",
        direction="north",
        location="f",
        frame_of_reference="warehouse_map",
    )
    pt = cal.transform_point(obj_ros, "base_link", robot_pose=pose_north)
    # Forward (+x) -> +y in map (+40) -> 240
    # Left (+y) -> -x in map (-10) -> 90
    assert pt.x_cm == 90.0
    assert pt.y_cm == 240.0


def test_transform_custom_calibrated_frame() -> None:
    cal = TransformCalibration(
        transforms={
            "overhead_camera": FrameTransform(
                source_frame="overhead_camera",
                target_frame="warehouse_map",
                offset_x_cm=500.0,
                offset_y_cm=300.0,
                rotation_deg=90.0,
            )
        }
    )
    # 90 deg CCW rotation of (10, 0): (0, 10). Offset: (500, 310)
    pt = cal.transform_point(Point(x_cm=10.0, y_cm=0.0), "overhead_camera")
    assert pt.x_cm == 500.0
    assert pt.y_cm == 310.0


def test_transform_unknown_frame_raises() -> None:
    cal = TransformCalibration()
    with pytest.raises(UnknownFrameError, match="Unknown or unconfigured"):
        cal.transform_point(Point(x_cm=1.0, y_cm=1.0), "unknown_arm_wrist_3")


# =============================================================================
# Deterministic Identity and Idempotence Tests
# =============================================================================


def test_deterministic_id_derivation() -> None:
    s1, o1 = derive_deterministic_ids("rec_01", "msg_001")
    s2, o2 = derive_deterministic_ids("rec_01", "msg_001")
    s3, o3 = derive_deterministic_ids("rec_01", "msg_002")

    assert s1 == s2
    assert o1 == o2
    assert isinstance(s1, UUID)
    assert isinstance(o1, UUID)
    assert s1 != s3
    assert o1 != o3


# =============================================================================
# Translation Tests Across All Capabilities
# =============================================================================


def test_translate_camera_detect_broad_and_targeted(
    clock_cal: ClockCalibration, default_robot_pose: Pose
) -> None:
    record = ExternalRoboticsRecord(
        schema_version=1,
        recording_id="rec_test",
        message_id="msg_cam_01",
        sequence=0,
        robot_id="robot_01",
        sensor_id="camera_rgb",
        capability="camera_detect",
        captured_time_rel_ms=1000,
        source_frame="robot_frame",
        robot_pose=default_robot_pose,
        detections=[
            ExternalDetection(
                object_id="parcel_02",
                apparent_color="blue",
                position_x_cm=10.0,
                position_y_cm=20.0,
            ),
            ExternalDetection(
                object_id="parcel_01",
                apparent_color="red",
                position_x_cm=-10.0,
                position_y_cm=30.0,
            ),
        ],
    )

    adapter = RecordedRoboticsSensorAdapter(records=[record], clock_calibration=clock_cal)

    # 1. Broad scan: all detections sorted by object_id
    broad_sample = adapter.translate_record(record)
    assert isinstance(broad_sample, ObservationSample)
    assert isinstance(broad_sample.result, SensorObservation)
    scan = CameraScan.model_validate(broad_sample.result.measurements)
    assert len(scan.objects) == 2
    assert [obj.object_id for obj in scan.objects] == ["parcel_01", "parcel_02"]

    # Check coordinate transform (pose is north at (100, 200))
    # parcel_01 was (-10, 30) -> map (90, 230)
    assert scan.objects[0].position.x_cm == 90.0
    assert scan.objects[0].position.y_cm == 230.0

    # 2. Targeted scan matching parcel_01
    req_match = ObservationRequest(capability="camera_detect", target="parcel_01")
    target_sample = adapter.translate_record(record, request=req_match)
    assert isinstance(target_sample.result, SensorObservation)
    targeted_scan = CameraScan.model_validate(target_sample.result.measurements)
    assert len(targeted_scan.objects) == 1
    assert targeted_scan.objects[0].object_id == "parcel_01"

    # 3. Targeted scan for unseen object
    req_unseen = ObservationRequest(capability="camera_detect", target="parcel_99")
    unseen_sample = adapter.translate_record(record, request=req_unseen)
    assert isinstance(unseen_sample.result, ObservationUnavailable)
    assert unseen_sample.result.reason == "target_not_visible"
    assert unseen_sample.result.message == "Observation unavailable."
    assert unseen_sample.result.reported_at == unseen_sample.captured_at


def test_translate_lidar_scan(clock_cal: ClockCalibration, default_robot_pose: Pose) -> None:
    blocked_rec = ExternalRoboticsRecord(
        schema_version=1,
        recording_id="rec_test",
        message_id="msg_lidar_01",
        sequence=1,
        robot_id="robot_01",
        sensor_id="lidar_front",
        capability="lidar_scan",
        captured_time_rel_ms=2000,
        robot_pose=default_robot_pose,
        lidar_measurement=ExternalLidarMeasurement(
            detected=True, nearest_distance_cm=88.5, direction="front", status="blocked"
        ),
    )
    adapter = RecordedRoboticsSensorAdapter(records=[blocked_rec], clock_calibration=clock_cal)
    sample = adapter.translate_record(blocked_rec)
    assert isinstance(sample.result, SensorObservation)
    lidar = LidarMeasurement.model_validate(sample.result.measurements)
    assert lidar.detected is True
    assert lidar.nearest_distance_cm == 88.5
    assert lidar.status == "blocked"


def test_translate_robot_pose_and_light(
    clock_cal: ClockCalibration, default_robot_pose: Pose
) -> None:
    pose_rec = ExternalRoboticsRecord(
        schema_version=1,
        recording_id="rec_test",
        message_id="msg_pose_01",
        sequence=2,
        robot_id="robot_01",
        sensor_id="wheel_odom",
        capability="robot_pose",
        captured_time_rel_ms=3000,
        robot_pose=default_robot_pose,
    )
    light_rec = ExternalRoboticsRecord(
        schema_version=1,
        recording_id="rec_test",
        message_id="msg_light_01",
        sequence=3,
        robot_id="robot_01",
        sensor_id="lux",
        capability="ambient_light",
        captured_time_rel_ms=4000,
        robot_pose=default_robot_pose,
        light_measurement=ExternalLightMeasurement(intensity=0.85, color_cast="yellow"),
    )

    adapter = RecordedRoboticsSensorAdapter(
        records=[pose_rec, light_rec], clock_calibration=clock_cal
    )

    sample_pose = adapter.translate_record(pose_rec)
    pose_meas = PoseMeasurement.model_validate(sample_pose.result.measurements)
    assert pose_meas.robot_id == "robot_01"
    assert pose_meas.x_cm == 100.0

    sample_light = adapter.translate_record(light_rec)
    light_meas = LightMeasurement.model_validate(sample_light.result.measurements)
    assert light_meas.intensity == 0.85
    assert light_meas.color_cast == "yellow"
    assert sample_light.result.context.extra_context == {"lighting": "yellow"}


def test_translate_unavailable_sensor(
    clock_cal: ClockCalibration, default_robot_pose: Pose
) -> None:
    fail_rec = ExternalRoboticsRecord(
        schema_version=1,
        recording_id="rec_test",
        message_id="msg_fail_01",
        sequence=4,
        robot_id="robot_01",
        sensor_id="lidar_front",
        capability="lidar_scan",
        captured_time_rel_ms=5000,
        robot_pose=default_robot_pose,
        is_unavailable=True,
        unavailable_reason="sensor_unavailable",
    )
    adapter = RecordedRoboticsSensorAdapter(records=[fail_rec], clock_calibration=clock_cal)
    sample = adapter.translate_record(fail_rec)
    assert isinstance(sample.result, ObservationUnavailable)
    assert sample.result.reason == "sensor_unavailable"
    assert sample.result.message == "Observation unavailable."
    assert sample.result.reported_at == sample.captured_at


# =============================================================================
# SensorSource Conformance & Playback Stream Tests
# =============================================================================


def test_sensorsource_protocol_conformance(clock_cal: ClockCalibration) -> None:
    adapter = RecordedRoboticsSensorAdapter.from_file(FIXTURE_PATH)
    assert isinstance(adapter, SensorSource)

    # Observe camera request
    obs_cam = adapter.observe(ObservationRequest(capability="camera_detect"))
    assert isinstance(obs_cam, SensorObservation)
    assert obs_cam.capability == "camera_detect"

    # Observe LiDAR request
    obs_lidar = adapter.observe(
        ObservationRequest(capability="lidar_scan", parameters={"direction": "front"})
    )
    assert isinstance(obs_lidar, SensorObservation)
    assert obs_lidar.capability == "lidar_scan"


def test_playback_cursor_and_play_all() -> None:
    adapter = RecordedRoboticsSensorAdapter.from_file(FIXTURE_PATH)
    total = adapter.total_records
    assert total == 9

    all_samples = adapter.play_all()
    assert len(all_samples) == total

    # Stream iteration
    adapter.reset()
    streamed = list(adapter)
    assert len(streamed) == total

    # Exhaustion error
    with pytest.raises(IndexError, match="exhausted"):
        adapter.play_next()

    # Seek
    adapter.seek(2)
    assert adapter.current_index == 2
    s2 = adapter.play_next()
    assert s2.sample_sequence == 2
