"""Phase 2 wire contracts. No engine, sensor geometry, persistence or scheduling.

Audience-only scenario/snapshot models must never be used as agent evidence.
ObservationSample is the bounded evidence envelope around the legacy result union.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal, Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from .models import ObservationRequest, ObservationUnavailable, SensorObservation

NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
Finite = Annotated[float, Field(allow_inf_nan=False, strict=True)]
Positive = Annotated[Finite, Field(gt=0)]
Identifier = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]
Direction = Literal["north", "east", "south", "west"]
PlaybackRate = Literal[0.25, 0.5, 1.0]


class Contract(BaseModel):
    """Reject unknown fields and nonfinite values on all new contracts."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SimulationConfig(Contract):
    """Simulation units are fixed independently of presenter playback pacing."""

    step_ms: PositiveInt = 100
    sensor_interval_ms: PositiveInt = 500
    duration_ms: PositiveInt = 30_000
    playback_rate: PlaybackRate = 0.5
    camera_fov_deg: Annotated[Finite, Field(gt=0, le=360)] = 90.0
    camera_range_cm: Positive = 350.0
    lidar_range_cm: Positive = 200.0
    robot_speed_cm_s: Positive = 30.0
    stopping_distance_cm: Positive = 50.0
    freshness_ms: NonNegativeInt = 1_000

    @field_validator("playback_rate", mode="before")
    @classmethod
    def numeric_rate(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("playback_rate must be numeric: 0.25, 0.5 or 1.0")
        return value

    @model_validator(mode="after")
    def tick_alignment(self) -> Self:
        if self.duration_ms % self.step_ms or self.sensor_interval_ms % self.step_ms:
            raise ValueError("duration and sensor interval must be multiples of step_ms")
        if self.stopping_distance_cm > self.lidar_range_cm:
            raise ValueError("stopping distance must not exceed LiDAR range")
        return self


class Point(Contract):
    x_cm: Finite
    y_cm: Finite


class Pose(Point):
    robot_id: Identifier
    direction: Direction
    location: Identifier
    frame_of_reference: Identifier


class Bounds(Contract):
    width_cm: Positive
    height_cm: Positive


class Rectangle(Point):
    """Center-based axis-aligned footprint, in map centimeters."""

    width_cm: Positive
    height_cm: Positive


class SceneEntity(Contract):
    """Simulator truth only; never a camera detection."""

    entity_id: Identifier
    kind: Literal["parcel", "station", "shelf", "cart"]
    location: Identifier
    footprint: Rectangle
    intrinsic_color: Identifier
    solid: StrictBool
    opaque: StrictBool
    visible: StrictBool = True


class RouteSegment(Contract):
    route_id: Identifier
    start: Point
    end: Point
    location: Identifier

    @model_validator(mode="after")
    def cardinal_segment(self) -> Self:
        if (self.start.x_cm == self.end.x_cm) == (self.start.y_cm == self.end.y_cm):
            raise ValueError("route must be a nonzero cardinal segment")
        return self


class MoveEvent(Contract):
    """Explicit linear entity motion; the engine implements interpolation later."""

    kind: Literal["move"]
    at_ms: NonNegativeInt
    sequence: NonNegativeInt
    entity_id: Identifier
    to: Point
    duration_ms: PositiveInt


class LightingEvent(Contract):
    kind: Literal["lighting"]
    at_ms: NonNegativeInt
    sequence: NonNegativeInt
    color_cast: Identifier
    intensity: Annotated[Finite, Field(ge=0)]


class SensorAvailabilityEvent(Contract):
    kind: Literal["sensor_availability"]
    at_ms: NonNegativeInt
    sequence: NonNegativeInt
    capability: Literal["camera_detect", "lidar_scan"]
    available: StrictBool


ScenarioEvent = Annotated[
    MoveEvent | LightingEvent | SensorAvailabilityEvent, Field(discriminator="kind")
]


class LightMeasurement(Contract):
    intensity: Annotated[Finite, Field(ge=0)]
    color_cast: Identifier


class SensorAvailability(Contract):
    camera_detect: StrictBool = True
    lidar_scan: StrictBool = True


class ScenarioDefinition(Contract):
    """Audience/engine input with a stable ordered script, not agent evidence."""

    scenario_id: Identifier
    config: SimulationConfig
    bounds: Bounds
    initial_light: LightMeasurement
    sensor_availability: SensorAvailability
    robot_pose: Pose
    robot_radius_cm: Positive
    entities: list[SceneEntity]
    routes: list[RouteSegment]
    patrol: list[Point]
    events: list[ScenarioEvent]

    @model_validator(mode="after")
    def coherent_scene(self) -> Self:
        def inside(point: Point, half_x: float = 0, half_y: float = 0) -> bool:
            return (
                half_x <= point.x_cm <= self.bounds.width_cm - half_x
                and half_y <= point.y_cm <= self.bounds.height_cm - half_y
            )

        ids = [entity.entity_id for entity in self.entities]
        route_ids = [route.route_id for route in self.routes]
        if len(set(ids + route_ids + [self.robot_pose.robot_id])) != len(ids + route_ids) + 1:
            raise ValueError("scene entity, route and robot IDs must be unique")
        if not inside(self.robot_pose, self.robot_radius_cm, self.robot_radius_cm):
            raise ValueError("robot footprint outside bounds")
        if not self.patrol or self.patrol[0] != Point(
            **self.robot_pose.model_dump(include={"x_cm", "y_cm"})
        ):
            raise ValueError("patrol must start at robot position")
        for point in self.patrol:
            if not inside(point, self.robot_radius_cm, self.robot_radius_cm):
                raise ValueError("patrol outside bounds")
        for start, end in zip(self.patrol, self.patrol[1:], strict=False):
            RouteSegment(
                route_id="validation", start=start, end=end, location=self.robot_pose.location
            )
        for route in self.routes:
            if route.location != self.robot_pose.location or not all(
                inside(p) for p in (route.start, route.end)
            ):
                raise ValueError("route room/bounds mismatch")
        by_id = {entity.entity_id: entity for entity in self.entities}
        for entity in self.entities:
            shape = entity.footprint
            if entity.location != self.robot_pose.location or not inside(
                shape, shape.width_cm / 2, shape.height_cm / 2
            ):
                raise ValueError("entity room/bounds mismatch")
        keys = [(event.at_ms, event.sequence) for event in self.events]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("events must be ordered by unique (at_ms, sequence)")
        move_ends: dict[str, int] = {}
        for event in self.events:
            end_ms = event.at_ms
            if isinstance(event, MoveEvent):
                if event.entity_id not in by_id:
                    raise ValueError("unknown motion entity")
                entity = by_id[event.entity_id]
                if entity.kind not in ("parcel", "cart"):
                    raise ValueError("only parcels and carts have scripted motion")
                if event.at_ms < move_ends.get(event.entity_id, 0):
                    raise ValueError("overlapping motion for entity")
                end_ms += event.duration_ms
                move_ends[event.entity_id] = end_ms
                if not inside(
                    event.to, entity.footprint.width_cm / 2, entity.footprint.height_cm / 2
                ):
                    raise ValueError("motion endpoint outside bounds")
                if event.duration_ms % self.config.step_ms:
                    raise ValueError("motion duration must align to ticks")
            if event.at_ms % self.config.step_ms or end_ms > self.config.duration_ms:
                raise ValueError("event must align to ticks and fit scenario duration")
        return self


class SimulationStatus(Contract):
    run_id: UUID
    epoch_utc: datetime
    sim_time_ms: NonNegativeInt
    state: Literal["paused", "running", "completed"]
    playback_rate: PlaybackRate = 0.5

    _numeric_rate = field_validator("playback_rate", mode="before")(
        SimulationConfig.numeric_rate.__func__
    )

    @field_validator("epoch_utc")
    @classmethod
    def aware_epoch(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("epoch_utc must be timezone-aware")
        return value


class SimulationSnapshot(Contract):
    """Audience-only physical state; contains no claim about robot knowledge."""

    scenario_id: Identifier
    config: SimulationConfig
    status: SimulationStatus
    snapshot_sequence: NonNegativeInt
    world_version: NonNegativeInt
    robot_pose: Pose
    entities: list[SceneEntity]
    color_cast: Identifier
    light_intensity: Annotated[Finite, Field(ge=0)]
    sensor_availability: SensorAvailability

    @model_validator(mode="after")
    def coherent_status(self) -> Self:
        if self.status.playback_rate != self.config.playback_rate:
            raise ValueError("config and status playback rates must agree")
        if (
            self.status.sim_time_ms > self.config.duration_ms
            or self.status.sim_time_ms % self.config.step_ms
        ):
            raise ValueError("snapshot time must align to ticks and duration")
        if (self.status.state == "completed") != (
            self.status.sim_time_ms == self.config.duration_ms
        ):
            raise ValueError("completed state requires scenario end time and vice versa")
        if len({e.entity_id for e in self.entities}) != len(self.entities):
            raise ValueError("snapshot entity IDs must be unique")
        return self


class SimulationControl(Contract):
    """Future wire request only; does not implement an HTTP handler."""

    action: Literal["play", "pause", "step", "reset", "set_rate"]
    playback_rate: PlaybackRate | None = None

    @field_validator("playback_rate", mode="before")
    @classmethod
    def numeric_optional_rate(cls, value: object) -> object:
        return None if value is None else SimulationConfig.numeric_rate(value)

    @model_validator(mode="after")
    def rate_only_for_set_rate(self) -> Self:
        if (self.action == "set_rate") != (self.playback_rate is not None):
            raise ValueError("only set_rate requires playback_rate")
        return self


class BoundingBox(Contract):
    width_cm: Positive
    height_cm: Positive


class CameraDetection(Contract):
    object_id: Identifier
    apparent_color: Identifier
    position: Point
    frame_of_reference: Identifier
    bounding_box: BoundingBox


class CameraScan(Contract):
    """Broad and targeted success share this shape, even for zero detections."""

    objects: list[CameraDetection]

    @model_validator(mode="after")
    def sorted_unique_detections(self) -> Self:
        ids = [obj.object_id for obj in self.objects]
        if ids != sorted(set(ids)):
            raise ValueError("detections must have unique IDs in sorted order")
        return self


class LidarMeasurement(Contract):
    detected: StrictBool
    nearest_distance_cm: Annotated[Finite, Field(ge=0)] | None
    direction: Literal["front"]
    status: Literal["clear", "blocked"]

    @model_validator(mode="after")
    def consistent_hit(self) -> Self:
        if self.detected != (self.nearest_distance_cm is not None):
            raise ValueError("detected and distance must agree")
        if self.detected != (self.status == "blocked"):
            raise ValueError("status and detected must agree")
        return self


class PoseMeasurement(Point):
    robot_id: Identifier
    direction: Direction


class ObservationSample(Contract):
    """Immutable acquisition identity; reception/storage UTC is added by Tier 1."""

    sample_id: UUID
    run_id: UUID
    sample_sequence: NonNegativeInt
    epoch_utc: datetime
    sim_time_ms: NonNegativeInt
    captured_at: datetime
    sensor_id: Identifier
    observer_pose: Pose
    world_version: NonNegativeInt
    request: ObservationRequest
    result: SensorObservation | ObservationUnavailable

    @model_validator(mode="after")
    def consistent_acquisition(self) -> Self:
        if self.epoch_utc.utcoffset() is None or self.captured_at.utcoffset() is None:
            raise ValueError("acquisition timestamps must be timezone-aware")
        try:
            expected_time = self.epoch_utc + timedelta(milliseconds=self.sim_time_ms)
        except OverflowError as exc:
            raise ValueError("simulation capture time is outside datetime range") from exc
        if self.captured_at != expected_time:
            raise ValueError("captured_at must equal epoch_utc + sim_time_ms")
        if self.request.capability != self.result.capability:
            raise ValueError("request and result capabilities must agree")
        known = {"camera_detect", "lidar_scan", "ambient_light", "robot_pose"}
        capability = self.request.capability
        if capability in known:
            expected_parameters = {"direction": "front"} if capability == "lidar_scan" else {}
            if self.request.parameters != expected_parameters:
                raise ValueError("unsupported Phase 2 request parameters")
            if capability != "camera_detect" and self.request.target is not None:
                raise ValueError("only camera accepts a target")
        if isinstance(self.result, ObservationUnavailable):
            if self.result.reason == "target_not_visible" and (
                capability != "camera_detect" or self.request.target is None
            ):
                raise ValueError("target_not_visible requires a targeted camera request")
            if self.result.reported_at != self.captured_at:
                raise ValueError("failure time must equal capture time")
            if self.result.message != "Observation unavailable.":
                raise ValueError("Phase 2 failure message must not disclose hidden world state")
            return self
        result = self.result
        context = result.context
        pose = self.observer_pose
        if (
            result.observed_at != self.captured_at
            or context.world_version != self.world_version
            or context.observer != pose.robot_id
            or context.location != pose.location
            or context.frame_of_reference != pose.frame_of_reference
        ):
            raise ValueError("result time/context must match acquisition metadata")
        if set(context.extra_context) - {"lighting"}:
            raise ValueError("Phase 2 evidence context may contain lighting only")
        if "lighting" in context.extra_context and not isinstance(
            context.extra_context["lighting"], str
        ):
            raise ValueError("lighting context must be a string")
        payload_types = {
            "camera_detect": CameraScan,
            "lidar_scan": LidarMeasurement,
            "ambient_light": LightMeasurement,
            "robot_pose": PoseMeasurement,
        }
        payload_type = payload_types.get(result.capability)
        if payload_type is None:
            raise ValueError("unknown successful Phase 2 capability")
        payload = payload_type.model_validate(result.measurements)
        if isinstance(payload, CameraScan):
            if any(obj.frame_of_reference != pose.frame_of_reference for obj in payload.objects):
                raise ValueError("camera coordinates must use acquisition map frame")
            if self.request.target is not None and (
                len(payload.objects) != 1 or payload.objects[0].object_id != self.request.target
            ):
                raise ValueError("targeted camera success must contain exactly the target")
        elif self.request.target is not None:
            raise ValueError("only camera accepts a target")
        if isinstance(payload, PoseMeasurement) and (
            payload.robot_id != pose.robot_id
            or payload.x_cm != pose.x_cm
            or payload.y_cm != pose.y_cm
            or payload.direction != pose.direction
        ):
            raise ValueError("pose measurement must match acquisition pose")
        return self


class StoredObservationSample(ObservationSample):
    """An ObservationSample persisted in Tier 1 SQLite with its storage UTC timestamp."""

    stored_at: datetime

    @field_validator("stored_at")
    @classmethod
    def stored_at_must_include_timezone(cls, value: datetime) -> datetime:
        """Reject naive storage timestamps."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("stored_at must be timezone-aware")
        return value


class TemporalObservationQuery(Contract):
    """Inclusive capture-time bounds; does not redefine FactQuery.as_of."""

    run_id: UUID
    mode: Literal["samples", "latest", "last_seen", "history"]
    capability: Identifier | None = None
    sensor_id: Identifier | None = None
    entity_id: Identifier | None = None
    start_ms: NonNegativeInt | None = None
    end_ms: NonNegativeInt | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=1000)] = 100

    @model_validator(mode="after")
    def coherent_query(self) -> Self:
        if self.start_ms is not None and self.end_ms is not None and self.start_ms > self.end_ms:
            raise ValueError("start_ms must not exceed end_ms")
        if self.mode in ("last_seen", "history") and self.entity_id is None:
            raise ValueError("sighting queries require entity_id")
        if self.mode == "latest" and self.capability is None:
            raise ValueError("latest requires capability")
        return self


@runtime_checkable
class SensorSource(Protocol):
    """Agent-visible sensor access; no world getters, controls or future script."""

    def observe(self, request: ObservationRequest) -> SensorObservation | ObservationUnavailable:
        """Return evidence without advancing simulation time."""
        ...


@runtime_checkable
class ObservationLedgerReader(Protocol):
    """Agent/Tier 2-visible protocol for temporal observation queries."""

    def get_observation_sample(self, sample_id: UUID) -> StoredObservationSample | None:
        """Retrieve a stored observation sample by unique sample ID."""
        ...

    def query_observations(self, query: TemporalObservationQuery) -> list[StoredObservationSample]:
        """Execute a structured temporal observation query."""
        ...

    def get_latest_sample(
        self, run_id: UUID, capability: str, at_sim_time_ms: int | None = None
    ) -> StoredObservationSample | None:
        """Return the latest sample for a capability at or before simulation time."""
        ...

    def get_last_sighting(
        self, run_id: UUID, entity_id: str, at_sim_time_ms: int | None = None
    ) -> StoredObservationSample | None:
        """Return the latest positive sighting for an entity at or before simulation time."""
        ...

    def get_entity_sighting_history(
        self,
        run_id: UUID,
        entity_id: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
    ) -> list[StoredObservationSample]:
        """Return chronological positive sightings for an entity within time bounds."""
        ...


@runtime_checkable
class ObservationLedgerWriter(Protocol):
    """Protocol for durably recording observation samples."""

    def record_observation_sample(self, sample: ObservationSample) -> StoredObservationSample:
        """Durably persist an observation sample and return the stored record."""
        ...


@runtime_checkable
class ObservationLedger(ObservationLedgerReader, ObservationLedgerWriter, Protocol):
    """Combined read/write protocol for the Tier 1 observation ledger."""

    ...
