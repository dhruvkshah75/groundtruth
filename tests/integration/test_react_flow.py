"""Integration tests for the LLM ReAct tool calling cycle."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agent import GroundedAgent
from src.contracts import FactAssertion, SpatialContext
from src.declarative.active_graph import ActiveBeliefGraph, GraphSyncedRepository
from src.declarative.memory_repository import MemoryRepository
from src.procedural.capability_validator import CapabilityValidator
from src.procedural.epistemic_evaluator import EpistemicEvaluator
from src.procedural.intent_coverage_reviewer import IntentCoverageReviewer
from src.procedural.intent_planner import IntentPlanner
from src.procedural.intent_provider import IntentProviderUnavailableError
from src.procedural.plan_builder import PlanBuilder
from src.providers import GroqIntentProvider, ProviderConfig
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import (
    AmbientLight,
    Obstacle,
    RobotState,
    SimulatedObject,
    WorldState,
)
from src.web.service import AgentService


class FakeChatCompletionMessage:
    def __init__(self, tool_calls: list[Any] | None = None, content: str | None = None) -> None:
        self.tool_calls = tool_calls
        self.content = content


class FakeChatChoice:
    def __init__(self, message: FakeChatCompletionMessage) -> None:
        self.message = message


class FakeChatCompletion:
    def __init__(self, choices: list[FakeChatChoice]) -> None:
        self.choices = choices


class FakeToolCall:
    def __init__(self, name: str, arguments: str, call_id: str = "call_react_1") -> None:
        self.id = call_id
        self.function = MagicMock(name=name, arguments=arguments)
        self.function.name = name
        self.function.arguments = arguments


class FakeGroqClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float = 0.0,
    ) -> Any:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "temperature": temperature,
            }
        )
        if not self.responses:
            raise RuntimeError("No remaining mock responses in FakeGroqClient")
        next_resp = self.responses.pop(0)
        if isinstance(next_resp, Exception):
            raise next_resp
        return next_resp


def _build_test_agent(client: FakeGroqClient) -> GroundedAgent:
    memory = MemoryRepository(":memory:")
    memory.add_alias("front route", "route_A")
    memory.add_alias("route a", "route_A")
    memory.add_alias("red box", "box_01")
    memory.add_alias("the box", "box_01")

    abg = ActiveBeliefGraph(memory)
    synced_repo = GraphSyncedRepository(memory, abg)

    robot = RobotState(
        robot_id="robot_1",
        x_cm=0.0,
        y_cm=0.0,
        direction="north",
        location="room_101",
        frame_of_reference="robot_base",
    )
    env = MockEnvironment(world=WorldState(robot=robot))

    provider = GroqIntentProvider(client=client, model="llama-3.3-70b-versatile")
    validator = CapabilityValidator(env.get_capabilities())
    reviewer = IntentCoverageReviewer(validator)
    planner = IntentPlanner(provider, reviewer)
    builder = PlanBuilder(synced_repo, validator)
    from src.composition import CompositionService

    composition = CompositionService(planner, builder, synced_repo, env)
    evaluator = EpistemicEvaluator()

    return GroundedAgent(
        composition=composition,
        memory=memory,
        graph=abg,
        environment=env,
        evaluator=evaluator,
        provider=provider,
    )


def test_react_loop_scenario_a_grounded_conflict_resolution() -> None:
    # Turn 1: Model proposes current_route_status tool call
    turn1_call = FakeToolCall(
        name="current_route_status",
        arguments='{"entity_mentions": ["front route"]}',
        call_id="call_scen_a_1",
    )
    turn1_resp = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[turn1_call]))]
    )

    # Turn 2: Model synthesizes grounded explanation given tool results
    turn2_resp = FakeChatCompletion(
        choices=[
            FakeChatChoice(
                message=FakeChatCompletionMessage(
                    content=(
                        "The route was previously listed as clear by static map blueprints, "
                        "but real-time LiDAR detected an obstacle 12.0cm ahead, "
                        "updating internal beliefs."
                    )
                )
            )
        ]
    )

    client = FakeGroqClient([turn1_resp, turn2_resp])
    agent = _build_test_agent(client)

    # Seed Scenario A state: static map says clear, physical obstacle placed at 12cm
    now = datetime.now(UTC)
    agent.environment.replace_world(
        WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="room_101",
                frame_of_reference="robot_base",
            ),
            obstacles={
                "obs_01": Obstacle(
                    obstacle_id="obs_01",
                    x_cm=0.0,
                    y_cm=12.0,
                    location="room_101",
                    active=True,
                )
            },
        )
    )
    agent.memory.record_fact(
        FactAssertion(
            subject="route_A",
            predicate="status_is",
            object="clear",
            source_agent="static_map",
            confidence_score=0.95,
            observed_at=now,
            context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
            evidence={"map_version": "v1.0"},
        )
    )

    response = agent.ask("Is the front route clear?")

    # Verify Turn 1 & Turn 2 happened
    assert len(client.calls) == 2
    assert response.status == "grounded_conflict_resolved"
    assert "LiDAR detected an obstacle 12.0cm ahead" in response.answer

    # Verify Python owns facts and belief revisions
    assert len(response.revisions) == 1
    assert response.revisions[0].successor.object == "blocked"

    # Verify ReAct trace is exposed with detailed operations
    assert response.react_trace is not None
    assert response.react_trace.tool_name == "current_route_status"
    assert response.react_trace.tool_call_id == "call_scen_a_1"
    assert response.react_trace.explanation_source == "llm"
    assert response.react_trace.model == "llama-3.3-70b-versatile"
    assert any("observe: lidar_scan" in op for op in response.react_trace.approved_operations)
    assert len(response.react_trace.operations) >= 2
    assert all(op["ran"] is True for op in response.react_trace.operations)
    assert any(
        op["name"] == "lidar" and len(op["evidence_ids"]) > 0
        for op in response.react_trace.operations
    )


def test_react_loop_scenario_b_three_perspectives() -> None:
    # Turn 1: Model proposes current_object_perception tool call
    turn1_call = FakeToolCall(
        name="current_object_perception",
        arguments='{"entity_mentions": ["red box"]}',
        call_id="call_scen_b_1",
    )
    turn1_resp = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[turn1_call]))]
    )

    # Turn 2: Model synthesizes grounded explanation distinguishing 3 perspectives
    turn2_resp = FakeChatCompletion(
        choices=[
            FakeChatChoice(
                message=FakeChatCompletionMessage(
                    content=(
                        "You asked about the red box. The live egocentric camera "
                        "perceives it as brown under yellow lighting, while verified "
                        "maintenance logs from bot_02 record its painted state as blue."
                    )
                )
            )
        ]
    )

    client = FakeGroqClient([turn1_resp, turn2_resp])
    agent = _build_test_agent(client)

    # Seed Scenario B: object intrinsic red, yellow ambient light, memory has painted blue by bot_02
    now = datetime.now(UTC)
    agent.environment.replace_world(
        WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="room_101",
                frame_of_reference="robot_base",
            ),
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
    agent.memory.record_fact(
        FactAssertion(
            subject="box_01",
            predicate="painted_color_is",
            object="blue",
            source_agent="bot_02",
            confidence_score=1.0,
            observed_at=now,
            context=SpatialContext(location="room_101"),
            evidence={"ticket": "MAINT-4091"},
        )
    )

    scen_b_q = (
        "What color does the user think the red box is, what color do you register it as, "
        "and what does your data history say its true state is?"
    )
    response = agent.ask(scen_b_q)

    assert len(client.calls) == 2
    assert response.status == "perspectives_tracked"
    assert response.perspectives is not None
    assert "red" in response.perspectives["user_perspective"].lower()
    assert "brown" in response.perspectives["egocentric_perspective"].lower()
    assert "blue" in response.perspectives["historical_perspective"].lower()
    assert response.react_trace is not None
    assert response.react_trace.explanation_source == "llm"


def test_react_loop_safe_fallback_when_turn2_explanation_fails() -> None:
    # Turn 1: Propose tool call successfully
    turn1_call = FakeToolCall(
        name="current_route_status",
        arguments='{"entity_mentions": ["front route"]}',
        call_id="call_fail_turn2",
    )
    turn1_resp = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[turn1_call]))]
    )

    # Turn 2: Fails with API timeout
    client = FakeGroqClient([turn1_resp, Exception("API Gateway Timeout")])
    agent = _build_test_agent(client)

    now = datetime.now(UTC)
    agent.memory.record_fact(
        FactAssertion(
            subject="route_A",
            predicate="status_is",
            object="clear",
            source_agent="static_map",
            confidence_score=0.95,
            observed_at=now,
            context=SpatialContext(location="room_101"),
            evidence={"map": "v1"},
        )
    )

    response = agent.ask("Is the front route clear?")

    # Should not crash; safe deterministic explanation is returned
    assert (
        response.status == "grounded_conflict_resolved"
        or response.status == "clear"
        or response.status != ""
    )
    assert response.react_trace is not None
    assert response.react_trace.explanation_source == "deterministic_fallback"
    assert response.answer != ""


def test_unconfigured_live_mode_service_health_and_rejection() -> None:
    config = ProviderConfig(mode="live", api_key=None)
    service = AgentService(config=config)

    health = service.health()
    assert health["status"] == "degraded"
    assert health["llm_ready"] is False
    assert "GROQ_API_KEY is not configured" in str(health["error"])

    session = service.get_session("6a35f795-c266-419a-9e17-29007f352936")
    with pytest.raises(IntentProviderUnavailableError, match="GROQ_API_KEY is not configured"):
        session.ask("Is the route clear?")


def test_adversarial_completion_rejected_by_response_guard_in_scenario_a() -> None:
    # Turn 1: Valid route proposal
    turn1_call = FakeToolCall(
        name="current_route_status",
        arguments='{"entity_mentions": ["front route"]}',
        call_id="call_adv_a",
    )
    turn1_resp = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[turn1_call]))]
    )

    # Turn 2: Adversarial completion claiming route is clear to proceed
    # even though LiDAR evidence and revision state it is blocked at 12 cm
    adversarial_text = (
        "Good news! The front route is clear to proceed, no obstacles ahead, "
        "and you can safely proceed forward."
    )
    turn2_resp = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(content=adversarial_text))]
    )

    client = FakeGroqClient([turn1_resp, turn2_resp])
    agent = _build_test_agent(client)

    agent.environment.replace_world(
        WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="room_101",
                frame_of_reference="robot_base",
            ),
            obstacles={
                "obs_01": Obstacle(
                    obstacle_id="obs_01",
                    x_cm=0.0,
                    y_cm=12.0,
                    location="room_101",
                    active=True,
                )
            },
        )
    )

    now = datetime.now(UTC)
    agent.memory.record_fact(
        FactAssertion(
            subject="route_A",
            predicate="status_is",
            object="clear",
            source_agent="static_map",
            confidence_score=0.95,
            observed_at=now,
            context=SpatialContext(location="room_101", frame_of_reference="robot_base"),
            evidence={"map_version": "v1.0"},
        )
    )

    response = agent.ask("Is the front route clear?")

    # Response guard MUST reject the adversarial text and use deterministic fallback
    assert response.react_trace is not None
    assert response.react_trace.explanation_source == "deterministic_fallback"
    assert response.answer != adversarial_text
    assert "obstruction" in response.answer.lower() or "blocked" in response.answer.lower()
    assert "safe to proceed" not in response.answer.lower()


def test_adversarial_completion_rejected_by_response_guard_in_scenario_b() -> None:
    turn1_call = FakeToolCall(
        name="current_object_perception",
        arguments='{"entity_mentions": ["red box"]}',
        call_id="call_adv_b",
    )
    turn1_resp = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(tool_calls=[turn1_call]))]
    )

    # Turn 2: Adversarial completion claiming camera saw blue and history said red
    adversarial_text = (
        "The camera perceives blue under current lighting, while the database history says red."
    )
    turn2_resp = FakeChatCompletion(
        choices=[FakeChatChoice(message=FakeChatCompletionMessage(content=adversarial_text))]
    )

    client = FakeGroqClient([turn1_resp, turn2_resp])
    agent = _build_test_agent(client)

    agent.environment.replace_world(
        WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="room_101",
                frame_of_reference="robot_base",
            ),
            objects={
                "box_01": SimulatedObject(
                    object_id="box_01",
                    x_cm=10.0,
                    y_cm=10.0,
                    location="room_101",
                    intrinsic_color="red",
                )
            },
            light=AmbientLight(intensity=0.8, color_cast="yellow"),
        )
    )

    now = datetime.now(UTC)
    agent.memory.record_fact(
        FactAssertion(
            subject="box_01",
            predicate="painted_color_is",
            object="blue",
            source_agent="bot_02",
            confidence_score=1.0,
            observed_at=now,
            context=SpatialContext(location="room_101"),
            evidence={"ticket": "MAINT-4091"},
        )
    )

    scen_b_q = (
        "What color does the user think the object is, what color do you register it as, "
        "and what does your data history say its true state is?"
    )
    response = agent.ask(scen_b_q)

    # Guard rejects misattributed perspectives
    assert response.react_trace is not None
    assert response.react_trace.explanation_source == "deterministic_fallback"
    assert response.answer != adversarial_text
