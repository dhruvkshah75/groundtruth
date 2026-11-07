"""Deterministic scripted robot patrol and sensor-backed reactive safety controller.

Consumes static waypoints, SimulationConfig, robot pose, and front-LiDAR samples.
Provides a strict two-step execution API:
    1. decide(current_sim_time_ms, pose, latest_lidar_sample) -> PatrolDecision
    2. reconcile(post_tick_pose) -> PatrolReconciliation
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.simulation import (
    Direction,
    LidarMeasurement,
    ObservationSample,
    ObservationUnavailable,
    Point,
    Pose,
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

    schema_version: Literal[2] = 2
    patrol: list[Point]
    config: SimulationConfig
    target_waypoint_index: int = Field(ge=0)
    pending_decision_time: int | None = Field(default=None, ge=0)
    fresh_required_at: int | None = Field(default=None, ge=0)
    run_id: UUID
    scenario_id: str | None = None
    waypoint_index: int = Field(ge=0)
    state: PatrolState
    current_pose: Pose
    last_usable_sample_id: UUID | None = None
    last_lidar_sample: ObservationSample | None = None
    last_capture_sim_time_ms: int | None = None
    last_capture_pose: Pose | None = None
    distance_travelled_since_capture_cm: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    pending_command: Pose | None = None
    reconciled: bool = True
    created_at_sim_time_ms: int = Field(ge=0)


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
        self._config = SimulationConfig.model_validate(config.model_dump(mode="json"))
        self._current_sim_time_ms = 0
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
        self._pending_decision_time: int | None = None
        self._pending_command: Pose | None = None
        self._reconciled: bool = True

        # Sensor tracking for conservative clearance between scans
        self._fresh_required_at: int | None = None
        self._last_lidar_sample: ObservationSample | None = None
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
            return self._patrol[self._target_waypoint_index].model_copy(deep=True)
        return None

    # ------------------------------------------------------------------
    # Step 1: Decision
    # ------------------------------------------------------------------

    def decide(
        self,
        current_sim_time_ms: int,
        pose_observation: ObservationSample | Pose,
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
        if type(current_sim_time_ms) is not int or current_sim_time_ms < 0:
            raise ValueError("current simulation time must be a nonnegative integer")
        if current_sim_time_ms < self._current_sim_time_ms:
            raise ValueError("decision time cannot precede the last observed controller time")
        if self._current_pose is not None and (
            pose.robot_id,
            pose.location,
            pose.frame_of_reference,
        ) != (
            self._current_pose.robot_id,
            self._current_pose.location,
            self._current_pose.frame_of_reference,
        ):
            raise ValueError("pose observer context mismatch")
        if isinstance(pose_observation, ObservationSample):
            if pose_observation.sim_time_ms != current_sim_time_ms:
                raise ValueError("pose sample must be captured at current simulation time")
            if self._run_id is not None and pose_observation.run_id != self._run_id:
                raise ValueError("pose sample run mismatch")
        elif self._run_id is None and latest_lidar_sample is not None:
            self._run_id = latest_lidar_sample.run_id
        if self._run_id is None:
            if isinstance(pose_observation, ObservationSample):
                self._run_id = pose_observation.run_id

        self._current_sim_time_ms = current_sim_time_ms
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
            return PatrolDecision(status="complete", command=None, record=record).model_copy(
                deep=True
            )

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
                return PatrolDecision(status="complete", command=None, record=record).model_copy(
                    deep=True
                )
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
            self._pending_decision_time = current_sim_time_ms
            self._pending_command = turn_command
            self._reconciled = False
            self._state = "turning"

            # A turn invalidates any previous front scan taken on the old heading
            self._fresh_required_at = current_sim_time_ms + self._config.step_ms
            self._last_lidar_sample = None
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
            return PatrolDecision(status="turning", command=turn_command, record=record).model_copy(
                deep=True
            )

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
            return PatrolDecision(
                status="waiting_for_sensor", command=None, record=record
            ).model_copy(deep=True)

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
            return PatrolDecision(status="blocked", command=None, record=record).model_copy(
                deep=True
            )

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
            return PatrolDecision(status="blocked", command=None, record=record).model_copy(
                deep=True
            )

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
        self._pending_decision_time = current_sim_time_ms
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
        return PatrolDecision(status="moving", command=move_command, record=record).model_copy(
            deep=True
        )

    # ------------------------------------------------------------------
    # Step 2: Post-Tick Reconciliation
    # ------------------------------------------------------------------

    def confirm_idle_tick(self, sample: ObservationSample) -> None:
        """Advance the checkpoint boundary when the decision issued no command."""
        if (
            not self._reconciled
            or sample.run_id != self._run_id
            or sample.sim_time_ms != self._current_sim_time_ms + self._config.step_ms
            or self._extract_pose(sample) != self._current_pose
        ):
            raise ValueError("Uncommanded tick does not match the patrol pose or time")
        self._current_sim_time_ms = sample.sim_time_ms

    def reconcile(
        self,
        post_tick_pose_observation: ObservationSample,
    ) -> PatrolReconciliation:
        """Verify the result of the commanded motion after the engine advances."""
        if self._reconciled:
            raise RuntimeError("No pending command to reconcile; call decide() first.")

        if not isinstance(post_tick_pose_observation, ObservationSample):
            raise ValueError("reconciliation requires a timestamped robot_pose sample")
        sample = ObservationSample.model_validate(
            post_tick_pose_observation.model_dump(mode="json")
        )
        actual_pose = self._extract_pose(sample)
        expected = self._pending_command
        if (
            sample.run_id != self._run_id
            or self._pending_decision_time is None
            or sample.sim_time_ms != self._pending_decision_time + self._config.step_ms
        ):
            raise ValueError("reconciliation run or post-tick capture time mismatch")
        if expected is None or (
            actual_pose.robot_id,
            actual_pose.location,
            actual_pose.frame_of_reference,
        ) != (expected.robot_id, expected.location, expected.frame_of_reference):
            raise ValueError("reconciliation observer context mismatch")
        self._current_sim_time_ms = sample.sim_time_ms
        self._pending_command = None
        self._pending_decision_time = None
        self._reconciled = True

        if expected is None:
            self._current_pose = actual_pose
            return PatrolReconciliation(
                executed=True,
                status=self._state,
                actual_pose=actual_pose,
                expected_command=None,
            ).model_copy(deep=True)

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
            ).model_copy(deep=True)

        # Command was rejected or differed (e.g. engine collision backstop)
        self._state = "stalled"
        self._fresh_required_at = sample.sim_time_ms
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
        ).model_copy(deep=True)

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
        sample_error = None
        if sample is not None:
            try:
                sample = ObservationSample.model_validate(sample.model_dump(mode="json"))
                sample_error = self._validate_lidar_sample(sample, current_sim_time_ms, pose)
            except ValueError:
                sample_error = "Malformed LiDAR acquisition."
            if sample_error is not None:
                self._fresh_required_at = current_sim_time_ms
                return {"status": "waiting_for_sensor", "reason": sample_error}
            self._update_lidar_baseline(sample)
        elif self._last_lidar_sample is not None:
            sample_error = self._validate_lidar_sample(
                self._last_lidar_sample, current_sim_time_ms, pose
            )
            if sample_error is not None:
                return {"status": "waiting_for_sensor", "reason": sample_error}

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
        if self._fresh_required_at is not None and (
            sample.sim_time_ms < self._fresh_required_at
            or sample.sample_id == self._last_usable_sample_id
        ):
            return "Fresh acquisition required after turn or rejected movement."
        if self._last_lidar_sample is not None and (sample.sim_time_ms, sample.sample_sequence) < (
            self._last_lidar_sample.sim_time_ms,
            self._last_lidar_sample.sample_sequence,
        ):
            return "LiDAR acquisition is older than the current baseline."

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

        delta_x = pose.x_cm - sample.observer_pose.x_cm
        delta_y = pose.y_cm - sample.observer_pose.y_cm
        forward_x, forward_y = CARDINAL_VECTORS[pose.direction]
        along = delta_x * forward_x + delta_y * forward_y
        lateral = delta_x * forward_y - delta_y * forward_x
        if abs(lateral) > 1e-6 or along < -1e-6:
            return "Robot path cannot be reconciled with the observed forward ray."

        if sample.sim_time_ms > current_sim_time_ms:
            return "Future-dated sample."

        if isinstance(sample.result, ObservationUnavailable):
            return "Sensor is unavailable."

        return None

    def _update_lidar_baseline(self, sample: ObservationSample) -> None:
        """Record valid LiDAR sample as the latest observation baseline."""
        self._fresh_required_at = None
        self._last_lidar_sample = sample.model_copy(deep=True)
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
    def _extract_pose(obs: ObservationSample | Pose) -> Pose:
        if isinstance(obs, Pose):
            return Pose.model_validate(obs.model_dump(mode="json"))
        if not isinstance(obs, ObservationSample):
            raise ValueError("pose requires explicit observer context")
        sample = ObservationSample.model_validate(obs.model_dump(mode="json"))
        if sample.request.capability != "robot_pose" or not isinstance(
            sample.result, SensorObservation
        ):
            raise ValueError("successful robot_pose observation required")
        return sample.observer_pose.model_copy(deep=True)

    # ------------------------------------------------------------------
    # Checkpoint and Restore
    # ------------------------------------------------------------------

    def export_checkpoint(self, current_sim_time_ms: int) -> PatrolCheckpoint:
        """Export a versioned, JSON-safe snapshot of the controller state."""
        if self._current_pose is None or self._run_id is None:
            raise ValueError("checkpoint requires an initialized pose and run")
        if current_sim_time_ms != self._current_sim_time_ms:
            raise ValueError("checkpoint time must match the last observed controller time")
        return PatrolCheckpoint(
            patrol=self._patrol,
            config=self._config,
            target_waypoint_index=self._target_waypoint_index,
            pending_decision_time=self._pending_decision_time,
            fresh_required_at=self._fresh_required_at,
            run_id=self._run_id,
            scenario_id=self._scenario_id,
            waypoint_index=self._current_waypoint_index,
            state=self._state,
            current_pose=self._current_pose,
            last_lidar_sample=self._last_lidar_sample,
            last_usable_sample_id=self._last_usable_sample_id,
            last_capture_sim_time_ms=self._last_capture_sim_time_ms,
            last_capture_pose=self._last_capture_pose,
            distance_travelled_since_capture_cm=self._distance_travelled_since_capture_cm,
            pending_command=self._pending_command,
            reconciled=self._reconciled,
            created_at_sim_time_ms=current_sim_time_ms,
        ).model_copy(deep=True)

    def restore_checkpoint(
        self,
        checkpoint: PatrolCheckpoint,
        *,
        engine_pose_observation: ObservationSample,
        engine_pending_command: Pose | None = None,
    ) -> None:
        """Validate a paired engine/controller checkpoint before replacing state.

        The runtime holds its session lock while capturing/restoring both owners.
        It supplies only the engine's robot-pose evidence and pending command here.
        """
        saved = PatrolCheckpoint.model_validate(checkpoint.model_dump(mode="json"))
        if self._run_id is not None and saved.run_id != self._run_id:
            raise ValueError("checkpoint does not match controller run_id")
        if (
            saved.scenario_id != self._scenario_id
            or saved.patrol != self._patrol
            or saved.config.model_dump(exclude={"playback_rate"})
            != self._config.model_dump(exclude={"playback_rate"})
        ):
            raise ValueError("checkpoint patrol/config/scenario mismatch")
        if saved.waypoint_index >= len(self._patrol) or saved.target_waypoint_index != min(
            saved.waypoint_index + 1, len(self._patrol)
        ):
            # A single-point patrol starts and finishes at index zero.
            if not (
                len(self._patrol) == 1
                and saved.waypoint_index == 0
                and saved.target_waypoint_index in (0, 1)
            ):
                raise ValueError("checkpoint waypoint progress mismatch")
        pose_sample = ObservationSample.model_validate(
            engine_pose_observation.model_dump(mode="json")
        )
        actual = self._extract_pose(pose_sample)
        if (
            pose_sample.run_id != saved.run_id
            or pose_sample.sim_time_ms != saved.created_at_sim_time_ms
            or actual != saved.current_pose
            or engine_pending_command != saved.pending_command
        ):
            raise ValueError("engine/controller checkpoint mismatch")
        if saved.reconciled != (saved.pending_command is None):
            raise ValueError("checkpoint pending state mismatch")
        if (saved.pending_command is None) != (saved.pending_decision_time is None):
            raise ValueError("checkpoint pending time mismatch")
        if (
            saved.pending_decision_time is not None
            and saved.pending_decision_time != saved.created_at_sim_time_ms
        ):
            raise ValueError("checkpoint pending command time mismatch")
        if saved.pending_command is not None:
            pending = saved.pending_command
            current = saved.current_pose
            displacement = abs(pending.x_cm - current.x_cm) + abs(pending.y_cm - current.y_cm)
            if (
                (pending.robot_id, pending.location, pending.frame_of_reference)
                != (current.robot_id, current.location, current.frame_of_reference)
                or (pending.x_cm != current.x_cm and pending.y_cm != current.y_cm)
                or displacement > self._config.robot_speed_cm_s * self._config.step_ms / 1000 + 1e-9
                or (displacement > 0 and pending.direction != current.direction)
            ):
                raise ValueError("checkpoint pending command violates motion constraints")
        if saved.state == "complete" and not self._points_equal(
            saved.current_pose, self._patrol[-1]
        ):
            raise ValueError("checkpoint completion pose mismatch")
        baseline = saved.last_lidar_sample
        if baseline is not None:
            baseline = ObservationSample.model_validate(baseline.model_dump(mode="json"))
            if (
                baseline.request.capability != "lidar_scan"
                or not isinstance(baseline.result, SensorObservation)
                or baseline.run_id != saved.run_id
                or baseline.sim_time_ms > saved.created_at_sim_time_ms
                or baseline.sample_id != saved.last_usable_sample_id
                or baseline.sim_time_ms != saved.last_capture_sim_time_ms
                or (
                    baseline.observer_pose.robot_id,
                    baseline.observer_pose.location,
                    baseline.observer_pose.frame_of_reference,
                    baseline.observer_pose.direction,
                )
                != (
                    saved.current_pose.robot_id,
                    saved.current_pose.location,
                    saved.current_pose.frame_of_reference,
                    saved.current_pose.direction,
                )
                or baseline.observer_pose != saved.last_capture_pose
            ):
                raise ValueError("checkpoint LiDAR evidence mismatch")
        elif saved.last_usable_sample_id is not None:
            raise ValueError("checkpoint missing LiDAR evidence")
        # Commit only after all validation succeeds.
        self._run_id = saved.run_id
        self._current_sim_time_ms = saved.created_at_sim_time_ms
        self._current_waypoint_index = saved.waypoint_index
        self._target_waypoint_index = saved.target_waypoint_index
        self._state = saved.state
        self._current_pose = saved.current_pose.model_copy(deep=True)
        self._last_lidar_sample = None
        self._last_usable_sample_id = None
        self._last_capture_sim_time_ms = None
        self._last_capture_pose = None
        self._last_capture_detected = False
        self._last_capture_measured_distance_cm = None
        if baseline is not None:
            self._update_lidar_baseline(baseline)
        self._fresh_required_at = saved.fresh_required_at
        self._distance_travelled_since_capture_cm = saved.distance_travelled_since_capture_cm
        self._pending_command = (
            saved.pending_command.model_copy(deep=True) if saved.pending_command else None
        )
        self._pending_decision_time = saved.pending_decision_time
        self._reconciled = saved.reconciled
