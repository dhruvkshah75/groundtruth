"""Tier 2 temporal belief ingestion and evaluation service (P2-05).

Consumes validated ObservationSample envelopes, maintains time-aware beliefs,
and records justified belief revisions in Tier 1.
Preserves strict Tier 2 architectural boundaries: depends only on contracts
and injected protocols; never imports concrete SQLite, NetworkX, or sensor code.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import (
    AuditEvent,
    CameraDetection,
    CameraScan,
    Direction,
    FactAssertion,
    FactQuery,
    LidarMeasurement,
    ObservationLedgerWriter,
    ObservationSample,
    ObservationUnavailable,
    Point,
    Pose,
    SensorObservation,
    SpatialContext,
    StoredFact,
    StoredObservationSample,
    TemporalObservationQuery,
)


@runtime_checkable
class RevisionResultProtocol(Protocol):
    successor: StoredFact
    audit_event: AuditEvent


@runtime_checkable
class FactRepositoryProtocol(Protocol):
    """Protocol for Tier 1 fact persistence and revision."""

    def observation_ingestion_lock(self) -> AbstractContextManager: ...

    def record_fact(self, assertion: FactAssertion) -> StoredFact: ...

    def record_revision(
        self,
        old_fact_id: UUID,
        replacement: FactAssertion,
        reason: str,
        policy_rule: str,
        revised_at: datetime,
    ) -> Any: ...

    def query_facts(self, query: FactQuery) -> list[StoredFact]: ...

    def get_facts_by_sample_id(self, sample_id: UUID) -> list[StoredFact]: ...


# ---------------------------------------------------------------------------
# Declarative Map Structures
# ---------------------------------------------------------------------------


class StationZone(BaseModel):
    """Static declarative configuration for an axis-aligned warehouse station zone."""

    model_config = ConfigDict(extra="forbid")

    zone_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    frame_of_reference: str = Field(min_length=1)
    x_min_cm: float
    x_max_cm: float
    y_min_cm: float
    y_max_cm: float

    @classmethod
    def from_center(
        cls,
        zone_id: str,
        location: str,
        frame_of_reference: str,
        center_x_cm: float,
        center_y_cm: float,
        width_cm: float,
        height_cm: float,
    ) -> StationZone:
        half_w = width_cm / 2.0
        half_h = height_cm / 2.0
        return cls(
            zone_id=zone_id,
            location=location,
            frame_of_reference=frame_of_reference,
            x_min_cm=center_x_cm - half_w,
            x_max_cm=center_x_cm + half_w,
            y_min_cm=center_y_cm - half_h,
            y_max_cm=center_y_cm + half_h,
        )

    def contains(self, point: Point, location: str, frame: str) -> bool:
        """Inclusive boundary check for point containment within this zone."""
        if location != self.location or frame != self.frame_of_reference:
            return False
        return (
            self.x_min_cm <= point.x_cm <= self.x_max_cm
            and self.y_min_cm <= point.y_cm <= self.y_max_cm
        )


class StationZoneMap(BaseModel):
    """Configured map of station zones with deterministic overlap and boundary resolution."""

    model_config = ConfigDict(extra="forbid")

    zones: list[StationZone] = Field(default_factory=list)

    def resolve_zone(
        self, point: Point, location: str, frame: str
    ) -> tuple[str | None, Literal["matched", "unmapped", "ambiguous"]]:
        """Map observed coordinates to an unambiguous station zone.

        Returns:
            (zone_id, "matched") if exactly one zone matches.
            (None, "ambiguous") if multiple zones overlap.
            (None, "unmapped") if no zone matches.
        """
        matches = [z for z in self.zones if z.contains(point, location, frame)]
        if len(matches) == 1:
            return matches[0].zone_id, "matched"
        if len(matches) > 1:
            return None, "ambiguous"
        return None, "unmapped"

    @classmethod
    def default_warehouse_zones(cls) -> StationZoneMap:
        """Standard Phase 2 warehouse station zones."""
        return cls(
            zones=[
                StationZone.from_center(
                    zone_id="packing_station",
                    location="warehouse",
                    frame_of_reference="warehouse_map",
                    center_x_cm=80.0,
                    center_y_cm=350.0,
                    width_cm=60.0,
                    height_cm=60.0,
                ),
                StationZone.from_center(
                    zone_id="inspection_station",
                    location="warehouse",
                    frame_of_reference="warehouse_map",
                    center_x_cm=150.0,
                    center_y_cm=390.0,
                    width_cm=60.0,
                    height_cm=60.0,
                ),
            ]
        )


class StaticRoute(BaseModel):
    """Static configuration for a named cardinal route segment."""

    model_config = ConfigDict(extra="forbid")

    route_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    frame_of_reference: str = Field(min_length=1)
    start: Point
    end: Point
    corridor_width_cm: float = 30.0

    @property
    def heading(self) -> Direction:
        dx = self.end.x_cm - self.start.x_cm
        dy = self.end.y_cm - self.start.y_cm
        if dx == 0 and dy > 0:
            return "north"
        if dx == 0 and dy < 0:
            return "south"
        if dy == 0 and dx > 0:
            return "east"
        if dy == 0 and dx < 0:
            return "west"
        raise ValueError(f"Route {self.route_id} is not a cardinal segment")

    def matches_pose(self, pose: Pose, corridor_tolerance_cm: float | None = None) -> bool:
        """Check if pose is aligned along this route segment and facing its heading."""
        if pose.location != self.location or pose.frame_of_reference != self.frame_of_reference:
            return False
        if pose.direction != self.heading:
            return False

        tol = (
            corridor_tolerance_cm
            if corridor_tolerance_cm is not None
            else (self.corridor_width_cm / 2.0)
        )

        if self.heading in ("north", "south"):
            if abs(pose.x_cm - self.start.x_cm) > tol:
                return False
            min_y = min(self.start.y_cm, self.end.y_cm) - tol
            max_y = max(self.start.y_cm, self.end.y_cm) + tol
            return min_y <= pose.y_cm <= max_y

        if abs(pose.y_cm - self.start.y_cm) > tol:
            return False
        min_x = min(self.start.x_cm, self.end.x_cm) - tol
        max_x = max(self.start.x_cm, self.end.x_cm) + tol
        return min_x <= pose.x_cm <= max_x


class StaticRouteMap(BaseModel):
    """Static route network with deterministic pose matching."""

    model_config = ConfigDict(extra="forbid")

    routes: list[StaticRoute] = Field(default_factory=list)

    def match_route(
        self, pose: Pose, corridor_tolerance_cm: float | None = None
    ) -> tuple[StaticRoute | None, Literal["matched", "unmapped", "ambiguous"]]:
        """Match robot pose to a single route segment.

        Returns:
            (route, "matched") if exactly one route matches.
            (None, "ambiguous") if multiple routes match.
            (None, "unmapped") if no route matches.
        """
        matches = [r for r in self.routes if r.matches_pose(pose, corridor_tolerance_cm)]
        if len(matches) == 1:
            return matches[0], "matched"
        if len(matches) > 1:
            return None, "ambiguous"
        return None, "unmapped"

    @classmethod
    def default_warehouse_routes(cls) -> StaticRouteMap:
        """Standard Phase 2 warehouse routes."""
        return cls(
            routes=[
                StaticRoute(
                    route_id="route_A",
                    location="warehouse",
                    frame_of_reference="warehouse_map",
                    start=Point(x_cm=100.0, y_cm=100.0),
                    end=Point(x_cm=100.0, y_cm=450.0),
                ),
                StaticRoute(
                    route_id="route_B",
                    location="warehouse",
                    frame_of_reference="warehouse_map",
                    start=Point(x_cm=100.0, y_cm=450.0),
                    end=Point(x_cm=650.0, y_cm=450.0),
                ),
            ]
        )


# ---------------------------------------------------------------------------
# Ingestion Configuration
# ---------------------------------------------------------------------------


class IngestionConfig(BaseModel):
    """Deterministic policy thresholds and rules."""

    model_config = ConfigDict(extra="forbid")

    freshness_ms: int = Field(default=1_000, ge=0)
    position_tolerance_cm: float = Field(default=5.0, ge=0.0)
    camera_policy_rule: str = "camera_observation_revision"
    lidar_policy_rule: str = "lidar_observation_route_status"


# ---------------------------------------------------------------------------
# Output Summary Models
# ---------------------------------------------------------------------------


class EntityBeliefSummary(BaseModel):
    """Comprehensive epistemic status for a parcel or entity at a simulation time."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    run_id: UUID
    as_of_sim_time_ms: int

    supported_location: str | None = None
    active_fact_id: UUID | None = None
    predecessor_fact_id: UUID | None = None
    revision_event_id: UUID | None = None

    last_positive_sighting: StoredObservationSample | None = None
    latest_sensor_attempt: StoredObservationSample | None = None

    verification_status: Literal["verified", "last_seen", "unverified", "unavailable"]
    visibility: Literal["currently_visible", "not_currently_visible", "unknown"]
    freshness: Literal["fresh", "stale", "future_dated", "incompatible", "unverified"]
    age_ms: int | None = None

    last_observed_position: Point | None = None
    last_observed_frame: str | None = None

    uncertainty: bool
    uncertainty_reason: str | None = None


class RouteBeliefSummary(BaseModel):
    """Comprehensive epistemic status for a route at a simulation time."""

    model_config = ConfigDict(extra="forbid")

    route_id: str
    run_id: UUID
    as_of_sim_time_ms: int

    status: Literal["clear", "blocked"] | None = None
    active_fact_id: UUID | None = None
    predecessor_fact_id: UUID | None = None
    revision_event_id: UUID | None = None

    latest_lidar_attempt: StoredObservationSample | None = None
    last_positive_lidar_sample: StoredObservationSample | None = None
    nearest_distance_cm: float | None = None

    verification_status: Literal["verified", "last_seen", "unverified", "unavailable"]
    freshness: Literal["fresh", "stale", "future_dated", "incompatible", "unverified"]
    age_ms: int | None = None

    uncertainty: bool
    uncertainty_reason: str | None = None


class IngestionResult(BaseModel):
    """Structured report returned upon ingesting an observation sample."""

    model_config = ConfigDict(extra="forbid")

    sample_id: UUID
    run_id: UUID
    sim_time_ms: int
    capability: str

    status: Literal[
        "applied",
        "duplicate_ignored",
        "retained_as_historical",
        "unmapped",
        "ambiguous",
        "rejected",
        "sensor_unavailable",
        "no_detection",
    ]

    stored_sample: StoredObservationSample | None = None
    facts_created: list[StoredFact] = Field(default_factory=list)
    revisions_created: list[AuditEvent] = Field(default_factory=list)

    entity_summaries: dict[str, EntityBeliefSummary] = Field(default_factory=dict)
    route_summary: RouteBeliefSummary | None = None

    uncertainty: bool = False
    uncertainty_reason: str | None = None


# ---------------------------------------------------------------------------
# Temporal Belief Ingestion Service
# ---------------------------------------------------------------------------


class TemporalBeliefIngestionService:
    """Tier 2 temporal observation ingestion and evaluation service.

    Bridges raw sensor evidence to Tier 1 SQLite facts and auditable revisions.
    Operates strictly through protocol interfaces.
    """

    def __init__(
        self,
        fact_repository: FactRepositoryProtocol,
        observation_ledger: ObservationLedgerWriter | None = None,
        zone_map: StationZoneMap | None = None,
        route_map: StaticRouteMap | None = None,
        config: IngestionConfig | None = None,
    ) -> None:
        self._repo = fact_repository
        # If observation_ledger is not provided separately, fact_repository might implement it
        if observation_ledger is not None:
            self._ledger = observation_ledger
        elif isinstance(fact_repository, ObservationLedgerWriter):
            self._ledger = fact_repository
        else:
            self._ledger = fact_repository  # type: ignore[assignment]

        self._zone_map = zone_map or StationZoneMap.default_warehouse_zones()
        self._route_map = route_map or StaticRouteMap.default_warehouse_routes()
        self._config = config or IngestionConfig()
        self._lock = fact_repository.observation_ingestion_lock()
        self._latest_entity_sighting: dict[tuple[UUID, str], tuple[int, int]] = {}
        self._latest_route_sighting: dict[tuple[UUID, str], tuple[int, int]] = {}

    def ingest_sample(
        self,
        sample: ObservationSample,
        current_sim_time_ms: int | None = None,
    ) -> IngestionResult:
        """Duranly record an observation sample and evaluate belief revisions."""
        with self._lock:
            return self._locked_ingest_sample(sample, current_sim_time_ms)

    def _locked_ingest_sample(
        self,
        sample: ObservationSample,
        current_sim_time_ms: int | None = None,
    ) -> IngestionResult:
        # 0. Check future-dated sample if current_sim_time_ms is provided
        sim_time_now = (
            current_sim_time_ms if current_sim_time_ms is not None else sample.sim_time_ms
        )
        if sample.sim_time_ms > sim_time_now:
            return IngestionResult(
                sample_id=sample.sample_id,
                run_id=sample.run_id,
                sim_time_ms=sample.sim_time_ms,
                capability=sample.request.capability,
                status="rejected",
                uncertainty=True,
                uncertainty_reason=(
                    "Sample capture time is future-dated relative to current simulation time."
                ),
            )

        # 1. Persist the raw sample in Tier 1 Observation Ledger
        prior_sample: StoredObservationSample | None = None
        if hasattr(self._ledger, "get_observation_sample"):
            prior_sample = self._ledger.get_observation_sample(sample.sample_id)

        try:
            stored_sample = self._ledger.record_observation_sample(sample)
        except Exception as exc:
            return IngestionResult(
                sample_id=sample.sample_id,
                run_id=sample.run_id,
                sim_time_ms=sample.sim_time_ms,
                capability=sample.request.capability,
                status="rejected",
                uncertainty=True,
                uncertainty_reason=f"Failed to persist observation sample: {exc}",
            )

        # 2. Handle Sensor Failure
        if isinstance(sample.result, ObservationUnavailable):
            return self._handle_unavailable_sample(sample, stored_sample)

        # 3. Handle Successful Sensor Observation
        capability = sample.request.capability
        if capability == "camera_detect":
            return self._handle_camera_sample(
                sample, stored_sample, prior_sample_existed=(prior_sample is not None)
            )
        if capability == "lidar_scan":
            return self._handle_lidar_sample(
                sample, stored_sample, prior_sample_existed=(prior_sample is not None)
            )

        # Other capabilities (e.g. ambient_light, robot_pose) do not directly
        # produce location/route facts.
        return IngestionResult(
            sample_id=sample.sample_id,
            run_id=sample.run_id,
            sim_time_ms=sample.sim_time_ms,
            capability=capability,
            status="duplicate_ignored" if prior_sample is not None else "applied",
            stored_sample=stored_sample,
            uncertainty=False,
        )

    # ------------------------------------------------------------------
    # Internal Handlers
    # ------------------------------------------------------------------

    def _handle_unavailable_sample(
        self, sample: ObservationSample, stored_sample: StoredObservationSample
    ) -> IngestionResult:
        result: ObservationUnavailable = sample.result  # type: ignore[assignment]
        entity_summaries: dict[str, EntityBeliefSummary] = {}

        if sample.request.capability == "camera_detect" and sample.request.target is not None:
            # Targeted failure applies only to targeted entity
            target = sample.request.target
            entity_summaries[target] = self.get_entity_belief_summary(
                run_id=sample.run_id,
                entity_id=target,
                current_sim_time_ms=sample.sim_time_ms,
            )

        return IngestionResult(
            sample_id=sample.sample_id,
            run_id=sample.run_id,
            sim_time_ms=sample.sim_time_ms,
            capability=sample.request.capability,
            status="sensor_unavailable",
            stored_sample=stored_sample,
            entity_summaries=entity_summaries,
            uncertainty=True,
            uncertainty_reason=f"Observation unavailable: {result.reason} - {result.message}",
        )

    def _handle_camera_sample(
        self,
        sample: ObservationSample,
        stored_sample: StoredObservationSample,
        prior_sample_existed: bool = False,
    ) -> IngestionResult:
        assert isinstance(sample.result, SensorObservation)
        scan = CameraScan.model_validate(sample.result.measurements)
        pose = sample.observer_pose

        facts_created: list[StoredFact] = []
        revisions_created: list[AuditEvent] = []
        entity_summaries: dict[str, EntityBeliefSummary] = {}
        detected_entity_ids: set[str] = set()
        write_errors: list[str] = []
        newly_written_facts: list[StoredFact] = []
        overall_status: Literal[
            "applied",
            "duplicate_ignored",
            "retained_as_historical",
            "unmapped",
            "ambiguous",
            "rejected",
            "sensor_unavailable",
            "no_detection",
        ] = "applied"
        uncertainty = False
        uncertainty_reasons: list[str] = []

        existing_facts: list[StoredFact] = []
        if hasattr(self._repo, "get_facts_by_sample_id"):
            existing_facts = self._repo.get_facts_by_sample_id(sample.sample_id)
        existing_by_subject = {f.subject: f for f in existing_facts}

        if not scan.objects:
            overall_status = "duplicate_ignored" if prior_sample_existed else "no_detection"

        has_matched = False
        has_ambiguous = False
        has_unmapped = False

        # A. Process detections
        for det in scan.objects:
            detected_entity_ids.add(det.object_id)

            if det.object_id in existing_by_subject:
                has_matched = True
                continue

            zone_id, zone_status = self._zone_map.resolve_zone(
                point=det.position,
                location=pose.location,
                frame=det.frame_of_reference,
            )

            if zone_status == "unmapped":
                has_unmapped = True
                uncertainty = True
                uncertainty_reasons.append(
                    f"Entity {det.object_id} at ({det.position.x_cm}, {det.position.y_cm}) "
                    "is outside all known station zones."
                )
                continue

            if zone_status == "ambiguous":
                has_ambiguous = True
                uncertainty = True
                uncertainty_reasons.append(
                    f"Entity {det.object_id} at ({det.position.x_cm}, {det.position.y_cm}) "
                    "overlaps multiple station zones."
                )
                continue

            has_matched = True
            assert zone_id is not None

            # Retrieve active located_at belief scoped to run, location, and frame
            active_facts = self._repo.query_facts(
                FactQuery(
                    subject=det.object_id,
                    predicate="located_at",
                    active_only=True,
                    context=SpatialContext(
                        location=pose.location,
                        frame_of_reference=det.frame_of_reference,
                        extra_context={"run_id": str(sample.run_id)},
                    ),
                )
            )
            active_facts = [
                f
                for f in active_facts
                if f.context.location == pose.location
                and (
                    f.context.extra_context.get("run_id") == str(sample.run_id)
                    or f.evidence.get("run_id") == str(sample.run_id)
                )
            ]

            # Determine latest applicable acquisition timestamp/sequence
            latest_applicable: tuple[int, int] | None = self._latest_entity_sighting.get(
                (sample.run_id, det.object_id)
            )
            if hasattr(self._ledger, "get_last_sighting"):
                ledger_sighting = self._ledger.get_last_sighting(
                    run_id=sample.run_id,
                    entity_id=det.object_id,
                    capability="camera_detect",
                )
                if ledger_sighting is not None and ledger_sighting.sample_id != sample.sample_id:
                    ledger_tuple = (
                        ledger_sighting.sim_time_ms,
                        ledger_sighting.sample_sequence,
                    )
                    if latest_applicable is None or ledger_tuple > latest_applicable:
                        latest_applicable = ledger_tuple

            if active_facts:
                f_sim_time = active_facts[0].evidence.get("sim_time_ms")
                f_seq = active_facts[0].evidence.get("sample_sequence")
                if isinstance(f_sim_time, int) and isinstance(f_seq, int):
                    fact_tuple = (f_sim_time, f_seq)
                    if latest_applicable is None or fact_tuple > latest_applicable:
                        latest_applicable = fact_tuple

            current_tuple = (sample.sim_time_ms, sample.sample_sequence)
            if latest_applicable is not None and current_tuple < latest_applicable:
                overall_status = "retained_as_historical"
                continue

            current_active = active_facts[0] if active_facts else None
            if current_active is not None and current_active.object == zone_id:
                # Sighting in the same zone: refresh metadata without fact/audit inflation
                self._latest_entity_sighting[(sample.run_id, det.object_id)] = current_tuple
                continue

            # Need to record fact (initial or revision)
            try:
                if current_active is not None:
                    # Transition to a new unambiguous zone: record atomic belief revision
                    successor, audit_event = self._revise_entity_location(
                        old_fact=current_active,
                        sample=sample,
                        detection=det,
                        new_zone=zone_id,
                    )
                    facts_created.append(successor)
                    revisions_created.append(audit_event)
                    newly_written_facts.append(successor)
                else:
                    # First sighting: record initial fact
                    new_fact = self._record_initial_entity_location(
                        sample=sample,
                        detection=det,
                        zone_id=zone_id,
                    )
                    facts_created.append(new_fact)
                    newly_written_facts.append(new_fact)

                self._latest_entity_sighting[(sample.run_id, det.object_id)] = current_tuple
            except Exception as exc:
                write_errors.append(f"Failed to record fact for entity {det.object_id}: {exc}")

        if write_errors:
            overall_status = "rejected"
            uncertainty = True
            uncertainty_reasons.extend(write_errors)
        elif prior_sample_existed and not newly_written_facts:
            overall_status = "duplicate_ignored"
            facts_created = []
            revisions_created = []
        elif not scan.objects:
            overall_status = "no_detection"
        elif has_ambiguous and not has_matched:
            overall_status = "ambiguous"
        elif has_unmapped and not has_matched:
            overall_status = "unmapped"
        elif overall_status == "retained_as_historical":
            overall_status = "retained_as_historical"
        else:
            overall_status = "applied"

        # B. Handle Broad Scan Non-Detection (Omission)
        # Only successful broad scans (target is None) without write errors provide
        # negative visibility evidence
        if overall_status != "rejected" and sample.request.target is None:
            active_located_facts = self._repo.query_facts(
                FactQuery(
                    predicate="located_at",
                    active_only=True,
                    context=SpatialContext(
                        location=pose.location,
                        extra_context={"run_id": str(sample.run_id)},
                    ),
                )
            )
            active_located_facts = [
                f
                for f in active_located_facts
                if f.context.location == pose.location
                and (
                    f.context.extra_context.get("run_id") == str(sample.run_id)
                    or f.evidence.get("run_id") == str(sample.run_id)
                )
            ]
            for fact in active_located_facts:
                if (
                    fact.subject not in detected_entity_ids
                    and fact.context.location == pose.location
                ):
                    # Entity omitted from healthy broad scan: mark not currently visible / last seen
                    entity_summaries[fact.subject] = self.get_entity_belief_summary(
                        run_id=sample.run_id,
                        entity_id=fact.subject,
                        current_sim_time_ms=sample.sim_time_ms,
                    )

        # Generate summaries for all detected entities
        for entity_id in detected_entity_ids:
            entity_summaries[entity_id] = self.get_entity_belief_summary(
                run_id=sample.run_id,
                entity_id=entity_id,
                current_sim_time_ms=sample.sim_time_ms,
            )

        return IngestionResult(
            sample_id=sample.sample_id,
            run_id=sample.run_id,
            sim_time_ms=sample.sim_time_ms,
            capability=sample.request.capability,
            status=overall_status,
            stored_sample=stored_sample,
            facts_created=facts_created,
            revisions_created=revisions_created,
            entity_summaries=entity_summaries,
            uncertainty=uncertainty,
            uncertainty_reason="; ".join(uncertainty_reasons) if uncertainty_reasons else None,
        )

    def _record_initial_entity_location(
        self,
        sample: ObservationSample,
        detection: CameraDetection,
        zone_id: str,
    ) -> StoredFact:
        extra_ctx = {"run_id": str(sample.run_id)}
        if isinstance(sample.result, SensorObservation):
            extra_ctx.update(sample.result.context.extra_context)

        assertion = FactAssertion(
            subject=detection.object_id,
            predicate="located_at",
            object=zone_id,
            source_agent=sample.sensor_id,
            confidence_score=1.0,
            observed_at=sample.captured_at,
            context=SpatialContext(
                location=sample.observer_pose.location,
                frame_of_reference=detection.frame_of_reference,
                world_version=sample.world_version,
                observer=sample.observer_pose.robot_id,
                extra_context=extra_ctx,
            ),
            evidence={
                "sample_id": str(sample.sample_id),
                "run_id": str(sample.run_id),
                "sim_time_ms": sample.sim_time_ms,
                "sample_sequence": sample.sample_sequence,
                "sensor_id": sample.sensor_id,
                "apparent_color": detection.apparent_color,
                "x_cm": detection.position.x_cm,
                "y_cm": detection.position.y_cm,
                "frame": detection.frame_of_reference,
                "policy_rule": "camera_observation_initial_sighting",
            },
        )
        return self._repo.record_fact(assertion)

    def _revise_entity_location(
        self,
        old_fact: StoredFact,
        sample: ObservationSample,
        detection: CameraDetection,
        new_zone: str,
    ) -> tuple[StoredFact, AuditEvent]:
        extra_ctx = {"run_id": str(sample.run_id)}
        if isinstance(sample.result, SensorObservation):
            extra_ctx.update(sample.result.context.extra_context)

        replacement = FactAssertion(
            subject=detection.object_id,
            predicate="located_at",
            object=new_zone,
            source_agent=sample.sensor_id,
            confidence_score=1.0,
            observed_at=sample.captured_at,
            context=SpatialContext(
                location=sample.observer_pose.location,
                frame_of_reference=detection.frame_of_reference,
                world_version=sample.world_version,
                observer=sample.observer_pose.robot_id,
                extra_context=extra_ctx,
            ),
            evidence={
                "sample_id": str(sample.sample_id),
                "run_id": str(sample.run_id),
                "sim_time_ms": sample.sim_time_ms,
                "sample_sequence": sample.sample_sequence,
                "sensor_id": sample.sensor_id,
                "apparent_color": detection.apparent_color,
                "x_cm": detection.position.x_cm,
                "y_cm": detection.position.y_cm,
                "frame": detection.frame_of_reference,
                "policy_rule": self._config.camera_policy_rule,
            },
        )
        reason = (
            f"Observed {detection.object_id} at "
            f"({detection.position.x_cm}, {detection.position.y_cm}) in {new_zone}, "
            f"revising previous location {old_fact.object}"
        )
        outcome = self._repo.record_revision(
            old_fact_id=old_fact.fact_id,
            replacement=replacement,
            reason=reason,
            policy_rule=self._config.camera_policy_rule,
            revised_at=sample.captured_at,
        )
        return outcome.successor, outcome.audit_event

    def _handle_lidar_sample(
        self,
        sample: ObservationSample,
        stored_sample: StoredObservationSample,
        prior_sample_existed: bool = False,
    ) -> IngestionResult:
        assert isinstance(sample.result, SensorObservation)
        measurements = LidarMeasurement.model_validate(sample.result.measurements)
        pose = sample.observer_pose

        route, match_status = self._route_map.match_route(pose)
        if match_status != "matched" or route is None:
            return IngestionResult(
                sample_id=sample.sample_id,
                run_id=sample.run_id,
                sim_time_ms=sample.sim_time_ms,
                capability=sample.request.capability,
                status=match_status,
                stored_sample=stored_sample,
                uncertainty=True,
                uncertainty_reason=(
                    f"Front LiDAR pose at ({pose.x_cm}, {pose.y_cm}) facing {pose.direction} "
                    f"cannot be mapped unambiguously to a static route ({match_status})."
                ),
            )

        route_id = route.route_id
        observed_status = measurements.status
        facts_created: list[StoredFact] = []
        revisions_created: list[AuditEvent] = []

        # Check if already recorded facts for this sample_id
        existing_facts: list[StoredFact] = []
        if hasattr(self._repo, "get_facts_by_sample_id"):
            existing_facts = self._repo.get_facts_by_sample_id(sample.sample_id)

        if existing_facts:
            return IngestionResult(
                sample_id=sample.sample_id,
                run_id=sample.run_id,
                sim_time_ms=sample.sim_time_ms,
                capability=sample.request.capability,
                status="duplicate_ignored",
                stored_sample=stored_sample,
                facts_created=existing_facts,
                revisions_created=[],
                route_summary=self.get_route_belief_summary(
                    run_id=sample.run_id,
                    route_id=route_id,
                    current_sim_time_ms=sample.sim_time_ms,
                ),
                uncertainty=False,
            )

        # Scoped query for active route facts
        active_route_facts = self._repo.query_facts(
            FactQuery(
                subject=route_id,
                predicate="status_is",
                active_only=True,
                context=SpatialContext(
                    location=pose.location,
                    extra_context={"run_id": str(sample.run_id)},
                ),
            )
        )
        active_route_facts = [
            f
            for f in active_route_facts
            if f.context.location == pose.location
            and (
                f.context.extra_context.get("run_id") == str(sample.run_id)
                or f.evidence.get("run_id") == str(sample.run_id)
            )
        ]

        # Determine latest applicable LiDAR acquisition for this route
        latest_applicable: tuple[int, int] | None = self._latest_route_sighting.get(
            (sample.run_id, route_id)
        )
        if hasattr(self._ledger, "query_observations"):
            try:
                lidar_samples = self._ledger.query_observations(
                    TemporalObservationQuery(
                        run_id=sample.run_id,
                        mode="samples",
                        capability="lidar_scan",
                        limit=1000,
                    )
                )
                for s in lidar_samples:
                    if s.sample_id == sample.sample_id:
                        continue
                    r_match, _ = self._route_map.match_route(s.observer_pose)
                    if r_match is not None and r_match.route_id == route_id:
                        s_tuple = (s.sim_time_ms, s.sample_sequence)
                        if latest_applicable is None or s_tuple > latest_applicable:
                            latest_applicable = s_tuple
            except Exception:
                pass

        if active_route_facts:
            f_sim_time = active_route_facts[0].evidence.get("sim_time_ms")
            f_seq = active_route_facts[0].evidence.get("sample_sequence")
            if isinstance(f_sim_time, int) and isinstance(f_seq, int):
                f_tuple = (f_sim_time, f_seq)
                if latest_applicable is None or f_tuple > latest_applicable:
                    latest_applicable = f_tuple

        current_tuple = (sample.sim_time_ms, sample.sample_sequence)
        if latest_applicable is not None and current_tuple < latest_applicable:
            return IngestionResult(
                sample_id=sample.sample_id,
                run_id=sample.run_id,
                sim_time_ms=sample.sim_time_ms,
                capability=sample.request.capability,
                status="retained_as_historical",
                stored_sample=stored_sample,
                route_summary=self.get_route_belief_summary(
                    run_id=sample.run_id,
                    route_id=route_id,
                    current_sim_time_ms=sample.sim_time_ms,
                ),
                uncertainty=False,
            )

        if active_route_facts:
            current_active = active_route_facts[0]
            if current_active.object == observed_status:
                self._latest_route_sighting[(sample.run_id, route_id)] = current_tuple
                status_res = "duplicate_ignored" if prior_sample_existed else "applied"
                return IngestionResult(
                    sample_id=sample.sample_id,
                    run_id=sample.run_id,
                    sim_time_ms=sample.sim_time_ms,
                    capability=sample.request.capability,
                    status=status_res,
                    stored_sample=stored_sample,
                    facts_created=[],
                    revisions_created=[],
                    route_summary=self.get_route_belief_summary(
                        run_id=sample.run_id,
                        route_id=route_id,
                        current_sim_time_ms=sample.sim_time_ms,
                    ),
                    uncertainty=False,
                )

            # Revision: clear -> blocked or blocked -> clear
            replacement = FactAssertion(
                subject=route_id,
                predicate="status_is",
                object=observed_status,
                source_agent=sample.sensor_id,
                confidence_score=1.0,
                observed_at=sample.captured_at,
                context=SpatialContext(
                    location=pose.location,
                    frame_of_reference=pose.frame_of_reference,
                    world_version=sample.world_version,
                    observer=pose.robot_id,
                    extra_context={"run_id": str(sample.run_id)},
                ),
                evidence={
                    "sample_id": str(sample.sample_id),
                    "run_id": str(sample.run_id),
                    "sim_time_ms": sample.sim_time_ms,
                    "sample_sequence": sample.sample_sequence,
                    "sensor_id": sample.sensor_id,
                    "status": observed_status,
                    "nearest_distance_cm": measurements.nearest_distance_cm,
                    "pose": pose.model_dump(mode="json"),
                    "policy_rule": self._config.lidar_policy_rule,
                },
            )
            reason = (
                f"Front LiDAR at sim_time {sample.sim_time_ms}ms observed {route_id} "
                f"is {observed_status} "
                f"(nearest obstacle: {measurements.nearest_distance_cm} cm)"
            )
            try:
                outcome = self._repo.record_revision(
                    old_fact_id=current_active.fact_id,
                    replacement=replacement,
                    reason=reason,
                    policy_rule=self._config.lidar_policy_rule,
                    revised_at=sample.captured_at,
                )
                facts_created.append(outcome.successor)
                revisions_created.append(outcome.audit_event)
                self._latest_route_sighting[(sample.run_id, route_id)] = current_tuple
            except Exception as exc:
                return IngestionResult(
                    sample_id=sample.sample_id,
                    run_id=sample.run_id,
                    sim_time_ms=sample.sim_time_ms,
                    capability=sample.request.capability,
                    status="rejected",
                    stored_sample=stored_sample,
                    uncertainty=True,
                    uncertainty_reason=f"Failed to record route belief revision: {exc}",
                )
        else:
            # Initial route belief
            assertion = FactAssertion(
                subject=route_id,
                predicate="status_is",
                object=observed_status,
                source_agent=sample.sensor_id,
                confidence_score=1.0,
                observed_at=sample.captured_at,
                context=SpatialContext(
                    location=pose.location,
                    frame_of_reference=pose.frame_of_reference,
                    world_version=sample.world_version,
                    observer=pose.robot_id,
                    extra_context={"run_id": str(sample.run_id)},
                ),
                evidence={
                    "sample_id": str(sample.sample_id),
                    "run_id": str(sample.run_id),
                    "sim_time_ms": sample.sim_time_ms,
                    "sample_sequence": sample.sample_sequence,
                    "sensor_id": sample.sensor_id,
                    "status": observed_status,
                    "nearest_distance_cm": measurements.nearest_distance_cm,
                    "pose": pose.model_dump(mode="json"),
                    "policy_rule": "lidar_observation_initial_status",
                },
            )
            try:
                new_fact = self._repo.record_fact(assertion)
                facts_created.append(new_fact)
                self._latest_route_sighting[(sample.run_id, route_id)] = current_tuple
            except Exception as exc:
                return IngestionResult(
                    sample_id=sample.sample_id,
                    run_id=sample.run_id,
                    sim_time_ms=sample.sim_time_ms,
                    capability=sample.request.capability,
                    status="rejected",
                    stored_sample=stored_sample,
                    uncertainty=True,
                    uncertainty_reason=f"Failed to record initial route belief: {exc}",
                )

        route_summary = self.get_route_belief_summary(
            run_id=sample.run_id,
            route_id=route_id,
            current_sim_time_ms=sample.sim_time_ms,
        )

        return IngestionResult(
            sample_id=sample.sample_id,
            run_id=sample.run_id,
            sim_time_ms=sample.sim_time_ms,
            capability=sample.request.capability,
            status="applied",
            stored_sample=stored_sample,
            facts_created=facts_created,
            revisions_created=revisions_created,
            route_summary=route_summary,
            uncertainty=False,
        )

    # ------------------------------------------------------------------
    # Query / Summary APIs
    # ------------------------------------------------------------------

    def get_entity_belief_summary(
        self,
        run_id: UUID,
        entity_id: str,
        current_sim_time_ms: int,
    ) -> EntityBeliefSummary:
        """Evaluate the time-aware epistemic status of an entity as-of current_sim_time_ms."""
        with self._lock:
            return self._locked_get_entity_belief_summary(run_id, entity_id, current_sim_time_ms)

    def _locked_get_entity_belief_summary(
        self,
        run_id: UUID,
        entity_id: str,
        current_sim_time_ms: int,
    ) -> EntityBeliefSummary:
        # 1. Query active fact for this entity's location scoped to run_id
        active_facts = self._repo.query_facts(
            FactQuery(
                subject=entity_id,
                predicate="located_at",
                active_only=True,
                context=SpatialContext(extra_context={"run_id": str(run_id)}),
            )
        )
        active_facts = [
            f
            for f in active_facts
            if (
                f.context.extra_context.get("run_id") == str(run_id)
                or f.evidence.get("run_id") == str(run_id)
            )
        ]
        active_fact = active_facts[0] if active_facts else None

        # 2. Query observation ledger if reader protocol is available
        last_sighting: StoredObservationSample | None = None
        latest_attempt: StoredObservationSample | None = None

        if hasattr(self._ledger, "get_last_sighting") and hasattr(
            self._ledger, "get_latest_sample"
        ):
            last_sighting = self._ledger.get_last_sighting(
                run_id=run_id,
                entity_id=entity_id,
                capability="camera_detect",
                at_sim_time_ms=current_sim_time_ms,
            )
            # Check if there is a future-dated sighting in this run
            if last_sighting is None:
                future_sighting = self._ledger.get_last_sighting(
                    run_id=run_id,
                    entity_id=entity_id,
                    capability="camera_detect",
                    at_sim_time_ms=None,
                )
                if (
                    future_sighting is not None
                    and future_sighting.sim_time_ms > current_sim_time_ms
                ):
                    last_sighting = future_sighting

        if last_sighting is not None:
            latest_attempt = self._ledger.get_latest_camera_attempt(
                run_id, entity_id, last_sighting.observer_pose, current_sim_time_ms
            )

        # 3. Determine location and coordinates
        supported_location: str | None = None
        last_pos: Point | None = None
        last_frame: str | None = None

        if active_fact is not None:
            supported_location = active_fact.object
            if "x_cm" in active_fact.evidence and "y_cm" in active_fact.evidence:
                last_pos = Point(
                    x_cm=float(active_fact.evidence["x_cm"]),  # type: ignore[arg-type]
                    y_cm=float(active_fact.evidence["y_cm"]),  # type: ignore[arg-type]
                )
                last_frame = str(
                    active_fact.evidence.get("frame", active_fact.context.frame_of_reference or "")
                )

        if last_sighting is not None and isinstance(last_sighting.result, SensorObservation):
            scan = CameraScan.model_validate(last_sighting.result.measurements)
            for det in scan.objects:
                if det.object_id == entity_id:
                    last_pos = det.position
                    last_frame = det.frame_of_reference
                    break

        # 4. Epistemic Freshness and Age
        freshness: Literal["fresh", "stale", "future_dated", "incompatible", "unverified"]
        age_ms: int | None = None
        uncertainty = False
        uncertainty_reason: str | None = None

        if last_sighting is None:
            if active_fact is not None:
                # Historical fact without ledger sample in this run
                freshness = "unverified"
                verification_status: Literal[
                    "verified", "last_seen", "unverified", "unavailable"
                ] = "last_seen"
                visibility: Literal["currently_visible", "not_currently_visible", "unknown"] = (
                    "unknown"
                )
                uncertainty = True
                uncertainty_reason = (
                    f"Entity {entity_id} has historical belief "
                    "but no positive sighting in this run."
                )
            else:
                freshness = "unverified"
                verification_status = "unverified"
                visibility = "unknown"
                uncertainty = True
                uncertainty_reason = f"No sighting or belief found for entity {entity_id}."
        else:
            diff_ms = current_sim_time_ms - last_sighting.sim_time_ms
            if diff_ms < 0:
                freshness = "future_dated"
                age_ms = 0  # Spec: Never assign negative age
                verification_status = "unverified"
                visibility = "unknown"
                uncertainty = True
                uncertainty_reason = (
                    "Query simulation time is earlier than the sighting capture time."
                )
            else:
                age_ms = diff_ms
                if age_ms <= self._config.freshness_ms:
                    freshness = "fresh"
                else:
                    freshness = "stale"

                # Check latest camera attempt
                if latest_attempt is not None and (
                    latest_attempt.sim_time_ms,
                    latest_attempt.sample_sequence,
                ) > (last_sighting.sim_time_ms, last_sighting.sample_sequence):
                    if isinstance(latest_attempt.result, ObservationUnavailable):
                        verification_status = "unavailable"
                        visibility = "unknown"
                        uncertainty = True
                        uncertainty_reason = (
                            f"Camera unavailable at {latest_attempt.sim_time_ms} ms."
                        )
                    elif latest_attempt.request.target is None:
                        # Successful broad scan that lacked entity
                        verification_status = "last_seen"
                        visibility = "not_currently_visible"
                        uncertainty = True
                        uncertainty_reason = (
                            f"Entity {entity_id} not detected in broad scan at "
                            f"{latest_attempt.sim_time_ms} ms; last seen at "
                            f"{supported_location} ({last_sighting.sim_time_ms} ms)."
                        )
                    else:
                        # Targeted attempt for something else
                        if freshness == "fresh":
                            verification_status = "verified"
                            visibility = "currently_visible"
                        else:
                            verification_status = "last_seen"
                            visibility = "not_currently_visible"
                            uncertainty = True
                            uncertainty_reason = (
                                f"Sighting is stale (age: {age_ms} ms > "
                                f"{self._config.freshness_ms} ms)."
                            )
                else:
                    if freshness == "fresh":
                        verification_status = "verified"
                        visibility = "currently_visible"
                    else:
                        verification_status = "last_seen"
                        visibility = "not_currently_visible"
                        uncertainty = True
                        uncertainty_reason = (
                            f"Sighting is stale (age: {age_ms} ms > "
                            f"{self._config.freshness_ms} ms)."
                        )

        return EntityBeliefSummary(
            entity_id=entity_id,
            run_id=run_id,
            as_of_sim_time_ms=current_sim_time_ms,
            supported_location=supported_location,
            active_fact_id=active_fact.fact_id if active_fact else None,
            predecessor_fact_id=None,
            revision_event_id=None,
            last_positive_sighting=last_sighting,
            latest_sensor_attempt=latest_attempt,
            verification_status=verification_status,
            visibility=visibility,
            freshness=freshness,
            age_ms=age_ms,
            last_observed_position=last_pos,
            last_observed_frame=last_frame,
            uncertainty=uncertainty,
            uncertainty_reason=uncertainty_reason,
        )

    def get_route_belief_summary(
        self,
        run_id: UUID,
        route_id: str,
        current_sim_time_ms: int,
    ) -> RouteBeliefSummary:
        """Evaluate the time-aware epistemic status of a route as-of current_sim_time_ms."""
        with self._lock:
            return self._locked_get_route_belief_summary(run_id, route_id, current_sim_time_ms)

    def _locked_get_route_belief_summary(
        self,
        run_id: UUID,
        route_id: str,
        current_sim_time_ms: int,
    ) -> RouteBeliefSummary:
        active_facts = self._repo.query_facts(
            FactQuery(
                subject=route_id,
                predicate="status_is",
                active_only=True,
                context=SpatialContext(extra_context={"run_id": str(run_id)}),
            )
        )
        active_facts = [
            f
            for f in active_facts
            if (
                f.context.extra_context.get("run_id") == str(run_id)
                or f.evidence.get("run_id") == str(run_id)
            )
        ]
        active_fact = active_facts[0] if active_facts else None

        latest_lidar: StoredObservationSample | None = None
        if hasattr(self._ledger, "query_observations"):
            try:
                candidates = self._ledger.query_observations(
                    TemporalObservationQuery(
                        run_id=run_id,
                        mode="samples",
                        capability="lidar_scan",
                        end_ms=current_sim_time_ms,
                        limit=1000,
                    )
                )
                route_matched = []
                for s in candidates:
                    r_match, _ = self._route_map.match_route(s.observer_pose)
                    if r_match is not None and r_match.route_id == route_id:
                        route_matched.append(s)
                if route_matched:
                    latest_lidar = route_matched[-1]
            except Exception:
                pass

        if latest_lidar is None and hasattr(self._ledger, "get_latest_sample"):
            latest_lidar = self._ledger.get_latest_sample(
                run_id=run_id,
                capability="lidar_scan",
                at_sim_time_ms=current_sim_time_ms,
            )
            if latest_lidar is None:
                future_lidar = self._ledger.get_latest_sample(
                    run_id=run_id,
                    capability="lidar_scan",
                    at_sim_time_ms=None,
                )
                if future_lidar is not None and future_lidar.sim_time_ms > current_sim_time_ms:
                    latest_lidar = future_lidar

        status_val: Literal["clear", "blocked"] | None = None
        if active_fact and active_fact.object in ("clear", "blocked"):
            status_val = active_fact.object  # type: ignore[assignment]

        nearest_dist: float | None = None
        if active_fact and "nearest_distance_cm" in active_fact.evidence:
            val = active_fact.evidence["nearest_distance_cm"]
            if isinstance(val, (int, float)):
                nearest_dist = float(val)

        freshness: Literal["fresh", "stale", "future_dated", "incompatible", "unverified"]
        verification_status: Literal["verified", "last_seen", "unverified", "unavailable"]
        age_ms: int | None = None
        uncertainty = False
        uncertainty_reason: str | None = None

        if latest_lidar is None:
            freshness = "unverified"
            verification_status = "unverified"
            uncertainty = True
            uncertainty_reason = f"No LiDAR sample recorded for route {route_id}."
        elif isinstance(latest_lidar.result, ObservationUnavailable):
            freshness = "unverified"
            verification_status = "unavailable"
            uncertainty = True
            uncertainty_reason = f"LiDAR unavailable at {latest_lidar.sim_time_ms} ms."
        else:
            diff_ms = current_sim_time_ms - latest_lidar.sim_time_ms
            if diff_ms < 0:
                freshness = "future_dated"
                age_ms = 0
                verification_status = "unverified"
                uncertainty = True
                uncertainty_reason = "Query simulation time is earlier than the LiDAR capture time."
            else:
                age_ms = diff_ms
                if age_ms <= self._config.freshness_ms:
                    freshness = "fresh"
                    verification_status = "verified"
                else:
                    freshness = "stale"
                    verification_status = "last_seen"
                    uncertainty = True
                    uncertainty_reason = (
                        f"LiDAR status is stale (age: {age_ms} ms > "
                        f"{self._config.freshness_ms} ms)."
                    )

        return RouteBeliefSummary(
            route_id=route_id,
            run_id=run_id,
            as_of_sim_time_ms=current_sim_time_ms,
            status=status_val,
            active_fact_id=active_fact.fact_id if active_fact else None,
            predecessor_fact_id=None,
            revision_event_id=None,
            latest_lidar_attempt=latest_lidar,
            last_positive_lidar_sample=latest_lidar,
            nearest_distance_cm=nearest_dist,
            verification_status=verification_status,
            freshness=freshness,
            age_ms=age_ms,
            uncertainty=uncertainty,
            uncertainty_reason=uncertainty_reason,
        )
