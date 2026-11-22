"""Manually pumped warehouse runtime; caller serializes all access.

This module does not start threads or manage sessions and persistence.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from src.contracts.models import ObservationRequest, ObservationUnavailable, SensorObservation
from src.contracts.simulation import (
    LightingEvent,
    MoveEvent,
    ObservationSample,
    Point,
    Rectangle,
    ScenarioDefinition,
    ScenarioEvent,
    SensorAvailabilityEvent,
    SimulationSnapshot,
)
from src.contracts.simulation_api import (
    MAX_RECENT_BELIEF_CHANGES,
    PatrolDecisionSummary,
    RelocateParcel,
    SetCart,
    SetLighting,
    SimulationBeliefChange,
    SimulationDisruptRequest,
)
from src.procedural.temporal_belief_ingestion import (
    IngestionResult,
    StaticRoute,
    StaticRouteMap,
    StationZone,
    StationZoneMap,
)
from src.sensorimotor.patrol_controller import (
    PatrolCheckpoint,
    PatrolController,
    PatrolDecisionRecord,
)
from src.sensorimotor.simulation_engine import SimulationEngine
from src.sensorimotor.warehouse_sensors import WarehouseSensors
from src.web.simulation_clock import SimulationClock

MAX_PRESENTER_INPUTS = 64
EVENT = TypeAdapter(ScenarioEvent)


def scenario_route_map(scenario: ScenarioDefinition) -> StaticRouteMap:
    """Project declared route geometry into the observation matching rules."""
    return StaticRouteMap(
        routes=[
            StaticRoute(
                route_id=route.route_id,
                location=route.location,
                frame_of_reference=scenario.robot_pose.frame_of_reference,
                start=route.start,
                end=route.end,
            )
            for route in scenario.routes
        ]
    )


def scenario_station_map(scenario: ScenarioDefinition) -> StationZoneMap:
    """Project declared station geometry into the zone matching rules."""
    return StationZoneMap(
        zones=[
            StationZone.from_center(
                zone_id=entity.entity_id,
                location=entity.location,
                frame_of_reference=scenario.robot_pose.frame_of_reference,
                center_x_cm=entity.footprint.x_cm,
                center_y_cm=entity.footprint.y_cm,
                width_cm=entity.footprint.width_cm,
                height_cm=entity.footprint.height_cm,
            )
            for entity in scenario.entities
            if entity.kind == "station"
        ]
    )


def scenario_shelf_footprints(scenario: ScenarioDefinition) -> list[Rectangle]:
    """Extract applicable opaque static shelf footprints matching the observer's location."""
    observer_location = scenario.robot_pose.location
    return [
        entity.footprint
        for entity in scenario.entities
        if entity.opaque
        and entity.location == observer_location
        and (entity.kind == "shelf" or entity.kind not in ("station", "parcel"))
    ]


@dataclass(frozen=True)
class ScenarioObservationContext:
    """Run-scoped observation and assessment configuration derived from scenario definition."""

    scenario_id: str
    zone_map: StationZoneMap
    route_map: StaticRouteMap
    camera_range_cm: float
    camera_fov_deg: float
    freshness_ms: int
    observer_robot_id: str
    observer_location: str
    observer_frame: str
    shelves: list[Rectangle]


def scenario_observation_context(scenario: ScenarioDefinition) -> ScenarioObservationContext:
    """Build the unified observation and assessment context from scenario definition."""
    return ScenarioObservationContext(
        scenario_id=scenario.scenario_id,
        zone_map=scenario_station_map(scenario),
        route_map=scenario_route_map(scenario),
        camera_range_cm=scenario.config.camera_range_cm,
        camera_fov_deg=scenario.config.camera_fov_deg,
        freshness_ms=scenario.config.freshness_ms,
        observer_robot_id=scenario.robot_pose.robot_id,
        observer_location=scenario.robot_pose.location,
        observer_frame=scenario.robot_pose.frame_of_reference,
        shelves=scenario_shelf_footprints(scenario),
    )


class SimulationRuntime:
    """Connect one physical run to its sensors, patrol and evidence sink."""

    def __init__(
        self,
        scenario: ScenarioDefinition,
        *,
        epoch_utc: datetime,
        ingest: Callable[[ObservationSample], object],
        now: Callable[[], float] = monotonic,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._wire(
            SimulationEngine(scenario, epoch_utc=epoch_utc, id_factory=id_factory),
            scenario,
            ingest,
            now,
            id_factory,
        )
        self._pose = self._sample("robot_pose")
        self._lidar = self._sample("lidar_scan")
        self._sample("camera_detect")
        self._sample("ambient_light")

    def _wire(self, engine, scenario, ingest, now, id_factory) -> None:
        self.engine = engine
        self.scenario = scenario.model_copy(deep=True)
        self._sequence = 0
        self.revision = 0
        self.belief_changes: deque[SimulationBeliefChange] = deque(maxlen=MAX_RECENT_BELIEF_CHANGES)
        self.presenter_requests: dict[UUID, tuple[SimulationDisruptRequest, ScenarioEvent]] = {}
        self._fresh_on_resume = False
        self.error: str | None = None
        self.persistence_error: str | None = None
        snap = self.engine.snapshot()
        self.run_id = snap.status.run_id
        self.sensors = WarehouseSensors(
            self.engine.snapshot, id_factory=id_factory, sequence_factory=self._allocate_sequence
        )
        self.controller = PatrolController(
            scenario.patrol,
            snap.config,
            initial_pose=scenario.robot_pose,
            run_id=snap.status.run_id,
            scenario_id=snap.scenario_id,
        )
        self.clock = SimulationClock(
            step_ms=snap.config.step_ms, rate=snap.status.playback_rate, now=now
        )
        self._ingest = ingest
        self._route_map = scenario_route_map(scenario)
        self.last_acquisition_ms = 0
        self._current_decision_record: PatrolDecisionRecord | None = None
        self._latest_stop_decision_record: PatrolDecisionRecord | None = None
        self._in_stop_episode: bool = False

    def _allocate_sequence(self, _run_id: UUID) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence

    def read(self) -> SimulationSnapshot:
        return self.engine.snapshot()

    def current_route_id(self) -> str | None:
        """Return the uniquely mapped forward route for the last sensed pose."""
        route, _ = self._route_map.match_route(self._pose.observer_pose)
        return route.route_id if route else None

    @property
    def sim_time_ms(self) -> int:
        return self.engine.snapshot().status.sim_time_ms

    @property
    def next_sample_sequence(self) -> int:
        return self._sequence

    def checkpoint(self) -> dict:
        return {
            "version": 1,
            "engine": self.engine.export_state(),
            "controller": self.controller.export_checkpoint(self._pose.sim_time_ms).model_dump(
                mode="json"
            ),
            "pose": self._pose.model_dump(mode="json"),
            "lidar": self._lidar.model_dump(mode="json"),
            "next_sequence": self._sequence,
            "backlog_ms": self.clock.backlog_ms,
            "response_revision": self.revision,
            "belief_changes": [change.model_dump(mode="json") for change in self.belief_changes],
            "presenter_requests": [
                [request.model_dump(mode="json"), event.model_dump(mode="json")]
                for request, event in self.presenter_requests.values()
            ],
            "current_decision": (
                self._current_decision_record.model_dump(mode="json")
                if self._current_decision_record is not None
                else None
            ),
            "latest_stop_decision": (
                self._latest_stop_decision_record.model_dump(mode="json")
                if self._latest_stop_decision_record is not None
                else None
            ),
            "in_stop_episode": self._in_stop_episode,
        }

    @classmethod
    def restore(cls, state: dict, *, ingest, now, memory, id_factory=uuid4) -> SimulationRuntime:
        if state["version"] != 1:
            raise ValueError("Unsupported runtime checkpoint version")
        engine = SimulationEngine.restore(state["engine"])
        engine.pause()
        snap = engine.snapshot()
        scenario = ScenarioDefinition.model_validate(state["engine"]["scenario"])
        runtime = cls.__new__(cls)
        runtime._wire(engine, scenario, ingest, now, id_factory)
        pose = ObservationSample.model_validate(state["pose"])
        lidar = ObservationSample.model_validate(state["lidar"])
        controller = PatrolCheckpoint.model_validate(state["controller"])
        for sample in (pose, lidar, controller.last_lidar_sample):
            if sample is None:
                continue
            stored = memory.get_observation_sample(sample.sample_id)
            if (
                stored is None
                or sample.model_dump() != stored.model_dump(exclude={"stored_at"})
                or sample.run_id != snap.status.run_id
                or sample.sim_time_ms > snap.status.sim_time_ms
                or sample.epoch_utc != snap.status.epoch_utc
            ):
                raise ValueError("Runtime evidence does not match the observation ledger")
        if (
            pose.sim_time_ms != snap.status.sim_time_ms
            or pose.observer_pose != snap.robot_pose
            or pose.world_version != snap.world_version
            or lidar.request.capability != "lidar_scan"
        ):
            raise ValueError("Runtime evidence does not match the engine")
        latest = memory.get_latest_run_sample(snap.status.run_id)
        if (
            latest is None
            or latest.sim_time_ms > snap.status.sim_time_ms
            or type(state["next_sequence"]) is not int
            or state["next_sequence"] != latest.sample_sequence + 1
        ):
            raise ValueError("Runtime sample sequence does not match the ledger")
        runtime.controller.restore_checkpoint(
            controller,
            engine_pose_observation=pose,
            engine_pending_command=engine.pending_robot_command,
        )
        runtime._pose, runtime._lidar = pose, lidar
        runtime._sequence = state["next_sequence"]
        # Restore pauses a saved run. Give that state a newer response revision.
        runtime.revision = state.get("response_revision", runtime._sequence) + 1
        runtime.belief_changes.extend(
            SimulationBeliefChange.model_validate(change)
            for change in state.get("belief_changes", [])
        )
        for saved_request, saved_event in state.get("presenter_requests", []):
            request = SimulationDisruptRequest.model_validate(saved_request)
            runtime.presenter_requests[request.request_id] = (
                request,
                EVENT.validate_python(saved_event),
            )
        runtime.last_acquisition_ms = latest.sim_time_ms
        runtime.clock.restore_backlog(state["backlog_ms"])
        runtime._fresh_on_resume = True

        def _validate_decision_citation(dec: PatrolDecisionRecord) -> None:
            if dec.sensor_sample_id is None:
                return
            sample = memory.get_observation_sample(dec.sensor_sample_id)
            if sample is None:
                raise ValueError(
                    f"Decision cites unknown observation sample {dec.sensor_sample_id}"
                )
            if sample.run_id != snap.status.run_id:
                raise ValueError(
                    f"Decision cites sample {dec.sensor_sample_id} from foreign run {sample.run_id}"
                )
            if sample.request.capability != "lidar_scan":
                raise ValueError(
                    f"Decision cites non-LiDAR sample {dec.sensor_sample_id} "
                    f"with capability '{sample.request.capability}'"
                )
            if sample.sim_time_ms > dec.sim_time_ms:
                raise ValueError(
                    f"Decision cites future sample {dec.sensor_sample_id} "
                    f"with capture time {sample.sim_time_ms} > decision time {dec.sim_time_ms}"
                )
            if dec.sensor_sim_time_ms is not None and sample.sim_time_ms != dec.sensor_sim_time_ms:
                raise ValueError(
                    f"Decision sensor timestamp {dec.sensor_sim_time_ms} "
                    f"does not match sample timestamp {sample.sim_time_ms}"
                )

        if state.get("current_decision") is not None:
            cur_dec = PatrolDecisionRecord.model_validate(state["current_decision"])
            if (
                cur_dec.run_id != snap.status.run_id
                or cur_dec.sim_time_ms > snap.status.sim_time_ms
            ):
                raise ValueError(
                    "Restored current patrol decision does not belong to the active run"
                )
            _validate_decision_citation(cur_dec)
            runtime._current_decision_record = cur_dec
        else:
            runtime._current_decision_record = None

        if state.get("latest_stop_decision") is not None:
            stop_dec = PatrolDecisionRecord.model_validate(state["latest_stop_decision"])
            if (
                stop_dec.run_id != snap.status.run_id
                or stop_dec.sim_time_ms > snap.status.sim_time_ms
            ):
                raise ValueError("Restored stop patrol decision does not belong to the active run")
            _validate_decision_citation(stop_dec)
            runtime._latest_stop_decision_record = stop_dec
        else:
            runtime._latest_stop_decision_record = None

        runtime._in_stop_episode = bool(state.get("in_stop_episode", False))
        return runtime

    def observe(self, request: ObservationRequest) -> SensorObservation | ObservationUnavailable:
        self._require_healthy()
        try:
            sample = self._acquire(request)
        except ValueError:
            if self.error is not None:
                raise
            # Malformed legacy requests do not have a valid Phase 2 envelope.
            return self.sensors.observe(request)
        if request.capability == "lidar_scan":
            self._lidar = sample
        return sample.result

    def _sample(self, capability: str) -> ObservationSample:
        params = {"direction": "front"} if capability == "lidar_scan" else {}
        return self._acquire(ObservationRequest(capability=capability, parameters=params))

    def _acquire(self, request: ObservationRequest) -> ObservationSample:
        sample = self.sensors.sample(request)
        try:
            result = self._ingest(sample)
            if isinstance(result, IngestionResult) and result.status == "rejected":
                raise RuntimeError("Observation ingestion rejected the sample.")
        except Exception:
            self.fail("Observation ingestion failed.")
            raise
        self.last_acquisition_ms = sample.sim_time_ms
        self.revision += 1
        if isinstance(result, IngestionResult):
            self._record_belief_changes(sample, result)
        return sample

    def _record_belief_changes(self, sample: ObservationSample, result: IngestionResult) -> None:
        revisions = {
            fact_id: revision.event_id
            for revision in result.revisions_created
            for fact_id in revision.output_fact_ids
        }
        for fact_id in dict.fromkeys(
            [*(fact.fact_id for fact in result.facts_created), *revisions]
        ):
            self.belief_changes.append(
                SimulationBeliefChange(
                    sample_id=sample.sample_id,
                    sample_sequence=sample.sample_sequence,
                    sim_time_ms=sample.sim_time_ms,
                    fact_id=fact_id,
                    revision_event_id=revisions.get(fact_id),
                )
            )

    def schedule_disruption(self, request: SimulationDisruptRequest) -> ScenarioEvent:
        self._require_healthy()
        snap = self.read()
        at_ms = (
            request.at_ms
            if request.at_ms is not None
            else snap.status.sim_time_ms + snap.config.step_ms
        )
        fields = {"at_ms": at_ms, "sequence": 0}
        disruption = request.disruption
        if isinstance(disruption, RelocateParcel):
            if not any(
                e.entity_id == disruption.entity_id and e.kind == "parcel" for e in snap.entities
            ):
                raise ValueError("The relocation target must be a warehouse parcel.")
            event = MoveEvent(
                kind="move",
                **fields,
                entity_id=disruption.entity_id,
                to=disruption.to,
                duration_ms=disruption.duration_ms,
            )
        elif isinstance(disruption, SetCart):
            # The named warehouse uses these two cart positions in its script.
            event = MoveEvent(
                kind="move",
                **fields,
                entity_id="cart_01",
                to=Point(x_cm=100 if disruption.kind == "cart_block" else 180, y_cm=470),
                duration_ms=1000,
            )
        elif isinstance(disruption, SetLighting):
            event = LightingEvent(**fields, **disruption.model_dump())
        else:
            event = SensorAvailabilityEvent(**fields, **disruption.model_dump())
        assigned = self.engine.schedule_presenter_event(event)
        self.presenter_requests[request.request_id] = (request, assigned)
        self.revision += 1
        return assigned

    def fail(self, message: str) -> None:
        self.pause()
        if self.error != message:
            self.revision += 1
        self.error = message

    def _require_healthy(self) -> None:
        if self.error is not None:
            raise RuntimeError(self.error)

    def _tick(self, *, manual: bool = False) -> bool:
        before = self.engine.snapshot()
        if self.engine.pending_robot_command is None:
            if self._fresh_on_resume:
                self._lidar = self._sample("lidar_scan")
                self._fresh_on_resume = False
            decision = self.controller.decide(before.status.sim_time_ms, self._pose, self._lidar)
            self._record_patrol_decision(decision.record)
            if decision.command is not None:
                self.engine.command_robot(decision.command)
        commanded = self.engine.pending_robot_command is not None
        after = self.engine.step() if manual else self.engine.advance()
        self._pose = self._sample("robot_pose")
        if commanded:
            reconciliation = self.controller.reconcile(self._pose)
            if not reconciliation.executed or reconciliation.status == "stalled":
                stall_record = PatrolDecisionRecord(
                    run_id=before.status.run_id,
                    sim_time_ms=self._pose.sim_time_ms,
                    waypoint_index=self.controller.current_waypoint_index,
                    target_waypoint=self.controller.target_waypoint,
                    patrol_state="stalled",
                    current_pose=reconciliation.actual_pose,
                    proposed_command=reconciliation.expected_command,
                    sensor_sample_id=None,
                    sensor_sim_time_ms=None,
                    measured_distance_cm=None,
                    computed_clearance_cm=None,
                    policy_reason=reconciliation.discrepancy_reason
                    or "Movement rejected by simulation engine.",
                )
                self._record_patrol_decision(stall_record)
        else:
            self.controller.confirm_idle_tick(self._pose)
        if after.status.sim_time_ms % after.config.sensor_interval_ms == 0:
            self._lidar = self._sample("lidar_scan")
            self._sample("camera_detect")
            self._sample("ambient_light")
        completed = after.status.state == "completed"
        if completed and manual:
            self.clock.restore_backlog(0)
        return not completed

    def play(self) -> None:
        self._require_healthy()
        if self.clock.running:
            return
        self.engine.play()
        if self.engine.snapshot().status.state == "running":
            self.clock.play()
            self.revision += 1

    def pause(self) -> None:
        if self.clock.running:
            self.revision += 1
        self.clock.pause()
        self.engine.pause()

    def set_rate(self, rate: float) -> None:
        self._require_healthy()
        if type(rate) in (float, int) and self.clock.rate == rate:
            return
        self.clock.set_rate(rate)
        self.engine.set_rate(rate)
        self.revision += 1

    def step(self) -> None:
        self._require_healthy()
        if self.engine.snapshot().status.state != "paused":
            raise ValueError("manual step requires a paused run")
        try:
            self._tick(manual=True)
        except Exception:
            self.fail(self.error or "Simulation step failed.")
            raise

    def pump(self, *, max_ticks: int = 10) -> int:
        try:
            return self.clock.pump(self._tick, max_ticks=max_ticks)
        except Exception:
            self.fail(self.error or "Simulation pump failed.")
            raise

    def _record_patrol_decision(self, record: PatrolDecisionRecord) -> None:
        self._current_decision_record = record
        is_stopped = record.patrol_state in ("blocked", "waiting_for_sensor", "stalled")
        if is_stopped:
            if not self._in_stop_episode or self._latest_stop_decision_record is None:
                self._latest_stop_decision_record = record
                self._in_stop_episode = True
            elif (
                self._latest_stop_decision_record.patrol_state == "waiting_for_sensor"
                and record.patrol_state in ("blocked", "stalled")
            ):
                self._latest_stop_decision_record = record
        else:
            self._in_stop_episode = False

    @property
    def current_decision_record(self) -> PatrolDecisionRecord | None:
        return self._current_decision_record

    @property
    def latest_stop_decision_record(self) -> PatrolDecisionRecord | None:
        return self._latest_stop_decision_record

    @property
    def current_decision_summary(self) -> PatrolDecisionSummary | None:
        if self._current_decision_record is None:
            return None
        return self._record_to_summary(self._current_decision_record)

    @property
    def latest_stop_decision_summary(self) -> PatrolDecisionSummary | None:
        if self._latest_stop_decision_record is None:
            return None
        return self._record_to_summary(self._latest_stop_decision_record)

    def patrol_decision_summaries(
        self,
    ) -> tuple[PatrolDecisionSummary | None, PatrolDecisionSummary | None]:
        """Return (current_decision, latest_stop_decision) summaries."""
        return self.current_decision_summary, self.latest_stop_decision_summary

    @staticmethod
    def _record_to_summary(record: PatrolDecisionRecord) -> PatrolDecisionSummary:
        return PatrolDecisionSummary(
            run_id=record.run_id,
            sim_time_ms=record.sim_time_ms,
            decision_sim_time_ms=record.sim_time_ms,
            state=record.patrol_state,
            policy_reason=record.policy_reason,
            waypoint_index=record.waypoint_index,
            target_waypoint=record.target_waypoint,
            sample_id=record.sensor_sample_id,
            sensor_sample_id=record.sensor_sample_id,
            sensor_sim_time_ms=record.sensor_sim_time_ms,
            measured_distance_cm=record.measured_distance_cm,
            computed_clearance_cm=record.computed_clearance_cm,
        )
