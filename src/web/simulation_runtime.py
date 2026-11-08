"""Manually pumped warehouse runtime; caller serializes all access.

This module does not start threads or manage sessions and persistence.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
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
    ScenarioDefinition,
    ScenarioEvent,
    SensorAvailabilityEvent,
    SimulationSnapshot,
)
from src.contracts.simulation_api import (
    MAX_RECENT_BELIEF_CHANGES,
    RelocateParcel,
    SetCart,
    SetLighting,
    SimulationBeliefChange,
    SimulationDisruptRequest,
)
from src.procedural.temporal_belief_ingestion import IngestionResult, StaticRoute, StaticRouteMap
from src.sensorimotor.patrol_controller import PatrolCheckpoint, PatrolController
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
            if decision.command is not None:
                self.engine.command_robot(decision.command)
        commanded = self.engine.pending_robot_command is not None
        after = self.engine.step() if manual else self.engine.advance()
        self._pose = self._sample("robot_pose")
        if commanded:
            self.controller.reconcile(self._pose)
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
