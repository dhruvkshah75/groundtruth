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


def _new_agent(config: ProviderConfig | None = None) -> GroundedAgent:
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
    return GroundedAgent.create(
        memory=memory,
        environment=MockEnvironment(world=world),
        provider=provider,
    )


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
        """Discard the current ephemeral ledger and create a fresh agent."""
        self.agent.close()
        self.agent = _new_agent(self.config)
        self.history.clear()
        self.suggested_question = ""

    def load_scenario(self, name: str) -> None:
        """Reset this session and seed one of the documented demonstration worlds."""
        self.reset()
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
                        )
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
                        )
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
