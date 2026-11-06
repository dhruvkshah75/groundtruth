"""Deterministic scripted robot patrol and sensor-backed reactive safety controller.

Consumes static waypoints, SimulationConfig, robot pose, and front-LiDAR samples.
Provides a strict two-step execution API:
    1. decide(current_sim_time_ms, pose, latest_lidar_sample) -> PatrolDecision
    2. reconcile(post_tick_pose) -> PatrolReconciliation
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.contracts.simulation import (
    Direction,
    LidarMeasurement,
    ObservationSample,
    ObservationUnavailable,
    Point,
    Pose,
    PoseMeasurement,
    SensorObservation,
    SimulationConfig,
)

PatrolState = Literal[
    "moving",
    "turning",
    "blocked",
    "waiting_for_sensor",
    "complete",
    "stalled",
]

CARDINAL_VECTORS: dict[Direction, tuple[int, int]] = {
    "north": (0, 1),
    "east": (1, 0),
    "south": (0, -1),
    "west": (-1, 0),
}


# ---------------------------------------------------------------------------
# Structured Models
# ---------------------------------------------------------------------------


class PatrolDecisionRecord(BaseModel):
    """Structured in-memory record of an individual tick decision."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    sim_time_ms: int
    waypoint_index: int
    target_waypoint: Point | None = None
    patrol_state: PatrolState
    current_pose: Pose
    proposed_command: Pose | None = None
    measured_distance_cm: float | None = None
    computed_clearance_cm: float | None = None
    sensor_sample_id: UUID | None = None
    sensor_sim_time_ms: int | None = None
    policy_reason: str


class PatrolDecision(BaseModel):
    """Result of controller.decide()."""

    model_config = ConfigDict(extra="forbid")

    status: PatrolState
    command: Pose | None = None
    record: PatrolDecisionRecord


class PatrolReconciliation(BaseModel):
    """Result of controller.reconcile() after engine advances one tick."""

    model_config = ConfigDict(extra="forbid")

    executed: bool
    status: PatrolState
    actual_pose: Pose
    expected_command: Pose | None = None
    discrepancy_reason: str | None = None


class PatrolCheckpoint(BaseModel):
    """Versioned JSON-safe state snapshot for pausing and deterministic restore."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: UUID
    scenario_id: str | None = None
    waypoint_index: int
    state: PatrolState
    current_pose: Pose
    last_usable_sample_id: UUID | None = None
    last_capture_sim_time_ms: int | None = None
    last_capture_pose: Pose | None = None
    distance_travelled_since_capture_cm: float = 0.0
    pending_command: Pose | None = None
    reconciled: bool = True
    created_at_sim_time_ms: int


# ---------------------------------------------------------------------------
# Patrol Controller
# ---------------------------------------------------------------------------


class PatrolController:
    """Deterministic patrol controller with sensor-backed forward clearance gating.

    Follows cardinal waypoints from a static ScenarioDefinition.patrol sequence.
    Stops before obstacles when fresh front-LiDAR detects objects within stopping distance.
    Waits for fresh front-LiDAR if sensor is unavailable, stale, or post-turn unverified.
    """

    def __init__(
        self,
        patrol: list[Point],
        config: SimulationConfig,
        *,
        initial_pose: Pose | None = None,
        run_id: UUID | None = None,
        scenario_id: str | None = None,
    ) -> None:
        if not patrol:
            raise ValueError("patrol sequence must contain at least one point")
        self._validate_patrol_cardinality(patrol)

        self._patrol = [Point(x_cm=p.x_cm, y_cm=p.y_cm) for p in patrol]
        self._config = config.model_copy(deep=True)
        self._run_id = run_id
        self._scenario_id = scenario_id

        # Waypoint tracking
        self._current_waypoint_index = 0
        if len(self._patrol) > 1:
            self._target_waypoint_index = 1
        else:
            self._target_waypoint_index = 0

        self._current_pose: Pose | None = (
            initial_pose.model_copy(deep=True) if initial_pose is not None else None
        )
        if self._current_pose is not None:
            if not self._points_equal(self._current_pose, self._patrol[0]):
                raise ValueError("initial pose position does not match first patrol waypoint")

        self._state: PatrolState = "waiting_for_sensor"
        self._pending_command: Pose | None = None
        self._reconciled: bool = True

        # Sensor tracking for conservative clearance between scans
        self._last_usable_sample_id: UUID | None = None
        self._last_capture_sim_time_ms: int | None = None
        self._last_capture_pose: Pose | None = None
        self._last_capture_measured_distance_cm: float | None = None
        self._last_capture_detected: bool = False
        self._distance_travelled_since_capture_cm: float = 0.0

    @staticmethod
    def _validate_patrol_cardinality(patrol: list[Point]) -> None:
        for i in range(len(patrol) - 1):
            p0, p1 = patrol[i], patrol[i + 1]
            dx = abs(p1.x_cm - p0.x_cm)
            dy = abs(p1.y_cm - p0.y_cm)
            if (dx == 0 and dy == 0) or (dx > 0 and dy > 0):
                raise ValueError(
                    f"Patrol segment {i}->{i + 1} from ({p0.x_cm}, {p0.y_cm}) to "
                    f"({p1.x_cm}, {p1.y_cm}) is not a nonzero cardinal segment."
                )

    @staticmethod
    def _points_equal(p0: Point, p1: Point, tolerance: float = 1e-6) -> bool:
        return abs(p0.x_cm - p1.x_cm) <= tolerance and abs(p0.y_cm - p1.y_cm) <= tolerance

    @property
    def state(self) -> PatrolState:
        return self._state

    @property
    def current_waypoint_index(self) -> int:
        return self._current_waypoint_index

    @property
    def target_waypoint(self) -> Point | None:
        if self._target_waypoint_index < len(self._patrol):
            return self._patrol[self._target_waypoint_index]
        return None

    # ------------------------------------------------------------------
    # Step 1: Decision
    # ------------------------------------------------------------------

    def decide(
        self,
        current_sim_time_ms: int,
        pose_observation: ObservationSample | PoseMeasurement | Pose,
        latest_lidar_sample: ObservationSample | None = None,
    ) -> PatrolDecision:
        """Compute the next deterministic robot command and structured record.

        Only one pending command is permitted at a time. Call reconcile() after
        advancing the engine before calling decide() again.
        """
        if not self._reconciled:
            raise RuntimeError(
                "Cannot call decide() while a previous command is pending reconciliation. "
                "Call reconcile(post_tick_pose) first."
            )

        pose = self._extract_pose(pose_observation)
        if self._run_id is None:
            if isinstance(pose_observation, ObservationSample):
                self._run_id = pose_observation.run_id

        self._current_pose = pose

        # Check if patrol is already completed
        if self._is_patrol_complete(pose):
            self._state = "complete"
            record = PatrolDecisionRecord(
                run_id=self._run_id or UUID(int=0),
                sim_time_ms=current_sim_time_ms,
                waypoint_index=self._current_waypoint_index,
                target_waypoint=None,
                patrol_state="complete",
                current_pose=pose,
                proposed_command=None,
                policy_reason="Final waypoint reached. Patrol complete.",
            )
            return PatrolDecision(status="complete", command=None, record=record)

        target = self._patrol[self._target_waypoint_index]

        # 1. Check if waypoint reached
        if self._points_equal(pose, target):
            self._current_waypoint_index = self._target_waypoint_index
            self._target_waypoint_index += 1
            if self._target_waypoint_index >= len(self._patrol):
                self._state = "complete"
                record = PatrolDecisionRecord(
                    run_id=self._run_id or UUID(int=0),
                    sim_time_ms=current_sim_time_ms,
                    waypoint_index=self._current_waypoint_index,
                    target_waypoint=None,
                    patrol_state="complete",
                    current_pose=pose,
                    proposed_command=None,
                    policy_reason="Final waypoint reached. Patrol complete.",
                )
                return PatrolDecision(status="complete", command=None, record=record)
            target = self._patrol[self._target_waypoint_index]

        # 2. Determine required direction to target
        dx = target.x_cm - pose.x_cm
        dy = target.y_cm - pose.y_cm
        required_direction = self._get_required_direction(dx, dy)

        # 3. Heading check: Turn if not aligned
        if pose.direction != required_direction:
            turn_command = Pose(
                x_cm=pose.x_cm,
                y_cm=pose.y_cm,
                robot_id=pose.robot_id,
                direction=required_direction,
                location=pose.location,
                frame_of_reference=pose.frame_of_reference,
            )
            self._pending_command = turn_command
            self._reconciled = False
            self._state = "turning"

            # A turn invalidates any previous front scan taken on the old heading
            self._last_usable_sample_id = None
            self._last_capture_pose = None
            self._last_capture_sim_time_ms = None
            self._distance_travelled_since_capture_cm = 0.0

            record = PatrolDecisionRecord(
                run_id=self._run_id or UUID(int=0),
                sim_time_ms=current_sim_time_ms,
                waypoint_index=self._current_waypoint_index,
                target_waypoint=target,
                patrol_state="turning",
                current_pose=pose,
                proposed_command=turn_command,
                policy_reason=f"Turning from {pose.direction} to align with {required_direction}.",
            )
            return PatrolDecision(status="turning", command=turn_command, record=record)

        # 4. Heading is aligned. Evaluate LiDAR safety clearance.
        clearance_eval = self._evaluate_clearance(
            current_sim_time_ms=current_sim_time_ms,
            pose=pose,
            sample=latest_lidar_sample,
        )

        if clearance_eval["status"] == "waiting_for_sensor":
            self._state = "waiting_for_sensor"
            record = PatrolDecisionRecord(
                run_id=self._run_id or UUID(int=0),
                sim_time_ms=current_sim_time_ms,
                waypoint_index=self._current_waypoint_index,
                target_waypoint=target,
                patrol_state="waiting_for_sensor",
                current_pose=pose,
                proposed_command=None,
                sensor_sample_id=clearance_eval.get("sample_id"),
                sensor_sim_time_ms=clearance_eval.get("sample_sim_time_ms"),
                policy_reason=clearance_eval["reason"],
            )
            return PatrolDecision(status="waiting_for_sensor", command=None, record=record)

        if clearance_eval["status"] == "blocked":
            self._state = "blocked"
            record = PatrolDecisionRecord(
                run_id=self._run_id or UUID(int=0),
                sim_time_ms=current_sim_time_ms,
                waypoint_index=self._current_waypoint_index,
                target_waypoint=target,
                patrol_state="blocked",
                current_pose=pose,
                proposed_command=None,
                measured_distance_cm=clearance_eval.get("measured_distance_cm"),
                computed_clearance_cm=clearance_eval.get("computed_clearance_cm"),
                sensor_sample_id=clearance_eval.get("sample_id"),
                sensor_sim_time_ms=clearance_eval.get("sample_sim_time_ms"),
                policy_reason=clearance_eval["reason"],
            )
            return PatrolDecision(status="blocked", command=None, record=record)

        # 5. Safe to translate. Compute bounded step distance.
        max_tick_distance = (self._config.robot_speed_cm_s * self._config.step_ms) / 1000.0
        dist_to_waypoint = abs(dx) + abs(dy)
        step_distance = min(max_tick_distance, dist_to_waypoint)

        # Bounded by remaining safe clearance before stopping distance
        safe_margin = clearance_eval["safe_margin_cm"]
        if safe_margin < step_distance:
            step_distance = max(0.0, safe_margin)

        if step_distance <= 1e-6:
            # Cannot safely move forward without violating stopping distance
            self._state = "blocked"
            comp_clear = clearance_eval.get("computed_clearance_cm", 0.0)
            stop_dist = self._config.stopping_distance_cm
            reason = (
                f"Remaining forward clearance ({comp_clear:.1f} cm) "
                f"insufficient for motion above stopping distance ({stop_dist:.1f} cm)."
            )
            record = PatrolDecisionRecord(
                run_id=self._run_id or UUID(int=0),
                sim_time_ms=current_sim_time_ms,
                waypoint_index=self._current_waypoint_index,
                target_waypoint=target,
                patrol_state="blocked",
                current_pose=pose,
                proposed_command=None,
                measured_distance_cm=clearance_eval.get("measured_distance_cm"),
                computed_clearance_cm=clearance_eval.get("computed_clearance_cm"),
                sensor_sample_id=clearance_eval.get("sample_id"),
                sensor_sim_time_ms=clearance_eval.get("sample_sim_time_ms"),
                policy_reason=reason,
            )
            return PatrolDecision(status="blocked", command=None, record=record)

        vec = CARDINAL_VECTORS[pose.direction]
        next_x = pose.x_cm + vec[0] * step_distance
        next_y = pose.y_cm + vec[1] * step_distance

        move_command = Pose(
            x_cm=next_x,
            y_cm=next_y,
            robot_id=pose.robot_id,
            direction=pose.direction,
            location=pose.location,
            frame_of_reference=pose.frame_of_reference,
        )
        self._pending_command = move_command
        self._reconciled = False
        self._state = "moving"

        comp_clear = clearance_eval.get("computed_clearance_cm", 0.0)
        record = PatrolDecisionRecord(
            run_id=self._run_id or UUID(int=0),
            sim_time_ms=current_sim_time_ms,
            waypoint_index=self._current_waypoint_index,
            target_waypoint=target,
            patrol_state="moving",
            current_pose=pose,
            proposed_command=move_command,
            measured_distance_cm=clearance_eval.get("measured_distance_cm"),
            computed_clearance_cm=clearance_eval.get("computed_clearance_cm"),
            sensor_sample_id=clearance_eval.get("sample_id"),
            sensor_sim_time_ms=clearance_eval.get("sample_sim_time_ms"),
            policy_reason=(
                f"Advancing {step_distance:.2f} cm towards waypoint {self._target_waypoint_index} "
                f"with safe clearance {comp_clear:.1f} cm."
            ),
        )
        return PatrolDecision(status="moving", command=move_command, record=record)

    # ------------------------------------------------------------------
    # Step 2: Post-Tick Reconciliation
    # ------------------------------------------------------------------

    def reconcile(
        self,
        post_tick_pose_observation: ObservationSample | PoseMeasurement | Pose,
    ) -> PatrolReconciliation:
        """Verify the result of the commanded motion after the engine advances."""
        if self._reconciled:
            raise RuntimeError("No pending command to reconcile; call decide() first.")

        actual_pose = self._extract_pose(post_tick_pose_observation)
        expected = self._pending_command
        self._pending_command = None
        self._reconciled = True

        if expected is None:
            self._current_pose = actual_pose
            return PatrolReconciliation(
                executed=True,
                status=self._state,
                actual_pose=actual_pose,
                expected_command=None,
            )

        # Check translation and direction execution
        pos_match = self._points_equal(actual_pose, expected, tolerance=1e-3)
        dir_match = actual_pose.direction == expected.direction

        if pos_match and dir_match:
            # Update odometry distance travelled since last LiDAR capture
            if (
                self._last_capture_pose is not None
                and actual_pose.direction == self._last_capture_pose.direction
            ):
                vec = CARDINAL_VECTORS[actual_pose.direction]
                dx = (actual_pose.x_cm - self._last_capture_pose.x_cm) * vec[0]
                dy = (actual_pose.y_cm - self._last_capture_pose.y_cm) * vec[1]
                self._distance_travelled_since_capture_cm = max(0.0, dx + dy)

            self._current_pose = actual_pose
            return PatrolReconciliation(
                executed=True,
                status=self._state,
                actual_pose=actual_pose,
                expected_command=expected,
            )

        # Command was rejected or differed (e.g. engine collision backstop)
        self._state = "stalled"
        self._current_pose = actual_pose

        reason = (
            f"Movement rejected: expected ({expected.x_cm:.2f}, {expected.y_cm:.2f}, "
            f"{expected.direction}) but actual post-tick pose is "
            f"({actual_pose.x_cm:.2f}, {actual_pose.y_cm:.2f}, {actual_pose.direction})."
        )
        return PatrolReconciliation(
            executed=False,
            status="stalled",
            actual_pose=actual_pose,
            expected_command=expected,
            discrepancy_reason=reason,
        )

    # ------------------------------------------------------------------
    # Clearance and Sensor Evaluation
    # ------------------------------------------------------------------

    def _evaluate_clearance(
        self,
        current_sim_time_ms: int,
        pose: Pose,
        sample: ObservationSample | None,
    ) -> dict:
        """Evaluate forward clearance conservatively across scans."""
        # Check if latest sample reports sensor unavailability
        if sample is not None and isinstance(sample.result, ObservationUnavailable):
            return {
                "status": "waiting_for_sensor",
                "reason": f"LiDAR sensor is unavailable ({sample.result.reason}).",
                "sample_id": sample.sample_id,
                "sample_sim_time_ms": sample.sim_time_ms,
            }

        # Check if we have a fresh, applicable new sample
        sample_error = None
        if sample is not None:
            validation_error = self._validate_lidar_sample(sample, current_sim_time_ms, pose)
            if validation_error is None:
                # New valid sample: update our active baseline
                self._update_lidar_baseline(sample)
            else:
                sample_error = validation_error

        # Do we have a usable baseline scan?
        if (
            self._last_usable_sample_id is None
            or self._last_capture_pose is None
            or self._last_capture_sim_time_ms is None
        ):
            reason = sample_error or "No applicable forward LiDAR observation available."
            return {
                "status": "waiting_for_sensor",
                "reason": reason,
                "sample_id": sample.sample_id if sample is not None else None,
                "sample_sim_time_ms": sample.sim_time_ms if sample is not None else None,
            }

        # Check age of active baseline
        age_ms = current_sim_time_ms - self._last_capture_sim_time_ms
        if age_ms < 0:
            return {
                "status": "waiting_for_sensor",
                "reason": "LiDAR observation capture time is in the future.",
                "sample_id": self._last_usable_sample_id,
                "sample_sim_time_ms": self._last_capture_sim_time_ms,
            }
        if age_ms > self._config.freshness_ms:
            return {
                "status": "waiting_for_sensor",
                "reason": (
                    f"LiDAR observation is stale ({age_ms} ms > "
                    f"{self._config.freshness_ms} ms limit)."
                ),
                "sample_id": self._last_usable_sample_id,
                "sample_sim_time_ms": self._last_capture_sim_time_ms,
            }

        # Heading must match capture heading
        if pose.direction != self._last_capture_pose.direction:
            return {
                "status": "waiting_for_sensor",
                "reason": "Robot heading has changed since last LiDAR capture.",
                "sample_id": self._last_usable_sample_id,
                "sample_sim_time_ms": self._last_capture_sim_time_ms,
            }

        # Calculate forward distance travelled since capture
        vec = CARDINAL_VECTORS[pose.direction]
        dx = (pose.x_cm - self._last_capture_pose.x_cm) * vec[0]
        dy = (pose.y_cm - self._last_capture_pose.y_cm) * vec[1]
        travelled = max(0.0, dx + dy)
        self._distance_travelled_since_capture_cm = travelled

        stopping_dist = self._config.stopping_distance_cm

        if self._last_capture_detected and self._last_capture_measured_distance_cm is not None:
            # Hit detected
            measured = self._last_capture_measured_distance_cm
            remaining_clearance = measured - travelled
            safe_margin = remaining_clearance - stopping_dist

            if remaining_clearance <= stopping_dist or safe_margin <= 1e-4:
                return {
                    "status": "blocked",
                    "reason": (
                        f"Obstacle detected at {remaining_clearance:.1f} cm "
                        f"(at or within stopping threshold {stopping_dist:.1f} cm)."
                    ),
                    "measured_distance_cm": measured,
                    "computed_clearance_cm": remaining_clearance,
                    "safe_margin_cm": 0.0,
                    "sample_id": self._last_usable_sample_id,
                    "sample_sim_time_ms": self._last_capture_sim_time_ms,
                }

            return {
                "status": "clear",
                "reason": (
                    f"Clearance {remaining_clearance:.1f} cm above stopping threshold "
                    f"{stopping_dist:.1f} cm."
                ),
                "measured_distance_cm": measured,
                "computed_clearance_cm": remaining_clearance,
                "safe_margin_cm": safe_margin,
                "sample_id": self._last_usable_sample_id,
                "sample_sim_time_ms": self._last_capture_sim_time_ms,
            }

        else:
            # No hit detected: clear space up to lidar_range_cm from capture pose
            lidar_range = self._config.lidar_range_cm
            remaining_verified_space = lidar_range - travelled
            safe_margin = remaining_verified_space - stopping_dist

            if remaining_verified_space <= stopping_dist or safe_margin <= 1e-4:
                return {
                    "status": "waiting_for_sensor",
                    "reason": (
                        f"Verified clear range exhausted ({remaining_verified_space:.1f} cm "
                        f"remaining; stopping threshold {stopping_dist:.1f} cm). "
                        "Waiting for fresh LiDAR."
                    ),
                    "measured_distance_cm": None,
                    "computed_clearance_cm": remaining_verified_space,
                    "safe_margin_cm": 0.0,
                    "sample_id": self._last_usable_sample_id,
                    "sample_sim_time_ms": self._last_capture_sim_time_ms,
                }

            return {
                "status": "clear",
                "reason": (
                    f"Verified clear range {remaining_verified_space:.1f} cm "
                    f"above stopping threshold {stopping_dist:.1f} cm."
                ),
                "measured_distance_cm": None,
                "computed_clearance_cm": remaining_verified_space,
                "safe_margin_cm": safe_margin,
                "sample_id": self._last_usable_sample_id,
                "sample_sim_time_ms": self._last_capture_sim_time_ms,
            }

    def _validate_lidar_sample(
        self,
        sample: ObservationSample,
        current_sim_time_ms: int,
        pose: Pose,
    ) -> str | None:
        """Validate sample compatibility, scope, direction, and timing."""
        if sample.request.capability != "lidar_scan":
            return "Capability is not lidar_scan."

        if sample.request.parameters.get("direction") != "front":
            return "LiDAR scan is not front-facing."

        if self._run_id is not None and sample.run_id != self._run_id:
            return "Run ID mismatch."

        if sample.observer_pose.robot_id != pose.robot_id:
            return "Observer robot ID mismatch."

        if sample.observer_pose.location != pose.location:
            return "Location mismatch."

        if sample.observer_pose.frame_of_reference != pose.frame_of_reference:
            return "Frame of reference mismatch."

        if sample.observer_pose.direction != pose.direction:
            return "Observer heading mismatch."

        if sample.sim_time_ms > current_sim_time_ms:
            return "Future-dated sample."

        if isinstance(sample.result, ObservationUnavailable):
            return "Sensor is unavailable."

        return None

    def _update_lidar_baseline(self, sample: ObservationSample) -> None:
        """Record valid LiDAR sample as the latest observation baseline."""
        self._last_usable_sample_id = sample.sample_id
        self._last_capture_sim_time_ms = sample.sim_time_ms
        self._last_capture_pose = sample.observer_pose.model_copy(deep=True)
        self._distance_travelled_since_capture_cm = 0.0

        if isinstance(sample.result, SensorObservation):
            meas = sample.result.measurements
            if isinstance(meas, LidarMeasurement):
                self._last_capture_detected = meas.detected
                self._last_capture_measured_distance_cm = meas.nearest_distance_cm
            elif isinstance(meas, dict):
                self._last_capture_detected = bool(meas.get("detected", False))
                self._last_capture_measured_distance_cm = meas.get("nearest_distance_cm")
            else:
                self._last_capture_detected = False
                self._last_capture_measured_distance_cm = None

    # ------------------------------------------------------------------
    # Helper Utilities
    # ------------------------------------------------------------------

    def _is_patrol_complete(self, pose: Pose) -> bool:
        if self._current_waypoint_index >= len(self._patrol) - 1:
            return self._points_equal(pose, self._patrol[-1])
        return False

    @staticmethod
    def _get_required_direction(dx: float, dy: float) -> Direction:
        if abs(dx) > abs(dy):
            return "east" if dx > 0 else "west"
        return "north" if dy > 0 else "south"

    @staticmethod
    def _extract_pose(obs: ObservationSample | PoseMeasurement | Pose) -> Pose:
        if isinstance(obs, Pose):
            return obs.model_copy(deep=True)
        if isinstance(obs, ObservationSample):
            if isinstance(obs.result, SensorObservation):
                meas = obs.result.measurements
                if isinstance(meas, Pose):
                    return meas.model_copy(deep=True)
                if isinstance(meas, PoseMeasurement):
                    ctx = obs.result.context
                    return Pose(
                        x_cm=meas.x_cm,
                        y_cm=meas.y_cm,
                        robot_id=meas.robot_id,
                        direction=meas.direction,
                        location=ctx.location,
                        frame_of_reference=ctx.frame_of_reference,
                    )
                if isinstance(meas, dict):
                    ctx = obs.result.context
                    return Pose(
                        x_cm=meas["x_cm"],
                        y_cm=meas["y_cm"],
                        robot_id=meas.get("robot_id", ctx.observer),
                        direction=meas["direction"],
                        location=ctx.location,
                        frame_of_reference=ctx.frame_of_reference,
                    )
            return obs.observer_pose.model_copy(deep=True)
        if isinstance(obs, PoseMeasurement):
            return Pose(
                x_cm=obs.x_cm,
                y_cm=obs.y_cm,
                robot_id=obs.robot_id,
                direction=obs.direction,
                location="warehouse",
                frame_of_reference="warehouse_map",
            )
        raise ValueError(f"Unsupported pose observation type: {type(obs)}")

    # ------------------------------------------------------------------
    # Checkpoint and Restore
    # ------------------------------------------------------------------

    def export_checkpoint(self, current_sim_time_ms: int) -> PatrolCheckpoint:
        """Export a versioned, JSON-safe snapshot of the controller state."""
        return PatrolCheckpoint(
            schema_version=1,
            run_id=self._run_id or UUID(int=0),
            scenario_id=self._scenario_id,
            waypoint_index=self._current_waypoint_index,
            state=self._state,
            current_pose=self._current_pose or self._patrol[0],  # type: ignore[arg-type]
            last_usable_sample_id=self._last_usable_sample_id,
            last_capture_sim_time_ms=self._last_capture_sim_time_ms,
            last_capture_pose=self._last_capture_pose,
            distance_travelled_since_capture_cm=self._distance_travelled_since_capture_cm,
            pending_command=self._pending_command,
            reconciled=self._reconciled,
            created_at_sim_time_ms=current_sim_time_ms,
        )

    def restore_checkpoint(self, checkpoint: PatrolCheckpoint) -> None:
        """Restore controller state from a checkpoint, validating run identity."""
        if self._run_id is not None and checkpoint.run_id != self._run_id:
            raise ValueError(
                f"Checkpoint run_id {checkpoint.run_id} does not match "
                f"controller run_id {self._run_id}"
            )
        if checkpoint.waypoint_index >= len(self._patrol):
            raise ValueError(
                f"Checkpoint waypoint index {checkpoint.waypoint_index} exceeds patrol bounds"
            )

        self._run_id = checkpoint.run_id
        self._current_waypoint_index = checkpoint.waypoint_index
        self._target_waypoint_index = min(checkpoint.waypoint_index + 1, len(self._patrol) - 1)
        self._state = checkpoint.state
        self._current_pose = checkpoint.current_pose.model_copy(deep=True)
        self._last_usable_sample_id = checkpoint.last_usable_sample_id
        self._last_capture_sim_time_ms = checkpoint.last_capture_sim_time_ms
        self._last_capture_pose = (
            checkpoint.last_capture_pose.model_copy(deep=True)
            if checkpoint.last_capture_pose is not None
            else None
        )
        self._distance_travelled_since_capture_cm = checkpoint.distance_travelled_since_capture_cm
        self._pending_command = (
            checkpoint.pending_command.model_copy(deep=True)
            if checkpoint.pending_command is not None
            else None
        )
        self._reconciled = checkpoint.reconciled
