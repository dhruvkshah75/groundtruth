"""Session-scoped application service behind the local browser API."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, NotRequired, TypedDict
from uuid import UUID, uuid4

from src.agent import AgentResponse, GroundedAgent
from src.contracts import CameraScan, FactAssertion, FactQuery, SensorObservation, SpatialContext
from src.contracts.simulation import (
    ObservationSample,
    ScenarioDefinition,
    SimulationControl,
    SimulationSnapshot,
)
from src.contracts.simulation_api import (
    SimulationAudience,
    SimulationBeliefState,
    SimulationControlRequest,
    SimulationDisruptRequest,
    SimulationDisruptResponse,
    SimulationExportBundle,
    SimulationLoadRequest,
    SimulationObservationState,
    SimulationPatrolState,
    SimulationRunIdentity,
    SimulationStateResponse,
)
from src.declarative.memory_repository import MemoryRepository
from src.procedural.intent_provider import IntentProviderUnavailableError
from src.procedural.temporal_belief_ingestion import IngestionResult, TemporalBeliefIngestionService
from src.providers import ProviderConfig, create_provider, get_provider_config
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import (
    AmbientLight,
    Obstacle,
    RobotState,
    SimulatedObject,
    WorldState,
)
from src.web.session_store import SessionStore
from src.web.simulation_export import SimulationExporter, SimulationExportTarget
from src.web.simulation_presets import warehouse_preset
from src.web.simulation_runtime import MAX_PRESENTER_INPUTS, SimulationRuntime

LOGGER = logging.getLogger(__name__)


class StaleRequestError(RuntimeError):
    """The conversation, run, or session changed while answering."""


class SimulationStateConflict(RuntimeError):
    """A valid command cannot be applied to the current run state."""


class SimulationInputRejected(ValueError):
    """The engine rejected a proposed presenter input without scheduling it."""


class SimulationUnavailable(RuntimeError):
    """The simulation cannot complete the operation."""


class SimulationExportNotReady(RuntimeError):
    """No replay exporter is wired into this service."""


class SessionUnavailable(RuntimeError):
    """The session registry is at capacity or shutting down."""


class SessionSimulationStatus(TypedDict):
    state: Literal["pending", "running", "paused", "completed", "error"]
    error: str | None
    run_id: NotRequired[str]
    epoch_utc: NotRequired[str]
    sim_time_ms: NotRequired[int]
    playback_rate: NotRequired[float]
    generation: NotRequired[int]
    backlog_ms: NotRequired[float]
    last_acquisition_ms: NotRequired[int]
    patrol_state: NotRequired[str]
    persistence_error: NotRequired[str | None]


class SessionSimulationRead(TypedDict):
    status: SessionSimulationStatus
    snapshot: SimulationSnapshot | None
    llm_ready: bool


SCENARIO_A_QUESTION = "Is the front route clear to move forward?"
SCENARIO_B_QUESTION = (
    "What color does the user think the red box is, what color do you register it as, "
    "and what does your data history say its true state is?"
)


def _record_scene_facts(agent: GroundedAgent, facts: list[FactAssertion]) -> None:
    """Add presentation context to memory without changing a scenario's focal claim."""
    for fact in facts:
        agent.memory.record_fact(fact)


def _record_scene_relations(
    agent: GroundedAgent,
    now: datetime,
    context: SpatialContext,
    relations: list[tuple[str, str, str, str, float]],
) -> None:
    """Store concise scene relationships as sourced Tier 1 facts."""
    _record_scene_facts(
        agent,
        [
            FactAssertion(
                subject=subject,
                predicate=predicate,
                object=object_id,
                source_agent=source,
                confidence_score=confidence,
                observed_at=now,
                context=context,
            )
            for subject, predicate, object_id, source, confidence in relations
        ],
    )


def _record_default_beliefs(agent: GroundedAgent) -> None:
    """Seed neutral facility knowledge. Takes an agent and returns nothing."""
    now = datetime.now(UTC)
    context = SpatialContext(
        location="room_101",
        frame_of_reference="map",
        world_version=1,
    )
    _record_scene_relations(
        agent,
        now,
        context,
        [
            ("robot_1", "located_in", "room_101", "localization", 0.99),
            ("robot_1", "facing", "north", "localization", 0.99),
            ("room_101", "part_of", "building_1", "facility_registry", 0.99),
            ("room_101", "lighting_is", "white", "ambient_light", 0.96),
            ("room_101", "environment_is", "indoor", "facility_registry", 0.99),
            ("camera_01", "mounted_on", "robot_1", "robot_config", 1.0),
            ("camera_01", "calibrated_for", "visible_spectrum", "robot_config", 1.0),
            ("lidar_front", "mounted_on", "robot_1", "robot_config", 1.0),
            ("lidar_front", "calibrated_for", "short_range", "robot_config", 1.0),
            ("corridor_01", "connects_to", "room_101", "static_map", 0.97),
            ("corridor_01", "leads_to", "loading_bay", "static_map", 0.97),
            ("charging_station", "located_in", "room_101", "facility_registry", 0.96),
            ("inventory_system", "operates_in", "loading_bay", "facility_registry", 0.98),
            ("facility_map", "describes", "building_1", "static_map", 0.99),
            ("localization_service", "tracks", "robot_1", "robot_config", 1.0),
            ("facility_map", "status_is", "active", "static_map", 0.99),
            ("building_1", "contains", "loading_bay", "facility_registry", 0.99),
        ],
    )


def _new_agent(
    config: ProviderConfig | None = None,
    *,
    seed_default_beliefs: bool = True,
) -> GroundedAgent:
    provider, _ = create_provider(config)
    memory = MemoryRepository(":memory:")
    for mention, entity_id in (
        ("front route", "route_A"),
        ("route a", "route_A"),
        ("route_A", "route_A"),
        ("the box", "box_01"),
        ("red box", "box_01"),
        ("blue box", "box_01"),
        ("brown box", "box_01"),
        ("box_01", "box_01"),
        ("box 1", "box_01"),
    ):
        memory.add_alias(mention, entity_id)

    world = WorldState(
        robot=RobotState(
            robot_id="robot_1",
            x_cm=0.0,
            y_cm=0.0,
            direction="north",
            location="room_101",
            frame_of_reference="robot_base",
        ),
        light=AmbientLight(intensity=1.0, color_cast="white"),
    )
    agent = GroundedAgent.create(
        memory=memory,
        environment=MockEnvironment(world=world),
        provider=provider,
    )
    if seed_default_beliefs:
        _record_default_beliefs(agent)
    return agent


@dataclass
class AgentSession:
    """One browser's agent, in-memory evidence ledger, and conversation history."""

    config: ProviderConfig = field(default_factory=get_provider_config)
    agent: GroundedAgent = field(init=False)
    history: list[dict[str, object]] = field(default_factory=list)
    audit_history: dict[str, dict[str, object]] = field(default_factory=dict)
    suggested_question: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)

    now: Callable[[], float] = monotonic
    id_factory: Callable[[], UUID] = uuid4
    runtime: SimulationRuntime | None = field(init=False, default=None)
    load_error: str | None = field(init=False, default=None)
    _request_lock: threading.Lock = field(init=False, default_factory=threading.Lock)
    _generation: int = field(init=False, default=0)
    _load_request: SimulationLoadRequest | None = field(init=False, default=None)
    _conversation_generation: int = field(init=False, default=0)
    _retired: bool = field(init=False, default=False)
    _dirty: bool = field(init=False, default=False)
    _last_save: float = field(init=False, default=0)
    _last_view: float = field(init=False, default=0)
    _save_revision: int = field(init=False, default=0)
    _pending_save: Future | None = field(init=False, default=None)
    _pending_tick: Future | None = field(init=False, default=None)
    _ticker: ThreadPoolExecutor | None = field(init=False, default=None)
    _writer: ThreadPoolExecutor | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.agent = _new_agent(self.config)
        self.touch()

    def touch(self) -> None:
        self.require_active()
        self._last_view = self.now()

    def require_active(self) -> None:
        if self._retired:
            raise StaleRequestError("This session was deleted or shut down.")

    def _ingestion(self) -> TemporalBeliefIngestionService:
        # The agent already registered graph invalidation on this ledger.
        return TemporalBeliefIngestionService(self.agent.memory)

    def _simulation_ingestion(self) -> Callable[[ObservationSample], IngestionResult]:
        """Register identities from successful camera observations, not simulator truth."""
        ingestion = self._ingestion()

        def ingest(sample: ObservationSample) -> IngestionResult:
            self.agent.memory.set_active_run(sample.run_id)
            result = ingestion.ingest_sample(sample)
            if (
                result.status != "rejected"
                and sample.request.capability == "camera_detect"
                and isinstance(sample.result, SensorObservation)
            ):
                for detection in CameraScan.model_validate(sample.result.measurements).objects:
                    self._remember_simulation_entity(detection.object_id)
            return result

        return ingest

    def _remember_simulation_entity(self, entity: str) -> None:
        self.agent.memory.add_alias(entity, entity)
        self.agent.memory.add_alias(entity.replace("_", " "), entity)
        numbered = re.fullmatch(r"(.+)_([0-9]+)", entity)
        if numbered:
            short = f"{numbered.group(1)}_{int(numbered.group(2))}"
            self.agent.memory.add_alias(short, entity)
            self.agent.memory.add_alias(short.replace("_", " "), entity)

    def _register_scenario_entities(self, runtime: SimulationRuntime) -> None:
        """Register known names only; observations still establish physical claims."""
        for entity in runtime.scenario.entities:
            self.agent.memory.register_simulation_entity(runtime.run_id, entity.entity_id)
            self._remember_simulation_entity(entity.entity_id)
            self.agent.memory.add_alias(entity.kind, entity.entity_id)
            self.agent.memory.add_alias(f"the {entity.kind}", entity.entity_id)

    def load_simulation(self, scenario: ScenarioDefinition, epoch_utc: datetime) -> None:
        self.touch()
        self._generation += 1
        self._load_request = None
        self.runtime = None
        self.load_error = None
        try:
            self.runtime = SimulationRuntime(
                scenario,
                epoch_utc=epoch_utc,
                ingest=self._simulation_ingestion(),
                now=self.now,
                id_factory=self.id_factory,
            )
            self._register_scenario_entities(self.runtime)
        except Exception:
            self.load_error = "Warehouse initialization failed. Reload the run to recover."
            raise
        self._dirty = True

    def persisted_state(self) -> dict:
        """Capture conversation and world alongside the separately stored ledger."""
        state = {
            "history": deepcopy(self.history),
            "audit_history": deepcopy(self.audit_history),
            "suggested_question": self.suggested_question,
            "world": self.agent.environment.get_world_state().model_dump(mode="json"),
            "simulation_generation": self._generation,
        }

        if self.runtime is not None:
            state["simulation_runtime"] = self.runtime.checkpoint()
        if self._load_request is not None:
            state["simulation_load_request"] = self._load_request.model_dump(mode="json")
        return state

    def restore(self, state: dict, memory: bytes) -> None:
        """Restore the exact world version, conversation, and SQLite ledger."""
        self.agent.close()
        self.agent = _new_agent(self.config, seed_default_beliefs=False)
        self.agent.memory.restore_snapshot(memory)
        self.agent.environment.restore_world(WorldState.model_validate(state["world"]))
        self.history = state.get("history", [])
        self.audit_history = state.get("audit_history", {})
        self.suggested_question = state.get("suggested_question", "")
        self._generation = state.get("simulation_generation", int("simulation_runtime" in state))
        if "simulation_load_request" in state:
            self._load_request = SimulationLoadRequest.model_validate(
                state["simulation_load_request"]
            )
        if "simulation_runtime" in state:
            self.runtime = SimulationRuntime.restore(
                state["simulation_runtime"],
                ingest=self._simulation_ingestion(),
                now=self.now,
                memory=self.agent.memory,
                id_factory=self.id_factory,
            )
            self.agent.memory.set_active_run(self.runtime.run_id)
            self._register_scenario_entities(self.runtime)
            for entity in self.agent.memory.observed_entity_ids(self.runtime.run_id):
                self._remember_simulation_entity(entity)
            # A recovered checkpoint can be behind the last observed live tick.
            self._generation += 1

    def reset(self) -> None:
        """Clear session activity and restore the neutral facility belief baseline."""
        self._invalidate_world()
        self.agent.close()
        self.agent = _new_agent(self.config)
        self.audit_history.clear()
        self.history.clear()
        self.suggested_question = ""

    def new_chat(self) -> None:
        """Clear the current conversation while preserving the agent and its world."""
        self.touch()
        self._conversation_generation += 1
        self.history.clear()
        self.suggested_question = ""
        self._dirty = True

    def _invalidate_world(self) -> None:
        self.touch()
        self._generation += 1
        self._load_request = None
        self.runtime = None
        self.load_error = None
        self._dirty = True

    def _reset_without_baseline(self) -> None:
        """Clear all session data before loading a controlled scenario preset."""
        self._invalidate_world()
        self.agent.close()
        self.agent = _new_agent(self.config, seed_default_beliefs=False)
        self.audit_history.clear()
        self.history.clear()
        self.suggested_question = ""

    def load_scenario(self, name: str) -> None:
        """Reset this session and seed one of the documented demonstration worlds."""
        self._reset_without_baseline()
        now = datetime.now(UTC)
        robot = RobotState(
            robot_id="robot_1",
            x_cm=0.0,
            y_cm=0.0,
            direction="north",
            location="room_101",
            frame_of_reference="robot_base",
        )

        if name == "scenario-a":
            self.agent.environment.replace_world(
                WorldState(
                    scenario_name="Scenario A · map vs. LiDAR",
                    robot=robot,
                    obstacles={
                        "obs_01": Obstacle(
                            obstacle_id="obs_01",
                            x_cm=0.0,
                            y_cm=12.0,
                            location="room_101",
                            active=True,
                        ),
                        "obs_02": Obstacle(
                            obstacle_id="obs_02",
                            x_cm=32.0,
                            y_cm=38.0,
                            location="room_101",
                            active=True,
                            radius_cm=6.0,
                        ),
                    },
                    objects={
                        "pallet_04": SimulatedObject(
                            object_id="pallet_04",
                            intrinsic_color="brown",
                            x_cm=-26.0,
                            y_cm=30.0,
                            location="room_101",
                        ),
                        "box_02": SimulatedObject(
                            object_id="box_02",
                            intrinsic_color="green",
                            x_cm=28.0,
                            y_cm=34.0,
                            location="loading_bay",
                        ),
                        "waypoint_01": SimulatedObject(
                            object_id="waypoint_01",
                            intrinsic_color="white",
                            x_cm=0.0,
                            y_cm=45.0,
                            location="room_101",
                        ),
                    },
                    light=AmbientLight(intensity=1.0, color_cast="white"),
                )
            )
            self.agent.memory.record_fact(
                FactAssertion(
                    subject="route_A",
                    predicate="status_is",
                    object="clear",
                    source_agent="static_map",
                    confidence_score=0.95,
                    observed_at=now,
                    context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
                    evidence={"map_version": "v1.0", "source": "building_blueprints"},
                )
            )
            scene_context = SpatialContext(
                location="room_101",
                frame_of_reference="map",
                world_version=1,
            )
            _record_scene_facts(
                self.agent,
                [
                    FactAssertion(
                        subject="robot_1",
                        predicate="located_in",
                        object="room_101",
                        source_agent="localization",
                        confidence_score=0.99,
                        observed_at=now,
                        context=scene_context,
                        evidence={"pose_source": "map_localization"},
                    ),
                    FactAssertion(
                        subject="robot_1",
                        predicate="facing",
                        object="north",
                        source_agent="localization",
                        confidence_score=0.99,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="route_A",
                        predicate="starts_at",
                        object="room_101",
                        source_agent="static_map",
                        confidence_score=0.98,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="route_A",
                        predicate="leads_to",
                        object="loading_bay",
                        source_agent="static_map",
                        confidence_score=0.98,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="room_101",
                        predicate="connects_to",
                        object="loading_bay",
                        source_agent="static_map",
                        confidence_score=0.97,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="lidar_front",
                        predicate="mounted_on",
                        object="robot_1",
                        source_agent="robot_config",
                        confidence_score=1.0,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="camera_01",
                        predicate="mounted_on",
                        object="robot_1",
                        source_agent="robot_config",
                        confidence_score=1.0,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="pallet_04",
                        predicate="stored_in",
                        object="room_101",
                        source_agent="facility_registry",
                        confidence_score=0.9,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="box_02",
                        predicate="stored_in",
                        object="loading_bay",
                        source_agent="facility_registry",
                        confidence_score=0.9,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="room_101",
                        predicate="lighting_is",
                        object="white",
                        source_agent="ambient_light",
                        confidence_score=0.96,
                        observed_at=now,
                        context=scene_context,
                    ),
                ],
            )
            _record_scene_relations(
                self.agent,
                now,
                scene_context,
                [
                    ("building_1", "contains", "room_101", "facility_registry", 0.99),
                    ("building_1", "contains", "loading_bay", "facility_registry", 0.99),
                    ("corridor_02", "connects_to", "room_101", "static_map", 0.97),
                    ("corridor_02", "leads_to", "loading_bay", "static_map", 0.97),
                    ("patrol_route_01", "includes", "route_A", "mission_registry", 0.98),
                    ("robot_1", "assigned_to", "patrol_route_01", "mission_registry", 0.98),
                    ("waypoint_01", "part_of", "route_A", "static_map", 0.97),
                    ("obs_02", "registered_in", "room_101", "facility_registry", 0.92),
                    ("inventory_system", "tracks", "box_02", "inventory_system", 0.99),
                    ("inventory_system", "tracks", "pallet_04", "inventory_system", 0.99),
                    ("building_1", "connects_to", "corridor_02", "static_map", 0.97),
                    ("route_A", "includes", "waypoint_01", "static_map", 0.97),
                ],
            )
            self.suggested_question = SCENARIO_A_QUESTION
            return

        if name == "scenario-b":
            self.agent.environment.replace_world(
                WorldState(
                    scenario_name="Scenario B · three perspectives",
                    robot=robot,
                    objects={
                        "box_01": SimulatedObject(
                            object_id="box_01",
                            intrinsic_color="red",
                            location="room_101",
                            x_cm=10.0,
                            y_cm=10.0,
                        ),
                        "box_02": SimulatedObject(
                            object_id="box_02",
                            intrinsic_color="green",
                            location="room_101",
                            x_cm=-18.0,
                            y_cm=24.0,
                        ),
                        "shelf_01": SimulatedObject(
                            object_id="shelf_01",
                            intrinsic_color="gray",
                            location="room_101",
                            x_cm=-24.0,
                            y_cm=32.0,
                            bounding_box={"width_cm": 80.0, "height_cm": 190.0},
                        ),
                        "lamp_02": SimulatedObject(
                            object_id="lamp_02",
                            intrinsic_color="yellow",
                            location="room_101",
                            x_cm=0.0,
                            y_cm=55.0,
                        ),
                    },
                    light=AmbientLight(intensity=0.8, color_cast="yellow"),
                )
            )
            self.agent.memory.record_fact(
                FactAssertion(
                    subject="box_01",
                    predicate="painted_color_is",
                    object="blue",
                    source_agent="bot_02",
                    confidence_score=1.0,
                    observed_at=now,
                    context=SpatialContext(location="room_101"),
                    evidence={"action": "painted_blue", "ticket_id": "MAINT-4091"},
                )
            )
            scene_context = SpatialContext(
                location="room_101",
                frame_of_reference="map",
                world_version=1,
            )
            _record_scene_facts(
                self.agent,
                [
                    FactAssertion(
                        subject="robot_1",
                        predicate="located_in",
                        object="room_101",
                        source_agent="localization",
                        confidence_score=0.99,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="robot_1",
                        predicate="facing",
                        object="north",
                        source_agent="localization",
                        confidence_score=0.99,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="camera_01",
                        predicate="mounted_on",
                        object="robot_1",
                        source_agent="robot_config",
                        confidence_score=1.0,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="camera_01",
                        predicate="observes",
                        object="room_101",
                        source_agent="robot_config",
                        confidence_score=1.0,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="box_01",
                        predicate="located_in",
                        object="room_101",
                        source_agent="facility_registry",
                        confidence_score=0.94,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="box_01",
                        predicate="intrinsic_color_is",
                        object="red",
                        source_agent="object_registry",
                        confidence_score=0.98,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="box_02",
                        predicate="stored_on",
                        object="shelf_01",
                        source_agent="facility_registry",
                        confidence_score=0.91,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="shelf_01",
                        predicate="located_in",
                        object="room_101",
                        source_agent="facility_registry",
                        confidence_score=0.96,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="light_01",
                        predicate="illuminates",
                        object="room_101",
                        source_agent="facility_registry",
                        confidence_score=0.97,
                        observed_at=now,
                        context=scene_context,
                    ),
                    FactAssertion(
                        subject="room_101",
                        predicate="lighting_is",
                        object="yellow",
                        source_agent="ambient_light",
                        confidence_score=0.96,
                        observed_at=now,
                        context=scene_context,
                    ),
                ],
            )
            _record_scene_relations(
                self.agent,
                now,
                scene_context,
                [
                    ("building_1", "contains", "room_101", "facility_registry", 0.99),
                    ("building_1", "contains", "loading_bay", "facility_registry", 0.99),
                    ("corridor_02", "connects_to", "room_101", "static_map", 0.97),
                    ("corridor_02", "leads_to", "loading_bay", "static_map", 0.97),
                    ("inspection_route_01", "includes", "room_101", "mission_registry", 0.98),
                    ("robot_1", "assigned_to", "inspection_route_01", "mission_registry", 0.98),
                    ("lamp_02", "located_in", "room_101", "facility_registry", 0.95),
                    ("lamp_02", "color_cast_is", "yellow", "ambient_light", 0.96),
                    ("inventory_system", "tracks", "box_01", "inventory_system", 0.99),
                    ("inventory_system", "tracks", "box_02", "inventory_system", 0.99),
                    ("building_1", "connects_to", "corridor_02", "static_map", 0.97),
                    ("inspection_route_01", "passes_through", "corridor_02", "static_map", 0.97),
                    ("shelf_01", "stocked_by", "inventory_system", "facility_registry", 0.95),
                ],
            )
            self.suggested_question = SCENARIO_B_QUESTION
            return

        raise ValueError("Unknown scenario. Choose 'scenario-a' or 'scenario-b'.")

    def ask(self, question: str) -> dict[str, object]:
        """Run a question through the composed Python agent and retain its result."""
        cleaned = question.strip()
        if not cleaned:
            raise ValueError("Question must not be empty.")
        if len(cleaned) > 4000:
            raise ValueError("Question must be 4,000 characters or fewer.")

        if self.config.mode == "live" and not self.config.has_valid_key:
            raise IntentProviderUnavailableError(
                "GROQ_API_KEY is not configured. Add GROQ_API_KEY to your .env file "
                "or set GROUNDTRUTH_PROVIDER_MODE=offline to use offline rule-based mode."
            )

        with self.lock:
            self.touch()
            identity = self._request_identity()
            request_id = str(uuid4())
        with self._request_lock:
            with self.lock:
                self._validate_request(identity)
                agent = self.agent
                if self.runtime is None:
                    world = agent.environment.get_world_state()
                    for entity_id in {*world.objects, *world.obstacles}:
                        self._remember_simulation_entity(entity_id)
            try:
                intent = agent.interpret(cleaned)
                with self.lock:
                    self._validate_request(identity)
                    if self.load_error:
                        raise RuntimeError(self.load_error)
                    runtime = self.runtime
                    snapshot = runtime.read() if runtime else None
                    capture_time_ms = snapshot.status.sim_time_ms if snapshot else None
                    capture = agent.capture(
                        cleaned, intent, sensors=runtime, capture_time_ms=capture_time_ms
                    )
                    self._dirty = True
                item = _response_to_dict(capture())
                if snapshot is not None:
                    item["simulation_evidence"] = {
                        "run_id": str(snapshot.status.run_id),
                        "sim_time_ms": snapshot.status.sim_time_ms,
                        "request_id": request_id,
                    }
            except Exception:
                with self.lock:
                    self._validate_request(identity)
                raise
            with self.lock:
                self._validate_request(identity)
                self.history.append(item)
                for event in item.get("audit_events", []):
                    if isinstance(event, dict):
                        self.audit_history[str(event["event_id"])] = event
                self.suggested_question = ""
                self._dirty = True
                return item

    def _request_identity(self) -> tuple[int, int, UUID | None]:
        run = self.runtime.run_id if self.runtime else None
        return self._generation, self._conversation_generation, run

    def _validate_request(self, identity: tuple[int, int, UUID | None]) -> None:
        self.require_active()
        if identity != self._request_identity():
            raise StaleRequestError("This question belongs to an earlier conversation or run.")

    def simulation_status(self) -> SessionSimulationStatus:
        runtime = self.runtime
        if runtime is None:
            return {"state": "error" if self.load_error else "pending", "error": self.load_error}
        snapshot = runtime.read()
        return {
            "run_id": str(snapshot.status.run_id),
            "epoch_utc": snapshot.status.epoch_utc.isoformat(),
            "sim_time_ms": snapshot.status.sim_time_ms,
            "playback_rate": snapshot.status.playback_rate,
            "state": "error" if runtime.error else snapshot.status.state,
            "generation": self._generation,
            "backlog_ms": runtime.clock.backlog_ms,
            "last_acquisition_ms": runtime.last_acquisition_ms,
            "patrol_state": runtime.controller.state,
            "error": runtime.error,
            "persistence_error": runtime.persistence_error,
        }

    def require_simulation_identity(self, expected: SimulationRunIdentity) -> None:
        self.require_active()
        current_run_id = self.runtime.run_id if self.runtime else None
        if (expected.run_id, expected.generation) != (current_run_id, self._generation):
            raise StaleRequestError("The simulation run has changed. Read state and retry.")

    def require_simulation(self, *, allow_failed: bool = False) -> SimulationRuntime:
        runtime = self.runtime
        if self.load_error or (runtime and runtime.error and not allow_failed):
            raise SimulationUnavailable("The simulation failed. Reset or load the run again.")
        if runtime is None:
            raise SimulationStateConflict("Load a warehouse simulation first.")
        return runtime

    def live_simulation_state(self) -> SimulationStateResponse:
        """Capture a detached polling response while the caller holds the session lock."""
        runtime = self.runtime
        snap = runtime.read() if runtime else None
        status = snap.status if snap else None
        observation = {}
        if status is not None:
            for capability in ("camera_detect", "lidar_scan", "ambient_light", "robot_pose"):
                latest = self.agent.memory.get_latest_sample(
                    status.run_id, capability, at_sim_time_ms=status.sim_time_ms
                )
                if latest is not None:
                    observation[capability] = latest
        error = runtime.error if runtime else self.load_error
        return SimulationStateResponse(
            run_id=status.run_id if status else None,
            generation=self._generation,
            revision=runtime.revision if runtime else 0,
            snapshot_sequence=snap.snapshot_sequence if snap else None,
            sim_time_ms=status.sim_time_ms if status else None,
            epoch_utc=status.epoch_utc if status else None,
            playback_rate=status.playback_rate if status else 0.5,
            state="error" if error else status.state if status else "pending",
            error=error,
            persistence_error=runtime.persistence_error if runtime else None,
            llm_ready=self.config.is_live and self.config.has_valid_key,
            patrol=SimulationPatrolState(
                state=runtime.controller.state,
                waypoint_index=runtime.controller.current_waypoint_index,
                target_waypoint=runtime.controller.target_waypoint,
            )
            if runtime
            else None,
            audience=SimulationAudience(
                snapshot=snap,
                bounds=runtime.scenario.bounds,
                robot_radius_cm=runtime.scenario.robot_radius_cm,
                routes=runtime.scenario.routes,
                patrol=runtime.scenario.patrol,
            ).model_copy(deep=True)
            if runtime
            else None,
            observation=SimulationObservationState(
                latest=observation,
                last_acquisition_ms=runtime.last_acquisition_ms if runtime else None,
            ),
            belief=SimulationBeliefState(
                recent_changes=list(runtime.belief_changes) if runtime else []
            ),
        )

    def snapshot(self) -> dict[str, object]:
        """Read actual environment, graph, active beliefs, and full memory ledger."""
        graph = self.agent.graph.get_graph()
        nodes = sorted(str(node) for node in graph.nodes)
        edges = [
            _edge_to_dict(u, v, key, data) for u, v, key, data in graph.edges(keys=True, data=True)
        ]
        active_facts = self.agent.memory.query_facts(FactQuery(active_only=True))
        all_facts = self.agent.memory.query_facts(FactQuery(active_only=False))
        fact_ids = {str(fact.fact_id) for fact in all_facts}
        audit_events = [
            event
            for event in self.audit_history.values()
            if self.runtime is None
            or fact_ids.intersection(
                event.get("input_fact_ids", []) + event.get("output_fact_ids", [])
            )
        ]
        world = self.agent.environment.get_world_state()
        if self.runtime is not None:
            physical = self.runtime.read()
            world = WorldState(
                world_version=physical.world_version,
                scenario_name=physical.scenario_id,
                robot=RobotState(**physical.robot_pose.model_dump()),
                light=AmbientLight(
                    intensity=physical.light_intensity, color_cast=physical.color_cast
                ),
                objects={
                    entity.entity_id: SimulatedObject(
                        object_id=entity.entity_id,
                        x_cm=entity.footprint.x_cm,
                        y_cm=entity.footprint.y_cm,
                        location=entity.location,
                        intrinsic_color=entity.intrinsic_color,
                        visible=entity.visible,
                        bounding_box={
                            "width_cm": entity.footprint.width_cm,
                            "height_cm": entity.footprint.height_cm,
                        },
                    )
                    for entity in physical.entities
                    if entity.kind != "station"
                },
                obstacles={
                    entity.entity_id: Obstacle(
                        obstacle_id=entity.entity_id,
                        x_cm=entity.footprint.x_cm,
                        y_cm=entity.footprint.y_cm,
                        location=entity.location,
                    )
                    for entity in physical.entities
                    if entity.solid
                },
            )

        result = {
            "history": list(self.history),
            "suggested_question": self.suggested_question,
            "graph": {
                "nodes": nodes,
                "edges": edges,
                "node_count": graph.number_of_nodes(),
                "edge_count": graph.number_of_edges(),
            },
            "active_facts": [_model_to_dict(fact) for fact in active_facts],
            "ledger": [_model_to_dict(fact) for fact in all_facts],
            "audit_events": audit_events,
            "environment": world.model_dump(mode="json"),
            "environment_source": "warehouse_truth" if self.runtime is not None else "mock",
            "capabilities": [
                capability.model_dump(mode="json")
                for capability in self.agent.environment.get_capabilities()
            ],
        }

        if self.runtime is not None or self.load_error is not None:
            result["simulation_status"] = self.simulation_status()
        return result


class AgentService:
    """Thread-safe registry for browser sessions and app metadata."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        store: SessionStore | None = None,
        *,
        now: Callable[[], float] = monotonic,
        background: bool = True,
        epoch_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], UUID] = uuid4,
        exporter: SimulationExporter | None = None,
    ) -> None:
        self._store = store
        self._sessions: dict[str, AgentSession] = {}
        self._lock = threading.Lock()
        self._lifecycle = threading.Condition()
        self._changing_ids: set[str] = set()
        self._reserved_slots = 0
        self._shutdown_lock = threading.Lock()
        self._config = config or get_provider_config()
        self._now = now
        self._epoch_now = epoch_now
        self._id_factory = id_factory
        self._exporter = exporter
        self._background = background
        self._stop = threading.Event()
        self._runner: threading.Thread | None = None

    @contextmanager
    def _change_session(self, key: str) -> Iterator[None]:
        """Serialize creation and deletion for this ID, without blocking other IDs."""
        with self._lifecycle:
            self._lifecycle.wait_for(lambda: key not in self._changing_ids or self._stop.is_set())
            if self._stop.is_set():
                raise SessionUnavailable("Agent service is shutting down.")
            self._changing_ids.add(key)
        try:
            yield
        finally:
            with self._lifecycle:
                self._changing_ids.remove(key)
                self._lifecycle.notify_all()

    def get_session(self, session_id: str) -> AgentSession:
        canonical_id = _canonical_session_id(session_id)
        with self._lock:
            if self._stop.is_set():
                raise SessionUnavailable("Agent service is shutting down.")
            existing = self._sessions.get(canonical_id)
        if existing is not None:
            return existing
        with self._change_session(canonical_id):
            with self._lock:
                if canonical_id in self._sessions:
                    return self._sessions[canonical_id]
                if len(self._sessions) + self._reserved_slots >= 128:
                    raise SessionUnavailable("The local demo has reached its 128-session limit.")
                self._reserved_slots += 1
            try:
                session = self._restore_session(canonical_id)
            except BaseException:
                with self._lock:
                    self._reserved_slots -= 1
                raise
            with self._lock:
                self._reserved_slots -= 1
                self._sessions[canonical_id] = session
            if session.runtime is not None:
                self._ensure_runner()
            return session

    def _restore_session(self, key: str) -> AgentSession:
        session = AgentSession(config=self._config, now=self._now, id_factory=self._id_factory)
        if self._store is None:
            return session
        session._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="groundtruth-save")
        try:
            stored = self._store.load(key)
            if stored:
                session.restore(*stored)
                if session.runtime is not None:
                    # Reserve this incarnation durably before publishing the session.
                    self.save_session(key, session)
            else:
                self.save_session(key, session)
        except Exception:
            session.runtime = None
            session._load_request = None
            session.load_error = (
                "Could not restore saved session. Reset or load a scenario to recover."
            )
            LOGGER.warning("Session restore or initialization failed")
        return session

    def _ensure_runner(self) -> None:
        with self._lock:
            if self._background and self._runner is None and not self._stop.is_set():
                self._runner = threading.Thread(
                    target=self._run, name="groundtruth-simulation", daemon=True
                )
                self._runner.start()

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            self._dispatch_ticks()

    def _session_entries(self) -> tuple[tuple[str, AgentSession], ...]:
        with self._lock:
            return tuple(self._sessions.items())

    def _dispatch_ticks(self) -> None:
        """Schedule at most one outstanding tick batch per session."""
        for key, session in self._session_entries():
            if not session.lock.acquire(blocking=False):
                continue
            try:
                runtime = session.runtime
                if (
                    self._stop.is_set()
                    or session._retired
                    or runtime is None
                    or runtime.error
                    or not (runtime.clock.running or (session._dirty and session._writer))
                    or (session._pending_tick is not None and not session._pending_tick.done())
                ):
                    continue
                if session._ticker is None:
                    session._ticker = ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix="groundtruth-tick"
                    )
                session._pending_tick = session._ticker.submit(
                    self._tick_session, key, session, runtime
                )
            finally:
                session.lock.release()

    def _tick_session(self, key: str, session: AgentSession, runtime: SimulationRuntime) -> None:
        with session.lock:
            if session.runtime is runtime:
                self._pump_locked(key, session)

    def pump(self) -> None:
        """Run one deterministic scheduling pass for headless use."""
        for key, session in self._session_entries():
            if not session.lock.acquire(blocking=False):
                continue
            try:
                self._pump_locked(key, session)
            finally:
                session.lock.release()

    def _pump_locked(self, key: str, session: AgentSession) -> None:
        runtime = session.runtime
        if self._stop.is_set() or session._retired or runtime is None or runtime.error:
            return
        try:
            now = self._now()
            idle = runtime.clock.running and now - session._last_view >= 60
            if idle:
                runtime.pause()
                session._dirty = True
            elif runtime.pump():
                session._dirty = True
            if session._dirty and (
                idle
                or (
                    now - session._last_save >= 1
                    and (session._pending_save is None or session._pending_save.done())
                )
            ):
                self._queue_save(key, session, defer_capture=True)
        except Exception:
            runtime.fail(runtime.error or "Simulation scheduler failed.")
            LOGGER.warning("Warehouse scheduler paused a failed run")

    def _queue_save(
        self, key: str, session: AgentSession, *, defer_capture: bool = False
    ) -> Future | None:
        if (
            session._writer is None
            or session.load_error
            or (session.runtime and session.runtime.error)
        ):
            return None
        assert self._store is not None
        store = self._store
        runtime = session.runtime

        def capture() -> tuple[dict, bytes]:
            state = session.persisted_state()
            image = session.agent.memory.export_snapshot()
            session._dirty = False
            return state, image

        def record_failure() -> None:
            if not session._retired and session.runtime is runtime and runtime is not None:
                runtime.persistence_error = "Simulation checkpoint could not be saved."
                runtime.fail(runtime.persistence_error)

        # Immediate controls capture their exact boundary. A periodic capture can
        # run later, but must be discarded if a newer boundary was already queued.
        try:
            checkpoint = None if defer_capture else capture()
        except Exception:
            record_failure()
            raise
        session._save_revision += 1
        revision = session._save_revision
        generation = session._generation
        conversation = session._conversation_generation

        def write() -> None:
            try:
                payload = checkpoint
                if payload is None:
                    with session.lock:
                        if (
                            session._retired
                            or session._save_revision != revision
                            or session._generation != generation
                            or session._conversation_generation != conversation
                            or (runtime is not None and runtime.error)
                        ):
                            return
                        payload = capture()
                elif runtime is not None:
                    with session.lock:
                        if runtime.persistence_error:
                            raise SimulationUnavailable("An earlier checkpoint failed.")
                store.save(key, *payload)
            except Exception:
                with session.lock:
                    record_failure()
                raise

        future = session._writer.submit(write)
        session._pending_save = future
        session._last_save = self._now()
        return future

    def save_session(self, session_id: str, session: AgentSession) -> None:
        with session.lock:
            session.require_active()
            pending = self._queue_save(_canonical_session_id(session_id), session)
        if pending is not None:
            pending.result()

    def load_simulation(
        self, session_id: str, scenario: ScenarioDefinition, *, epoch_utc: datetime | None = None
    ) -> SessionSimulationStatus:
        session = self.get_session(session_id)
        with session.lock:
            session.load_simulation(scenario, epoch_utc or self._epoch_now())
            pending = self._queue_save(_canonical_session_id(session_id), session)
            status = session.simulation_status()
        self._ensure_runner()
        if pending is not None:
            pending.result()
        return status

    def control_simulation(
        self, session_id: str, control: SimulationControl
    ) -> SessionSimulationStatus:
        control = SimulationControl.model_validate(control.model_dump(mode="json"))
        session = self.get_session(session_id)
        with session.lock:
            session.touch()
            runtime = session.runtime
            if runtime is None:
                raise ValueError("Load a warehouse simulation first.")
            self._apply_simulation_control(session, runtime, control)
            pending = self._queue_save(_canonical_session_id(session_id), session)
            status = session.simulation_status()
        if pending is not None:
            pending.result()
        return status

    def _apply_simulation_control(
        self, session: AgentSession, runtime: SimulationRuntime, control: SimulationControl
    ) -> None:
        if control.action == "reset":
            scenario = runtime.scenario.model_copy(deep=True)
            scenario.config.playback_rate = runtime.clock.rate
            session.load_simulation(scenario, self._epoch_now())
        elif control.action == "set_rate":
            assert control.playback_rate is not None
            runtime.set_rate(control.playback_rate)
        else:
            {"play": runtime.play, "pause": runtime.pause, "step": runtime.step}[control.action]()

    def load_warehouse(
        self, session_id: str, request: SimulationLoadRequest
    ) -> SimulationStateResponse:
        key = _canonical_session_id(session_id)
        scenario = warehouse_preset()
        session = self.get_session(key)
        with session.lock:
            session.require_active()
            previous = session._load_request
            if previous and previous.request_id == request.request_id:
                if previous != request:
                    raise SimulationStateConflict("This request ID has a different load payload.")
                session.require_simulation()
                session.touch()
                pending = session._pending_save
            else:
                session.require_simulation_identity(request.expected)
                try:
                    session.load_simulation(scenario, self._epoch_now())
                except Exception as exc:
                    raise SimulationUnavailable("Could not initialize the simulation.") from exc
                session._load_request = request
                pending = self._save_simulation_api(key, session)
            state = session.live_simulation_state()
        self._ensure_runner()
        self._confirm_simulation_save(session, pending, state)
        return state

    def apply_simulation_control(
        self, session_id: str, request: SimulationControlRequest
    ) -> SimulationStateResponse:
        key = _canonical_session_id(session_id)
        session = self.get_session(key)
        with session.lock:
            session.require_simulation_identity(request.expected)
            runtime = session.require_simulation(allow_failed=request.action == "reset")
            state = runtime.read().status.state
            if request.action == "step" and state != "paused":
                raise SimulationStateConflict("Manual step requires a paused run.")
            if request.action == "play" and state == "completed":
                raise SimulationStateConflict("Reset the completed run before playing.")
            session.touch()
            before = runtime.revision
            try:
                self._apply_simulation_control(session, runtime, request)
            except Exception as exc:
                raise SimulationUnavailable("The simulation control failed.") from exc
            if session.runtime is not runtime or runtime.revision != before:
                pending = self._save_simulation_api(key, session)
            else:
                pending = session._pending_save
            result = session.live_simulation_state()
        self._confirm_simulation_save(session, pending, result)
        return result

    def disrupt_simulation(
        self, session_id: str, request: SimulationDisruptRequest
    ) -> SimulationDisruptResponse:
        key = _canonical_session_id(session_id)
        session = self.get_session(key)
        with session.lock:
            session.require_active()
            runtime = session.runtime
            previous = (
                runtime.presenter_requests.get(request.request_id)
                if runtime and request.expected.run_id == runtime.run_id
                else None
            )
            if previous is not None:
                saved_request, event = previous
                if saved_request != request:
                    raise SimulationStateConflict(
                        "This request ID has a different disruption payload."
                    )
                session.require_simulation()
                pending = session._pending_save
            else:
                session.require_simulation_identity(request.expected)
                runtime = session.require_simulation()
                if runtime.read().status.state == "completed":
                    raise SimulationStateConflict(
                        "Reset the completed run before scheduling events."
                    )
                if len(runtime.presenter_requests) >= MAX_PRESENTER_INPUTS:
                    raise SimulationStateConflict("This run has reached its presenter input limit.")
                try:
                    event = runtime.schedule_disruption(request)
                except ValueError as exc:
                    raise SimulationInputRejected(
                        "The disruption has an invalid target, geometry, or schedule."
                    ) from exc
                pending = self._save_simulation_api(key, session)
            session.touch()
            state = session.live_simulation_state()
        self._confirm_simulation_save(session, pending, state)
        return SimulationDisruptResponse(event=event, state=state)

    def _save_simulation_api(self, key: str, session: AgentSession) -> Future | None:
        try:
            return self._queue_save(key, session)
        except Exception as exc:
            raise SimulationUnavailable("Could not save the simulation checkpoint.") from exc

    @staticmethod
    def _confirm_simulation_save(
        session: AgentSession, pending: Future | None, state: SimulationStateResponse
    ) -> None:
        if pending is not None:
            try:
                pending.result()
            except Exception as exc:
                raise SimulationUnavailable("Could not save the simulation checkpoint.") from exc
        with session.lock:
            session.require_simulation_identity(state)
            session.require_simulation()

    def read_live_simulation(self, session_id: str) -> SimulationStateResponse:
        session = self.get_session(session_id)
        with session.lock:
            session.touch()
            return session.live_simulation_state()

    def export_simulation(self, session_id: str) -> SimulationExportBundle:
        key = _canonical_session_id(session_id)
        if self._exporter is None:
            raise SimulationExportNotReady("Replay export is not ready. It requires P2-12.")
        session = self.get_session(key)
        with session.lock:
            session.touch()
            runtime = session.require_simulation()
            snapshot = runtime.read()
            target = SimulationExportTarget(
                session_id=key,
                run_id=runtime.run_id,
                generation=session._generation,
                epoch_utc=snapshot.status.epoch_utc,
                sim_time_ms=snapshot.status.sim_time_ms,
                snapshot_sequence=snapshot.snapshot_sequence,
                next_sample_sequence=runtime.next_sample_sequence,
                scenario=runtime.scenario.model_copy(deep=True),
                presenter_events=tuple(
                    event.model_copy(deep=True) for _, event in runtime.presenter_requests.values()
                ),
            )
        bundle = SimulationExportBundle.model_validate(
            self._exporter.export(deepcopy(target)).model_dump()
        )
        if (
            bundle.run_id != target.run_id
            or bundle.epoch_utc != target.epoch_utc
            or bundle.sim_time_ms != target.sim_time_ms
            or bundle.snapshot_sequence != target.snapshot_sequence
            or bundle.scenario != target.scenario
            or bundle.presenter_events != list(target.presenter_events)
            or any(
                sample.sample_sequence >= target.next_sample_sequence
                for sample in bundle.observations
            )
        ):
            raise RuntimeError("Exporter returned an inconsistent run boundary.")
        with session.lock:
            session.require_simulation_identity(
                SimulationRunIdentity(run_id=target.run_id, generation=target.generation)
            )
        return bundle

    def read_simulation(self, session_id: str) -> SessionSimulationRead:
        session = self.get_session(session_id)
        with session.lock:
            session.touch()
            return {
                "status": session.simulation_status(),
                "snapshot": session.runtime.read() if session.runtime else None,
                "llm_ready": self._config.is_live and self._config.has_valid_key,
            }

    def delete_session(self, session_id: str) -> None:
        key = _canonical_session_id(session_id)
        with self._change_session(key):
            with self._lock:
                session = self._sessions.pop(key, None)
                if session is not None:
                    # Count workers that are still draining against the session limit.
                    self._reserved_slots += 1
            try:
                if session is not None:
                    with session.lock:
                        session._retired = True
                        session.runtime = None
                    if session._ticker is not None:
                        session._ticker.shutdown(cancel_futures=True)
                    with session.lock:
                        session.agent.close()
                if self._store is not None:
                    if session is not None and session._writer is not None:
                        try:
                            session._writer.submit(self._store.delete, key).result()
                        finally:
                            session._writer.shutdown()
                    else:
                        self._store.delete(key)
            finally:
                if session is not None:
                    with self._lock:
                        self._reserved_slots -= 1

    def shutdown(self) -> None:
        with self._shutdown_lock:
            with self._lifecycle:
                if self._stop.is_set():
                    return
                self._stop.set()
                self._lifecycle.notify_all()
                self._lifecycle.wait_for(lambda: not self._changing_ids)
            with self._lock:
                sessions = tuple(self._sessions.items())
                self._sessions.clear()
                runner = self._runner
            if runner is not None:
                runner.join()
            for _, session in sessions:
                if session._ticker is not None:
                    session._ticker.shutdown(cancel_futures=True)
            for key, session in sessions:
                with session.lock:
                    if session.runtime is not None:
                        session.runtime.pause()
                        try:
                            self._queue_save(key, session)
                        except Exception:
                            LOGGER.warning("Failed to capture shutdown checkpoint")
                    session._retired = True
            for _, session in sessions:
                if session._writer is not None:
                    session._writer.shutdown()
            for _, session in sessions:
                with session.lock:
                    session.runtime = None
                    session.agent.close()

    def health(self) -> dict[str, object]:
        """Report this running API and the provider actually wired into the agent."""
        if self._config.mode == "live":
            if self._config.has_valid_key:
                return {
                    "status": "ok",
                    "service": "GroundTruth local agent API",
                    "provider": "GroqIntentProvider",
                    "provider_mode": f"Live LLM ({self._config.model})",
                    "llm_ready": True,
                    "model": self._config.model,
                    "error": None,
                }
            else:
                return {
                    "status": "degraded",
                    "service": "GroundTruth local agent API",
                    "provider": "GroqIntentProvider",
                    "provider_mode": "Live LLM (unconfigured)",
                    "llm_ready": False,
                    "model": self._config.model,
                    "error": (
                        "GROQ_API_KEY is not configured. Add GROQ_API_KEY to .env "
                        "or switch to offline mode."
                    ),
                }
        else:
            return {
                "status": "ok",
                "service": "GroundTruth local agent API",
                "provider": "RuleBasedIntentProvider",
                "provider_mode": "Offline (deterministic rules)",
                "llm_ready": False,
                "model": None,
                "error": None,
            }


def _canonical_session_id(session_id: str) -> str:
    try:
        return str(UUID(session_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("X-Session-ID must be a valid UUID.") from exc


def _model_to_dict(value: object) -> dict[str, object]:
    return value.model_dump(mode="json")  # type: ignore[attr-defined,no-any-return]


def _edge_to_dict(
    source: object, target: object, key: object, data: dict[str, object]
) -> dict[str, object]:
    context = data.get("context")
    return {
        "source": str(source),
        "target": str(target),
        "key": str(key),
        "fact_id": str(data.get("fact_id", key)),
        "predicate": data.get("predicate"),
        "source_agent": data.get("source_agent"),
        "confidence_score": data.get("confidence_score"),
        "observed_at": _iso(data.get("observed_at")),
        "context": context.model_dump(mode="json") if hasattr(context, "model_dump") else context,
    }


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _response_to_dict(response: AgentResponse) -> dict[str, object]:
    react_trace_dict: dict[str, object] | None = None
    if response.react_trace is not None:
        react_trace_dict = {
            "tool_name": response.react_trace.tool_name,
            "tool_call_id": response.react_trace.tool_call_id,
            "arguments": response.react_trace.arguments,
            "approved_operations": response.react_trace.approved_operations,
            "execution_summary": response.react_trace.execution_summary,
            "explanation_source": response.react_trace.explanation_source,
            "model": response.react_trace.model,
            "operations": response.react_trace.operations,
        }

    return {
        "question": response.question,
        "answer": response.answer,
        "status": response.status,
        "perspectives": response.perspectives,
        "revisions": [
            {
                "successor": _model_to_dict(revision.successor),
                "audit_event": _model_to_dict(revision.audit_event),
            }
            for revision in response.revisions
        ],
        "audit_events": [_model_to_dict(event) for event in response.audit_events],
        "audit_trails": [_model_to_dict(trail) for trail in getattr(response, "audit_trails", [])],
        "active_facts": [_model_to_dict(fact) for fact in response.active_facts],
        "sensor_telemetry": response.sensor_telemetry,
        "plan_verifiable": response.plan_verifiable,
        "plan_reason": getattr(response, "plan_reason", None),
        "react_trace": react_trace_dict,
    }
