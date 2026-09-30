"""Tests for the composition boundary."""

from src.composition import CompositionService
from src.declarative.memory_repository import MemoryRepository
from src.procedural.capability_validator import CapabilityValidator
from src.procedural.execution_results import PlanExecutionResult
from src.procedural.fallback_results import PlanningOutcome
from src.procedural.intent_coverage_reviewer import IntentCoverageReviewer
from src.procedural.intent_planner import IntentPlanner
from src.procedural.intent_provider import IntentProvider
from src.procedural.plan_builder import PlanBuilder
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import RobotState, WorldState


class FakeIntentProvider(IntentProvider):
    def propose_intent(self, user_question: str) -> dict:
        return {
            "intent": "historical_fact_lookup",
            "entity_mentions": ["route A"],
            "user_question": user_question,
        }

    def repair_intent(self, user_question: str, validation_error: str) -> dict:
        raise NotImplementedError

    def reconsider_unsupported(self, user_question: str, allowed_intents: tuple[str, ...]) -> dict:
        raise NotImplementedError


def test_composition_success_flow():
    with MemoryRepository(":memory:") as memory:
        memory.add_alias("route A", "route_A")

        world = WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="room_1",
                frame_of_reference="robot_base",
            )
        )
        env = MockEnvironment(world=world)

        validator = CapabilityValidator(env.get_capabilities())
        resolver = memory
        builder = PlanBuilder(resolver, validator)
        provider = FakeIntentProvider()
        reviewer = IntentCoverageReviewer(validator)

        planner = IntentPlanner(provider, reviewer)
        composition = CompositionService(planner, builder, memory, env)

        result = composition.process_question("What is the history of route A?")

        assert isinstance(result, PlanExecutionResult)
        assert len(result.memory_results) == 1
        assert len(result.observation_results) == 0
        assert len(result.audit_results) == 0


def test_composition_fallback_flow():
    with MemoryRepository(":memory:") as memory:
        # We don't add the alias, so entity resolution will fail (missing_entity)
        world = WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="room_1",
                frame_of_reference="robot_base",
            )
        )
        env = MockEnvironment(world=world)

        validator = CapabilityValidator(env.get_capabilities())
        resolver = memory
        builder = PlanBuilder(resolver, validator)
        provider = FakeIntentProvider()
        reviewer = IntentCoverageReviewer(validator)

        planner = IntentPlanner(provider, reviewer)
        composition = CompositionService(planner, builder, memory, env)

        from unittest.mock import MagicMock

        env.observe = MagicMock()
        memory.query_facts = MagicMock()

        result = composition.process_question("What is the history of route A?")

        assert isinstance(result, PlanningOutcome)
        assert result.fallback is not None
        assert result.fallback.category == "missing_entity"

        env.observe.assert_not_called()
        memory.query_facts.assert_not_called()
