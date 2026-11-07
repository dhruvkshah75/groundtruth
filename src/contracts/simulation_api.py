"""Public presenter and polling contracts for the session simulation."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .simulation import (
    Bounds,
    Contract,
    Finite,
    NonNegativeInt,
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


class SimulationLoadRequest(Contract):
    preset: Literal["warehouse_inspection_v1"]
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


class SimulationPatrolState(Contract):
    state: Literal["moving", "turning", "blocked", "waiting_for_sensor", "complete", "stalled"]
    waypoint_index: NonNegativeInt
    target_waypoint: Point | None


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
