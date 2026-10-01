"""Session-scoped application service behind the local browser API."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from src.agent import AgentResponse, GroundedAgent
from src.contracts import FactAssertion, FactQuery, SpatialContext
from src.declarative.memory_repository import MemoryRepository
from src.procedural.intent_provider import IntentProviderUnavailableError
from src.providers import ProviderConfig, create_provider, get_provider_config
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import (
    AmbientLight,
    Obstacle,
    RobotState,
    SimulatedObject,
    WorldState,
)

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
    suggested_question: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.agent = _new_agent(self.config)

    def reset(self) -> None:
        """Clear session activity and restore the neutral facility belief baseline."""
        self.agent.close()
        self.agent = _new_agent(self.config)
        self.history.clear()
        self.suggested_question = ""

    def _reset_without_baseline(self) -> None:
        """Clear all session data before loading a controlled scenario preset."""
        self.agent.close()
        self.agent = _new_agent(self.config, seed_default_beliefs=False)
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

        response = self.agent.ask(cleaned)
        item = _response_to_dict(response)
        self.history.append(item)
        self.suggested_question = ""
        return item

    def snapshot(self) -> dict[str, object]:
        """Read actual environment, graph, active beliefs, and full memory ledger."""
        graph = self.agent.graph.get_graph()
        nodes = sorted(str(node) for node in graph.nodes)
        edges = [
            _edge_to_dict(u, v, key, data) for u, v, key, data in graph.edges(keys=True, data=True)
        ]
        active_facts = self.agent.memory.query_facts(FactQuery(active_only=True))
        all_facts = self.agent.memory.query_facts(FactQuery(active_only=False))
        world = self.agent.environment.get_world_state()

        audit_events: dict[str, dict[str, object]] = {}
        for item in self.history:
            for event in item.get("audit_events", []):
                if isinstance(event, dict):
                    audit_events[str(event["event_id"])] = event

        return {
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
            "audit_events": list(audit_events.values()),
            "environment": world.model_dump(mode="json"),
            "capabilities": [
                capability.model_dump(mode="json")
                for capability in self.agent.environment.get_capabilities()
            ],
        }


class AgentService:
    """Thread-safe registry for browser sessions and app metadata."""

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = threading.Lock()
        self._config = config or get_provider_config()

    def get_session(self, session_id: str) -> AgentSession:
        """Return the session identified by a caller-provided UUID."""
        try:
            canonical_id = str(UUID(session_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("X-Session-ID must be a valid UUID.") from exc

        with self._lock:
            if canonical_id not in self._sessions:
                if len(self._sessions) >= 128:
                    raise RuntimeError("The local demo has reached its 128-session limit.")
                self._sessions[canonical_id] = AgentSession(config=self._config)
            return self._sessions[canonical_id]

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
