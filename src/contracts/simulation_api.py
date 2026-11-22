"""Public presenter and polling contracts for the session simulation."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .simulation import (
    Bounds,
    Contract,
    Finite,
    Identifier,
    NonNegativeInt,
    ObservationSample,
    PlaybackRate,
    Point,
    Positive,
    PositiveInt,
    RouteSegment,
    ScenarioDefinition,
    ScenarioEvent,
    SimulationControl,
    SimulationSnapshot,
    StoredObservationSample,
)

MAX_RECENT_BELIEF_CHANGES = 16
SensorCapability = Literal["camera_detect", "lidar_scan"]
ObservationCapability = Literal["camera_detect", "lidar_scan", "ambient_light", "robot_pose"]


class SimulationRunIdentity(Contract):
    run_id: UUID | None
    generation: NonNegativeInt


class SimulationEvidenceRequest(Contract):
    """Read bounded evidence for a catalog entity in one simulation incarnation."""

    expected: SimulationRunIdentity
    entity_id: Identifier | None = None
    sample_id: UUID | None = None

    @model_validator(mode="after")
    def target_specified(self) -> Self:
        if self.entity_id is None and self.sample_id is None:
            raise ValueError("Either entity_id or sample_id must be provided")
        return self


class SimulationLoadRequest(Contract):
    preset: Literal["warehouse_inspection_v1", "warehouse_belief_demo_v1"]
    expected: SimulationRunIdentity
    request_id: UUID


class SimulationControlRequest(SimulationControl):
    expected: SimulationRunIdentity


class RelocateParcel(Contract):
    kind: Literal["relocate_parcel"]
    entity_id: Annotated[str, Field(min_length=1, max_length=64)]
    to: Point
    duration_ms: PositiveInt


class SetCart(Contract):
    kind: Literal["cart_block", "cart_clear"]


class SetLighting(Contract):
    kind: Literal["lighting"]
    color_cast: Literal["neutral", "yellow"]
    intensity: Annotated[Finite, Field(ge=0, le=1)]


class SetSensorAvailability(Contract):
    kind: Literal["sensor_availability"]
    capability: SensorCapability
    available: Annotated[bool, Field(strict=True)]


PresenterDisruption = Annotated[
    RelocateParcel | SetCart | SetLighting | SetSensorAvailability, Field(discriminator="kind")
]


class SimulationDisruptRequest(Contract):
    expected: SimulationRunIdentity
    request_id: UUID
    at_ms: NonNegativeInt | None = None
    disruption: PresenterDisruption


class SimulationAudience(Contract):
    """Simulator geometry for the audience only."""

    snapshot: SimulationSnapshot
    bounds: Bounds
    robot_radius_cm: Positive
    routes: list[RouteSegment]
    patrol: list[Point]


class SimulationObservationState(Contract):
    latest: dict[ObservationCapability, StoredObservationSample]
    last_acquisition_ms: NonNegativeInt | None


class SimulationBeliefChange(Contract):
    sample_id: UUID
    sample_sequence: NonNegativeInt
    sim_time_ms: NonNegativeInt
    fact_id: UUID
    revision_event_id: UUID | None = None


class SimulationBeliefState(Contract):
    graph_path: Literal["/api/state"] = "/api/state"
    recent_changes: Annotated[
        list[SimulationBeliefChange], Field(max_length=MAX_RECENT_BELIEF_CHANGES)
    ]


class PatrolDecisionSummary(Contract):
    run_id: UUID
    sim_time_ms: NonNegativeInt
    state: Literal["moving", "turning", "blocked", "waiting_for_sensor", "complete", "stalled"]
    policy_reason: str
    decision_sim_time_ms: NonNegativeInt | None = None
    waypoint_index: NonNegativeInt | None = None
    target_waypoint: Point | None = None
    sample_id: UUID | None = None
    sensor_sample_id: UUID | None = None
    sensor_sim_time_ms: NonNegativeInt | None = None
    measured_distance_cm: float | None = None
    computed_clearance_cm: float | None = None

    @model_validator(mode="before")
    @classmethod
    def sync_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            t1 = data.get("sim_time_ms")
            t2 = data.get("decision_sim_time_ms")
            if t1 is not None and t2 is not None and t1 != t2:
                raise ValueError(
                    "sim_time_ms and decision_sim_time_ms must be equal when both provided"
                )
            canonical_time = t1 if t1 is not None else t2
            data["sim_time_ms"] = canonical_time
            data["decision_sim_time_ms"] = canonical_time

            s1 = data.get("sample_id")
            s2 = data.get("sensor_sample_id")
            if s1 is not None and s2 is not None and str(s1) != str(s2):
                raise ValueError("sample_id and sensor_sample_id must be equal when both provided")
            canonical_sample = s1 if s1 is not None else s2
            data["sample_id"] = canonical_sample
            data["sensor_sample_id"] = canonical_sample
        return data

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        if self.decision_sim_time_ms is not None and self.decision_sim_time_ms != self.sim_time_ms:
            raise ValueError("sim_time_ms and decision_sim_time_ms must match")
        if self.sample_id != self.sensor_sample_id:
            raise ValueError("sample_id and sensor_sample_id must match")
        if (self.sample_id is None) != (self.sensor_sim_time_ms is None):
            raise ValueError(
                "sample_id and sensor_sim_time_ms must be provided together or both omitted"
            )
        if self.sensor_sim_time_ms is not None and self.sensor_sim_time_ms > self.sim_time_ms:
            raise ValueError("sensor_sim_time_ms cannot exceed decision sim_time_ms")
        return self


class SimulationPatrolState(Contract):
    state: Literal["moving", "turning", "blocked", "waiting_for_sensor", "complete", "stalled"]
    waypoint_index: NonNegativeInt
    target_waypoint: Point | None
    current_decision: PatrolDecisionSummary | None = None
    latest_stop_decision: PatrolDecisionSummary | None = None


class SimulationSampleEvidenceResponse(SimulationRunIdentity):
    sim_time_ms: NonNegativeInt
    sample: ObservationSample


class SimulationStateResponse(SimulationRunIdentity):
    revision: NonNegativeInt
    snapshot_sequence: NonNegativeInt | None
    sim_time_ms: NonNegativeInt | None
    epoch_utc: datetime | None
    playback_rate: PlaybackRate
    state: Literal["pending", "paused", "running", "completed", "error"]
    error: str | None
    persistence_error: str | None
    llm_ready: bool
    patrol: SimulationPatrolState | None
    audience: SimulationAudience | None
    observation: SimulationObservationState
    belief: SimulationBeliefState

    @model_validator(mode="after")
    def coherent_snapshot(self) -> Self:
        if self.audience is None:
            if (
                any(
                    value is not None
                    for value in (
                        self.run_id,
                        self.epoch_utc,
                        self.sim_time_ms,
                        self.snapshot_sequence,
                        self.patrol,
                        self.observation.last_acquisition_ms,
                    )
                )
                or self.observation.latest
                or self.belief.recent_changes
                or self.state not in ("pending", "error")
            ):
                raise ValueError("An unloaded simulation has no run data.")
            return self

        snapshot = self.audience.snapshot
        status = snapshot.status
        if (
            (self.run_id, self.epoch_utc, self.sim_time_ms, self.playback_rate)
            != (status.run_id, status.epoch_utc, status.sim_time_ms, status.playback_rate)
            or self.snapshot_sequence != snapshot.snapshot_sequence
            or self.state not in (status.state, "error")
            or self.patrol is None
        ):
            raise ValueError("State metadata must match the audience snapshot boundary.")
        if self.patrol is not None:
            for dec, name in (
                (self.patrol.current_decision, "Current"),
                (self.patrol.latest_stop_decision, "Latest stop"),
            ):
                if dec is not None:
                    if dec.run_id != status.run_id or dec.sim_time_ms > status.sim_time_ms:
                        raise ValueError(
                            f"{name} patrol decision does not belong to this snapshot boundary."
                        )
                    if (
                        dec.decision_sim_time_ms is not None
                        and dec.decision_sim_time_ms > status.sim_time_ms
                    ):
                        raise ValueError(f"{name} patrol decision timestamp exceeds snapshot time.")
                    if (
                        dec.sensor_sim_time_ms is not None
                        and dec.sensor_sim_time_ms > status.sim_time_ms
                    ):
                        raise ValueError(f"{name} patrol sensor timestamp exceeds snapshot time.")
        for capability, sample in self.observation.latest.items():
            if (
                sample.run_id != status.run_id
                or sample.epoch_utc != status.epoch_utc
                or sample.sim_time_ms > status.sim_time_ms
                or sample.request.capability != capability
            ):
                raise ValueError("Observation does not belong to this snapshot boundary.")
        return self


class SimulationDisruptResponse(Contract):
    event: ScenarioEvent
    state: SimulationStateResponse


class SimulationExportBundle(Contract):
    """Version 1 replay boundary. All data is scoped to the selected run."""

    schema_version: Literal[1]
    run_id: UUID
    epoch_utc: datetime
    sim_time_ms: NonNegativeInt
    snapshot_sequence: NonNegativeInt
    scenario: ScenarioDefinition
    presenter_events: list[ScenarioEvent]
    observations: list[StoredObservationSample]
    belief_changes: list[SimulationBeliefChange]

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Schema version must be an integer.")
        return value

    @model_validator(mode="after")
    def coherent_evidence(self) -> Self:
        if (
            self.epoch_utc.utcoffset() is None
            or self.sim_time_ms != self.snapshot_sequence * self.scenario.config.step_ms
            or self.sim_time_ms > self.scenario.config.duration_ms
        ):
            raise ValueError("Invalid replay clock boundary.")
        by_id = {sample.sample_id: sample for sample in self.observations}
        sequences = [sample.sample_sequence for sample in self.observations]
        if (
            len(by_id) != len(self.observations)
            or sequences != sorted(set(sequences))
            or any(
                sample.run_id != self.run_id
                or sample.epoch_utc != self.epoch_utc
                or sample.sim_time_ms > self.sim_time_ms
                for sample in self.observations
            )
        ):
            raise ValueError("Replay evidence must be unique, ordered and scoped to the run.")
        for change in self.belief_changes:
            sample = by_id.get(change.sample_id)
            if sample is None or (sample.sample_sequence, sample.sim_time_ms) != (
                change.sample_sequence,
                change.sim_time_ms,
            ):
                raise ValueError("Replay belief changes must reference matching evidence.")
        return self


class SimulationApiError(Contract):
    code: Literal[
        "invalid_request",
        "invalid_session",
        "stale_run",
        "state_conflict",
        "runtime_unavailable",
        "provider_unavailable",
        "export_not_ready",
        "simulation_failed",
    ]
    error: str
