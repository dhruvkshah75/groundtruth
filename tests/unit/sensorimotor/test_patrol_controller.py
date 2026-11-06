"""Unit tests for the deterministic robot patrol and safety controller (P2-06)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from src.contracts.models import (
    ObservationRequest,
    ObservationUnavailable,
    SensorObservation,
    SpatialContext,
)
from src.contracts.simulation import (
    Direction,
    LidarMeasurement,
    ObservationSample,
    Point,
    Pose,
    SimulationConfig,
)
from src.sensorimotor.patrol_controller import (
    PatrolController,
)

EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
DEFAULT_RUN_ID = UUID("11111111-1111-1111-1111-111111111111")


def make_config(**overrides) -> SimulationConfig:
    data = {
        "step_ms": 100,
        "sensor_interval_ms": 500,
        "duration_ms": 30000,
        "playback_rate": 1.0,
        "camera_fov_deg": 90.0,
        "camera_range_cm": 350.0,
        "lidar_range_cm": 200.0,
        "robot_speed_cm_s": 30.0,
        "stopping_distance_cm": 50.0,
        "freshness_ms": 1000,
    }
    data.update(overrides)
    return SimulationConfig.model_validate(data)


def make_pose(
    x: float = 100.0,
    y: float = 100.0,
    direction: Direction = "north",
    robot_id: str = "robot_1",
    location: str = "warehouse",
    frame: str = "warehouse_map",
) -> Pose:
    return Pose(
        x_cm=x,
        y_cm=y,
        direction=direction,
        robot_id=robot_id,
        location=location,
        frame_of_reference=frame,
    )


def make_lidar_sample(
    sim_time_ms: int = 0,
    pose: Pose | None = None,
    run_id: UUID = DEFAULT_RUN_ID,
    detected: bool = False,
    nearest_distance_cm: float | None = None,
    unavailable_reason: str | None = None,
    capability: str = "lidar_scan",
    direction: str = "front",
    sample_id: UUID | None = None,
) -> ObservationSample:
    current_pose = pose or make_pose()
    sid = sample_id or uuid4()
    captured_at = EPOCH + timedelta(milliseconds=sim_time_ms)
    req = ObservationRequest(capability=capability, parameters={"direction": direction})

    if unavailable_reason is not None:
        res = ObservationUnavailable(
            capability=capability,
            reason=unavailable_reason,
            reported_at=captured_at,
            message="Observation unavailable.",
        )
    else:
        status_val = "blocked" if detected else "clear"
        measurement = LidarMeasurement(
            detected=detected,
            nearest_distance_cm=nearest_distance_cm,
            direction="front",
            status=status_val,
        )
        ctx = SpatialContext(
            observer=current_pose.robot_id,
            location=current_pose.location,
            frame_of_reference=current_pose.frame_of_reference,
            world_version=0,
        )
        res = SensorObservation(
            observation_id=uuid4(),
            sensor=capability,
            capability=capability,
            observed_at=captured_at,
            measurements=measurement.model_dump(mode="json"),
            confidence_score=1.0,
            context=ctx,
        )

    return ObservationSample(
        sample_id=sid,
        run_id=run_id,
        sample_sequence=1,
        epoch_utc=EPOCH,
        sim_time_ms=sim_time_ms,
        captured_at=captured_at,
        sensor_id=capability,
        observer_pose=current_pose,
        world_version=0,
        request=req,
        result=res,
    )


# ---------------------------------------------------------------------------
# Lifecycle and Basic Movement Tests
# ---------------------------------------------------------------------------


def test_patrol_validates_cardinal_segments():
    cfg = make_config()
    # Diagonal segment is invalid
    with pytest.raises(ValueError, match="not a nonzero cardinal segment"):
        PatrolController(patrol=[Point(x_cm=0, y_cm=0), Point(x_cm=10, y_cm=10)], config=cfg)

    # Zero length segment is invalid
    with pytest.raises(ValueError, match="not a nonzero cardinal segment"):
        PatrolController(patrol=[Point(x_cm=0, y_cm=0), Point(x_cm=0, y_cm=0)], config=cfg)


def test_initial_pose_must_match_first_waypoint():
    cfg = make_config()
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=200)]
    wrong_pose = make_pose(x=50, y=50)
    with pytest.raises(ValueError, match="does not match first patrol waypoint"):
        PatrolController(patrol=patrol, config=cfg, initial_pose=wrong_pose)


def test_straight_movement_to_waypoint_without_overshoot():
    cfg = make_config(robot_speed_cm_s=30.0, step_ms=100)  # 3.0 cm per tick
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=105)]
    start_pose = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=start_pose)

    sample = make_lidar_sample(sim_time_ms=0, pose=start_pose, detected=False)

    # Tick 1: move 3.0 cm -> y=103
    dec1 = controller.decide(0, start_pose, sample)
    assert dec1.status == "moving"
    assert dec1.command is not None
    assert dec1.command.y_cm == pytest.approx(103.0)
    rec1 = controller.reconcile(dec1.command)
    assert rec1.executed

    # Tick 2: remaining distance to waypoint is 2.0 cm (< 3.0 cm tick limit)
    # Must shorten step to stop exactly at y=105 without overshooting!
    dec2 = controller.decide(100, dec1.command, sample)
    assert dec2.status == "moving"
    assert dec2.command is not None
    assert dec2.command.y_cm == pytest.approx(105.0)
    rec2 = controller.reconcile(dec2.command)
    assert rec2.executed

    # Tick 3: at waypoint, patrol complete
    dec3 = controller.decide(200, dec2.command, sample)
    assert dec3.status == "complete"
    assert dec3.command is None


def test_cardinal_turns_and_post_turn_sensor_invalidation():
    cfg = make_config()
    # (100, 100) -> north to (100, 110) -> east to (120, 110)
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=110), Point(x_cm=120, y_cm=110)]
    start_pose = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=start_pose)

    sample_north = make_lidar_sample(sim_time_ms=0, pose=start_pose, detected=False)

    # Advance directly to first waypoint (100, 110)
    at_wp1 = make_pose(x=100, y=110, direction="north")
    dec_turn = controller.decide(1000, at_wp1, sample_north)
    assert dec_turn.status == "turning"
    assert dec_turn.command is not None
    assert dec_turn.command.direction == "east"
    assert dec_turn.command.x_cm == 100 and dec_turn.command.y_cm == 110

    rec_turn = controller.reconcile(dec_turn.command)
    assert rec_turn.executed

    # Next tick: turned east, but the only LiDAR sample is still facing NORTH
    # It must NOT move east using the old north scan!
    at_wp1_east = make_pose(x=100, y=110, direction="east")
    dec_wait = controller.decide(1100, at_wp1_east, sample_north)
    assert dec_wait.status == "waiting_for_sensor"
    assert dec_wait.command is None

    # When a fresh scan facing EAST arrives, it resumes moving!
    sample_east = make_lidar_sample(sim_time_ms=1100, pose=at_wp1_east, detected=False)
    dec_move = controller.decide(1100, at_wp1_east, sample_east)
    assert dec_move.status == "moving"
    assert dec_move.command is not None
    assert dec_move.command.direction == "east"
    assert dec_move.command.x_cm > 100.0


def test_engine_rejected_command_results_in_stalled():
    cfg = make_config()
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=200)]
    start_pose = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=start_pose)

    sample = make_lidar_sample(sim_time_ms=0, pose=start_pose, detected=False)
    dec = controller.decide(0, start_pose, sample)
    assert dec.status == "moving"
    assert dec.command is not None

    # Simulate engine rejection: actual pose did not move to commanded pose
    rec = controller.reconcile(start_pose)
    assert not rec.executed
    assert rec.status == "stalled"
    assert controller.state == "stalled"


def test_reconciliation_ordering_enforcement():
    cfg = make_config()
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=200)]
    start_pose = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=start_pose)

    # Calling reconcile when no command was issued raises RuntimeError
    with pytest.raises(RuntimeError, match="No pending command to reconcile"):
        controller.reconcile(start_pose)

    sample = make_lidar_sample(sim_time_ms=0, pose=start_pose, detected=False)
    controller.decide(0, start_pose, sample)

    # Calling decide a second time without reconciling raises RuntimeError
    with pytest.raises(RuntimeError, match="pending reconciliation"):
        controller.decide(100, start_pose, sample)


# ---------------------------------------------------------------------------
# Sensor & Clearance Safety Tests
# ---------------------------------------------------------------------------


def test_obstacle_within_stopping_threshold_causes_stop():
    cfg = make_config(stopping_distance_cm=50.0)
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=300)]
    pose = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=pose)

    # Obstacle detected at 45.0 cm (<= 50.0 cm stopping threshold)
    sample_close = make_lidar_sample(
        sim_time_ms=0,
        pose=pose,
        detected=True,
        nearest_distance_cm=45.0,
    )
    dec = controller.decide(0, pose, sample_close)

    assert dec.status == "blocked"
    assert dec.command is None
    assert dec.record.measured_distance_cm == 45.0
    assert dec.record.computed_clearance_cm == 45.0


def test_obstacle_cleared_permits_resumption():
    cfg = make_config(stopping_distance_cm=50.0)
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=300)]
    pose = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=pose)

    # Step 1: Blocked
    sample_blocked = make_lidar_sample(
        sim_time_ms=0,
        pose=pose,
        detected=True,
        nearest_distance_cm=40.0,
    )
    dec1 = controller.decide(0, pose, sample_blocked)
    assert dec1.status == "blocked"

    # Step 2: Clear scan arrives at t=500ms
    sample_clear = make_lidar_sample(sim_time_ms=500, pose=pose, detected=False)
    dec2 = controller.decide(500, pose, sample_clear)
    assert dec2.status == "moving"
    assert dec2.command is not None


def test_forward_distance_travelled_reduces_usable_clearance():
    # Speed: 30 cm/s, step: 100 ms -> 3 cm/tick
    cfg = make_config(robot_speed_cm_s=30.0, step_ms=100, stopping_distance_cm=50.0)
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=300)]
    pose0 = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=pose0)

    # Obstacle at 55 cm. Stopping dist = 50 cm.
    # At t=0, robot is at y=100. Clearance = 55 cm. Safe margin = 5 cm.
    sample = make_lidar_sample(sim_time_ms=0, pose=pose0, detected=True, nearest_distance_cm=55.0)

    # Tick 1: moves 3.0 cm to y=103
    dec1 = controller.decide(0, pose0, sample)
    assert dec1.status == "moving"
    assert dec1.command.y_cm == pytest.approx(103.0)
    controller.reconcile(dec1.command)

    # Tick 2: at y=103, no new scan. Robot has travelled 3 cm since capture!
    # Remaining clearance = 55 - 3 = 52 cm. Safe margin = 52 - 50 = 2 cm.
    # Max tick speed is 3 cm, but only 2 cm safe margin remains!
    # Must cap step to 2 cm to stop exactly at 50 cm boundary!
    dec2 = controller.decide(100, dec1.command, None)
    assert dec2.status == "moving"
    assert dec2.command.y_cm == pytest.approx(105.0)
    controller.reconcile(dec2.command)

    # Tick 3: at y=105. Travelled 5 cm. Remaining clearance = 50 cm = stopping distance.
    # Cannot move further!
    dec3 = controller.decide(200, dec2.command, None)
    assert dec3.status == "blocked"
    assert dec3.command is None


def test_finite_range_no_hit_exhaustion_requires_fresh_scan():
    # lidar_range = 100 cm, stopping_dist = 50 cm
    cfg = make_config(lidar_range_cm=100.0, stopping_distance_cm=50.0, freshness_ms=5000)
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=300)]
    pose0 = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=pose0)

    # No-hit scan at t=0, y=100 confirms clear space up to y=200
    sample = make_lidar_sample(sim_time_ms=0, pose=pose0, detected=False)
    dec0 = controller.decide(0, pose0, sample)
    controller.reconcile(dec0.command)

    # Advance robot directly to y=150 (travelled 50 cm)
    pose_advanced = make_pose(x=100, y=150, direction="north")
    # At y=150, remaining verified clear space = 100 - 50 = 50 cm = stopping threshold!
    # Verified range is exhausted; cannot blindly keep moving without a fresh scan.
    dec = controller.decide(500, pose_advanced, None)
    assert dec.status == "waiting_for_sensor"
    assert dec.command is None


def test_stale_lidar_sample_causes_waiting():
    cfg = make_config(freshness_ms=1000)
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=200)]
    pose = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=pose)

    # Sample captured at t=0
    sample_t0 = make_lidar_sample(sim_time_ms=0, pose=pose, detected=False)

    # At t=1001 ms, sample is stale (1001 > 1000 limit)
    dec_stale = controller.decide(1001, pose, sample_t0)
    assert dec_stale.status == "waiting_for_sensor"
    assert "stale" in dec_stale.record.policy_reason


def test_future_dated_lidar_sample_causes_waiting():
    cfg = make_config(freshness_ms=1000)
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=200)]
    pose = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=pose)

    # Sample captured at t=500 evaluated at t=100 (future-dated)
    sample_future = make_lidar_sample(sim_time_ms=500, pose=pose, detected=False)
    dec_future = controller.decide(100, pose, sample_future)
    assert dec_future.status == "waiting_for_sensor"
    assert "future" in dec_future.record.policy_reason.lower()


def test_sensor_unavailable_causes_waiting():
    cfg = make_config()
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=200)]
    pose = make_pose(x=100, y=100, direction="north")
    controller = PatrolController(patrol=patrol, config=cfg, initial_pose=pose)

    sample_unavail = make_lidar_sample(
        sim_time_ms=0, pose=pose, unavailable_reason="sensor_unavailable"
    )
    dec = controller.decide(0, pose, sample_unavail)
    assert dec.status == "waiting_for_sensor"


# ---------------------------------------------------------------------------
# Checkpoint and Restore Tests
# ---------------------------------------------------------------------------


def test_checkpoint_export_and_restore_cycle():
    cfg = make_config()
    patrol = [Point(x_cm=100, y_cm=100), Point(x_cm=100, y_cm=200)]
    pose0 = make_pose(x=100, y=100, direction="north")
    controller1 = PatrolController(
        patrol=patrol,
        config=cfg,
        initial_pose=pose0,
        run_id=DEFAULT_RUN_ID,
    )

    sample = make_lidar_sample(sim_time_ms=0, pose=pose0, detected=False)
    dec = controller1.decide(0, pose0, sample)
    controller1.reconcile(dec.command)

    # Export checkpoint
    ckpt = controller1.export_checkpoint(current_sim_time_ms=100)
    assert ckpt.run_id == DEFAULT_RUN_ID
    assert ckpt.current_pose.y_cm > 100.0

    # Restore into fresh controller
    controller2 = PatrolController(
        patrol=patrol,
        config=cfg,
        initial_pose=pose0,
        run_id=DEFAULT_RUN_ID,
    )
    controller2.restore_checkpoint(ckpt)

    assert controller2.state == controller1.state
    assert controller2.current_waypoint_index == controller1.current_waypoint_index
    assert controller2._current_pose.y_cm == controller1._current_pose.y_cm

    # Verify run mismatch is rejected
    wrong_ckpt = ckpt.model_copy(update={"run_id": uuid4()})
    with pytest.raises(ValueError, match="does not match controller run_id"):
        controller2.restore_checkpoint(wrong_ckpt)
